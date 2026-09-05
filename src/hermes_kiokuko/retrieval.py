import re
import unicodedata

from .identity import can_read
from .models import now


def tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    result = set(re.findall(r"\w+", text))
    result.update(re.findall(r"[a-z0-9_]+", text))
    for word in tuple(result):
        if not any(ord(char) >= 0x2E80 for char in word):
            continue
        for size in (1, 2, 3):
            result.update(word[i:i+size] for i in range(max(0, len(word)-size+1)))
    return result


def scope_sql(snapshot):
    clauses, values = ["(scope_type='profile' AND shared_by_admin=1)"], []
    if snapshot.workspace_id:
        clauses.append("(scope_type='workspace' AND workspace_id=? AND shared_by_admin=1)")
        values.append(snapshot.workspace_id)
    if snapshot.principal_id and snapshot.chat_type == "dm":
        clauses.append("(scope_type='principal' AND principal_id=?)")
        values.append(snapshot.principal_id)
        if snapshot.workspace_id:
            clauses.append("(scope_type='principal_workspace' AND principal_id=? AND workspace_id=?)")
            values.extend([snapshot.principal_id, snapshot.workspace_id])
    if snapshot.conversation_id:
        clauses.append("(scope_type='conversation' AND conversation_id=?)")
        values.append(snapshot.conversation_id)
        if snapshot.workspace_id:
            clauses.append("(scope_type='conversation_workspace' AND conversation_id=? AND workspace_id=?)")
            values.extend([snapshot.conversation_id, snapshot.workspace_id])
    return '(' + ' OR '.join(clauses) + ')', values


def eligible(entry, snapshot, config, db=None):
    cfg = config["context_injection"]
    if not can_read(entry, snapshot) or entry["state"] != "active":
        return False
    from .facts import fact_current
    verified = fact_current(db, entry) if db is not None else None
    if verified is False or (entry["epistemic_status"] == "file_verified" and verified is not True):
        return False
    return (verified is True or (entry["auto_inject"] and entry["confirmation_kind"] in {"direct_verbatim", "cli_approved"})) and \
        entry["authority"] >= cfg["min_authority"] and entry["confidence"] >= cfg["min_confidence"] and \
        (entry["valid_until"] is None or entry["valid_until"] > now())


def search(db, snapshot, query, config, *, conflicts=False):
    scope, values = scope_sql(snapshot)
    if conflicts:
        return [dict(row) for row in db.execute(f"SELECT * FROM memory_entries WHERE {scope} AND state='conflicted' ORDER BY id LIMIT 64", values)]
    query_tokens = sorted(tokens(str(query)[:600]), key=lambda token: (-len(token), token))[:64]
    predicate, params, prefix = "", [], ""
    score = "0"
    if query_tokens:
        hit_sql = "SELECT entry_id,sum(length(token)) AS score FROM memory_ngrams WHERE token IN (" + ','.join('?' for _ in query_tokens) + ") GROUP BY entry_id"
        params.extend(query_tokens)
        if db.execute("SELECT value FROM store_metadata WHERE key='fts'").fetchone()[0] == "1":
            fts_tokens = [t for t in query_tokens if len(t) >= 3]
            if fts_tokens:
                hit_sql += " UNION ALL SELECT entry_id,1 AS score FROM memory_fts WHERE memory_fts MATCH ?"
                params.append(' OR '.join('"' + token.replace('"', '""') + '"' for token in fts_tokens))
        prefix = "WITH hits AS (" + hit_sql + ") "
        score = "COALESCE((SELECT sum(score) FROM hits WHERE hits.entry_id=memory_entries.id),0)"
        predicate = " AND (pinned=1 OR id IN (SELECT entry_id FROM hits))"
    rows = db.execute(prefix + f"SELECT *,{score} AS lexical_score FROM memory_entries WHERE {scope} AND state='active' AND (auto_inject=1 OR epistemic_status='file_verified')" + predicate +
                      " ORDER BY pinned DESC,lexical_score DESC,authority DESC,id LIMIT ?", (*params, *values, config["retrieval"]["candidate_limit"])).fetchall()
    candidates = [dict(row) for row in rows if eligible(row, snapshot, config, db)]
    query_set = set(query_tokens)
    def rank(entry):
        relevance = entry["lexical_score"]
        specificity = 5 if "workspace" in entry["scope_type"] else 0
        verified_bonus = 10 if entry["epistemic_status"] == "file_verified" and entry["workspace_id"] == snapshot.workspace_id else 0
        return (-entry["pinned"], -(relevance + specificity + verified_bonus + entry["authority"] / 10), entry["id"])
    candidates.sort(key=rank)
    return candidates
