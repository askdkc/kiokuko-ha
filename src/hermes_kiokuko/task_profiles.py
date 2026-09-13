"""Authenticated, bounded task history; independent of approved memory entries."""
from datetime import datetime, timedelta
import json
import os
from pathlib import PurePosixPath
import stat
import time

from .errors import KiokukoError
from .models import TurnSnapshot, canonical, digest, new_id, now
from .profile_resolver import POLICY_VERSION, MAX_TARGETS, identifiers, relative_target
from .retrieval import tokens
from .service import insert
from .workspace import resolve_workspace

RETENTION_DAYS = 30
MAX_PROFILES = 1000
BATCH_SIZE = 64
FENCE = 'KIOKUKO TASK PROFILE CORRECTION: All earlier task-profile hints are invalid. Use only current hints below.'


def allowed(snapshot):
    return bool(snapshot.origin in {'cli', 'cli_user', 'dm'} and snapshot.chat_type == 'dm'
                and snapshot.principal_id and snapshot.conversation_id and snapshot.workspace_id)


def bound_root(db, snapshot):
    row = db.execute('SELECT * FROM snapshot_roots WHERE profile_key=? AND session_id=? AND turn_id=?', snapshot.key).fetchone()
    if row is None:
        return None
    identity = resolve_workspace(row['canonical_root'])
    mapped = db.execute('SELECT workspace_id FROM workspace_aliases WHERE identity_hash=?', (identity,)).fetchone()
    if identity != row['identity_hash'] or (mapped[0] if mapped else identity) != snapshot.workspace_id:
        raise KiokukoError('WORKSPACE_CHANGED')
    return row['canonical_root']


def current_path(root, relative):
    """Check one explicit relative path with no symlink traversal or file reads."""
    if relative_target(relative, root) != relative:
        return False
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        return stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def decode(row):
    try:
        targets = json.loads(row['targets_json'])
        if (not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS
                or any(not isinstance(t, str) or relative_target(t, '/') != t for t in targets)
                or len(set(targets)) != len(targets)
                or row['policy_version'] != POLICY_VERSION
                or digest(canonical([row['excerpt'], targets])) != row['content_sha256']):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise KiokukoError('TASK_PROFILE_INTEGRITY') from None
    return targets


def source_current(db, row, snapshot, root):
    if (row['profile_key'], row['principal_id'], row['workspace_id']) != (
            snapshot.profile_key, snapshot.principal_id, snapshot.workspace_id):
        return False
    source = db.execute('''SELECT s.*,b.generation,r.canonical_root,r.identity_hash
        FROM turn_snapshots s JOIN session_bindings b ON b.session_id=s.session_id
        JOIN snapshot_roots r USING(profile_key,session_id,turn_id)
        JOIN turn_syncs t USING(profile_key,session_id,turn_id)
        WHERE s.profile_key=? AND s.session_id=? AND s.turn_id=?''',
        (row['profile_key'], row['session_id'], row['turn_id'])).fetchone()
    return bool(source and source['session_generation'] == source['generation'] == row['session_generation']
                and source['principal_id'] == row['principal_id'] and source['workspace_id'] == row['workspace_id']
                and source['conversation_id'] == row['conversation_id'] and source['chat_type'] == 'dm'
                and source['origin'] in {'cli', 'cli_user', 'dm'} and source['canonical_root'] == root
                and row['expires_at'] > now())


def project(db, row):
    targets = decode(row)
    db.execute('DELETE FROM task_profile_documents WHERE profile_id=?', (row['id'],))
    db.execute('DELETE FROM task_profile_signals WHERE profile_id=?', (row['id'],))
    text = row['excerpt'] + '\n' + '\n'.join(targets)
    insert(db, 'task_profile_documents', {'profile_id': row['id'], 'text': text})
    exact = {v for t in targets for v in (t, PurePosixPath(t).name)}
    db.executemany('INSERT INTO task_profile_signals VALUES (?,?,?)',
                   [(row['id'], 'exact', value) for value in sorted(exact)] +
                   [(row['id'], 'ngram', value) for value in sorted(tokens(text))])


def remove(db, profile_id):
    # Invalidate the signature before erasing content. Content-free delivery refs
    # remain so compressed/unobserved history still receives a correction fence.
    db.execute('''UPDATE retrieval_deliveries SET rendered_sha256=NULL WHERE id IN
        (SELECT delivery_id FROM task_profile_deliveries WHERE profile_id=?)''', (profile_id,))
    db.execute('DELETE FROM task_profiles WHERE id=?', (profile_id,))


def invalidate_session(db, session_id):
    for row in db.execute('SELECT id FROM task_profiles WHERE session_id=?', (session_id,)).fetchall():
        remove(db, row[0])


def invalidate_workspace_binding(db, identity_hash):
    rows = db.execute('''SELECT p.id FROM task_profiles p JOIN snapshot_roots r
        USING(profile_key,session_id,turn_id) WHERE r.identity_hash=?''', (identity_hash,)).fetchall()
    for row in rows:
        remove(db, row[0])


def cleanup(db, *, reserve=0):
    expired = db.execute('''SELECT p.id FROM task_profiles p JOIN session_bindings s ON s.session_id=p.session_id
        WHERE p.expires_at<=? OR p.session_generation<>s.generation ORDER BY p.created_at,p.id LIMIT ?''',
        (now(), BATCH_SIZE)).fetchall()
    for row in expired:
        remove(db, row[0])
    count = db.execute('SELECT count(*) FROM task_profiles').fetchone()[0]
    excess = min(BATCH_SIZE, max(0, count - MAX_PROFILES + reserve))
    for row in db.execute('SELECT id FROM task_profiles ORDER BY created_at,id LIMIT ?', (excess,)).fetchall():
        remove(db, row[0])
    return len(expired) + excess


def capture_completed(service, snapshot, messages):
    """Only the verified last user row of this completed host turn can be stored."""
    if service.config['task_profile_memory']['mode'] == 'off' or not allowed(snapshot):
        return None
    from .deliveries import verified_history
    users = [m for m in messages or [] if isinstance(m, dict) and m.get('role') == 'user']
    if not users:
        raise KiokukoError('SYNC_CONTEXT_UNAVAILABLE')
    message = users[-1]
    with service.transaction(snapshot, write=True, deadline=time.monotonic() + .15) as db:
        verified = verified_history(service, db, [message], [snapshot.session_id])
        if len(verified) != 1 or verified[0][1] != snapshot or not db.execute(
                'SELECT 1 FROM turn_syncs WHERE profile_key=? AND session_id=? AND turn_id=?', snapshot.key).fetchone():
            raise KiokukoError('SYNC_CONTEXT_UNAVAILABLE')
        if db.execute('SELECT 1 FROM task_profile_receipts WHERE profile_key=? AND session_id=? AND turn_id=?', snapshot.key).fetchone():
            return None
        raw = message['content']
        # Check the whole bounded request before taking a prefix; no truncated secret.
        from .security import scan
        scan(raw, max_chars=32000)
        if service.content_guard:
            service.content_guard(raw)
        excerpt = raw[:600]
        service.validate_content(excerpt)
        root = bound_root(db, snapshot)
        if root is None:
            return None
        targets = sorted({target for value in identifiers(excerpt)
                          if (target := relative_target(value, root)) and current_path(root, target)})
        if not targets:
            return None
        cleanup(db, reserve=1)
        if db.execute('SELECT count(*) FROM task_profiles').fetchone()[0] >= MAX_PROFILES:
            raise KiokukoError('TASK_PROFILE_CAPACITY')
        stamp = now()
        row = {'id': new_id('profile'), **dict(zip(('profile_key', 'session_id', 'turn_id'), snapshot.key)),
               'session_generation': snapshot.session_generation, 'principal_id': snapshot.principal_id,
               'conversation_id': snapshot.conversation_id, 'workspace_id': snapshot.workspace_id,
               'excerpt': excerpt, 'targets_json': canonical(targets),
               'content_sha256': digest(canonical([excerpt, targets])), 'policy_version': POLICY_VERSION,
               'created_at': stamp, 'expires_at': (datetime.fromisoformat(stamp) + timedelta(days=RETENTION_DAYS)).isoformat(timespec='microseconds')}
        insert(db, 'task_profiles', row)
        project(db, row)
        db.execute('INSERT INTO task_profile_receipts VALUES (?,?,?)', snapshot.key)
        return row['id']


def needs_fence(db, lineage, snapshot):
    marks = ','.join('?' for _ in lineage)
    return bool(db.execute(f'''SELECT 1 FROM task_profile_deliveries p JOIN retrieval_deliveries d ON d.id=p.delivery_id
        WHERE d.profile_key=? AND d.session_id IN ({marks}) LIMIT 1''',
        (snapshot.profile_key, *lineage)).fetchone())


def review(service, profile_id):
    with service.transaction() as db:
        row = db.execute('SELECT * FROM task_profiles WHERE id=?', (profile_id,)).fetchone()
        if row is None:
            raise KiokukoError('TASK_PROFILE_UNAVAILABLE')
        result = dict(row)
        result['targets'] = decode(row)
        return result, digest(canonical(result))


def delete(service, profile_id, review_digest):
    with service.transaction(write=True) as db:
        row = db.execute('SELECT * FROM task_profiles WHERE id=?', (profile_id,)).fetchone()
        if row is None:
            raise KiokukoError('TASK_PROFILE_UNAVAILABLE')
        reviewed = {**dict(row), 'targets': decode(row)}
        if digest(canonical(reviewed)) != review_digest:
            raise KiokukoError('APPROVAL_CHANGED')
        remove(db, profile_id)
    return {'deleted': profile_id, 'scope': 'live Kiokuko task profile only; Hermes history and backups remain'}


def reindex(service):
    # Mark partial before the first batch. A crash keeps resolve disabled until
    # this command is rerun; rebuilding one source never scans chat history.
    with service.transaction(write=True) as db:
        db.execute("UPDATE store_metadata SET value='partial' WHERE key='task_profile_projection_version'")
    cursor, count = '', 0
    while True:
        with service.transaction(write=True) as db:
            rows = db.execute('SELECT * FROM task_profiles WHERE id>? ORDER BY id LIMIT ?', (cursor, BATCH_SIZE)).fetchall()
            for row in rows:
                project(db, row)
            if not rows:
                db.execute("UPDATE store_metadata SET value=? WHERE key='task_profile_projection_version'", (POLICY_VERSION,))
                break
            cursor = rows[-1]['id']
            count += len(rows)
    return {'reindexed': count}
