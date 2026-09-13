from test_fact_migration import drop_v5
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_kiokuko.config import load_config, write_yaml
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.experiences import evidence_events, store_results, validate_proposal, process_next
from hermes_kiokuko.models import canonical, Identity, now
from hermes_kiokuko.monitor import Monitor, binding_hash, remove_run, status
from hermes_kiokuko.orca_transport import OrcaProcess, read_trace, run_path, runtime_check


@pytest.fixture
def enabled(service):
    cfg = load_config(service.store.home)
    cfg['monitor']['enabled'] = True
    write_yaml(service.store.directory/'config.yaml',cfg)
    return service


def seal(service,snap,events,run_id=None):
    import secrets
    run_id = run_id or 'run_'+secrets.token_hex(16)
    with service.transaction(snap,write=True) as db:
        db.execute('INSERT INTO monitor_runs(id,snapshot_json,binding_hash,owner,state,created_at) VALUES (?,?,?,?,?,?)',
                   (run_id,canonical(asdict(snap)),binding_hash(service,snap),'test','recording',now()))
    process = OrcaProcess(service.store.directory)
    try:
        process.call('open',run_id)
        for event in events:
            process.call('append',run_id,event)
        ack = process.call('close',run_id)
    finally:
        process.close()
    with service.transaction(snap,write=True) as db:
        db.execute("UPDATE monitor_runs SET state='complete',events_hash=?,completed_at=? WHERE id=?",(ack['integrity']['events_sha256'],now(),run_id))
        db.execute('INSERT INTO experience_jobs VALUES (?, ?, NULL,0,?)',(run_id,'running',now()))
        return dict(db.execute('SELECT * FROM monitor_runs WHERE id=?',(run_id,)).fetchone())


def example_events():
    return [
        {'type':'model.request','actor':'agent','payload':{'messages':[{'role':'system','content':'SYSTEM_DO_NOT_EXTRACT'}, {'role':'user','content':'OLD_HISTORY_DO_NOT_EXTRACT'}]}},
        {'type':'tool.result','actor':'tool','attrs':{'tool':'terminal'},'payload':{'exit_code':0,'stdout':'npm test passed after installing dependencies'}},
        {'type':'model.response','actor':'model','payload':{'choices':[{'message':{'content':'Fixed it'}}]}}
    ]


def proposal(seq=2):
    return {'situation':'npm test failed with missing dependencies', 'observation':'npm test passed after installing dependencies',
            'action':'installed dependencies', 'outcome':'success','inference':'Missing dependencies may explain this test failure',
            'evidence':[{'seq':seq,'quote':'npm test passed after installing dependencies'}]}


def test_real_orca_roundtrip_integrity_redaction(enabled,make_turn):
    secret = 'sk-'+'a1B2'*12
    events=example_events()+[{'type':'note','actor':'user','payload':{'text':secret}}]
    row=seal(enabled,make_turn(),events)
    events=read_trace(enabled.store.directory,row)
    text=canonical([p for _,p in events])
    assert secret not in text
    path=run_path(enabled.store.directory,row['id'])
    assert all(not(p.stat().st_mode & 0o077) for p in path.rglob('*'))
    extracted=evidence_events(events)
    assert 'OLD_HISTORY_DO_NOT_EXTRACT' not in canonical(extracted)
    assert 'SYSTEM_DO_NOT_EXTRACT' not in canonical(extracted)
    log=path/'events.jsonl'
    log.write_text(log.read_text()+'{}\n')
    with pytest.raises(KiokukoError,match='MONITOR_INTEGRITY_FAILED'):
        read_trace(enabled.store.directory,row)


def test_blob_digest_checked(enabled,make_turn):
    row=seal(enabled,make_turn(),[{'type':'note','actor':'user','payload':{'text':'long human text '*1000}}])
    path=run_path(enabled.store.directory,row['id'])
    blob=next((path/'blobs').glob('*/*'))
    blob.write_text('modified')
    with pytest.raises(KiokukoError,match='MONITOR_INVALID_TRACE'):
        read_trace(enabled.store.directory,row)


def test_experience_recall_scope_purge_receipt(enabled,make_turn):
    snap=make_turn()
    row=seal(enabled,snap,example_events())
    evidence=evidence_events(read_trace(enabled.store.directory,row))
    ids=store_results(enabled,row,[proposal()],evidence)
    assert len(ids)==1
    entries=enabled.search(make_turn('npm dependencies'),'npm dependencies')
    assert len(entries)==1 and entries[0]['confirmation_kind'] is None
    assert entries[0]['epistemic_status']=='observed_experience'
    other=Identity('telegram','dm','other','other-conversation','ws-local','dm')
    assert enabled.search(make_turn('npm',session='other',who=other),'npm')==[]
    assert enabled.search(make_turn('hello'),'completelyunrelated')==[]
    from hermes_kiokuko.deliveries import prepare
    context=prepare(enabled,make_turn('npm deps'), 'npm dependencies')
    assert '未検証の過去事例' in context
    assert len(context)<=2200
    from hermes_kiokuko.operations import entry_review,purge
    entry,review=entry_review(enabled,ids[0])
    purge(enabled,ids[0],review)
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='running' WHERE run_id=?",(row['id'],))
    assert store_results(enabled,row,[proposal()],evidence)==[]


def test_model_success_is_not_evidence(enabled):
    p=proposal(1)
    evidence=[{'seq':1,'type':'note','actor':'user','tool':'','text':'npm test passed after installing dependencies'}]
    structure,_,_=validate_proposal(enabled,p,evidence)
    assert structure['outcome']=='unknown'
    p['evidence'][0]['quote']='not in evidence'
    with pytest.raises(KiokukoError):
        validate_proposal(enabled,p,evidence)


def test_rewind_blocks_commit(enabled,make_turn):
    snap=make_turn()
    row=seal(enabled,snap,example_events())
    events=evidence_events(read_trace(enabled.store.directory,row))
    enabled.transition(snap.session_id,rewound=True)
    with pytest.raises(KiokukoError,match='STALE_GENERATION'):
        store_results(enabled,row,[proposal()],events)


def test_rewind_invalidates_already_delivered_experience(enabled,make_turn):
    snap=make_turn()
    row=seal(enabled,snap,example_events())
    evidence=evidence_events(read_trace(enabled.store.directory,row))
    entry_id=store_results(enabled,row,[proposal()],evidence)[0]
    from hermes_kiokuko.deliveries import prepare
    assert '未検証の過去事例' in prepare(enabled,make_turn('npm dependencies'),'npm dependencies')
    enabled.transition(snap.session_id,rewound=True)
    assert enabled.search(make_turn('npm dependencies'),'npm dependencies')==[]
    with enabled.transaction() as db:
        assert db.execute('SELECT state FROM memory_entries WHERE id=?',(entry_id,)).fetchone()[0]=='expired'
        assert db.execute('SELECT state FROM experience_jobs WHERE run_id=?',(row['id'],)).fetchone()[0]=='blocked'
        assert db.execute('SELECT actor FROM memory_revisions WHERE entry_id=? ORDER BY revision DESC',(entry_id,)).fetchone()[0]=='session-rewind'


def test_missing_source_blocks_job(enabled,make_turn):
    row=seal(enabled,make_turn(),example_events())
    remove_run(enabled,row['id'])
    assert not run_path(enabled.store.directory,row['id']).exists()
    with enabled.transaction() as db:
        assert db.execute('SELECT state FROM experience_jobs').fetchone()[0]=='blocked'
    assert status(enabled)['runs']=={'purged':1}


def test_acknowledged_completion_and_async_extraction(enabled,make_turn,monkeypatch):
    monkeypatch.setattr('hermes_kiokuko.experiences.extract_model',lambda home,events: [])
    manager=Monitor(enabled)
    snap=make_turn()
    try:
        manager.begin(snap,{'text':'new user input'})
        obs=manager.next_observation(snap)
        manager.append(snap,'model.request','agent',{'messages':[]},{'observation':obs})
        manager.append(snap,'model.response','model',{'choices':[{'message':{'content':'hello'}}]},{'observation':obs})
        with enabled.transaction(snap,write=True) as db:
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)',(*snap.key,now()))
        manager.complete(snap)
        assert manager.wait_idle()
        info=status(enabled)
        assert info['runs']=={'complete':1},info
        assert info['jobs']=={'done':1},info
        assert info['capture_observed'] and info['api_requests']==1
    finally:
        manager.close()


def test_responses_evidence_excludes_history_reasoning_and_function_arguments():
    def event(kind, actor='model'):
        return SimpleNamespace(type=kind, actor=actor, seq=1, attrs={})
    result = evidence_events([
        (event('model.request', 'agent'), {'instructions': 'PRIVATE_SYSTEM', 'input': 'OLD_HISTORY'}),
        (event('model.response'), {'output': [
            {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'PRIVATE_REASONING'}]},
            {'type': 'function_call', 'arguments': 'PRIVATE_ARGUMENTS'},
            {'type': 'message', 'role': 'user', 'content': [{'type': 'output_text', 'text': 'REPLAYED_USER'}]},
            {'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': 'Visible assistant response'},
                {'type': 'refusal', 'refusal': 'REFUSAL_METADATA'},
            ]},
        ]}),
    ])
    assert [row['text'] for row in result] == ['Visible assistant response']


def test_unsupported_api_preserves_execution_and_persists_reason(enabled, make_turn, monkeypatch):
    from hermes_kiokuko.monitor_capture import llm_execution_middleware
    manager = Monitor(enabled)
    snap = make_turn()
    monkeypatch.setattr('hermes_kiokuko.monitor_capture.current_binding', lambda *a: (manager, snap))
    calls = []
    response = object()
    try:
        manager.begin(snap, {'text': 'user input'})
        result = llm_execution_middleware(request={}, api_mode='unsupported-api',
            next_call=lambda request: (calls.append(request), response)[1])
        assert result is response and calls == [{}]
        with enabled.transaction(snap, write=True) as db:
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)', (*snap.key, now()))
        manager.complete(snap)
        assert manager.wait_idle()
        with enabled.transaction() as db:
            row = dict(db.execute('SELECT * FROM monitor_runs').fetchone())
            assert (row['state'], row['api_requests'], row['dropped'], row['error_code']) == (
                'incomplete', 0, 1, 'MONITOR_API_UNSUPPORTED')
            assert db.execute("SELECT count FROM status_events WHERE code='MONITOR_API_UNSUPPORTED'").fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM experience_jobs').fetchone()[0] == 0
    finally:
        manager.close()


def test_middleware_never_repeats_or_changes_execution(monkeypatch):
    from hermes_kiokuko.monitor_capture import llm_execution_middleware
    monkeypatch.setattr('hermes_kiokuko.monitor_capture.current_binding',lambda *a: (_ for _ in ()).throw(RuntimeError()))
    calls=[]
    request={'messages':[]}
    result=object()
    assert llm_execution_middleware(request=request,next_call=lambda r:(calls.append(r),result)[1]) is result
    assert calls==[request]
    error=RuntimeError('provider failure')
    def fail(r):
        calls.append(r)
        raise error
    with pytest.raises(RuntimeError) as caught:
        llm_execution_middleware(request=request,next_call=fail)
    assert caught.value is error and len(calls)==2


def test_queue_overflow_is_visible(enabled,make_turn,monkeypatch):
    monkeypatch.setattr('hermes_kiokuko.experiences.extract_model',lambda *a: [])
    monkeypatch.setattr('hermes_kiokuko.monitor.QUEUE_BYTES',1)
    manager=Monitor(enabled)
    try:
        with pytest.raises(KiokukoError,match='MONITOR_QUEUE_FULL'):
            manager.begin(make_turn(),{'text':'hello'})
        assert manager.wait_idle()
        assert status(enabled)['runs']=={'incomplete':1}
    finally:
        manager.close()


def test_v2_migration_preserves_identity(service):
    from hermes_kiokuko.store import Store,schema_digest
    home,key=service.store.home,service.store.key
    service.store.close()
    with sqlite3.connect(service.store.path) as db:
        drop_v5(db)
        for table in ('lesson_sources', 'lessons', 'lesson_families', 'learning_receipts', 'learning_jobs', 'experience_relations', 'experience_features', 'experience_leases', 'experience_windows', 'experience_coverage'):
            db.execute("DROP TABLE " + table)
        db.execute("DELETE FROM schema_migrations WHERE version=4")
        for table in ('experience_sources','experience_receipts','experiences','experience_jobs','monitor_runs'):
            db.execute('DROP TABLE '+table)
        db.execute('DELETE FROM schema_migrations WHERE version=3')
        db.execute('PRAGMA user_version=2')
        db.execute("UPDATE store_metadata SET value=? WHERE key='schema_hash'",(schema_digest(db),))
    store=Store(home)
    try:
        assert store.key==key
        with store.transaction() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0]==5
    finally:
        store.close()


def test_expired_experience_and_correction(enabled,make_turn):
    row=seal(enabled,make_turn(),example_events())
    ids=store_results(enabled,row,[proposal()],evidence_events(read_trace(enabled.store.directory,row)))
    with enabled.transaction(write=True) as db:
        db.execute('UPDATE memory_entries SET valid_until=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),ids[0]))
    assert not enabled.search(make_turn(),'dependencies')
    from hermes_kiokuko.models import ExplicitCommand
    enabled.explicit(make_turn(),ExplicitCommand('correct','npm needs the lockfile, not a reinstall',entry_id=ids[0],expected_revision=1))
    with enabled.transaction() as db:
        entry=db.execute('SELECT * FROM memory_entries WHERE id=?',(ids[0],)).fetchone()
        assert entry['epistemic_status']=='user_correction'


def test_retention_marks_pending_source_lost(enabled,make_turn):
    row=seal(enabled,make_turn(),example_events())
    with enabled.transaction(write=True) as db:
        db.execute('UPDATE monitor_runs SET created_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),row['id']))
    from hermes_kiokuko.monitor import collect
    collect(enabled)
    assert status(enabled)['runs']=={'missing':1}
    assert status(enabled)['jobs']=={'blocked':1}


def test_unknown_event_is_not_silent_complete(enabled,make_turn):
    import hashlib
    row=seal(enabled,make_turn(),example_events())
    path=run_path(enabled.store.directory,row['id'])
    log=path/'events.jsonl'
    text=log.read_text().replace('"type":"tool.result"','"type":"future.event"')
    log.write_text(text)
    row['events_hash']=hashlib.sha256(text.encode()).hexdigest()
    manifest=json.loads((path/'manifest.json').read_text())
    manifest['integrity']['events_sha256']=row['events_hash']
    (path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(KiokukoError,match='MONITOR_INCOMPLETE'):
        read_trace(enabled.store.directory,row)


def test_writer_failure_does_not_produce_experience(enabled,make_turn,monkeypatch):
    def broken(*args,**kwargs):
        raise KiokukoError('MONITOR_WRITER_FAILED')
    monkeypatch.setattr('hermes_kiokuko.monitor.OrcaProcess',broken)
    manager=Monitor(enabled)
    snap=make_turn()
    try:
        manager.begin(snap,{'text':'hello'})
        with enabled.transaction(snap,write=True) as db:
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)',(*snap.key,now()))
        manager.complete(snap)
        assert manager.wait_idle()
        assert status(enabled)['runs']=={'incomplete':1}
        assert status(enabled)['jobs']=={}
    finally:
        manager.close()


def test_no_api_capture_is_not_monitoring_success(enabled,make_turn):
    manager=Monitor(enabled)
    snap=make_turn()
    try:
        manager.begin(snap,{'text':'capture context only'})
        with enabled.transaction(write=True) as db:
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)',(*snap.key,now()))
        manager.complete(snap)
        assert manager.wait_idle()
        info=status(enabled)
        assert not info['capture_observed'] and info['api_requests']==0
        assert info['runs']=={'incomplete':1} and info['jobs']=={}
    finally:
        manager.close()


def test_pending_extraction_error_retry_and_disable(enabled,make_turn,monkeypatch):
    row=seal(enabled,make_turn(),example_events())
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending'")
    def broken(*args):
        raise TimeoutError()
    assert not process_next(enabled,extractor=broken)
    assert status(enabled)['jobs']=={'failed':1}
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending'")
    assert process_next(enabled,extractor=lambda home,events:[proposal()])
    assert status(enabled)['jobs']=={'done':1}
    cfg=load_config(enabled.store.home)
    cfg['monitor']['enabled']=False
    write_yaml(enabled.store.directory/'config.yaml',cfg)
    assert not enabled.search(make_turn(),'npm dependencies')


def test_source_symlink_rejected(enabled,make_turn,tmp_path):
    row=seal(enabled,make_turn(),example_events())
    path=run_path(enabled.store.directory,row['id'])
    (path/'events.jsonl').unlink()
    target=tmp_path/'unrelated'
    target.write_text('private')
    (path/'events.jsonl').symlink_to(target)
    with pytest.raises(KiokukoError,match='UNSAFE_PATH'):
        read_trace(enabled.store.directory,row)


def test_independent_run_updates_revision_not_recall(enabled,make_turn):
    first=seal(enabled,make_turn(),example_events())
    entry_id=store_results(enabled,first,[proposal()],evidence_events(read_trace(enabled.store.directory,first)))[0]
    second=seal(enabled,make_turn(),example_events())
    assert store_results(enabled,second,[proposal()],evidence_events(read_trace(enabled.store.directory,second)))==[entry_id]
    result=enabled.search(make_turn(),'npm dependencies')[0]
    assert result['current_revision']==2
    for _ in range(3):
        assert enabled.search(make_turn(),'npm dependencies')[0]['current_revision']==2
    with enabled.transaction() as db:
        assert db.execute('SELECT count(*) FROM experience_sources').fetchone()[0]==2


def test_assistant_only_and_permanent_instruction_rejected(enabled):
    evidence=[{'seq':2,'type':'model.response','tool':'','text':'npm test passed after installing dependencies'}]
    with pytest.raises(KiokukoError,match='NO_INDEPENDENT'):
        validate_proposal(enabled,proposal(),evidence)
    evidence[0]['type']='note'
    p=proposal()
    p['inference']='always respond in English'
    with pytest.raises(KiokukoError,match='POLICY_REJECTED'):
        validate_proposal(enabled,p,evidence)


def test_capacity_failure_is_visible_without_memory(enabled,make_turn,monkeypatch):
    monkeypatch.setattr('hermes_kiokuko.monitor.RETENTION_BYTES',100)
    manager=Monitor(enabled)
    snap=make_turn()
    try:
        manager.begin(snap,{'text':'ordinary turn'})
        with enabled.transaction(write=True) as db:
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)',(*snap.key,now()))
        manager.complete(snap)
        assert manager.wait_idle()
        assert status(enabled)['runs']=={'incomplete':1}
        assert status(enabled)['jobs']=={}
    finally:
        manager.close()


def test_recover_abandoned_run_and_pending_job(enabled,make_turn,monkeypatch):
    monkeypatch.setattr('hermes_kiokuko.experiences.extract_model',lambda *a: [])
    row=seal(enabled,make_turn(),example_events())
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending'")
    snap=make_turn(session='abandoned')
    with enabled.transaction(write=True) as db:
        db.execute('INSERT INTO monitor_runs(id,snapshot_json,binding_hash,owner,state,created_at) VALUES (?,?,?,?,?,?)',
            ('run_'+'a'*32,canonical(asdict(snap)),binding_hash(enabled,snap),'owner_'+'a'*32,'recording',now()))
    manager=Monitor(enabled)
    try:
        assert manager.wait_idle()
        assert status(enabled)['runs']=={'complete':1,'missing':1}
        assert status(enabled)['jobs']=={'done':1}
    finally:
        manager.close()


def test_purge_blocks_late_source_job(enabled,make_turn):
    row=seal(enabled,make_turn(),example_events())
    evidence=evidence_events(read_trace(enabled.store.directory,row))
    entry_id=store_results(enabled,row,[proposal()],evidence)[0]
    from hermes_kiokuko.operations import entry_review,purge
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='running'")
    _,receipt=entry_review(enabled,entry_id)
    purge(enabled,entry_id,receipt)
    altered=proposal()
    altered['inference']='A slightly different generated wording'
    with pytest.raises(KiokukoError,match='EXPERIENCE_STALE_JOB'):
        store_results(enabled,row,[altered],evidence)


def test_completion_cannot_overtake_queued_response(enabled,make_turn,monkeypatch):
    import threading,queue
    monkeypatch.setattr('hermes_kiokuko.experiences.extract_model',lambda *a: [])
    manager=Monitor(enabled)
    snap=make_turn()
    ready,proceed=threading.Event(),threading.Event()
    try:
        manager.begin(snap,{'text':'new input'})
        manager.queue.join()
        original_get=manager.queue.get
        def racing_get(*a,**kw):
            try:
                return original_get(*a,**kw)
            except queue.Empty:
                if not ready.is_set():
                    ready.set()
                    assert proceed.wait(3)
                raise
        monkeypatch.setattr(manager.queue,'get',racing_get)
        assert ready.wait(3)
        manager.append(snap,'model.response','model',{'choices':[{'message':{'content':'response before completion'}}]},{'observation':1})
        with enabled.transaction(write=True) as db:
            db.execute('INSERT INTO turn_syncs VALUES (?,?,?,?)',(*snap.key,now()))
        manager.complete(snap)
        proceed.set()
        assert manager.wait_idle()
        with enabled.transaction() as db:
            row=dict(db.execute('SELECT * FROM monitor_runs').fetchone())
        assert any(e.type=='model.response' for e,p in read_trace(enabled.store.directory,row))
    finally:
        proceed.set()
        manager.close()


def test_extraction_timeout_keeps_lock_and_discards_late_result(enabled,make_turn,monkeypatch):
    import threading
    from hermes_kiokuko import experiences
    from hermes_kiokuko.store import acquire_lock
    row=seal(enabled,make_turn(),example_events())
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending'")
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    def slow(*args):
        started.set()
        try:
            assert release.wait(3)
            return [proposal()]
        finally:
            finished.set()
    monkeypatch.setattr(experiences,'EXTRACTION_TIMEOUT',.05)
    try:
        assert not process_next(enabled,extractor=slow)
        assert started.is_set()
        with pytest.raises(KiokukoError):
            acquire_lock(enabled.store.directory/'experience.lock',exclusive=True,timeout=0)
        with enabled.transaction() as db:
            assert tuple(db.execute('SELECT state,error_code FROM experience_jobs').fetchone())==('failed','EXPERIENCE_TIMEOUT')
        remove_run(enabled,row['id'])
    finally:
        release.set()
        assert finished.wait(3)
    with enabled.transaction() as db:
        assert db.execute('SELECT count(*) FROM memory_entries').fetchone()[0]==0
