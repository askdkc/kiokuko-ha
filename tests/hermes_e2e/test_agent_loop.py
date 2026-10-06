"""Real AIAgent loop and SessionDB; only the external LLM transport is replaced."""
import json
import pytest


@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize('selection', ['explicit', 'setup'])
def test_real_agent_save_propose_correct_continue_resume(host, monkeypatch, bridge, selection):
    home, _ = host
    from hermes_kiokuko.config import read_yaml, write_yaml
    cfg = read_yaml(home / "config.yaml")
    cfg["tools"] = {"tool_search": {"enabled": "on" if bridge else "off"}}
    write_yaml(home / "config.yaml", cfg)
    import httpx
    from hermes_kiokuko import runtime
    from hermes_kiokuko.cli import cli_snapshot
    from hermes_kiokuko.models import ExplicitCommand
    from run_agent import AIAgent
    from hermes_state import SessionDB
    captured, responses = [], []

    def send(client, request, **kwargs):
        assert request.url.host == "kiokuko-test.invalid", f"Unexpected network: {request.url.host}"
        payload = json.loads(request.content)
        captured.append(payload)
        message = responses.pop(0) if responses else {"role": "assistant", "content": "done"}
        calls = message.get("tool_calls")
        finish = "tool_calls" if calls else "stop"
        if payload.get("stream"):
            delta = dict(message)
            if calls:
                delta["tool_calls"] = [{**call, "index": i} for i, call in enumerate(calls)]
            chunks = [{"id": "chat-test", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
                       "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "chat-test", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
                       "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
            content = ''.join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, content=content.encode(), headers={"content-type": "text/event-stream"}, request=request)
        return httpx.Response(200, json={"id": "chat-test", "object": "chat.completion", "created": 1, "model": "test-model",
                   "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    # Prevent optional model-catalog probes from contacting an external service.
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Network disabled in test")))
    db = SessionDB(db_path=home / "state.db")
    (home / "memories").mkdir(exist_ok=True)
    for name in ("MEMORY.md", "USER.md"):
        (home / "memories" / name).write_text("NATIVE_MUST_NOT_LEAK")
    def new_agent():
        from hermes_cli.tools_config import _get_platform_tools
        enabled = ['memory'] if selection == 'explicit' else sorted(_get_platform_tools(read_yaml(home / 'config.yaml'), 'cli'))
        return AIAgent(api_key="test-key", base_url="https://kiokuko-test.invalid/v1", provider="openai-compat",
                       model="test-model", max_iterations=4, enabled_toolsets=enabled,
                       quiet_mode=True, skip_context_files=True, skip_memory=False,
                       save_trajectories=False, platform="cli", session_db=db, session_id="agent-session")
    agent = new_agent()
    try:
        raw = "@kiokuko remember --scope principal\n日本語で返答する。"
        agent.run_conversation(raw, conversation_history=[], task_id="task-1")
        assert agent._memory_manager.flush_pending(timeout=5)
        service = runtime.current()
        with service.transaction() as sql:
            entry = dict(sql.execute("SELECT * FROM memory_entries").fetchone())
            assert entry["claim"] == "日本語で返答する。"
            assert sql.execute("SELECT state FROM retrieval_deliveries").fetchone()[0] == "observed_in_history"
        assert "NATIVE_MUST_NOT_LEAK" not in json.dumps(captured)
        chat_requests = [request for request in captured if "messages" in request]
        assert "<!--kiokuko:v1:" in chat_requests[0]["messages"][-1]["content"]
        if selection == 'setup':
            from hermes_kiokuko.diagnostics import diagnose
            observed = diagnose(home, agent=agent)['tool_publication']['session']
            assert observed['available'], observed
            assert observed['status'] == 'observed_in_this_process'
            if bridge:
                responses.append({'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call-search', 'type': 'function', 'function': {'name': 'tool_search',
                     'arguments': json.dumps({'queries': ['kiokuko'], 'limit': 10})}}]})
        proposal_args = {"action": "propose", "claim": "PostgreSQLを採用している", "evidence_quote": "PostgreSQL"}
        tool_name = "tool_call" if bridge else "kiokuko_propose"
        tool_args = {"name": "kiokuko_propose", "arguments": proposal_args} if bridge else proposal_args
        responses.append({"role": "assistant", "content": None, "tool_calls": [{"id": "call-propose", "type": "function",
                           "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}]})
        agent.run_conversation("PostgreSQLへの移行は却下した", conversation_history=db.get_messages_as_conversation("agent-session"), task_id="task-2")
        assert agent._memory_manager.flush_pending(timeout=5)
        with service.transaction() as sql:
            candidate_row = sql.execute("SELECT state,id FROM memory_candidates").fetchone()
            assert candidate_row is not None, [m for r in captured if "messages" in r for m in r["messages"] if m["role"] == "tool"]
            assert candidate_row[0] == "pending"
            assert sql.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 1
        if selection == 'setup':
            import argparse
            from hermes_kiokuko.cli import setup_parser, execute
            parser = argparse.ArgumentParser()
            setup_parser(parser)
            proposal_id = candidate_row['id']
            displayed = []
            approved = execute(parser.parse_args(['approve', proposal_id]), home,
                               input_fn=lambda _: proposal_id, output=displayed.append)
            assert json.loads(displayed[-1])['candidate']['id'] == proposal_id
            with service.transaction() as sql:
                persisted = sql.execute('SELECT state,promoted_entry_id FROM memory_candidates WHERE id=?', (proposal_id,)).fetchone()
                assert tuple(persisted) == ('accepted', approved['entry_id'])
                confirmed = sql.execute('SELECT confirmation_kind,state FROM memory_entries WHERE id=?', (approved['entry_id'],)).fetchone()
                assert tuple(confirmed) == ('cli_approved', 'active')
            recall_args = {'action': 'get', 'entry_id': approved['entry_id']}
            name = 'tool_call' if bridge else 'kiokuko_recall'
            args = {'name': 'kiokuko_recall', 'arguments': recall_args} if bridge else recall_args
            responses.append({'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call-recall', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]})
            agent.run_conversation('確認済みの記憶を取得する', conversation_history=db.get_messages_as_conversation('agent-session'), task_id='task-recall')
            results = {m['tool_call_id']: json.loads(m['content']) for r in captured if 'messages' in r
                       for m in r['messages'] if m['role'] == 'tool' and m.get('tool_call_id') in {'call-search', 'call-recall'}}
            assert results['call-recall']['ok'] and results['call-recall']['data']['id'] == approved['entry_id']
            if bridge:
                assert {'kiokuko_recall', 'kiokuko_propose', 'kiokuko_manage'} <= set(results['call-search']['tools'])
        snap = cli_snapshot(service, "human correction")
        service.explicit(snap, ExplicitCommand("correct", "英語で返答する。", entry_id=entry["id"], expected_revision=1))
        previous = db.get_messages_as_conversation("agent-session")
        agent.close()
        agent = new_agent()
        agent.run_conversation("continue", conversation_history=previous, task_id="task-3")
        assert agent._memory_manager.flush_pending(timeout=5)
        chat_requests = [request for request in captured if "messages" in request]
        user = [m for m in chat_requests[-1]["messages"] if m["role"] == "user"][-1]
        assert "KIOKUKO CORRECTION" in user["content"] and "英語で返答する。" in user["content"]
        assert "日本語で返答する。" not in user["content"]
        for name in ("MEMORY.md", "USER.md"):
            assert (home / "memories" / name).read_text() == "NATIVE_MUST_NOT_LEAK"
    finally:
        agent.close()
        db.close()


def test_configuration_change_distinguishes_existing_and_new_agents(host, monkeypatch):
    """Actual agent snapshots, no model requests or production profile."""
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Network disabled in test')))
    from hermes_kiokuko.config import setup, read_yaml, write_yaml
    from hermes_kiokuko.diagnostics import diagnose
    from hermes_cli.tools_config import _get_platform_tools
    from run_agent import AIAgent
    from hermes_state import SessionDB
    home, _ = host
    cfg = read_yaml(home / 'config.yaml')
    cfg['platform_toolsets'] = {'cli': []}
    write_yaml(home / 'config.yaml', cfg)
    db = SessionDB(db_path=home / 'state.db')
    def new_agent(session_id):
        enabled = sorted(_get_platform_tools(read_yaml(home / 'config.yaml'), 'cli'))
        return AIAgent(api_key='test-key', base_url='https://kiokuko-test.invalid/v1',
            provider='openai-compat', model='test-model', enabled_toolsets=enabled,
            quiet_mode=True, skip_context_files=True, skip_memory=False, save_trajectories=False,
            platform='cli', session_db=db, session_id=session_id)
    old = new_agent('old-session')
    fresh = None
    try:
        before = diagnose(home, agent=old)['tool_publication']['session']
        assert not before.get('available'), before
        setup(home, enable_memory_for=['cli'])
        existing = diagnose(home, agent=old)['tool_publication']
        assert not existing['session'].get('available'), existing
        assert existing['restart_or_new_session_required']
        fresh = new_agent('fresh-session')
        current = diagnose(home, agent=fresh)['tool_publication']['session']
        assert current['available'], current
        assert current['session_id'] == 'fresh-session'
        assert diagnose(home)['session_verification'] == 'unconfirmed'
        # A host schema cache/snapshot may still show an old tool. A live deny
        # must prevent doctor from advertising it as usable.
        from hermes_kiokuko.config import load_config
        private = load_config(home)
        private['tool_access']['excluded_tools'] = ['kiokuko_propose']
        write_yaml(home / 'kiokuko/config.yaml', private)
        excluded = diagnose(home, agent=fresh)['tool_publication']['session']
        assert not excluded['available']
        assert excluded['current_admission']['kiokuko_propose'] is False
    finally:
        old.close()
        if fresh:
            fresh.close()
        db.close()
