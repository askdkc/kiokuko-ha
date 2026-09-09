"""Codex Responses over real localhost HTTP, including the real extraction route."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import contextvars
import json
import threading
from types import SimpleNamespace

import pytest

from hermes_kiokuko.config import read_yaml, write_yaml
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.experiences import PROMPT, extract_model


@pytest.fixture
def codex_http(host, monkeypatch):
    home, _ = host
    requests = []
    control = {'terminal': 'completed', 'status': 200, 'tool_once': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['content-length'])))
            if self.path != '/v1/responses':
                # Hermes probes local endpoints for model metadata at construction.
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            requests.append((self.path, body))
            if control['status'] != 200:
                data = b'{"error":{"message":"local failure"}}'
                kind = 'application/json'
            else:
                extraction = body.get('instructions') == PROMPT
                if extraction:
                    # Prove extraction sees only current evidence, not request history.
                    raw = body['input'][0]['content']
                    if isinstance(raw, list):
                        raw = raw[0]['text']
                    evidence = json.loads(raw)
                    event = next(e for e in evidence if e.get('actor') == 'user')
                    text = json.dumps([{
                        'situation': 'npm dependencies issue', 'observation': 'user reported missing dependencies',
                        'action': 'investigate dependencies', 'outcome': 'unknown',
                        'inference': 'Check installed dependencies',
                        'evidence': [{'seq': event['seq'], 'quote': 'npm dependencies'}],
                    }])
                else:
                    text = 'Check npm dependencies before running tests.'
                item = {'id': 'msg-local', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
                if not extraction and control['tool_once']:
                    control['tool_once'] = False
                    item = {'id': 'fc-local', 'type': 'function_call', 'call_id': 'call-local',
                            'name': 'monitor_test_check', 'arguments': '{}', 'status': 'completed'}
                events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item}]
                terminal = control['terminal']
                if terminal:
                    # Codex may send output only in item.done, with null terminal output.
                    events.append({'type': 'response.' + terminal, 'response': {
                        'id': 'resp-local', 'object': 'response', 'status': terminal,
                        'model': body['model'], 'output': None,
                        'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
                    }})
                data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
                kind = 'text/event-stream'
            self.send_response(control['status'])
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}/v1'
    from agent import auxiliary_client
    monkeypatch.setattr(auxiliary_client, '_select_pool_entry', lambda provider: (False, None))
    monkeypatch.setattr(auxiliary_client, '_read_codex_access_token', lambda: 'local-test')
    monkeypatch.setattr(auxiliary_client, '_CODEX_AUX_BASE_URL', url)
    import requests as requests_module
    monkeypatch.setattr(requests_module.sessions.Session, 'request',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('external probes disabled')))
    cfg = read_yaml(home / 'config.yaml')
    cfg['model'] = {'provider': 'openai-codex', 'default': 'gpt-5.6-luna-900k', 'context_length': 128000}
    cfg['tools'] = {'tool_search': {'enabled': 'off'}}
    write_yaml(home / 'config.yaml', cfg)
    try:
        yield url, requests, control
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize('terminal', ['completed', 'incomplete', 'failed', None])
def test_codex_extraction_requires_terminal_completion(host, codex_http, terminal):
    home, _ = host
    codex_http[2]['terminal'] = terminal
    evidence = [{'seq': 1, 'actor': 'user', 'text': 'npm dependencies'}]
    if terminal == 'completed':
        assert len(extract_model(home, evidence)) == 1
    else:
        with pytest.raises(KiokukoError, match='EXPERIENCE_INCOMPLETE'):
            extract_model(home, evidence)
    assert len(codex_http[1]) == 1
    path, body = codex_http[1][0]
    assert path == '/v1/responses'
    assert body['model'] == 'gpt-5.6-luna'  # Hermes picker suffix never reaches API.
    assert body['store'] is False and body['stream'] is True


def test_codex_extraction_does_not_retry_or_fallback(host, codex_http):
    codex_http[2]['status'] = 500
    with pytest.raises(Exception):
        extract_model(host[0], [{'seq': 1, 'actor': 'user', 'text': 'npm dependencies'}])
    assert len(codex_http[1]) == 1


@pytest.mark.parametrize('stream', [False, True])
def test_photon_codex_capture_extract_and_recall(host, codex_http, stream):
    home, _ = host
    from hermes_kiokuko.config import load_config
    cfg = load_config(home)
    cfg['monitor']['enabled'] = True
    cfg['verified_compaction']['enabled'] = False
    write_yaml(home / 'kiokuko/config.yaml', cfg)
    from tools.registry import registry
    calls = []
    def check(args, **kwargs):
        calls.append(args)
        return json.dumps({'exit_code': 0, 'stdout': 'local check passed'})
    registry.register(name='monitor_test_check', toolset='memory',
        schema={'name': 'monitor_test_check', 'description': 'Local check',
                'parameters': {'type': 'object', 'properties': {}}}, handler=check)
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from gateway import session_context as sc
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from gateway.session import SessionContext, SessionSource
    from hermes_cli.profiles import get_active_profile_name
    from hermes_kiokuko import runtime
    from hermes_kiokuko.monitor import get_manager, status
    from hermes_kiokuko.orca_transport import read_trace
    db = SessionDB(db_path=home / 'state.db')
    agent = AIAgent(api_key='local-test', base_url=codex_http[0], provider='openai-codex',
        model='gpt-5.6-luna-900k', max_iterations=4, enabled_toolsets=['memory'], quiet_mode=True,
        skip_context_files=True, skip_memory=False, save_trajectories=False,
        platform='photon', session_db=db, session_id='codex-photon')
    agent._disable_streaming = not stream
    source = SessionSource(platform=Platform('photon'), chat_id='alice-dm', chat_type='private',
                           user_id='alice', profile=get_active_profile_name())
    context = SessionContext(source=source, session_key='photon-alice-key', session_id=agent.session_id,
                             connected_platforms=[], home_channels={})
    def run_gateway_turn(message, task_id):
        tokens = GatewayRunner._set_session_env(SimpleNamespace(adapters={}), context)
        try:
            # Unmodified Hermes: cached inbound turns have an explicitly empty ID.
            assert sc._VAR_MAP['HERMES_SESSION_ID'].get() == ''
            return contextvars.copy_context().run(agent.run_conversation, message,
                                                 conversation_history=[], task_id=task_id)
        finally:
            sc.clear_session_vars(tokens)
    codex_http[2]['tool_once'] = True
    try:
        run_gateway_turn('npm dependencies are missing', 'first')
        assert agent._memory_manager.flush_pending(timeout=5)
        service = runtime.current()
        manager = get_manager(service)
        assert manager.wait_idle(), status(service)
        with service.transaction() as sql:
            row = dict(sql.execute('SELECT * FROM monitor_runs').fetchone())
            assert sql.execute('SELECT state FROM experience_jobs').fetchone()[0] == 'done', status(service)
            assert sql.execute("SELECT count(*) FROM memory_entries WHERE kind='experience'").fetchone()[0] == 1
        assert row['state'] == 'complete' and row['api_requests'] == 2 and row['dropped'] == 0
        events = read_trace(service.store.directory, row)
        assert sum(e.type == 'model.request' for e, _ in events) == 2
        assert sum(e.type == 'model.response' for e, _ in events) == 2
        assert sum(e.type == 'tool.result' for e, _ in events) == 1 and len(calls) == 1
        assert len(codex_http[1]) == 3  # Two main calls and one real extraction request.
        run_gateway_turn('npm dependencies are missing again', 'second')
        assert agent._memory_manager.flush_pending(timeout=5)
        assert manager.wait_idle(), status(service)
        main_requests = [r for _, r in codex_http[1] if r.get('instructions') != PROMPT]
        assert '未検証の過去事例' in json.dumps(main_requests[-1], ensure_ascii=False)
        assert status(service)['runs'] == {'complete': 2}
        assert status(service)['jobs'] == {'done': 2}
    finally:
        agent.close()
        db.close()
        registry._tools.pop('monitor_test_check', None)
