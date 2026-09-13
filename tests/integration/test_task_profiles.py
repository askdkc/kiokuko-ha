import argparse
from dataclasses import replace
import json
import sqlite3

import pytest

from hermes_kiokuko.config import load_config, write_yaml
from hermes_kiokuko.deliveries import prepare, sync_completed
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.models import Identity
from hermes_kiokuko.profile_resolver import Candidate, decide, identifiers, relative_target
from hermes_kiokuko.task_profiles import (capture_completed, current_path, delete, review,
                                          reindex, FENCE)
from hermes_kiokuko.workspace import resolve_workspace


@pytest.fixture
def profiles(service, tmp_path):
    root = tmp_path / 'workspace'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'widget.py').write_text('print(1)\n')
    who = Identity('cli', 'cli', 'profile-owner', 'conversation', resolve_workspace(root), 'dm')
    def turn(raw, session='session', turn='turn', identity=None):
        return service.snapshot(session, turn, raw, identity or who, workspace_root=root)
    mode(service, 'resolve')
    return root, who, turn


def mode(service, value):
    config = load_config(service.store.home)
    config['task_profile_memory']['mode'] = value
    write_yaml(service.store.directory / 'config.yaml', config)


def complete(service, turn, raw='Please inspect `src/widget.py`', session='source', turn_id='source-turn'):
    snap = turn(raw, session, turn_id)
    context = prepare(service, snap, raw)
    messages = [{'role': 'user', 'content': raw, 'api_content': raw + '\n\n' + context}]
    synced = sync_completed(service, snap.session_id, raw, messages)
    profile_id = capture_completed(service, synced, messages)
    return profile_id, snap, messages


def count(service, table):
    with service.transaction() as db:
        return db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def test_capture_resolve_replay_delete_and_correction(service, profiles):
    root, who, turn = profiles
    source, snap, messages = complete(service, turn)
    assert source and count(service, 'task_profiles') == 1
    assert capture_completed(service, snap, messages) is None
    query = 'Please inspect `widget.py`'
    target = turn(query, 'consumer', 'one')
    context = prepare(service, target, query)
    assert 'Resolved target reference: src/widget.py' in context and source in context
    assert len(context) <= 2200
    second = prepare(service, target, query)
    assert 'Resolved target reference: src/widget.py' in second and FENCE in second
    assert prepare(service, target, query) == second
    assert count(service, 'task_profile_candidates') == 1
    record, review_hash = review(service, source)
    delete(service, source, review_hash)
    replay = prepare(service, target, query)
    assert FENCE in replay and 'src/widget.py' not in replay
    assert count(service, 'task_profiles') == 0
    assert count(service, 'task_profile_documents') == 0
    assert count(service, 'task_profile_signals') == 0
    # Receipt survives even if a new source marker is prepared after purge.
    new_context = prepare(service, snap, messages[0]['content'])
    new_messages = [{'role':'user', 'content':messages[0]['content'], 'api_content':messages[0]['content']+'\n\n'+new_context}]
    assert capture_completed(service, snap, new_messages) is None
    fresh = turn(query, 'consumer', 'two')
    after = prepare(service, fresh, query, [])
    assert FENCE in after and 'src/widget.py' not in after
    with service.transaction() as db:
        assert db.execute('SELECT profile_id FROM task_profile_candidates').fetchone()[0] is None
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('setting,label', [('off',None), ('shadow',None), ('suggest','Possible target:'), ('resolve','Resolved target reference:')])
def test_modes(service, profiles, setting, label):
    root, who, turn = profiles
    complete(service, turn)
    mode(service, setting)
    query = '`widget.py`'
    context = prepare(service, turn(query, 'consumer', setting), query)
    if label:
        assert label in context
    else:
        assert 'KIOKUKO TASK PROFILE:' not in context
    with service.transaction() as db:
        row = db.execute('SELECT * FROM task_profile_resolutions WHERE session_id=?', ('consumer',)).fetchone()
        assert (row is None) == (setting == 'off')


@pytest.mark.parametrize('query', ['Use `src/widget.py`', 'Create `new/widget.py`', 'hello', 'Fix `../widget.py`'])
def test_current_target_or_no_identifier_skips_probe(service, profiles, query):
    root, who, turn = profiles
    complete(service, turn)
    with service.transaction() as db:
        before = db.execute('SELECT count(*) FROM task_profile_candidates').fetchone()[0]
    context = prepare(service, turn(query, 'consumer', 'skip'), query)
    assert 'KIOKUKO TASK PROFILE:' not in context
    assert count(service, 'task_profile_candidates') == before


def test_ambiguous_same_basename_suggests_without_adoption(service, profiles):
    root, who, turn = profiles
    complete(service, turn)
    (root / 'tests').mkdir()
    (root / 'tests' / 'widget.py').write_text('pass')
    complete(service, turn, 'Inspect `tests/widget.py`', 'source-2')
    query = '`widget.py`'
    context = prepare(service, turn(query, 'consumer'), query)
    assert 'Resolved target' not in context
    assert 'src/widget.py' in context and 'tests/widget.py' in context


@pytest.mark.parametrize('mutation', ['principal', 'workspace', 'group', 'delegation', 'cron', 'unknown'])
def test_scope_rejection(service, profiles, mutation, tmp_path):
    root, who, turn = profiles
    complete(service, turn)
    if mutation == 'principal': identity = replace(who, principal_id='other')
    elif mutation == 'workspace':
        other = tmp_path / 'other'
        other.mkdir()
        identity = replace(who, workspace_id=resolve_workspace(other))
    elif mutation == 'group': identity = replace(who, origin='group_chat', chat_type='group')
    else: identity = replace(who, origin=mutation)
    query = '`widget.py`'
    snapshot = service.snapshot('other', mutation, query, identity, workspace_root=None if mutation=='workspace' else root)
    assert 'KIOKUKO TASK PROFILE:' not in prepare(service, snapshot, query)


@pytest.mark.parametrize('mutation', ['raw', 'signature', 'uncompleted', 'assistant', 'generation'])
def test_unverified_completion_is_rejected(service, profiles, mutation):
    root, who, turn = profiles
    raw = 'Inspect `src/widget.py`'
    snapshot = turn(raw)
    context = prepare(service, snapshot, raw)
    messages = [{'role':'user','content':raw,'api_content':raw+'\n\n'+context}]
    if mutation != 'uncompleted': sync_completed(service, snapshot.session_id, raw, messages)
    if mutation == 'raw': messages[0]['content'] += ' changed'
    if mutation == 'signature': messages[0]['api_content'] = raw+'\n\n'+context.replace('v1:', 'v0:')
    if mutation == 'assistant': messages[0]['role'] = 'assistant'
    if mutation == 'generation': service.transition(snapshot.session_id, rewound=True)
    with pytest.raises(KiokukoError): capture_completed(service, snapshot, messages)
    assert count(service, 'task_profiles') == 0


@pytest.mark.parametrize('mutation', ['expired','rewind','symlink','removed','off','partial'])
def test_invalid_source_never_adopts(service, profiles, mutation):
    root, who, turn = profiles
    source, snap, messages = complete(service, turn)
    if mutation == 'expired':
        with service.transaction(write=True) as db:
            db.execute("UPDATE task_profiles SET expires_at='2000-01-01'")
    if mutation == 'rewind': service.transition(snap.session_id, rewound=True)
    if mutation == 'removed': (root/'src/widget.py').unlink()
    if mutation == 'symlink':
        (root/'src/widget.py').unlink()
        (root/'src/widget.py').symlink_to('/etc/passwd')
    if mutation == 'off': mode(service, 'off')
    if mutation == 'partial':
        with service.transaction(write=True) as db:
            db.execute("UPDATE store_metadata SET value='partial' WHERE key='task_profile_projection_version'")
    query = '`widget.py`'
    result = prepare(service, turn(query, 'consumer'), query)
    assert 'Resolved target' not in result


def test_capture_off_and_secret_rejection(service, profiles):
    root, who, turn = profiles
    mode(service, 'off')
    source, *_ = complete(service, turn)
    assert source is None
    mode(service, 'suggest')
    raw = 'Inspect `src/widget.py` password=do-not-store'
    snap = turn(raw, 'secret')
    context = prepare(service, snap, raw)
    messages = [{'role':'user','content':raw,'api_content':raw+'\n\n'+context}]
    sync_completed(service, snap.session_id, raw, messages)
    with pytest.raises(KiokukoError, match='SECRET_REJECTED'):
        capture_completed(service, snap, messages)
    assert count(service,'task_profiles') == 0


def test_reindex_restores_signals_and_partial_prevents_adopt(service, profiles):
    root, who, turn = profiles
    source, *_ = complete(service, turn)
    with service.transaction(write=True) as db:
        db.execute('DELETE FROM task_profile_signals')
        db.execute("UPDATE store_metadata SET value='partial' WHERE key='task_profile_projection_version'")
    assert reindex(service) == {'reindexed':1}
    query = '`widget.py`'
    assert 'Resolved target' in prepare(service, turn(query, 'consumer'), query)


def test_pure_resolver_and_paths():
    assert identifiers('対象は `日本語/例.py` と widget.py') == ('日本語/例.py','widget.py')
    assert relative_target('../outside.py','/root') is None
    assert relative_target('/elsewhere/file.py','/root') is None
    candidate = Candidate('a',0,'src/x.py',999,True,True)
    assert decide([candidate], ['x.py'], complete=True, mode='resolve')[0].action == 'adopt'
    assert decide([candidate], ['x.py'], complete=False, mode='resolve')[0].action == 'suggest'
    conflict = Candidate('b',0,'tests/x.py',0,True,False)
    assert decide([candidate,conflict], ['x.py'], complete=True, mode='resolve')[0].action == 'suggest'


def test_config_mode_is_strict(service):
    mode(service, 'surprise')
    with pytest.raises(KiokukoError, match='INVALID_CONFIG'): load_config(service.store.home)


def test_v4_migration_rollback_and_backup_restore(service, profiles, tmp_path, monkeypatch):
    from test_fact_migration import drop_v5
    from hermes_kiokuko import store as store_module
    from hermes_kiokuko.store import Store, schema_digest
    from hermes_kiokuko.operations import backup, restore
    root, who, turn = profiles
    home = service.store.home
    key = service.store.key
    service.store.close()
    with sqlite3.connect(service.store.path) as db:
        drop_v5(db)
        db.execute('PRAGMA user_version=4')
        db.execute("UPDATE store_metadata SET value=? WHERE key='schema_hash'", (schema_digest(db),))
    script = store_module.PROFILE_SCHEMA
    monkeypatch.setattr(store_module,'PROFILE_SCHEMA',script+'\nINVALID SQL;\n')
    with pytest.raises(KiokukoError,match='DATABASE_ERROR'):Store(home)
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==4
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_profiles'").fetchone()
    monkeypatch.setattr(store_module,'PROFILE_SCHEMA',script)
    store=Store(home)
    from hermes_kiokuko.service import Service
    upgraded=Service(store)
    try:
        assert store.key==key
        snap=upgraded.snapshot('restored-source','one','Inspect `src/widget.py`',who,workspace_root=root)
        raw='Inspect `src/widget.py`'
        body=prepare(upgraded,snap,raw)
        messages=[{'role':'user','content':raw,'api_content':raw+'\n\n'+body}]
        sync_completed(upgraded,snap.session_id,raw,messages)
        profile_id=capture_completed(upgraded,snap,messages)
        backup(upgraded,tmp_path/'backup')
    finally:store.close()
    checkpoint = sqlite3.connect(store.path)
    try:
        checkpoint.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        checkpoint.close()
    restore(home,tmp_path/'backup')
    store=Store(home)
    try:
        restored=Service(store)
        assert review(restored,profile_id)[0]['targets']==['src/widget.py']
        with restored.transaction() as db:
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
    finally:store.close()


def test_capture_limit_cleanup_receipt_and_fts_fallback(service, profiles, monkeypatch):
    from hermes_kiokuko import task_profiles
    root, who, turn = profiles
    monkeypatch.setattr(task_profiles,'MAX_PROFILES',2)
    source,snap,messages=complete(service,turn)
    complete(service,turn,session='second')
    complete(service,turn,session='third')
    assert count(service,'task_profiles')==2
    assert capture_completed(service,snap,messages) is None
    with service.transaction(write=True) as db:
        assert not db.execute('SELECT 1 FROM task_profiles WHERE id=?',(source,)).fetchone()
        assert not db.execute('SELECT 1 FROM task_profile_fts WHERE profile_id=?',(source,)).fetchone()
        db.execute("UPDATE store_metadata SET value='0' WHERE key='task_profile_fts'")
    query='`widget.py`'
    assert 'Resolved target' in prepare(service,turn(query,'consumer'),query)


def test_candidate_budget_is_incomplete_and_replay_does_not_choose_new_source(service, profiles):
    root, who, turn = profiles
    for index in range(65):complete(service,turn,session=f'source-{index}')
    query='`widget.py`'
    snap=turn(query,'consumer')
    result=prepare(service,snap,query,deadline=__import__('time').monotonic()+2)
    assert 'Resolved target' not in result
    with service.transaction() as db:
        resolution=db.execute("SELECT * FROM task_profile_resolutions WHERE session_id='consumer'").fetchone()
        assert resolution['status']=='incomplete' and resolution['scanned']==64
        initial=[tuple(r) for r in db.execute('SELECT profile_id,target_index,decision FROM task_profile_candidates WHERE resolution_id=?',(resolution['id'],))]
    complete(service,turn,session='newest')
    prepare(service,snap,query,deadline=__import__('time').monotonic()+2)
    with service.transaction() as db:
        assert initial==[tuple(r) for r in db.execute('SELECT profile_id,target_index,decision FROM task_profile_candidates WHERE resolution_id=?',(resolution['id'],))]
