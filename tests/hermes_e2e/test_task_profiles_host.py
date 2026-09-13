"""Real Hermes agent, signed history and completion worker; only HTTP is replaced."""
import argparse
import json

import pytest


@pytest.mark.parametrize('platform', ['cli', 'telegram'])
def test_real_agent_profile_capture_recall_and_delete(host, monkeypatch, tmp_path, platform):
    home, _ = host
    from hermes_kiokuko.config import load_config, write_yaml
    config = load_config(home)
    config['task_profile_memory']['mode'] = 'resolve'
    config['verified_compaction']['enabled'] = False
    write_yaml(home / 'kiokuko/config.yaml', config)
    root = tmp_path / 'project'
    (root / 'src').mkdir(parents=True)
    (root / 'src/widget.py').write_text('pass\n')
    monkeypatch.chdir(root)
    monkeypatch.delenv('TERMINAL_CWD', raising=False)
    from agent.runtime_cwd import _SESSION_CWD, set_session_cwd
    cwd_token = set_session_cwd(str(root))
    import httpx
    captured = []
    def send(client, request, **kwargs):
        assert request.url.host == 'kiokuko-test.invalid'
        payload = json.loads(request.content)
        captured.append(payload)
        message = {'role':'assistant','content':'done'}
        response = {'id':'fixture','object':'chat.completion','created':1,'model':'test-model',
                    'choices':[{'index':0,'message':message,'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}
        if payload.get('stream'):
            chunks = [{'id':'fixture','object':'chat.completion.chunk','created':1,'model':'test-model',
                       'choices':[{'index':0,'delta':message,'finish_reason':None}]},
                      {'id':'fixture','object':'chat.completion.chunk','created':1,'model':'test-model',
                       'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}]
            content = ''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
            return httpx.Response(200,content=content.encode(),headers={'content-type':'text/event-stream'},request=request)
        return httpx.Response(200,json=response,request=request)
    monkeypatch.setattr(httpx.Client, 'send', send)
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a,**k: (_ for _ in ()).throw(RuntimeError('Network disabled')))
    from gateway import session_context as sc
    from hermes_cli.profiles import get_active_profile_name
    envelope = sc.set_session_vars(platform=platform,chat_id='alice-chat',chat_type='private',user_id='alice',
                                   session_id='profile-test',session_key='alice-key',profile=get_active_profile_name()) if platform != 'cli' else []
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from hermes_kiokuko import runtime
    from hermes_kiokuko.task_profiles import delete, review, FENCE
    db = SessionDB(db_path=home/'state.db')
    agent = None
    def new_agent():
        return AIAgent(api_key='test-key',base_url='https://kiokuko-test.invalid/v1',provider='openai-compat',
                       model='test-model',max_iterations=3,enabled_toolsets=['memory'],quiet_mode=True,
                       skip_context_files=True,skip_memory=False,save_trajectories=False,platform=platform,
                       session_db=db,session_id='profile-test')
    try:
        agent = new_agent()
        agent.run_conversation('Please inspect `src/widget.py`',conversation_history=[],task_id='one')
        assert agent._memory_manager.flush_pending(timeout=5)
        service = runtime.current()
        with service.transaction() as sql:
            rows = sql.execute('SELECT id FROM task_profiles').fetchall()
            assert len(rows) == 1, runtime.status_counts()
            profile_id = rows[0][0]
        agent.close()
        agent = new_agent()
        agent.run_conversation('Please inspect `widget.py`',conversation_history=db.get_messages_as_conversation('profile-test'),task_id='two')
        assert agent._memory_manager.flush_pending(timeout=5)
        query = [m for m in captured[-1]['messages'] if m['role']=='user'][-1]['content']
        assert 'Resolved target reference: src/widget.py' in query
        service = runtime.current()
        record, review_hash = review(service,profile_id)
        delete(service,profile_id,review_hash)
        agent.run_conversation('Please inspect `widget.py`',conversation_history=[],task_id='three')
        assert agent._memory_manager.flush_pending(timeout=5)
        query = [m for m in captured[-1]['messages'] if m['role']=='user'][-1]['content']
        assert FENCE in query and 'Resolved target reference:' not in query
    finally:
        if agent: agent.close()
        db.close()
        sc.clear_session_vars(envelope)
        _SESSION_CWD.reset(cwd_token)


def test_profile_cli_mode_review_cancel_delete_and_gateway_rejection(host, monkeypatch, tmp_path):
    home, _ = host
    from hermes_kiokuko.cli import setup_parser, execute
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko import runtime
    from hermes_kiokuko.errors import KiokukoError
    from agent.runtime_cwd import _SESSION_CWD, set_session_cwd
    root = tmp_path/'root'
    root.mkdir()
    (root/'widget.py').write_text('pass')
    token = set_session_cwd(str(root))
    provider = KiokukoMemoryProvider()
    provider.initialize('s',hermes_home=str(home))
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    def command(*args, **kwargs):
        return execute(parser.parse_args(['task-profiles',*args]),home,**kwargs)
    try:
        assert command('mode','suggest')['mode']=='suggest'
        raw='Inspect `widget.py`'
        context=pre_llm_call(session_id='s',turn_id='t',user_message=raw,platform='cli')['context']
        messages=[{'role':'user','content':raw,'api_content':raw+'\n\n'+context}]
        provider.sync_turn(raw,'done',session_id='s',messages=messages)
        profile_id=command('list')[0]['id']
        displayed=[]
        with pytest.raises(KiokukoError,match='CANCELLED'):
            command('delete',profile_id,input_fn=lambda _: 'no',output=displayed.append)
        assert command('status')['profiles']==1
        assert 'Hermes history and backups remain' in displayed[-1]
        assert command('reindex',output=displayed.append)['reindexed']==1
        assert command('delete',profile_id,input_fn=lambda _:profile_id,output=displayed.append)['deleted']==profile_id
        from gateway import session_context as sc
        envelope=sc.set_session_vars(platform='telegram',user_id='alice',chat_type='private',chat_id='chat',session_id='s')
        try:
            with pytest.raises(KiokukoError,match='LOCAL_CLI_REQUIRED'):command('mode','resolve')
        finally:sc.clear_session_vars(envelope)
    finally:
        provider.shutdown()
        _SESSION_CWD.reset(token)


def test_optional_capture_failure_preserves_completion_and_monitor(host, monkeypatch):
    home, _ = host
    from hermes_kiokuko import monitor, runtime, task_profiles
    from hermes_kiokuko.errors import KiokukoError
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.turn_hook import pre_llm_call
    calls = []
    def fail_capture(*args):
        raise KiokukoError('TASK_PROFILE_CAPACITY')
    monkeypatch.setattr(task_profiles, 'capture_completed', fail_capture)
    monkeypatch.setattr(monitor, 'complete_turn', lambda service, snapshot: calls.append(snapshot.key))
    provider = KiokukoMemoryProvider()
    provider.initialize('capture-failure', hermes_home=str(home))
    try:
        raw = 'Inspect widget.py'
        context = pre_llm_call(session_id='capture-failure', turn_id='one', user_message=raw, platform='cli')['context']
        messages = [{'role':'user', 'content':raw, 'api_content':raw+'\n\n'+context}]
        provider.sync_turn(raw, 'done', session_id='capture-failure', messages=messages)
        assert len(calls) == 1
        with runtime.current().transaction() as sql:
            assert sql.execute('SELECT count(*) FROM turn_syncs').fetchone()[0] == 1
            assert sql.execute('SELECT count(*) FROM task_profiles').fetchone()[0] == 0
        assert runtime.status_counts()['TASK_PROFILE_CAPACITY'] == 1
    finally:
        provider.shutdown()
