import contextvars
import json
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('platform', ['cli', 'telegram'])
def test_normal_setup_publishes_canonical_tools(host, platform):
    """No test-side memory opt-in: use the same resolver as CLI/Gateway."""
    home, _ = host
    from hermes_kiokuko.config import read_yaml
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions
    from tools.tool_search import dispatch_tool_search
    cfg = read_yaml(home / 'config.yaml')
    enabled = sorted(_get_platform_tools(cfg, platform))
    definitions = get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True,
                                      skip_tool_search_assembly=True)
    expected = {'kiokuko_recall', 'kiokuko_propose', 'kiokuko_manage'}
    assert expected <= {tool['function']['name'] for tool in definitions}, enabled
    result = json.loads(dispatch_tool_search({'queries': ['kiokuko'], 'limit': 10},
                                            current_tool_defs=definitions))
    assert expected <= set(result['tools']), result


def test_doctor_does_not_call_disabled_memory_available(host):
    home, _ = host
    from hermes_kiokuko.config import read_yaml, write_yaml
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.diagnostics import diagnose
    provider = KiokukoMemoryProvider()
    provider.initialize('session', hermes_home=str(home))
    try:
        cfg = read_yaml(home / 'config.yaml')
        cfg['agent'] = {'disabled_toolsets': ['memory']}
        write_yaml(home / 'config.yaml', cfg)
        info = diagnose(home, load_plugin=True)
        assert not info['ok']
        assert info['tool_publication']['configuration']['error'] == 'MEMORY_TOOLSET_DISABLED'
        assert info['tool_publication']['session']['status'] == 'unconfirmed'
    finally:
        provider.shutdown()


@pytest.mark.parametrize('cause,code', [
    ('empty_selection', 'MEMORY_TOOLSET_DISABLED'),
    ('excluded_tool', 'TOOL_INDIVIDUALLY_EXCLUDED'),
    ('plugin_disabled', 'GENERAL_PLUGIN_DISABLED'),
    ('profile_mismatch', 'PROFILE_IDENTITY_MISMATCH'),
    ('incompatible', 'HOST_CONTRACT_MISMATCH'),
])
def test_doctor_visibility_denials_are_specific_and_readonly(host, monkeypatch, cause, code):
    home, manager = host
    from hermes_kiokuko.config import read_yaml, write_yaml, load_config
    from hermes_kiokuko.diagnostics import diagnose
    cfg = read_yaml(home / 'config.yaml')
    if cause == 'empty_selection':
        cfg['platform_toolsets'] = {'cli': []}
    elif cause == 'plugin_disabled':
        cfg['plugins']['disabled'] = ['kiokuko-tools']
    elif cause == 'excluded_tool':
        private = load_config(home)
        private['tool_access']['excluded_tools'] = ['kiokuko_propose']
        write_yaml(home / 'kiokuko/config.yaml', private)
    elif cause == 'profile_mismatch':
        from gateway import session_context as sc
        sc._VAR_MAP['HERMES_SESSION_PROFILE'].set('foreign-profile')
    elif cause == 'incompatible':
        monkeypatch.setattr('hermes_kiokuko.compatibility.host_contract_report',
                            lambda: {'ok': False, 'error': code})
    write_yaml(home / 'config.yaml', cfg)
    if cause == 'plugin_disabled':
        manager.discover_and_load(force=True)
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    info = diagnose(home)
    assert code in info['tool_publication']['errors'], info['tool_publication']
    assert info['ok'] is False
    assert info['memory_operations_available_in_session'] is None
    assert info['session_verification'] == 'unconfirmed'
    assert not (home / 'kiokuko/kiokuko.db').exists()
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()} == before


def test_setup_opt_in_preserves_suppression_and_exclusions(host):
    home, _ = host
    from hermes_kiokuko.config import setup, read_yaml, write_yaml, load_config
    from hermes_kiokuko.tool_publication import tool_publication
    cfg = read_yaml(home / 'config.yaml')
    cfg['platform_toolsets'] = {'cli': [], 'telegram': []}
    cfg['agent'] = {'disabled_toolsets': ['memory']}
    write_yaml(home / 'config.yaml', cfg)
    private = load_config(home)
    private['tool_access']['excluded_tools'] = ['kiokuko_manage']
    write_yaml(home / 'kiokuko/config.yaml', private)
    setup(home)
    assert read_yaml(home / 'config.yaml')['platform_toolsets'] == {'cli': [], 'telegram': []}
    setup(home, enable_memory_for=['telegram'])
    updated = read_yaml(home / 'config.yaml')
    assert updated['platform_toolsets'] == {'cli': [], 'telegram': ['memory']}
    assert updated['agent'] == {'disabled_toolsets': ['memory']}
    assert load_config(home)['tool_access']['excluded_tools'] == ['kiokuko_manage']
    assert tool_publication(home, platform='telegram')['configuration']['available'] is False
    # A deliberate plugin disable is also preserved by repeated setup.
    updated['plugins']['disabled'] = ['kiokuko-tools']
    write_yaml(home / 'config.yaml', updated)
    setup(home)
    assert read_yaml(home / 'config.yaml')['plugins']['disabled'] == ['kiokuko-tools']


def test_doctor_before_plugin_load_is_unconfirmed_without_loading(host):
    home, _ = host
    from hermes_cli.plugins import _reset_plugin_managers_for_tests
    from hermes_kiokuko.diagnostics import diagnose
    _reset_plugin_managers_for_tests()
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    info = diagnose(home)
    assert info['tool_publication']['plugin']['loaded'] is False
    assert 'PLUGIN_NOT_LOADED_IN_THIS_PROCESS' in info['tool_publication']['errors']
    assert info['session_verification'] == 'unconfirmed'
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()} == before


def test_doctor_reports_check_fn_denial_separately_from_registration(host, monkeypatch):
    home, _ = host
    from tools.registry import registry
    from hermes_kiokuko.diagnostics import diagnose
    monkeypatch.setattr(registry.get_entry('kiokuko_propose'), 'check_fn', lambda: False)
    info = diagnose(home)
    assert info['tool_publication']['registration']['kiokuko_propose'] is True
    assert info['tool_publication']['check_fn']['kiokuko_propose']['allowed'] is False
    assert 'CHECK_FN_DENIED' in info['tool_publication']['errors']


@pytest.mark.parametrize('restriction', ['individual', 'toolset'])
def test_excluded_tool_rejects_stale_dispatch_without_saving(host, restriction):
    home, _ = host
    from hermes_kiokuko.config import load_config, read_yaml, write_yaml
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.turn_hook import pre_llm_call
    from model_tools import handle_function_call
    from hermes_kiokuko import runtime
    provider = KiokukoMemoryProvider()
    provider.initialize('session', hermes_home=str(home))
    try:
        pre_llm_call(session_id='session', turn_id='turn', task_id='task', platform='cli', user_message='hello')
        if restriction == 'individual':
            private = load_config(home)
            private['tool_access']['excluded_tools'] = ['kiokuko_propose']
            write_yaml(home / 'kiokuko/config.yaml', private)
            code = 'TOOL_INDIVIDUALLY_EXCLUDED'
        else:
            cfg = read_yaml(home / 'config.yaml')
            cfg['agent'] = {'disabled_toolsets': ['memory']}
            write_yaml(home / 'config.yaml', cfg)
            code = 'MEMORY_TOOLSET_DISABLED'
        result = json.loads(handle_function_call('kiokuko_propose', {'action': 'propose', 'claim': 'must not save'},
            session_id='session', turn_id='turn', task_id='task'))
        assert result == {'ok': False, 'error': code}
        with runtime.current().transaction() as db:
            assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM memory_entries').fetchone()[0] == 0
    finally:
        provider.shutdown()


def test_memory_provider_resolves_to_package_directory(host):
    home, _ = host
    from pathlib import Path
    from plugins.memory import find_provider_dir, find_provider_entry_point, load_memory_provider
    import hermes_kiokuko.memory_plugin

    entry = find_provider_entry_point("kiokuko")
    assert entry is not None
    directory = find_provider_dir("kiokuko")
    assert directory == Path(hermes_kiokuko.memory_plugin.__file__).parent
    assert directory.joinpath("__init__.py").is_file()
    provider = load_memory_provider("kiokuko")
    assert provider is not None and provider.name == "kiokuko" and provider.is_available()
    assert not (home / "kiokuko" / "kiokuko.db").exists()


@pytest.mark.parametrize("platform", ["cli", "photon"])
def test_entrypoints_hook_worker_middleware_and_manager_sync(host, platform):
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
    if platform != "cli":
        from gateway import session_context
        from hermes_cli.profiles import get_active_profile_name
        session_context.set_session_vars(platform=platform, chat_id="chat", chat_type="private",
                                        user_id="sender", session_id="session", profile=get_active_profile_name())
    seen = []
    worker_only = contextvars.ContextVar("test_worker_only", default=False)
    original = manager._hooks["pre_llm_call"][0]
    def record(**kwargs):
        seen.append(threading.get_ident())
        worker_only.set(True)
        return original(**kwargs)
    manager._hooks["pre_llm_call"][0] = record
    raw = "PostgreSQLへの移行は却下した"
    agent = SimpleNamespace(session_id="session", model="test", platform=platform)
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


def test_doctor_distinguishes_api_checks_from_fixture_contract(host):
    from hermes_kiokuko.compatibility import host_contract_report
    result = host_contract_report()
    assert result['ok']
    assert result['dispatch_contract']['status'] == 'tested_fixture_source_match'
    assert result['dispatch_contract']['runtime_exercised'] is False
