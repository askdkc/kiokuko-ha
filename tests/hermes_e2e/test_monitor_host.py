"""Real Hermes/SDK/Orca path against a local HTTP server, no paid model calls."""
import contextvars
from concurrent.futures import ThreadPoolExecutor
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest


@pytest.fixture
def http_model():
    captured=[]
    controls={"status":200,"delay":0,"content":"npm test passed after installing dependencies"}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['content-length'])))
            captured.append(body)
            if controls['delay']:
                threading.Event().wait(controls['delay'])
            if controls['status'] != 200:
                data=b'{"error":{"message":"temporary local model failure"}}'
                self.send_response(controls['status'])
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass
                return
            message={'role':'assistant','content':controls['content']}
            finish_reason='stop'
            if controls.get('tool_once'):
                controls['tool_once']=False
                message={'role':'assistant','content':None,'tool_calls':[{'id':'check-1','type':'function',
                    'function':{'name':'monitor_test_check','arguments':'{}'}}]}
                finish_reason='tool_calls'
            if body.get('stream'):
                chunks=[{'id':'chat-local','object':'chat.completion.chunk','created':1,'model':'test-model',
                         'choices':[{'index':0,'delta':message,'finish_reason':None}]},
                        {'id':'chat-local','object':'chat.completion.chunk','created':1,'model':'test-model',
                         'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}]
                data=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
                kind='text/event-stream'
            else:
                data=json.dumps({'id':'chat-local','object':'chat.completion','created':1,'model':'test-model',
                    'choices':[{'index':0,'message':message,'finish_reason':finish_reason}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode()
                kind='application/json'
            self.send_response(200)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1',captured,controls
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def enable(home):
    from hermes_kiokuko.config import load_config,write_yaml
    config=load_config(home)
    config['monitor']['enabled']=True
    config['verified_compaction']['enabled']=False
    write_yaml(home/'kiokuko'/'config.yaml',config)


@pytest.mark.parametrize('platform',['cli','telegram'])
@pytest.mark.parametrize('stream',[False,True])
def test_real_http_capture_to_next_turn(host,http_model,monkeypatch,stream,platform):
    home,_=host
    enable(home)
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('external probes disabled')))
    from hermes_kiokuko import experiences,runtime
    def extract(home,events):
        event=next(e for e in events if e['actor']=='user')
        return [{'situation':'npm dependencies issue','observation':'test failure reported by the user',
                 'action':'investigate dependencies','outcome':'unknown','inference':'Check installed dependencies',
                 'evidence':[{'seq':event['seq'],'quote':'npm dependencies'}]}]
    monkeypatch.setattr(experiences,'extract_model',extract)
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from hermes_kiokuko.monitor import get_manager,status
    from hermes_kiokuko.orca_transport import read_trace
    db=SessionDB(db_path=home/'state.db')
    agent=AIAgent(api_key='local-test',base_url=http_model[0],provider='openai-compat',model='test-model',
        max_iterations=2,enabled_toolsets=['memory'],quiet_mode=True,skip_context_files=True,
        skip_memory=False,save_trajectories=False,platform=platform,session_db=db,session_id='monitor-host')
    agent._disable_streaming=not stream
    from gateway import session_context as sc
    from hermes_cli.profiles import get_active_profile_name
    tokens=sc.set_session_vars(platform=platform,chat_id='alice-dm',chat_type='private',user_id='alice',
        session_id='monitor-host',profile=get_active_profile_name()) if platform!='cli' else []
    try:
        agent.run_conversation('npm dependencies are missing',conversation_history=[],task_id='first')
        assert agent._memory_manager.flush_pending(timeout=5)
        service=runtime.current()
        manager=get_manager(service)
        assert manager.wait_idle(),status(service)
        with service.transaction() as sql:
            row=dict(sql.execute('SELECT * FROM monitor_runs').fetchone())
            jobs=[dict(r) for r in sql.execute('SELECT * FROM experience_jobs')]
            assert sql.execute('SELECT count(*) FROM memory_entries WHERE kind=\'experience\'').fetchone()[0]==1,jobs
        events=read_trace(service.store.directory,row)
        assert sum(e.type=='model.request' for e,_ in events)==1
        assert sum(e.type=='model.response' for e,_ in events)==1
        assert len(http_model[1])==1
        agent.run_conversation('npm dependencies are missing again',conversation_history=[],task_id='second')
        assert '未検証の過去事例' in json.dumps(http_model[1][-1],ensure_ascii=False)
        assert agent._memory_manager.flush_pending(timeout=5)
        assert manager.wait_idle(),status(service)
    finally:
        agent.close()
        db.close()
        sc.clear_session_vars(tokens)


def test_real_http_tool_loop(host,http_model,monkeypatch):
    home,_=host
    enable(home)
    from hermes_kiokuko.config import read_yaml,write_yaml
    cfg=read_yaml(home/'config.yaml')
    cfg['tools']={'tool_search':{'enabled':'off'}}
    write_yaml(home/'config.yaml',cfg)
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('external probes disabled')))
    from hermes_kiokuko import experiences,runtime
    monkeypatch.setattr(experiences,'extract_model',lambda *a: [])
    from tools.registry import registry
    calls=[]
    def check(args,**kwargs):
        calls.append(args)
        return json.dumps({'exit_code':0,'stdout':'local check passed'})
    registry.register(name='monitor_test_check',toolset='memory',
        schema={'name':'monitor_test_check','description':'Harmless local verification',
                'parameters':{'type':'object','properties':{}}},handler=check)
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from hermes_kiokuko.monitor import get_manager,status
    from hermes_kiokuko.orca_transport import read_trace
    db=SessionDB(db_path=home/'state.db')
    agent=AIAgent(api_key='local-test',base_url=http_model[0],provider='openai-compat',model='test-model',
        max_iterations=4,enabled_toolsets=['memory'],quiet_mode=True,skip_context_files=True,
        skip_memory=False,save_trajectories=False,platform='cli',session_db=db,session_id='tool-loop')
    agent._disable_streaming=True
    http_model[2]['tool_once']=True
    try:
        agent.run_conversation('Run the local check',conversation_history=[],task_id='tool-task')
        assert agent._memory_manager.flush_pending(timeout=5)
        service=runtime.current()
        assert get_manager(service).wait_idle(),status(service)
        with service.transaction() as sql:
            row=dict(sql.execute('SELECT * FROM monitor_runs').fetchone())
        events=read_trace(service.store.directory,row)
        assert len(calls)==1 and len(http_model[1])==2
        assert sum(e.type=='model.request' for e,p in events)==2
        assert sum(e.type=='tool.call' for e,p in events)==1
        assert any(e.type=='tool.result' and 'local check passed' in json.dumps(p) for e,p in events)
    finally:
        agent.close()
        db.close()
        registry._tools.pop('monitor_test_check',None)


def test_real_gateway_middleware_concurrent_scope(host,monkeypatch):
    home,_=host
    enable(home)
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import runtime,experiences
    from hermes_kiokuko.monitor import get_manager,status
    from hermes_cli.middleware import run_llm_execution_middleware,run_tool_execution_middleware
    from gateway import session_context as sc
    from hermes_cli.profiles import get_active_profile_name
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko.deliveries import sync_completed
    from hermes_kiokuko.monitor import complete_turn
    from openai.types.chat import ChatCompletion
    monkeypatch.setattr(experiences,'extract_model',lambda *a:[])
    provider=KiokukoMemoryProvider()
    provider.initialize('a',hermes_home=str(home))
    barrier=threading.Barrier(3)
    def turn(user,chat_type):
        tokens=sc.set_session_vars(platform='telegram',chat_id=user,chat_type=chat_type,user_id=user,
                                   session_id=user,profile=get_active_profile_name())
        try:
            raw='npm dependencies '+user
            context=pre_llm_call(session_id=user,turn_id='turn',task_id='task',user_message=raw,platform='telegram')['context']
            barrier.wait(timeout=10)
            args=dict(session_id=user,turn_id='turn',task_id='task',platform='telegram',api_mode='chat_completions')
            response=ChatCompletion(id='local',model='test',created=1,object='chat.completion',choices=[{'index':0,'message':{'role':'assistant','content':user},'finish_reason':'stop'}])
            assert run_llm_execution_middleware({'messages':[{'role':'user','content':raw}]},lambda r:response,**args) is response
            result={'exit_code':0,'stdout':user}
            assert run_tool_execution_middleware('terminal',{'command':'test'},lambda a:result,**args) is result
            service=runtime.current()
            from agent.turn_context import compose_user_api_content
            snap=sync_completed(service,user,raw,[{'role':'user','content':raw,'api_content':compose_user_api_content(raw,'',context)}])
            assert snap is not None
            complete_turn(service,snap)
            return user
        finally:
            for token in reversed(tokens):
                token.var.reset(token)
    try:
        with ThreadPoolExecutor(3) as pool:
            assert set(pool.map(lambda x:turn(*x),[('a','private'),('b','private'),('group','group')]))=={'a','b','group'}
        service=runtime.current()
        manager=get_manager(service)
        assert manager.wait_idle(),status(service)
        from hermes_kiokuko.orca_transport import read_trace
        with service.transaction() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM monitor_runs')]
        assert len(rows)==3
        for row in rows:
            snap=json.loads(row['snapshot_json'])
            events=read_trace(service.store.directory,row)
            values=[p for e,p in events if e.type=='tool.result']
            assert values==[{'exit_code':0,'stdout':snap['session_id']}]
    finally:
        provider.shutdown()


@pytest.mark.parametrize('failure',[429,500,'timeout','cancel'])
def test_http_errors_and_cancellation_observe_once(host,http_model,monkeypatch,failure):
    home,_=host
    enable(home)
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import runtime,experiences
    from hermes_kiokuko.monitor import get_manager,status,complete_turn
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko.deliveries import sync_completed
    from hermes_kiokuko.orca_transport import read_trace
    from hermes_cli.middleware import run_llm_execution_middleware
    from agent.turn_context import compose_user_api_content
    from openai import OpenAI
    monkeypatch.setattr(experiences,'extract_model',lambda *a:[])
    provider=KiokukoMemoryProvider()
    provider.initialize('error',hermes_home=str(home))
    raw='test request'
    context=pre_llm_call(session_id='error',turn_id='turn',task_id='task',user_message=raw,platform='cli')['context']
    if failure=='timeout':
        http_model[2].update(delay=.3,status=500)
    elif failure!='cancel':
        http_model[2]['status']=failure
    client=OpenAI(api_key='local-test',base_url=http_model[0],max_retries=0,timeout=.05 if failure=='timeout' else 2)
    calls=[]
    error=None
    def execute(request):
        calls.append(request)
        if failure=='cancel':
            raise InterruptedError('cancelled')
        return client.chat.completions.create(**request)
    try:
        with pytest.raises(Exception) as caught:
            run_llm_execution_middleware({'model':'test-model','messages':[{'role':'user','content':raw}]},execute,
                session_id='error',turn_id='turn',task_id='task',platform='cli',api_mode='chat_completions')
        assert len(calls)==1
        service=runtime.current()
        snap=sync_completed(service,'error',raw,[{'role':'user','content':raw,'api_content':compose_user_api_content(raw,'',context)}])
        complete_turn(service,snap)
        manager=get_manager(service)
        assert manager.wait_idle(),status(service)
        with service.transaction() as db:
            row=dict(db.execute('SELECT * FROM monitor_runs').fetchone())
        events=read_trace(service.store.directory,row)
        errors=[p for e,p in events if e.type=='error']
        assert len(errors)==1 and errors[0]['error_type']==type(caught.value).__name__
        assert not any(e.type=='model.response' for e,p in events)
        assert len(http_model[1])==(0 if failure=='cancel' else 1)
    finally:
        client.close()
        provider.shutdown()


def test_extraction_uses_one_configured_route(host,http_model):
    home,_=host
    from hermes_kiokuko.config import read_yaml,write_yaml
    from hermes_kiokuko.experiences import extract_model
    cfg=read_yaml(home/'config.yaml')
    cfg['auxiliary']['compression']={'provider':'custom','model':'test-model','base_url':http_model[0],'api_key':'local-test'}
    write_yaml(home/'config.yaml',cfg)
    http_model[2]['content']='[]'
    assert extract_model(home,[{'seq':1,'text':'ordinary conversation'}])==[]
    assert len(http_model[1])==1
    assert http_model[1][0]['model']=='test-model'
    http_model[2]['status']=500
    with pytest.raises(Exception):
        extract_model(home,[{'seq':1,'text':'ordinary conversation'}])
    assert len(http_model[1])==2  # No retry or provider fallback.


def test_monitor_toggle_preserves_http_request_and_response(host,http_model,monkeypatch):
    home,_=host
    enable(home)
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import experiences
    from hermes_kiokuko.config import load_config,write_yaml
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_cli.middleware import run_llm_execution_middleware
    from openai import OpenAI
    monkeypatch.setattr(experiences,'extract_model',lambda *a: [])
    provider=KiokukoMemoryProvider()
    provider.initialize('invariance',hermes_home=str(home))
    client=OpenAI(api_key='local-test',base_url=http_model[0],max_retries=0)
    try:
        pre_llm_call(session_id='invariance',turn_id='turn',task_id='task',user_message='hello',platform='cli')
        request={'model':'test-model','messages':[{'role':'user','content':'hello'}]}
        def call():
            return run_llm_execution_middleware(request,lambda r:client.chat.completions.create(**r),
                session_id='invariance',turn_id='turn',task_id='task',platform='cli',api_mode='chat_completions')
        monitored=call()
        cfg=load_config(home)
        cfg['monitor']['enabled']=False
        write_yaml(home/'kiokuko/config.yaml',cfg)
        plain=call()
        assert monitored.model_dump()==plain.model_dump()
        assert http_model[1]==[request,request]
    finally:
        client.close()
        provider.shutdown()


def test_monitor_cli_enable_disable_and_gateway_denial(host,monkeypatch):
    home,_=host
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import runtime
    from hermes_kiokuko.monitor_cli import execute
    from hermes_kiokuko.errors import KiokukoError
    from gateway import session_context as sc
    from types import SimpleNamespace
    provider=KiokukoMemoryProvider()
    provider.initialize('cli',hermes_home=str(home))
    try:
        service=runtime.current()
        assert execute(service,SimpleNamespace(monitor_action='enable'))['enabled']
        assert not execute(service,SimpleNamespace(monitor_action='disable'))['enabled']
        monkeypatch.setattr('hermes_kiokuko.orca_transport.shutil.which',lambda name:None)
        assert execute(service,SimpleNamespace(monitor_action='status'))['node']=='not_required'
        with pytest.raises(KiokukoError,match='NODE_MISSING'):
            execute(service,SimpleNamespace(monitor_action='enable'))
        tokens=sc.set_session_vars(platform='telegram',chat_id='chat',chat_type='private',user_id='other',session_id='chat')
        try:
            with pytest.raises(KiokukoError,match='LOCAL_CLI_REQUIRED'):
                execute(service,SimpleNamespace(monitor_action='status'))
        finally:
            sc.clear_session_vars(tokens)
    finally:
        provider.shutdown()
