"""Memory assistance must preserve the task and report its own failures honestly."""
import json

import pytest


@pytest.mark.parametrize('code', ['STORE_BUSY', 'DEADLINE_EXCEEDED'])
def test_context_failure_keeps_work_and_closes_owned_capture(host, monkeypatch, code):
    home, _ = host
    from hermes_kiokuko.config import load_config, read_yaml, write_yaml
    cfg = load_config(home)
    cfg['monitor']['enabled'] = True
    cfg['verified_compaction']['enabled'] = False
    write_yaml(home / 'kiokuko/config.yaml', cfg)
    native = read_yaml(home / 'config.yaml')
    native['tools'] = {'tool_search': {'enabled': 'off'}}
    write_yaml(home / 'config.yaml', native)
    from hermes_kiokuko.errors import KiokukoError
    def unavailable(*args, **kwargs):
        raise KiokukoError(code)
    monkeypatch.setattr('hermes_kiokuko.turn_hook.prepare', unavailable)
    request = 'Inspect widget.py; retain the original error and do not change production.'
    sent, executed = [], []
    from tools.registry import registry
    registry.register(name='continuity_test_check', toolset='memory',
        schema={'name': 'continuity_test_check', 'description': 'Local test check',
                'parameters': {'type': 'object', 'properties': {}}},
        handler=lambda args, **kwargs: executed.append('checked') or 'local check done')
    import httpx
    def send(client, wire, **kwargs):
        assert wire.url.host == 'continuity-test.invalid'
        payload = json.loads(wire.content)
        sent.append(payload)
        message = ({'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'check', 'type': 'function', 'function': {
                'name': 'continuity_test_check', 'arguments': '{}'}}]} if len(sent) == 1
            else {'role': 'assistant', 'content': 'Normal task completed.'})
        return httpx.Response(200, request=wire, json={
            'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'choices': [{'index': 0, 'message': message,
                         'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]})
    monkeypatch.setattr(httpx.Client, 'send', send)
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('External network disabled')))
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from hermes_kiokuko import runtime
    from hermes_kiokuko.monitor import get_manager
    db = SessionDB(db_path=home / 'state.db')
    agent = AIAgent(api_key='test', base_url='https://continuity-test.invalid/v1',
        provider='openai-compat', model='test', max_iterations=4, enabled_toolsets=['memory'],
        quiet_mode=True, skip_context_files=True, skip_memory=False,
        save_trajectories=False, platform='cli', session_db=db, session_id='continuity')
    agent._disable_streaming = True
    try:
        result = agent.run_conversation(request, conversation_history=[], task_id='task')
        assert executed == ['checked'] and result['final_response'] == 'Normal task completed.'
        current = [m for m in sent[0]['messages'] if m['role'] == 'user'][-1]['content']
        assert current.startswith(request + '\n\n')
        assert code in current and 'Memory context unavailable' in current
        assert '<!--kiokuko:v1:' not in current
        assert agent._memory_manager.flush_pending(timeout=5)
        service = runtime.current()
        manager = get_manager(service)
        assert manager.wait_idle(timeout=2), 'Failed context must not leave a recording capture'
        with service.transaction() as sql:
            row = dict(sql.execute('SELECT * FROM monitor_runs').fetchone())
            assert row['state'] == 'incomplete' and row['error_code'] == code
            assert sql.execute('SELECT count(*) FROM retrieval_deliveries').fetchone()[0] == 0
            assert sql.execute('SELECT count(*) FROM experience_jobs').fetchone()[0] == 0
            assert sql.execute('SELECT count(*) FROM memory_entries').fetchone()[0] == 0
            assert sql.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 0
        from hermes_kiokuko.diagnostics import diagnose
        info = diagnose(home)
        assert info['ok_scope'] == 'readiness_checks_only'
        assert info['answer_application_verified'] is None
        assert info['deliveries'] == {}
        assert any(item['code'] == code for item in info['sync_skips_and_errors'])
    finally:
        agent.close()
        db.close()
        registry._tools.pop('continuity_test_check', None)
