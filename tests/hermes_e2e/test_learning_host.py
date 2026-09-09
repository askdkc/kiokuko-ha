"""Fixed Hermes middleware -> real Orca -> lessons -> real next-turn hook."""
import json

import pytest


@pytest.mark.parametrize('platform',['cli','telegram'])
def test_host_learning_and_counterexample_invalidation(host,monkeypatch,platform):
    home,_=host
    from hermes_kiokuko.config import load_config,write_yaml
    cfg=load_config(home)
    cfg['monitor']['enabled']=True
    cfg['experience_learning']['mode']='auto'
    cfg['verified_compaction']['enabled']=False
    write_yaml(home/'kiokuko'/'config.yaml',cfg)
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import runtime,experiences,learning
    from hermes_kiokuko.monitor import get_manager,complete_turn,status
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko.deliveries import sync_completed
    from hermes_cli.middleware import run_llm_execution_middleware,run_tool_execution_middleware
    from gateway import session_context as sc
    from hermes_cli.profiles import get_active_profile_name
    from agent.turn_context import compose_user_api_content
    from openai.types.chat import ChatCompletion
    provider=KiokukoMemoryProvider()
    provider.initialize('bootstrap',hermes_home=str(home))
    condition='Node 20'
    def extracted(home,events,**kwargs):
        if isinstance(events,dict):
            return synthesize(events)
        note=next(e for e in events if e['type']=='note')
        call=next(e for e in events if e['type']=='tool.call')
        result=next(e for e in events if e['type']=='tool.result')
        return [{'situation':condition,'observation':'fixture check','action':'npm test',
                 'outcome':'unknown','inference':'Only the fixture tool result was observed.',
                 'evidence':[{'seq':note['seq'],'quote':condition},{'seq':call['seq'],'quote':'npm test'},
                             {'seq':result['seq'],'quote':'fixture check'}],
                 'span':{'start':note['seq'],'end':result['seq']},
                 'conditions':[{'value':condition,'evidence':0}],
                 'field_evidence':{'situation':[0],'action':[1],'observation':[2]},
                 'attempts':[{'call_seq':call['seq'],'result_seq':result['seq'],'target':'npm test'}]}]
    def synthesize(payload):
        pool=payload['experiences']
        success=[e for e in pool if e['structure']['outcome']=='success']
        if not success:return None
        entry=success[0]
        def ref(field,quote):return {'entry_id':entry['id'],'field':field,'quote':quote}
        return {'kind':'recommend','conditions':[ref('condition',condition)],'steps':[ref('action','npm test')],
                'avoid':[],'verification':[ref('verification','npm test')],'exclusions':[],
                'support_ids':[e['id'] for e in success],
                'counterexample_ids':[e['id'] for e in pool if e['structure']['outcome']=='failure']}
    monkeypatch.setattr(experiences,'extract_model',extracted)
    def turn(session,turn_id,code=None):
        tokens=sc.set_session_vars(platform=platform,chat_id='alice-chat',chat_type='private',user_id='alice',
                                   session_id=session,profile=get_active_profile_name()) if platform!='cli' else []
        try:
            raw='Node 20 npm test'
            context=pre_llm_call(session_id=session,turn_id=turn_id,task_id=turn_id,user_message=raw,platform=platform)['context']
            if code is None:return context
            args=dict(session_id=session,turn_id=turn_id,task_id=turn_id,platform=platform,api_mode='chat_completions')
            response=ChatCompletion(id='local',model='test',created=1,object='chat.completion',choices=[{
                'index':0,'message':{'role':'assistant','content':'fixture response'},'finish_reason':'stop'}])
            run_llm_execution_middleware({'messages':[{'role':'user','content':raw}]},lambda r:response,**args)
            result={'exit_code':code,'stdout':'fixture check'}
            assert run_tool_execution_middleware('terminal',{'command':'npm test'},lambda r:result,**args) is result
            service=runtime.current()
            snap=sync_completed(service,session,raw,[{'role':'user','content':raw,'api_content':compose_user_api_content(raw,'',context)}])
            assert snap is not None
            complete_turn(service,snap)
            return context
        finally:sc.clear_session_vars(tokens)
    service=runtime.current()
    manager=get_manager(service)
    try:
        for session in ('learn-a','learn-b','learn-c'):
            turn(session,'one',0)
            assert manager.wait_idle(),status(service)
            # wait_idle includes learning jobs after the v4 worker integration.
        with service.transaction() as db:
            lessons=[dict(r) for r in db.execute("SELECT e.id,l.lifecycle FROM memory_entries e JOIN lessons l ON l.entry_id=e.id AND l.entry_revision=e.current_revision")]
        assert len(lessons)==1 and lessons[0]['lifecycle']=='active',status(service)
        context=turn('reader','one')
        assert '過去経験からの推論' in context and '適用条件' in context
        manager.abort(service.get_snapshot('reader','one'))
        # Record delivery exposure so the next hook must invalidate it.
        from hermes_kiokuko.models import TurnSnapshot
        from hermes_kiokuko.deliveries import record_manual_read
        with service.transaction() as db:
            row=db.execute("SELECT * FROM turn_snapshots WHERE session_id='reader'").fetchone()
            snapshot=TurnSnapshot(**{k:row[k] for k in TurnSnapshot.__dataclass_fields__})
        record_manual_read(service,snapshot,service.get(snapshot,lessons[0]['id']))
        turn('learn-failure','one',1)
        assert manager.wait_idle(),status(service)
        stopped=turn('reader','two')
        assert 'KIOKUKO CORRECTION' in stopped
        assert '過去経験からの推論' not in stopped
        with service.transaction() as db:
            lesson=db.execute('SELECT l.lifecycle FROM lessons l JOIN memory_entries e ON e.id=l.entry_id AND e.current_revision=l.entry_revision').fetchone()
            assert lesson[0]=='contested'
    finally:provider.shutdown()
