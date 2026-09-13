"""Bounded local probe and replay. No model, network or permission decisions."""
import time

from .errors import KiokukoError
from .models import digest, new_id, now
from .profile_resolver import (Candidate, MAX_CANDIDATES, MAX_HINT_CHARS, POLICY_VERSION,
                               decide, exact_match, identifiers, relative_target)
from .retrieval import tokens
from .service import insert
from .task_profiles import allowed, bound_root, current_path, decode, source_current


def candidates(db, snapshot, query, selectors, *, deadline=None):
    """IDs only until scope, limits and deterministic stage order are established."""
    scope = 'p.profile_key=? AND p.principal_id=? AND p.workspace_id=? AND p.expires_at>?'
    scope_values = (snapshot.profile_key, snapshot.principal_id, snapshot.workspace_id, now())
    values = {}
    complete = db.execute("SELECT value FROM store_metadata WHERE key='task_profile_projection_version'").fetchone()[0] == POLICY_VERSION
    query_tokens = sorted(tokens(query[:600]), key=lambda t: (-len(t), t))[:64]
    stages = [('exact', list(selectors)), ('ngram', query_tokens)]
    fts = db.execute("SELECT value FROM store_metadata WHERE key='task_profile_fts'").fetchone()[0] == '1'
    if fts:
        stages.append(('fts', [t for t in query_tokens if len(t) >= 3]))
    for kind, terms in stages:
        if not terms:
            continue
        if deadline and time.monotonic() >= deadline:
            return values, False
        if kind == 'fts':
            matching = ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms)
            sql = f'''SELECT p.id,1 AS score FROM task_profile_fts f JOIN task_profiles p ON p.id=f.profile_id
                WHERE task_profile_fts MATCH ? AND {scope} ORDER BY p.id LIMIT ?'''
            params = (matching, *scope_values, MAX_CANDIDATES + 1)
        else:
            marks = ','.join('?' for _ in terms)
            sql = f'''SELECT p.id,sum(length(s.value)) AS score FROM task_profile_signals s
                JOIN task_profiles p ON p.id=s.profile_id WHERE s.kind=? AND s.value IN ({marks}) AND {scope}
                GROUP BY p.id ORDER BY score DESC,p.id LIMIT ?'''
            params = (kind, *terms, *scope_values, MAX_CANDIDATES + 1)
        rows = db.execute(sql, params).fetchall()
        for row in rows:
            if row['id'] in values:
                continue
            if len(values) == MAX_CANDIDATES:
                return values, False
            values[row['id']] = row['score']
        if kind == 'exact' and values:
            # Exact lookup already covers all conflicting target mappings. Common
            # suffix tokens (such as "py") must not drown a precise identifier.
            return values, complete
    return values, complete


def resolve(service, db, snapshot, query, *, deadline=None, config=None):
    config = config if config is not None else service.config
    mode = config['task_profile_memory']['mode']
    if mode == 'off' or not allowed(snapshot) or not config['context_injection']['enabled']:
        return None
    if not isinstance(query, str) or digest(query) != snapshot.user_content_sha256:
        raise KiokukoError('TURN_CONTEXT_CONFLICT')
    old = db.execute('SELECT * FROM task_profile_resolutions WHERE profile_key=? AND session_id=? AND turn_id=?', snapshot.key).fetchone()
    if old:
        if old['session_generation'] != snapshot.session_generation or old['policy_version'] != POLICY_VERSION:
            raise KiokukoError('STALE_GENERATION')
        return dict(old)
    result = {'id': new_id('resolution'), **dict(zip(('profile_key', 'session_id', 'turn_id'), snapshot.key)),
              'session_generation': snapshot.session_generation, 'mode': mode, 'policy_version': POLICY_VERSION,
              'status': 'skipped', 'reason': 'no_identifier', 'scanned': 0, 'created_at': now()}
    selectors = identifiers(query)
    decisions = ()
    if selectors and (not deadline or time.monotonic() < deadline):
        root = bound_root(db, snapshot)
        if root is None:
            result.update(status='unavailable', reason='workspace_unavailable')
        elif any('/' in s or ((target := relative_target(s, root)) and current_path(root, target)) for s in selectors):
            # An explicit path (including a nonexistent one) is already a current
            # scope statement. Never replace it with a different historical path.
            result['reason'] = 'current_target'
        else:
            ids, complete = candidates(db, snapshot, query, selectors, deadline=deadline)
            complete = complete and len(query) <= 600 and len(selectors) < 8
            expanded = []
            for profile_id, score in ids.items():
                if deadline and time.monotonic() >= deadline:
                    complete = False
                    break
                row = db.execute('SELECT * FROM task_profiles WHERE id=?', (profile_id,)).fetchone()
                result['scanned'] += 1
                targets = decode(row)
                if not source_current(db, row, snapshot, root):
                    continue
                service.validate_content(row['excerpt'])
                for index, target in enumerate(targets):
                    service.validate_content(target)
                    expanded.append(Candidate(profile_id, index, target, score,
                                              exact_match(selectors, target), current_path(root, target)))
            decisions = decide(expanded, selectors, complete=complete, mode=mode)
            result.update(status='complete' if complete else 'incomplete', reason='evaluated')
    elif selectors:
        result.update(status='incomplete', reason='deadline')
    insert(db, 'task_profile_resolutions', result)
    for rank, decision in enumerate(decisions):
        c = decision.candidate
        db.execute('INSERT INTO task_profile_candidates VALUES (?,?,?,?,?)',
                   (result['id'], rank, c.profile_id, c.target_index, decision.action))
    return result


def render(service, db, snapshot, resolution, *, deadline=None, config=None):
    """Revalidate stored references on every replay; never persist copied paths."""
    config = config if config is not None else service.config
    mode = config['task_profile_memory']['mode']
    if (not resolution or mode not in {'suggest', 'resolve'} or resolution['mode'] == 'shadow'
            or not config['context_injection']['enabled']):
        return '', []
    rows = db.execute('''SELECT p.*,c.target_index,c.decision FROM task_profile_candidates c
        JOIN task_profiles p ON p.id=c.profile_id WHERE c.resolution_id=? ORDER BY c.rank''', (resolution['id'],)).fetchall()
    if not rows or (deadline and time.monotonic() >= deadline):
        return '', []
    root = bound_root(db, snapshot)
    if root is None:
        return '', []
    parts, refs = [], []
    for row in rows:
        if deadline and time.monotonic() >= deadline:
            break
        targets = decode(row)
        if not source_current(db, row, snapshot, root):
            continue
        index = row['target_index']
        if index >= len(targets):
            raise KiokukoError('TASK_PROFILE_INTEGRITY')
        target = targets[index]
        if not current_path(root, target):
            continue
        service.validate_content(target)
        label = 'Resolved target reference' if row['decision'] == 'adopt' and mode == 'resolve' else 'Possible target'
        line = f"{label}: {target} [source {row['id']}; past user mention, not permission or verified success]"
        if len('\n'.join(['KIOKUKO TASK PROFILE:'] + parts + [line])) > MAX_HINT_CHARS:
            continue
        parts.append(line)
        refs.append(row['id'])
    return ('KIOKUKO TASK PROFILE:\n' + '\n'.join(parts), refs) if parts else ('', [])
