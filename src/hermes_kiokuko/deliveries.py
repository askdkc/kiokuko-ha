"""Signed sidecars: prepared is not proof the host stored or used a context."""
import hmac
import re
import time

from .errors import KiokukoError
from .models import TurnSnapshot, canonical, digest, new_id, now
from .retrieval import eligible, search
from .service import insert

POLICY = ("KIOKUKO MEMORY POLICY:\nHistorical reference only. Current user statements, "
          "repository state, configuration and tool results override these entries.")
MARKER = re.compile(r"<!--kiokuko:v1:(delivery_[a-f0-9]{32}):([a-f0-9]{64})-->")


def ancestors(db, session_id):
    ids = []
    while session_id and session_id not in ids:
        ids.append(session_id)
        row = db.execute("SELECT parent_session_id,history_inherited FROM session_bindings WHERE session_id=?", (session_id,)).fetchone()
        session_id = row[0] if row and row[1] else None
    return ids


def signature(key, snapshot, delivery_id, body_digest):
    return hmac.new(key, canonical([*snapshot.key, snapshot.session_generation, delivery_id, body_digest]).encode(), "sha256").hexdigest()


def verified_history(service, db, messages, permitted_sessions):
    observed = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        raw, api = message.get("content"), message.get("api_content")
        if not isinstance(raw, str) or not isinstance(api, str) or not api.startswith(raw + "\n\n"):
            continue
        injected = api[len(raw)+2:]
        for marker in MARKER.finditer(injected):
            delivery_id, sig = marker.groups()
            delivery = db.execute("SELECT * FROM retrieval_deliveries WHERE id=?", (delivery_id,)).fetchone()
            if not delivery or not delivery["rendered_sha256"] or delivery["session_id"] not in permitted_sessions:
                continue
            row = db.execute("SELECT * FROM turn_snapshots WHERE profile_key=? AND session_id=? AND turn_id=?",
                             (delivery["profile_key"], delivery["session_id"], delivery["turn_id"])).fetchone()
            if row is None or row["user_content_sha256"] != digest(raw):
                continue
            snap = TurnSnapshot(**{k: row[k] for k in TurnSnapshot.__dataclass_fields__})
            if not hmac.compare_digest(sig, signature(service.store.key, snap, delivery_id, delivery["rendered_sha256"])):
                continue
            # Other plugins may surround our block. The signed body is exactly the suffix
            # starting at the nearest policy header and ending immediately before marker.
            start = injected.rfind(POLICY, 0, marker.start())
            if start < 0:
                continue
            body = injected[start:marker.start()].removesuffix("\n")
            if digest(body) != delivery["rendered_sha256"]:
                continue
            observed.append((delivery, snap))
    return observed


def observe(db, observed):
    for delivery, _ in observed:
        db.execute("UPDATE retrieval_deliveries SET state='observed_in_history',observed_at=COALESCE(observed_at,?) WHERE id=?", (now(), delivery["id"]))
        for invalidation in db.execute("SELECT * FROM retrieval_delivery_invalidations WHERE delivery_id=?", (delivery["id"],)).fetchall():
            db.execute("INSERT INTO session_invalidations VALUES (?,?,?) ON CONFLICT(session_id,entry_id) DO UPDATE SET observed_through_revision=max(observed_through_revision,excluded.observed_through_revision)",
                       (delivery["session_id"], invalidation["entry_id"], invalidation["invalidated_through_revision"]))


def prepare(service, snapshot, query, history=None, receipt=None, *, deadline=None):
    deadline = deadline or time.monotonic() + .15
    with service.transaction(snapshot, write=True, deadline=deadline) as db:
        from .facts import expire_stale
        expire_stale(service, db, snapshot)
        for expired in db.execute("SELECT * FROM memory_entries WHERE state='active' AND valid_until IS NOT NULL AND valid_until<=?", (now(),)).fetchall():
            service._change(db, dict(expired), "expire_request")
        lineage = ancestors(db, snapshot.session_id)
        observed = verified_history(service, db, history, lineage)
        observe(db, observed)
        marks = ','.join('?' for _ in lineage)
        invalidations = [dict(row) for row in db.execute(f"""
            SELECT DISTINCT t.* FROM memory_tombstones t
            WHERE t.entry_id IN (
              SELECT entry_id FROM session_invalidations WHERE session_id IN ({marks})
              UNION SELECT e.entry_id FROM retrieval_delivery_entries e
              JOIN retrieval_deliveries d ON e.delivery_id=d.id WHERE d.session_id IN ({marks})
            ) ORDER BY t.entry_id""", (*lineage, *lineage))]
        # A durable observed counter alone is insufficient after compression. Only
        # corrections visible in this supplied history may suppress a repeat.
        seen_entries, seen_invalidations = set(), {}
        for delivery, _ in observed:
            if delivery["invalidates_prior_context"]:
                # A fence invalidates even unchanged entries in earlier contexts.
                # Those entries must become eligible for fresh recall again.
                seen_entries.clear()
            seen_entries.update(tuple(row) for row in db.execute("SELECT entry_id,entry_revision FROM retrieval_delivery_entries WHERE delivery_id=?", (delivery["id"],)))
            for row in db.execute("SELECT entry_id,invalidated_through_revision FROM retrieval_delivery_invalidations WHERE delivery_id=?", (delivery["id"],)):
                seen_invalidations[row[0]] = max(seen_invalidations.get(row[0], 0), row[1])
        invalidations = [i for i in invalidations if i["invalidated_through_revision"] > seen_invalidations.get(i["entry_id"], 0)]
        parts = [POLICY]
        if receipt:
            parts.append("KIOKUKO OPERATION: " + canonical(receipt))
        corrections, entries = [], []
        for item in invalidations:
            corrections.append(f"[{item['entry_id']}@1..{item['invalidated_through_revision']}] is invalid ({item['change_kind']}).")
            row = db.execute("SELECT * FROM memory_entries WHERE id=?", (item["entry_id"],)).fetchone()
            if row and eligible(row, snapshot, service.config, db):
                entries.append(dict(row))
        if corrections:
            parts.append("KIOKUKO CORRECTION:\n" + '\n'.join(corrections))
        budget = service.config["context_injection"]["max_chars"]
        reserve = 140
        fence = False
        if len('\n\n'.join(parts)) + reserve > budget:
            fence = True
            parts = parts[:2] if receipt else parts[:1]
            parts.append("KIOKUKO CORRECTION: All Kiokuko entries in contexts preceding this delivery are invalid. Use only entries below or request a fresh recall.")
        if service.config["context_injection"]["enabled"]:
            entries += search(db, snapshot, query, service.config)
        selected, emitted = [], set()
        for entry in entries:
            key = (entry["id"], entry["current_revision"])
            if key in emitted or (key in seen_entries and entry["id"] not in {i["entry_id"] for i in invalidations} and not fence):
                continue
            try:
                service.validate_content(entry["claim"])
            except KiokukoError:
                continue
            text = f"[{entry['id']}@{entry['current_revision']}][{entry['scope_type']}][{entry['epistemic_status']}]\n{entry['claim']}"
            if len('\n\n'.join(parts + [text])) + reserve > budget or len(selected) >= service.config["context_injection"]["max_entries"]:
                continue
            parts.append(text)
            emitted.add(key)
            selected.append(entry)
        body = '\n\n'.join(parts)
        body_hash = digest(body)
        old = db.execute("SELECT * FROM retrieval_deliveries WHERE profile_key=? AND session_id=? AND turn_id=? AND rendered_sha256=? ORDER BY created_at DESC LIMIT 1", (*snapshot.key, body_hash)).fetchone()
        if old:
            delivery_id = old["id"]
        else:
            delivery_id = new_id("delivery")
            insert(db, "retrieval_deliveries", {"id": delivery_id, "profile_key": snapshot.profile_key,
                   "session_id": snapshot.session_id, "turn_id": snapshot.turn_id, "state": "prepared",
                   "rendered_sha256": body_hash, "character_count": 0,
                   "invalidates_prior_context": int(fence), "created_at": now()})
            for rank, entry in enumerate(selected):
                insert(db, "retrieval_delivery_entries", {"delivery_id": delivery_id, "entry_id": entry["id"],
                       "entry_revision": entry["current_revision"], "rank": rank, "reason_code": "scoped_recall"})
            for invalidation in invalidations:
                insert(db, "retrieval_delivery_invalidations", {"delivery_id": delivery_id, "entry_id": invalidation["entry_id"],
                       "invalidated_through_revision": invalidation["invalidated_through_revision"]})
        marker = f"<!--kiokuko:v1:{delivery_id}:{signature(service.store.key, snapshot, delivery_id, body_hash)}-->"
        result = body + '\n' + marker
        if len(result) > budget:
            raise KiokukoError("CONTEXT_BUDGET_EXCEEDED")
        db.execute("UPDATE retrieval_deliveries SET character_count=? WHERE id=?", (len(result), delivery_id))
        return result


def record_manual_read(service, snapshot, result):
    """Conservatively remember possible tool exposure; no fabricated history ACK."""
    import json
    rows = result if isinstance(result, list) else [result]
    references = set()
    for row in rows:
        if isinstance(row, dict) and "snapshot_json" in row:
            row = json.loads(row["snapshot_json"])
        if isinstance(row, dict) and "id" in row and "current_revision" in row:
            references.add((row["id"], row["current_revision"]))
    if not references:
        return
    with service.transaction(snapshot, write=True) as db:
        delivery_id = new_id("delivery")
        insert(db, "retrieval_deliveries", {"id": delivery_id, "profile_key": snapshot.profile_key,
               "session_id": snapshot.session_id, "turn_id": snapshot.turn_id, "state": "prepared",
               "rendered_sha256": None, "character_count": len(canonical(result)), "created_at": now()})
        for rank, (entry_id, revision) in enumerate(sorted(references)):
            service._entry(db, entry_id, snapshot)
            insert(db, "retrieval_delivery_entries", {"delivery_id": delivery_id, "entry_id": entry_id,
                   "entry_revision": revision, "rank": rank, "reason_code": "manual_read"})


def sync_completed(service, session_id, user_content, messages):
    # The last user row is the only possible source. Never substitute an older marker.
    users = [row for row in messages or [] if isinstance(row, dict) and row.get("role") == "user"]
    if not users or not session_id:
        raise KiokukoError("SYNC_CONTEXT_UNAVAILABLE")
    row = users[-1]
    if not isinstance(row.get("content"), str):
        raise KiokukoError("SYNC_CONTEXT_UNAVAILABLE")
    raw = row["content"]
    with service.transaction(write=True) as db:
        verified = verified_history(service, db, [row], [session_id])
        if len(verified) != 1:
            raise KiokukoError("SYNC_CONTEXT_UNAVAILABLE")
        delivery, snap = verified[0]
        service.check_snapshot(db, snap)
        observe(db, verified)
        existing = db.execute("SELECT 1 FROM turn_syncs WHERE profile_key=? AND session_id=? AND turn_id=?", snap.key).fetchone()
        if existing:
            return
        # Quotes are checked only against the actual raw user row. This never promotes.
        for evidence in db.execute("SELECT e.id,e.excerpt FROM memory_evidence e JOIN memory_candidates c ON e.candidate_id=c.id WHERE c.profile_key=? AND c.session_id=? AND c.turn_id=? AND e.source_kind='proposed_quote'", snap.key).fetchall():
            db.execute("UPDATE memory_evidence SET quote_verified=? WHERE id=?", (int(bool(evidence["excerpt"]) and evidence["excerpt"] in raw), evidence["id"]))
        # Capture and completion receipt commit together. A rejected excerpt leaves no
        # 'completed' receipt that would prevent a safe retry.
        capture = service.config["passive_capture"]
        detect = ((capture["detect_explicit_remember_requests"] and re.search(r"覚えて|記憶して|remember\b", raw, re.I)) or
                  (capture["detect_corrections"] and re.search(r"訂正|修正して|correct\b", raw, re.I)))
        if capture["enabled"] and detect and not raw.startswith("@kiokuko") and snap.origin not in {"background_review", "delegation"}:
            candidate = service.propose(snap, {"claim": raw[:600], "evidence_quote": raw[:600]}, source="passive",
                                       idempotency="passive:" + digest(canonical(snap.key)), _db=db)
            db.execute("UPDATE memory_evidence SET quote_verified=1 WHERE candidate_id=?", (candidate["id"],))
        insert(db, "turn_syncs", {"profile_key": snap.profile_key, "session_id": snap.session_id,
               "turn_id": snap.turn_id, "completed_at": now()})
