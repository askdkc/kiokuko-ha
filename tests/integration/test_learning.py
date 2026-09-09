import json
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from hermes_kiokuko.config import load_config, write_yaml
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.experience_algorithms import windows, verify_ref
from hermes_kiokuko.experiences import evidence_events, validate_proposal, store_results, process_next as extract_next
from hermes_kiokuko import learning
from hermes_kiokuko.models import canonical, Identity, ExplicitCommand
from hermes_kiokuko.orca_transport import read_trace
from hermes_kiokuko.deliveries import prepare
from hermes_kiokuko.operations import entry_review, purge, backup, restore
from hermes_kiokuko.monitor import remove_run
from hermes_kiokuko.store import Store, schema_digest
from test_monitor import enabled, seal


def events(code=0, condition='Node 20', target='npm test', recovery=False):
    values = [
        {'type':'note','actor':'user','payload':{'text':condition}},
        {'type':'tool.call','actor':'agent','attrs':{'tool':'terminal','observation':1},'payload':{'command':target}},
        {'type':'tool.result','actor':'tool','attrs':{'tool':'terminal','observation':1},'payload':{'exit_code':1 if recovery else code,'stdout':'check completed'}},
    ]
    if recovery:
        values += [
            {'type':'tool.call','actor':'agent','attrs':{'tool':'terminal','observation':2},'payload':{'command':target}},
            {'type':'tool.result','actor':'tool','attrs':{'tool':'terminal','observation':2},'payload':{'exit_code':code,'stdout':'check completed'}},
        ]
    return values


def proposal(evidence, condition='Node 20'):
    calls=[e for e in evidence if e['type']=='tool.call']
    results=[e for e in evidence if e['type']=='tool.result']
    target=json.loads(calls[0]['text'])['command']
    note=next(e for e in evidence if e['type']=='note')
    return {'situation':condition,'observation':'check completed','action':target,
            'outcome':'success','inference':'Only this check was observed.',
            'evidence':[{'seq':note['seq'],'quote':condition},
                        {'seq':calls[0]['seq'],'quote':target},
                        {'seq':results[-1]['seq'],'quote':'check completed'}],
            'span':{'start':evidence[0]['seq'],'end':evidence[-1]['seq']},
            'conditions':[{'value':condition,'evidence':0}],
            'field_evidence':{'situation':[0],'action':[1],'observation':[2]},
            'attempts':[{'call_seq':c['seq'],'result_seq':r['seq'],'target':json.loads(c['text'])['command']}
                        for c,r in zip(calls,results)]}


def add(service, make_turn, session, code=0, condition='Node 20', recovery=False, target='npm test', who=None):
    row=seal(service,make_turn(condition,session=session,who=who),events(code,condition,target,recovery))
    ev=evidence_events(read_trace(service.store.directory,row),row['id'])
    ids=store_results(service,row,[proposal(ev,condition)],ev)
    assert len(ids)==1
    return ids[0],row


def mode(service,value='auto'):
    cfg=load_config(service.store.home)
    cfg['experience_learning']['mode']=value
    write_yaml(service.store.directory/'config.yaml',cfg)


def draft(payload,kind='recommend'):
    pool=payload['experiences']
    wanted='success' if kind=='recommend' else 'failure'
    support=[e for e in pool if e['structure']['outcome']==wanted]
    if not support:
        return None
    entry=support[0]
    def ref(field,value=None):
        return {'entry_id':entry['id'],'field':field,'quote':value or entry['allowed'][field][0]}
    return {'kind':kind,'conditions':[ref('condition',v) for v in payload['required_conditions']],
            'steps':[ref('action',entry['structure']['attempts'][-1]['action'])] if kind=='recommend' else [],
            'avoid':[ref('action',entry['structure']['attempts'][-1]['action'])] if kind=='avoid' else [],
            'verification':[ref('verification')], 'exclusions':[],
            'support_ids':[e['id'] for e in support],
            'counterexample_ids':[e['id'] for e in pool if e['structure']['outcome'] not in {wanted,'unknown'}]}


def learn(service,kind='recommend'):
    assert learning.process_next(service,extractor=lambda home,payload:draft(payload,kind))
    with service.transaction() as db:
        row=db.execute("SELECT * FROM memory_entries WHERE epistemic_status='derived_lesson' ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def state(service,entry_id):
    with service.transaction() as db:
        entry=db.execute('SELECT * FROM memory_entries WHERE id=?',(entry_id,)).fetchone()
        return learning.details(db,entry)


def test_window_overlap_pair_and_late_recovery(enabled,make_turn):
    ev=[{'seq':i,'type':'note','actor':'user','tool':'','text':'x'*350,'observation':None} for i in range(1,9)]
    parts=windows(ev,limit=1500)
    assert all(len(canonical(part))<=1500 for part in parts)
    assert len(parts)>1 and set(e['seq'] for e in parts[0]) & set(e['seq'] for e in parts[1])
    row=seal(enabled,make_turn(),events(recovery=True))
    evidence=evidence_events(read_trace(enabled.store.directory,row))
    proposed=proposal(evidence)
    structure,_,_=validate_proposal(enabled,proposed,evidence)
    assert structure['outcome']=='success' and structure['recovery']=='recovered'
    assert [a['outcome'] for a in structure['attempts']]==['failure','success']


def test_fragment_positions_and_escaped_json_budget():
    ev={'seq':1,'type':'note','actor':'user','tool':'','text':'"\\\n'*2000}
    parts=windows([ev],limit=32000)
    for part in parts:
        assert len(canonical(part))<=32000
        for e in part:
            ref=verify_ref({'seq':1,'quote':e['text'][:9],'start':e['start']},part)
            assert ref['start']==e['start']
    limited=windows([ev],limit=600,max_windows=4)
    assert len(limited)==4 and limited.excluded
    assert limited.excluded[-1]['end']==len(ev['text'])


@pytest.mark.parametrize('mutation', ['different_check','pair_mismatch','wrong_target','assistant_result','invented_condition'])
def test_invalid_verification_never_establishes_recovery(enabled,make_turn,mutation):
    row=seal(enabled,make_turn(),events(recovery=True))
    ev=evidence_events(read_trace(enabled.store.directory,row))
    p=proposal(ev)
    if mutation=='different_check':
        ev[-2]['text']=canonical({'command':'npm lint'})
        p['attempts'][-1]['target']='npm lint'
        s,_,_=validate_proposal(enabled,p,ev)
        assert s['outcome']=='unknown' and s['recovery']=='unknown'
        return
    if mutation=='pair_mismatch': ev[-1]['observation']=999
    if mutation=='wrong_target': p['attempts'][-1]['target']='npm lint'
    if mutation=='assistant_result': ev[-1]['type']='model.response'
    if mutation=='invented_condition': p['conditions'][0]['value']='Node 22'
    with pytest.raises(KiokukoError): validate_proposal(enabled,p,ev)


def test_windows_checkpoint_retry_and_global_selection(enabled,make_turn):
    # Several unrelated large user events produce >1 window. Only the last window
    # contains a real failure/recovery; the extractor must actually reach it.
    es=[{'type':'note','actor':'user','payload':{'text':'context '+str(i)+' '+('x'*10000)}} for i in range(4)]
    es+=events(recovery=True)
    row=seal(enabled,make_turn(),es)
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending'")
    calls=[]
    def extract(home,part):
        calls.append(part)
        if any(e['type']=='tool.call' for e in part):
            tail=[e for e in part if e['type']!='note' or e['text']=='Node 20']
            return [proposal(tail)]
        return []
    assert extract_next(enabled,extractor=extract)
    with enabled.transaction() as db:
        assert db.execute('SELECT count(*) FROM experience_windows').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM memory_entries').fetchone()[0]==0
    while extract_next(enabled,extractor=extract): pass
    assert len(calls)>1
    with enabled.transaction() as db:
        assert db.execute('SELECT state FROM experience_jobs').fetchone()[0]=='done'
        s=json.loads(db.execute('SELECT structure_json FROM experiences').fetchone()[0])
        assert s['recovery']=='recovered'
        assert db.execute('SELECT count(*) FROM experience_windows').fetchone()[0]==0


def test_independent_sessions_activate_and_inject_once(enabled,make_turn):
    mode(enabled)
    source,_=add(enabled,make_turn,'one')
    entry=learn(enabled)
    assert state(enabled,entry['id'])['lifecycle']=='candidate'
    add(enabled,make_turn,'one')
    entry=learn(enabled)
    assert state(enabled,entry['id'])['support']['support_sessions']==1
    add(enabled,make_turn,'two');learn(enabled)
    add(enabled,make_turn,'three');entry=learn(enabled)
    assert state(enabled,entry['id'])['lifecycle']=='active'
    snap=make_turn('Node 20 npm test',session='reader')
    body=prepare(enabled,snap,'Node 20 npm test')
    assert '過去経験からの推論' in body and source not in body
    assert len(body)<=2200
    assert len(enabled.search(snap,'Node 20 npm test'))==1
    for query in ('npm test','Node 200 npm test','Node 22 npm test','not Node 20 npm test','Node 20ではない'):
        assert not any(e['kind']=='lesson' for e in enabled.search(snap,query))
    before=state(enabled,entry['id'])
    for _ in range(3): enabled.search(snap,'Node 20 npm test')
    assert state(enabled,entry['id'])==before


def test_negative_evidence_suspends_before_llm_and_cannot_be_omitted(enabled,make_turn):
    mode(enabled)
    for session in ('a','b','c'): add(enabled,make_turn,session)
    entry=learn(enabled)
    add(enabled,make_turn,'bad',code=1)
    assert state(enabled,entry['id'])['lifecycle']=='contested'
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))
    def cheat(home,payload):
        result=draft(payload);result['counterexample_ids']=[];return result
    assert not learning.process_next(enabled,extractor=cheat)
    with enabled.transaction() as db:
        assert db.execute('SELECT error_code FROM learning_jobs').fetchone()[0]=='LEARNING_EVIDENCE_OMITTED'
    assert state(enabled,entry['id'])['lifecycle']=='contested'


def test_avoidance_requires_three_failures_and_no_untried_alternative(enabled,make_turn):
    mode(enabled)
    for session in ('a','b','c'): add(enabled,make_turn,session,code=1)
    entry=learn(enabled,'avoid')
    assert state(enabled,entry['id'])['lifecycle']=='active'
    with enabled.transaction() as db:
        family=db.execute('SELECT family_hash FROM learning_jobs').fetchone()[0]
        payload=learning.input_payload(db,family)
    proposed=draft(payload,'avoid');proposed['steps']=[proposed['avoid'][0]]
    with pytest.raises(KiokukoError,match='UNOBSERVED'): learning.validate_draft(enabled,proposed,payload)


@pytest.mark.parametrize('field,value', [('entry_id','foreign'),('quote','rm -rf /'),('field','invented')])
def test_unobserved_lesson_reference_rejected(enabled,make_turn,field,value):
    mode(enabled);add(enabled,make_turn,'a')
    with enabled.transaction() as db:
        family=db.execute('SELECT family_hash FROM learning_jobs').fetchone()[0]
        payload=learning.input_payload(db,family)
    proposed=draft(payload);proposed['steps'][0][field]=value
    with pytest.raises(KiokukoError):learning.validate_draft(enabled,proposed,payload)


def test_scopes_versions_targets_and_negation_never_merge(enabled,make_turn):
    mode(enabled)
    other=Identity('cli','cli','bob','bob-chat','ws-local','dm')
    a,_=add(enabled,make_turn,'a')
    b,_=add(enabled,make_turn,'b',who=other)
    c,_=add(enabled,make_turn,'c',condition='Node 22')
    d,_=add(enabled,make_turn,'d',target='npm lint')
    e,_=add(enabled,make_turn,'e',condition='not Node 20')
    with enabled.transaction() as db:
        assert db.execute('SELECT count(DISTINCT family_hash) FROM experience_features').fetchone()[0]==5
    assert len({a,b,c,d,e})==5


def test_shadow_off_and_late_mode_change(enabled,make_turn):
    for session in ('a','b','c'):add(enabled,make_turn,session)
    assert not learning.process_next(enabled,extractor=lambda *a:pytest.fail('off must not call model'))
    mode(enabled,'shadow');entry=learn(enabled)
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))
    mode(enabled,'auto')
    assert any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))
    add(enabled,make_turn,'d')
    def change(home,payload): mode(enabled,'off');return draft(payload)
    assert not learning.process_next(enabled,extractor=change)
    assert state(enabled,entry['id'])['lifecycle']=='contested'


@pytest.mark.parametrize('operation',['purge','rewind','expire','source_purge'])
def test_source_invalidations_and_late_job_never_restore_lesson(enabled,make_turn,operation):
    mode(enabled)
    rows=[add(enabled,make_turn,s) for s in ('a','b','c')]
    entry=learn(enabled)
    source,row=rows[0]
    add(enabled,make_turn,'d')
    def mutate(home,payload):
        if operation=='purge':
            _,review=entry_review(enabled,source);purge(enabled,source,review)
        elif operation=='rewind':enabled.transition('a',rewound=True)
        elif operation=='source_purge':remove_run(enabled,row['id'])
        else:
            with enabled.transaction(write=True) as db:
                e=dict(db.execute('SELECT * FROM memory_entries WHERE id=?',(source,)).fetchone())
                enabled._change(db,e,'expire_request')
        return draft(payload)
    assert not learning.process_next(enabled,extractor=mutate)
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))
    with enabled.transaction() as db:
        if operation in {'purge','source_purge'}:
            assert not db.execute('SELECT 1 FROM memory_entries WHERE id=?',(entry['id'],)).fetchone()
            assert not db.execute('SELECT 1 FROM lesson_sources WHERE source_id=?',(source,)).fetchone()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_retention_is_not_purge_and_expiration_does_not_extend_on_read(enabled,make_turn):
    mode(enabled)
    rows=[add(enabled,make_turn,s) for s in ('a','b','c')]
    entry=learn(enabled)
    old=state(enabled,entry['id'])
    remove_run(enabled,rows[0][1]['id'],missing=True)
    assert any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))
    assert state(enabled,entry['id'])==old
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE memory_entries SET valid_until='2000-01-01' WHERE id=?",(rows[0][0],))
    prepare(enabled,make_turn(),'Node 20 npm test')
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))


def test_manual_correction_detaches_from_automatic_updates(enabled,make_turn):
    mode(enabled)
    for s in ('a','b','c'):add(enabled,make_turn,s)
    entry=learn(enabled)
    with enabled.transaction(write=True) as db:
        e=dict(db.execute('SELECT * FROM memory_entries WHERE id=?',(entry['id'],)).fetchone())
        enabled._change(db,e,'correct',body='A human correction',approved=True)
    add(enabled,make_turn,'d')
    assert not learning.process_next(enabled,extractor=lambda *a:pytest.fail('detached'))
    assert enabled.get(make_turn(),entry['id'])['claim']=='A human correction'


def test_cross_turn_recovery_is_reference_in_same_generation(enabled,make_turn):
    first,_=add(enabled,make_turn,'a',code=1)
    later,_=add(enabled,make_turn,'a',code=0)
    add(enabled,make_turn,'b',code=0)
    with enabled.transaction() as db:
        assert [tuple(r) for r in db.execute("SELECT earlier_id,later_id FROM experience_relations WHERE relation='recovery'")]==[(first,later)]


def test_learning_timeout_keeps_worker_lock_and_discards_response(enabled,make_turn,monkeypatch):
    mode(enabled);add(enabled,make_turn,'a')
    from hermes_kiokuko.model_job import ModelJob
    original=ModelJob.call
    monkeypatch.setattr(ModelJob,'call',lambda self,fn,timeout=12:original(self,fn,.03))
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    def slow(home,payload):
        started.set()
        try:
            assert release.wait(3)
            return draft(payload)
        finally:finished.set()
    try:
        assert not learning.process_next(enabled,extractor=slow)
        assert started.is_set()
        assert not learning.process_next(enabled,extractor=lambda *a:pytest.fail('overlap'))
    finally:
        release.set();assert finished.wait(3)
    with enabled.transaction() as db:
        assert db.execute('SELECT count(*) FROM lessons').fetchone()[0]==0


def test_v3_migration_and_unknown_checksum_preserve_database(service):
    names=('lesson_sources','lessons','lesson_families','learning_receipts','learning_jobs','experience_relations','experience_features','experience_leases','experience_windows','experience_coverage')
    home,key=service.store.home,service.store.key
    service.store.close()
    with sqlite3.connect(service.store.path) as db:
        for name in names:db.execute('DROP TABLE '+name)
        db.execute('DELETE FROM schema_migrations WHERE version=4')
        db.execute('PRAGMA user_version=3')
        db.execute("UPDATE store_metadata SET value=? WHERE key='schema_hash'",(schema_digest(db),))
    store=Store(home)
    try:
        assert store.key==key
        with store.transaction() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0]==4
            assert db.execute('SELECT count(*) FROM lessons').fetchone()[0]==0
    finally:store.close()


def test_field_supported_narrowing_keeps_counterexample_history(enabled,make_turn):
    mode(enabled)
    # Both subsets share Node 20, but their original user evidence records an OS.
    def add_os(session,operating_system,code):
        es=events(code=code)
        es[0]['payload']['text']='Node 20 '+operating_system
        row=seal(enabled,make_turn(session=session),es)
        ev=evidence_events(read_trace(enabled.store.directory,row))
        p=proposal(ev)
        p['evidence'][0]['quote']='Node 20 '+operating_system
        p['situation']='Node 20 '+operating_system
        return store_results(enabled,row,[p],ev)[0]
    for s in ('a','b','c'):add_os(s,'linux',0)
    entry=learn(enabled)
    bad=add_os('bad','macOS',1)
    assert state(enabled,entry['id'])['lifecycle']=='contested'
    def narrow(home,payload):
        good=next(e for e in payload['experiences'] if e['structure']['outcome']=='success')
        result=draft(payload)
        result['conditions'].append({'entry_id':good['id'],'field':'condition','quote':'Node 20 linux'})
        result['counterexample_ids']=[]
        result['exclusions']=[{'entry_id':bad,'field':'condition','quote':'Node 20 macOS'}]
        return result
    assert learning.process_next(enabled,extractor=narrow)
    after=state(enabled,entry['id'])
    assert after['lifecycle']=='active'
    assert any(s['source_id']==bad and s['relation']=='excluded' for s in after['sources'])
    assert any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 linux npm test'))
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 macOS npm test'))
    assert len(enabled.get(make_turn(),entry['id'],history=True))>=3


def test_backup_restore_and_corrupt_v3_checksum(enabled,make_turn,tmp_path):
    mode(enabled)
    for s in ('a','b','c'):add(enabled,make_turn,s)
    entry=learn(enabled)
    target=tmp_path/'backup'
    backup(enabled,target)
    home=enabled.store.home
    enabled.store.close()
    assert restore(home,target)['restored']
    from hermes_kiokuko.service import Service
    store=Store(home)
    try:
        with store.transaction() as db:
            assert db.execute('SELECT count(*) FROM lessons').fetchone()[0]>0
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
    finally:store.close()
    # Refuse a corrupted migration ledger without changing its schema/version.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE schema_migrations SET checksum='wrong' WHERE version=3")
    with pytest.raises(KiokukoError,match='CHECKSUM_MISMATCH'):Store(home)
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==4
        assert db.execute('SELECT claim FROM memory_entries WHERE id=?',(entry['id'],)).fetchone()


def test_user_correction_survives_source_purge_without_automatic_history(enabled,make_turn):
    mode(enabled)
    source=None
    for s in ('a','b','c'):source,_=add(enabled,make_turn,s)
    entry=learn(enabled)
    with enabled.transaction(write=True) as db:
        e=dict(db.execute('SELECT * FROM memory_entries WHERE id=?',(entry['id'],)).fetchone())
        enabled._change(db,e,'correct',body='Independent human text',approved=True)
    _,review=entry_review(enabled,source);purge(enabled,source,review)
    assert enabled.get(make_turn(),entry['id'])['claim']=='Independent human text'
    with enabled.transaction() as db:
        assert not db.execute("SELECT 1 FROM memory_revisions WHERE entry_id=? AND json_extract(snapshot_json,'$.epistemic_status')='derived_lesson'",(entry['id'],)).fetchone()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_mode_toggle_cancels_inflight_even_when_restored_to_auto(enabled,make_turn,monkeypatch):
    mode(enabled)
    add(enabled,make_turn,'a')
    from hermes_kiokuko import learning_cli
    # This integration test exercises cancellation with an authenticated local
    # CLI context; the release test environment does not install Hermes Gateway.
    monkeypatch.setattr(learning_cli,'bound_values',lambda:{'PLATFORM':'cli'})
    transitions=[]
    def toggle(home,payload):
        for value in ('off','auto'):
            transitions.append(learning_cli.execute(enabled,value)['mode'])
        return draft(payload)
    assert not learning.process_next(enabled,extractor=toggle)
    # process_next records extractor exceptions as job failures. Assert the
    # setup actually completed so such errors cannot masquerade as cancellation.
    assert transitions==['off','auto']
    with enabled.transaction() as db:
        assert db.execute('SELECT count(*) FROM lessons').fetchone()[0]==0
        assert tuple(db.execute('SELECT state,lease_token,error_code FROM learning_jobs').fetchone())==('pending',None,None)


def test_shadow_reads_do_not_restart_completed_learning(enabled,make_turn):
    mode(enabled,'shadow')
    for s in ('a','b','c'):add(enabled,make_turn,s)
    entry=learn(enabled)
    before=state(enabled,entry['id'])
    for i in range(3):prepare(enabled,make_turn(),'Node 20 npm test')
    assert state(enabled,entry['id'])==before
    assert not learning.process_next(enabled,extractor=lambda *a:pytest.fail('unchanged shadow evidence'))


def test_unknown_and_generation_changes_do_not_inflate_support(enabled,make_turn):
    mode(enabled)
    add(enabled,make_turn,'a')
    entry=learn(enabled)
    enabled.transition('a',rewound=True)
    row=seal(enabled,make_turn('Node 20',session='a'),events())
    ev=evidence_events(read_trace(enabled.store.directory,row),row['id'])
    assert store_results(enabled,row,[proposal(ev)],ev)==[]
    # A generation change must not bypass the expired evidence receipt.
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))


def test_old_observations_expire_even_with_one_recent_support(enabled,make_turn):
    mode(enabled)
    source,_=add(enabled,make_turn,'a')
    add(enabled,make_turn,'b');add(enabled,make_turn,'c')
    entry=learn(enabled)
    old=(datetime.now(timezone.utc)-timedelta(days=91)).isoformat()
    with enabled.transaction(write=True) as db:
        db.execute('UPDATE experience_sources SET observed_at=? WHERE entry_id=? AND run_id IN (SELECT id FROM monitor_runs WHERE json_extract(snapshot_json,\'$.session_id\') IN (\'a\',\'b\'))',(old,source))
    assert not any(e['kind']=='lesson' for e in enabled.search(make_turn(),'Node 20 npm test'))
    prepare(enabled,make_turn(),'Node 20 npm test')
    assert state(enabled,entry['id'])['lifecycle']=='contested'
    after=learn(enabled)
    assert state(enabled,after['id'])['support']['support_sessions']==1


def test_delayed_extraction_uses_observation_time(enabled,make_turn):
    row=seal(enabled,make_turn(),events())
    old=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
    with enabled.transaction(write=True) as db:
        db.execute('UPDATE monitor_runs SET completed_at=? WHERE id=?',(old,row['id']))
    row['completed_at']=old
    ev=evidence_events(read_trace(enabled.store.directory,row),row['id'])
    source=store_results(enabled,row,[proposal(ev)],ev)[0]
    with enabled.transaction() as db:
        assert db.execute('SELECT observed_at FROM experience_sources WHERE entry_id=?',(source,)).fetchone()[0]==old
        assert db.execute('SELECT valid_until FROM memory_entries WHERE id=?',(source,)).fetchone()[0]==(datetime.fromisoformat(old)+timedelta(days=90)).isoformat()


def test_unknown_condition_does_not_explain_away_a_counterexample(enabled,make_turn):
    mode(enabled)
    row=seal(enabled,make_turn(session='good'),events(condition='Node 20 linux'))
    ev=evidence_events(read_trace(enabled.store.directory,row))
    p=proposal(ev,'Node 20 linux');p['conditions'][0]['value']='Node 20'
    store_results(enabled,row,[p],ev)
    bad,_=add(enabled,make_turn,'bad',code=1)
    def conceal(home,payload):
        good=next(e for e in payload['experiences'] if e['structure']['outcome']=='success')
        result=draft(payload)
        result['conditions'].append({'entry_id':good['id'],'field':'condition','quote':'Node 20 linux'})
        result['counterexample_ids']=[]
        result['exclusions']=[{'entry_id':bad,'field':'condition','quote':'Node 20'}]
        return result
    assert not learning.process_next(enabled,extractor=conceal)
    with enabled.transaction() as db:
        assert db.execute('SELECT error_code FROM learning_jobs').fetchone()[0]=='LEARNING_UNPROVEN_EXCLUSION'


def test_unknown_outcomes_never_count_as_support(enabled,make_turn):
    mode(enabled)
    add(enabled,make_turn,'success')
    for session in ('u1','u2','u3'):
        add(enabled,make_turn,session,code='0')
    entry=learn(enabled)
    assert state(enabled,entry['id'])['support']['support_sessions']==1
    assert state(enabled,entry['id'])['lifecycle']=='candidate'


def test_oversized_run_commits_four_windows_and_records_excluded_range(enabled,make_turn):
    row=seal(enabled,make_turn(),[{'type':'note','actor':'user','payload':{'text':'public context '*12000}}]+events())
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending' WHERE run_id=?",(row['id'],))
    calls=[]
    for _ in range(4):
        assert extract_next(enabled,extractor=lambda home,part:calls.append(part) or [])
    assert len(calls)==4
    with enabled.transaction() as db:
        assert db.execute('SELECT state FROM experience_jobs WHERE run_id=?',(row['id'],)).fetchone()[0]=='done'
        coverage=db.execute('SELECT * FROM experience_coverage WHERE run_id=?',(row['id'],)).fetchone()
        assert coverage['window_count']==4 and json.loads(coverage['excluded_json'])
    assert not extract_next(enabled,extractor=lambda *a:pytest.fail('fifth model call'))


def test_worker_shutdown_discards_late_lesson_and_window(enabled,make_turn):
    mode(enabled)
    add(enabled,make_turn,'a')
    stopped=threading.Event()
    def finish_lesson(home,payload):
        stopped.set()
        return draft(payload)
    assert not learning.process_next(enabled,extractor=finish_lesson,cancelled=stopped.is_set)
    stopped.clear()
    row=seal(enabled,make_turn(),events())
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending' WHERE run_id=?",(row['id'],))
    def finish_window(home,part):
        stopped.set()
        return [proposal(part)]
    assert not extract_next(enabled,extractor=finish_window,cancelled=stopped.is_set)
    with enabled.transaction() as db:
        assert db.execute('SELECT count(*) FROM lessons').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM experience_sources WHERE run_id=?',(row['id'],)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM experience_windows WHERE run_id=?',(row['id'],)).fetchone()[0]==0


@pytest.mark.parametrize('value',['off','shadow'])
def test_disabled_learning_does_not_leak_through_manual_model_recall(enabled,make_turn,value):
    mode(enabled)
    for s in ('a','b','c'):add(enabled,make_turn,s)
    entry=learn(enabled)
    mode(enabled,value)
    for history in (False,True):
        with pytest.raises(KiokukoError,match='ENTRY_UNAVAILABLE'):
            enabled.get(make_turn(),entry['id'],history=history)
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE memory_entries SET state='conflicted' WHERE id=?",(entry['id'],))
    assert enabled.search(make_turn(),conflicts=True)==[]


def test_group_owners_workspaces_and_dm_do_not_pool_support(enabled,make_turn):
    mode(enabled)
    identities=[Identity('telegram','group_chat','alice','room','project','group'),
                Identity('telegram','group_chat','bob','room','project','group'),
                Identity('telegram','group_chat','alice','room','other-project','group'),
                Identity('telegram','dm','alice','direct','project','dm')]
    sources=[]
    for i,who in enumerate(identities):
        source,_=add(enabled,make_turn,str(i),who=who);sources.append(source)
    assert len(set(sources))==4
    for _ in identities:
        assert learning.process_next(enabled,extractor=lambda home,payload:draft(payload))
    with enabled.transaction() as db:
        assert db.execute('SELECT count(DISTINCT family_hash) FROM experience_features').fetchone()[0]==4
        assert all(json.loads(r[0])['support_sessions']==1 for r in db.execute('SELECT stats_json FROM lessons'))
        assert not db.execute("SELECT 1 FROM lessons WHERE lifecycle='active'").fetchone()


def test_busy_database_does_not_call_model_or_lose_pending_job(enabled,make_turn):
    mode(enabled)
    row=seal(enabled,make_turn(),events())
    with enabled.transaction(write=True) as db:
        db.execute("UPDATE experience_jobs SET state='pending' WHERE run_id=?",(row['id'],))
    with enabled.transaction(write=True):
        assert not extract_next(enabled,extractor=lambda *a:pytest.fail('model during DB contention'))
    assert extract_next(enabled,extractor=lambda home,part:[proposal(part)])
    with enabled.transaction(write=True):
        assert not learning.process_next(enabled,extractor=lambda *a:pytest.fail('model during DB contention'))
    assert learning.process_next(enabled,extractor=lambda home,payload:draft(payload))
