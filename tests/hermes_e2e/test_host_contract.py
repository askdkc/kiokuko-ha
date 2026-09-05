import contextvars
import json
import threading
from types import SimpleNamespace

import pytest


def test_entrypoints_hook_worker_middleware_and_manager_sync(host):
    home, manager = host
    from agent.turn_context import _collect_pre_llm_call_context, compose_user_api_content
    from agent.memory_manager import MemoryManager
    from hermes_cli.middleware import run_tool_execution_middleware
    from plugins.memory import load_memory_provider
    from tools.registry import registry
    from hermes_kiokuko import runtime
    from hermes_kiokuko.plugin_tools import propose_handler
    # The general plugin is discoverable before the provider creates its database.
    assert not (home / "kiokuko" / "kiokuko.db").exists()
    assert registry.get_schema("kiokuko_propose") is not None
    assert json.loads(propose_handler({"claim": "unbound"}))["error"] == "TOOL_CONTEXT_UNAVAILABLE"
    provider = load_memory_provider("kiokuko")
    assert provider is not None and provider.is_available()
    provider.initialize("session", hermes_home=str(home))
    seen = []
    worker_only = contextvars.ContextVar("test_worker_only", default=False)
    original = manager._hooks["pre_llm_call"][0]
    def record(**kwargs):
        seen.append(threading.get_ident())
        worker_only.set(True)
        return original(**kwargs)
    manager._hooks["pre_llm_call"][0] = record
    raw = "PostgreSQLへの移行は却下した"
    agent = SimpleNamespace(session_id="session", model="test", platform="cli")
    context = _collect_pre_llm_call_context(agent, effective_task_id="task", turn_id="turn", original_user_message=raw,
                  messages=[{"role": "user", "content": raw}], conversation_history=[])
    assert "<!--kiokuko:v1:" in context
    assert seen == [seen[0]] and seen[0] != threading.get_ident()
    assert worker_only.get() is False
    args = {"action": "propose", "claim": "PostgreSQLを採用している", "evidence_quote": "PostgreSQL"}
    result = run_tool_execution_middleware("kiokuko_propose", args,
              lambda values: registry.dispatch("kiokuko_propose", values, session_id="session", task_id="task", user_task=None),
              session_id="session", turn_id="turn", task_id="task")
    parsed = json.loads(result)
    assert parsed["ok"] and parsed["data"]["state"] == "pending"
    api = compose_user_api_content(raw, provider.prefetch(raw), context)
    messages = [{"role": "user", "content": raw, "api_content": api}, {"role": "assistant", "content": "done"}]
    mm = MemoryManager()
    mm._providers.append(provider)
    mm.sync_all(raw, "done", session_id="session", messages=messages)
    assert mm.flush_pending(timeout=5)
    service = runtime.current()
    review, _ = service.candidate_review(parsed["data"]["id"])
    assert review["candidate"]["state"] == "pending" and review["evidence"][0]["quote_verified"] == 1
    with service.transaction() as db:
        assert db.execute("SELECT state FROM retrieval_deliveries").fetchone()[0] == "observed_in_history"
        assert db.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0
    mm.shutdown_all()


def test_gateway_context_isolation_and_no_env_fallback(host, monkeypatch):
    home, manager = host
    from gateway import session_context as sc
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko import runtime
    p = KiokukoMemoryProvider()
    p.initialize("A", hermes_home=str(home))
    def turn(user, chat_type, session, turn_id, text):
        from hermes_cli.profiles import get_active_profile_name
        tokens = sc.set_session_vars(platform="telegram", chat_id=session, chat_type=chat_type, user_id=user, session_id=session, profile=get_active_profile_name())
        try:
            return pre_llm_call(session_id=session, turn_id=turn_id, user_message=text, platform="telegram")["context"]
        finally:
            sc.clear_session_vars(tokens)
    first = turn("A", "private", "dm-A", "one", "@kiokuko remember --scope principal\nA専用の記憶")
    assert "A専用の記憶" in first
    assert "A専用の記憶" not in turn("B", "private", "dm-B", "one", "A専用の記憶")
    assert "A専用の記憶" not in turn("A", "group", "group", "one", "A専用の記憶")
    sc.reset_session_vars()
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "A")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    assert "A専用の記憶" not in pre_llm_call(session_id="unknown", turn_id="one", user_message="A専用の記憶", platform="telegram")["context"]
    with runtime.current().transaction() as db:
        assert db.execute("SELECT principal_id FROM turn_snapshots WHERE session_id='unknown'").fetchone()[0] is None


def test_middleware_exception_cannot_bypass_handler(host, monkeypatch):
    home, manager = host
    from hermes_cli.middleware import run_tool_execution_middleware
    from hermes_kiokuko.plugin_tools import propose_handler
    def broken(**kwargs):
        raise RuntimeError("simulate host fail-open")
    manager._middleware["tool_execution"] = [broken]
    result = run_tool_execution_middleware("kiokuko_propose", {"claim": "must not save"},
                                          lambda args: propose_handler(args, session_id="s", task_id="t"), session_id="s", turn_id="t")
    assert json.loads(result)["error"] == "TOOL_CONTEXT_UNAVAILABLE"


def test_provider_shutdown_does_not_revoke_other_owner(host):
    home, _ = host
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import runtime
    a, b = KiokukoMemoryProvider(), KiokukoMemoryProvider()
    a.initialize("a", hermes_home=str(home))
    b.initialize("b", hermes_home=str(home))
    a.shutdown()
    assert runtime.current().store.holder is not None
    b.shutdown()
    from hermes_kiokuko.errors import KiokukoError
    with pytest.raises(KiokukoError, match="PROVIDER_NOT_READY"):
        runtime.current()
