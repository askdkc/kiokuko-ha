"""Gateway resets ContextVars on each inbound message, including cached-agent turns."""
import contextvars
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("publish_initial", [False, True])
def test_gateway_cached_turn_binding(host, publish_initial):
    home, _ = host
    from agent.agent_init import _publish_session_id
    from agent.turn_context import _collect_pre_llm_call_context
    from gateway import run, session_context as sc
    from gateway.config import Platform
    from gateway.session import SessionContext, SessionSource
    from hermes_cli.middleware import run_tool_execution_middleware
    from hermes_cli.profiles import get_active_profile_name
    from hermes_kiokuko import runtime
    from hermes_kiokuko.plugin_tools import propose_handler
    from hermes_kiokuko.provider import KiokukoMemoryProvider

    provider = KiokukoMemoryProvider()
    provider.initialize("session", hermes_home=str(home))
    source = SessionSource(platform=Platform("photon"), chat_id="chat", chat_type="dm",
                           user_id="sender", profile=get_active_profile_name())
    context = SessionContext(source=source, session_key="photon-test-key", session_id="session",
                             connected_platforms=[], home_channels={})
    runner = SimpleNamespace(adapters={})
    agent = SimpleNamespace(session_id="session", model="test", platform="photon")

    def turn(number):
        # Only construction publishes the ID. Reusing the agent does not construct it again.
        if number == 0 and publish_initial:
            _publish_session_id(agent.session_id)
        before = sc._VAR_MAP['HERMES_SESSION_ID'].get()
        raw = "user text"
        rendered = _collect_pre_llm_call_context(agent, effective_task_id="session", turn_id=str(number),
                   original_user_message=raw, messages=[{"role": "user", "content": raw}], conversation_history=[])
        result = run_tool_execution_middleware("kiokuko_propose", {"claim": "candidate"},
                 lambda args: propose_handler(args, session_id="session", task_id="session"),
                 session_id="session", turn_id=str(number), task_id="session")
        assert sc._VAR_MAP['HERMES_SESSION_ID'].get() == before
        return rendered, json.loads(result)

    results = []
    for number in range(3):
        tokens = run.GatewayRunner._set_session_env(runner, context)
        try:
            assert sc._VAR_MAP['HERMES_SESSION_ID'].get() == ''
            # The Gateway copies each inbound task's context into the agent worker.
            results.append(contextvars.copy_context().run(turn, number))
        finally:
            sc.clear_session_vars(tokens)
    assert all(result['ok'] is True and result['data']['state'] == 'pending' for _, result in results)
    # The callback ID cannot authorize a different sender in the same DM session.
    source.user_id = "different-sender"
    tokens = run.GatewayRunner._set_session_env(runner, context)
    try:
        rejected_context, rejected_result = contextvars.copy_context().run(turn, 3)
    finally:
        sc.clear_session_vars(tokens)
    assert "SESSION_IDENTITY_MISMATCH" in rejected_context
    assert rejected_result == {"ok": False, "error": "TURN_CONTEXT_UNAVAILABLE"}
    with runtime.current().transaction() as db:
        assert db.execute("SELECT count(*) FROM turn_snapshots").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0


@pytest.mark.parametrize('field,value,code', [
    ('ID', 'other-session', 'SESSION_IDENTITY_MISMATCH'),
    ('ID', None, 'TOOL_CONTEXT_MISMATCH'),
    ('PROFILE', 'other-profile', 'PROFILE_IDENTITY_MISMATCH'),
    ('PLATFORM', 'telegram', 'PLATFORM_IDENTITY_MISMATCH'),
    ('USER_ID', 'other-sender', 'TOOL_CONTEXT_MISMATCH'),
    ('CHAT_ID', 'other-chat', 'TOOL_CONTEXT_MISMATCH'),
    ('THREAD_ID', 'other-thread', 'TOOL_CONTEXT_MISMATCH'),
    ('CHAT_TYPE', 'group', 'TOOL_CONTEXT_MISMATCH'),
    ('KEY', '', 'TOOL_CONTEXT_MISMATCH'),
])
def test_empty_id_still_rechecks_live_gateway_scope(host, field, value, code):
    home, _ = host
    from gateway import session_context as sc
    from hermes_cli.profiles import get_active_profile_name
    from hermes_kiokuko import runtime
    from hermes_kiokuko.config import load_config, write_yaml
    from hermes_kiokuko.errors import KiokukoError
    from hermes_kiokuko.monitor_capture import current_binding
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.tool_context import tool_execution_middleware
    from hermes_kiokuko.turn_hook import pre_llm_call
    provider = KiokukoMemoryProvider()
    provider.initialize('session', hermes_home=str(home))
    tokens = sc.set_session_vars(platform='photon', session_id='', user_id='sender', chat_id='chat',
                                chat_type='dm', session_key='photon-key', profile=get_active_profile_name())
    try:
        pre_llm_call(session_id='session', turn_id='turn', task_id='task', platform='photon', user_message='hello')
        service = runtime.current()
        assert service.get_snapshot('session', 'turn').principal_id is not None
        cfg = load_config(home)
        cfg['monitor']['enabled'] = True
        write_yaml(home / 'kiokuko/config.yaml', cfg)
        variable = sc._VAR_MAP['HERMES_SESSION_' + field]
        token = variable.set(sc._UNSET if value is None else value)
        try:
            calls = []
            result = tool_execution_middleware(tool_name='kiokuko_propose', args={'claim': 'candidate'},
                next_call=lambda args: calls.append(args), session_id='session', turn_id='turn', task_id='task')
            assert json.loads(result) == {'ok': False, 'error': code}
            assert calls == []
            with pytest.raises(KiokukoError):
                current_binding('session', 'turn', 'task')
        finally:
            variable.reset(token)
    finally:
        sc.clear_session_vars(tokens)
        provider.shutdown()


def test_callback_fallback_never_uses_process_env_or_model_arguments(host, monkeypatch):
    home, _ = host
    from gateway import session_context as sc
    from hermes_cli.profiles import get_active_profile_name
    from hermes_kiokuko import runtime
    from hermes_kiokuko.identity import resolve_identity
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.tool_context import tool_execution_middleware
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko.plugin_tools import propose_handler
    provider = KiokukoMemoryProvider()
    provider.initialize('session', hermes_home=str(home))
    monkeypatch.setenv('HERMES_SESSION_ID', 'unrelated-process-session')
    tokens = sc.set_session_vars(platform='photon', session_id='', user_id='sender', chat_id='chat',
                                chat_type='dm', session_key='photon-key', profile=get_active_profile_name())
    try:
        service = runtime.current()
        assert resolve_identity(service.store, 'session', 'photon').principal_id is None
        pre_llm_call(session_id='session', turn_id='turn', platform='photon', user_message='hello')
        # Schema/handler arguments cannot supply host metadata or bypass middleware.
        assert json.loads(propose_handler({'claim': 'candidate'}, session_id='session')) == {
            'ok': False, 'error': 'TOOL_CONTEXT_UNAVAILABLE'}
        result = tool_execution_middleware(tool_name='kiokuko_propose',
            args={'claim': 'candidate', 'session_id': 'victim', 'host_session': True},
            next_call=lambda args: propose_handler(args, session_id='session'),
            session_id='session', turn_id='turn')
        assert json.loads(result) == {'ok': False, 'error': 'INVALID_ARGUMENTS'}
        assert sc._VAR_MAP['HERMES_SESSION_ID'].get() == ''
        import os
        assert os.environ['HERMES_SESSION_ID'] == 'unrelated-process-session'
    finally:
        sc.clear_session_vars(tokens)
        provider.shutdown()


def test_concurrent_empty_gateway_ids_cannot_cross_sessions(host):
    home, _ = host
    from gateway import run, session_context as sc
    from gateway.config import Platform
    from gateway.session import SessionContext, SessionSource
    from hermes_cli.profiles import get_active_profile_name
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.tool_context import tool_execution_middleware
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko.plugin_tools import propose_handler
    provider = KiokukoMemoryProvider()
    provider.initialize('alice', hermes_home=str(home))
    ready = threading.Barrier(2)
    profile = get_active_profile_name()
    def turn(sender, other):
        source = SessionSource(platform=Platform('photon'), chat_id=sender + '-chat', chat_type='dm',
                               user_id=sender, profile=profile)
        context = SessionContext(source=source, session_key=sender + '-key', session_id=sender,
                                 connected_platforms=[], home_channels={})
        tokens = run.GatewayRunner._set_session_env(SimpleNamespace(adapters={}), context)
        try:
            assert sc._VAR_MAP['HERMES_SESSION_ID'].get() == ''
            pre_llm_call(session_id=sender, turn_id='turn', platform='photon', user_message='hello')
            ready.wait(timeout=5)
            def propose(session):
                return json.loads(tool_execution_middleware(tool_name='kiokuko_propose', args={'claim': 'candidate'},
                    next_call=lambda args: propose_handler(args, session_id=session), session_id=session, turn_id='turn'))
            assert propose(sender)['ok'] is True
            assert propose(other) == {'ok': False, 'error': 'TOOL_CONTEXT_MISMATCH'}
        finally:
            sc.clear_session_vars(tokens)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contextvars.copy_context().run, turn, sender, other)
                       for sender, other in [('alice', 'bob'), ('bob', 'alice')]]
            for future in futures:
                future.result(timeout=10)
    finally:
        provider.shutdown()
