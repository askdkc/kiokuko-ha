"""Real pinned command registration/CLI dispatch; no stdin or model invocation."""
import asyncio
import re
import threading

import pytest


@pytest.fixture
def slash(host, tmp_path, monkeypatch):
    import cli as host_cli
    import agent.runtime_cwd as cwd
    from hermes_kiokuko.facts import store_verified
    from hermes_kiokuko.models import Identity
    from hermes_kiokuko.service import Service
    from hermes_kiokuko.store import Store
    from hermes_kiokuko.workspace import resolve_workspace
    from hermes_cli.plugins import get_plugin_command_handler
    home, manager = host
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.json").write_text('{"language":"ja"}')
    monkeypatch.setattr(cwd, "resolve_agent_cwd", lambda: root)
    store = Store(home, initialize=True)
    service = Service(store)
    identity = Identity("cli", "cli", "profile-owner", "source", resolve_workspace(root), "dm")
    snap = service.snapshot("source-session", "one", "config.json", identity, workspace_root=root)
    store_verified(service, snap, [{"path": "config.json", "format": "json", "selector": ["language"], "value": "ja"}], "slash-host")
    cli = object.__new__(host_cli.HermesCLI)
    cli.session_id = "interactive-session"
    cli._agent_running = False
    cli.config = {}
    manager._cli_ref = cli
    output = []
    monkeypatch.setattr(host_cli, "_cprint", lambda value: output.append(str(value)))
    # Unrelated skill inventory isn't needed for exercising command precedence.
    monkeypatch.setattr(host_cli, "_ensure_skill_commands", lambda: {})
    monkeypatch.setattr(host_cli, "get_skill_bundles", lambda: {})
    handler = get_plugin_command_handler("kioku-curation")
    assert handler is not None
    def run(args):
        output.clear()
        assert cli.process_command("/kioku-curation " + args)
        return "\n".join(output)
    try:
        yield service, cli, handler, run
    finally:
        manager._cli_ref = None
        store.close()


def test_real_cli_slash_review_select_confirm(slash):
    service, _, _, run = slash
    assert "[ ] 1." in run("")
    assert "[x] 1." in run("select 1")
    review = run("share")
    assert "全利用者" in review and "config.json" in review
    confirmation = re.search(r"/kioku-curation (confirm [a-f0-9]+)", review)[1]
    assert "1 件をGlobal記憶へ採用" in run(confirmation)
    assert "候補が未表示" in run(confirmation)
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_entries WHERE scope_type='profile'").fetchone()[0] == 1


def test_cli_session_switch_and_rewind_invalidate_review(slash):
    service, cli, _, run = slash
    run("")
    run("all")
    confirmation = re.search(r"/kioku-curation (confirm [a-f0-9]+)", run("share"))[1]
    service.transition(cli.session_id, rewound=True)
    assert "候補が未表示" in run(confirmation)
    run("")
    cli.session_id = "new-session"
    assert "候補が未表示" in run("show")


@pytest.mark.parametrize("chat_type", ["private", "group"])
def test_gateway_identity_never_gains_admin_or_reads_candidates(slash, chat_type):
    from gateway import session_context as sc
    _, _, handler, _ = slash
    tokens = sc.set_session_vars(platform="telegram", chat_id="chat", chat_type=chat_type,
                                 user_id="A", session_id="gateway-session")
    try:
        result = handler("")
        assert "候補を表示・共有しません" in result
        assert "config.json" not in result
    finally:
        sc.clear_session_vars(tokens)


def test_unbound_gateway_event_loop_denied_even_with_cli_attached(slash):
    from types import SimpleNamespace
    from gateway.run_inbound import GatewayInboundMixin
    runner = SimpleNamespace(_draining=False, _hm_quick_commands=lambda: {})
    event = SimpleNamespace(get_command_args=lambda: "")
    handled, result, _ = asyncio.run(GatewayInboundMixin._hm_dispatch_quick_and_plugin_commands(
        runner, event, None, "kioku_curation"))
    assert handled and "候補を表示・共有しません" in result
    assert "config.json" not in result


def test_missing_cli_and_running_agent_deny(slash):
    _, cli, handler, _ = slash
    cli._agent_running = True
    assert "候補を表示・共有しません" in handler("")
    handler.ctx._manager._cli_ref = None
    assert "候補を表示・共有しません" in handler("")


def test_update_registration_profile_capture_and_duplicate_start(slash, tmp_path, monkeypatch):
    import os
    import hermes_kiokuko.slash_update as module
    from hermes_kiokuko.filesystem import acquire_lock
    from hermes_cli.plugins import get_plugin_command_handler
    service, _, _, _ = slash
    monkeypatch.setattr(module, "_job", None)
    monkeypatch.setattr(module.sys, "prefix", str(tmp_path))
    started, release = threading.Event(), threading.Event()
    calls = []
    def update(job, env, fd):
        import os
        try:
            calls.append((job, env))
            started.set()
            release.wait(5)
            with module._lock:
                job.state, job.version = "complete", "9.9"
        finally:
            os.close(fd)
    monkeypatch.setattr(module, "perform_update", update)
    handler = get_plugin_command_handler("kiokuko-update")
    assert handler is not None
    assert "まだ更新" in handler("status")
    fd = acquire_lock(tmp_path / ".kiokuko-update.lock", exclusive=True)
    try:
        assert "LIVE_HOLDER" in handler("")
        assert not calls
    finally:
        os.close(fd)
    monkeypatch.setattr(module, "_job", module.UpdateJob(service.store.home, module.sys.executable,
                                                       "0.1.1", state="failed", error="PIP_INSTALL_FAILED"))
    assert "失敗" in handler("")
    try:
        assert "更新中" in handler("retry")
        assert started.wait(2)
        assert "更新中" in handler("")
        assert len(calls) == 1
        job, env = calls[0]
        assert job.home == service.store.home
        assert env["HERMES_HOME"] == str(service.store.home)
        assert job.python == module.sys.executable
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "kiokuko-update":
                thread.join(3)
    assert "再起動" in handler("status")
    assert "9.9" in handler("")
    assert len(calls) == 1


def test_update_gateway_denied_without_spawning(slash, monkeypatch):
    import hermes_kiokuko.slash_update as module
    from hermes_cli.plugins import get_plugin_command_handler
    monkeypatch.setattr(module, "_job", None)
    handler = get_plugin_command_handler("kiokuko-update")
    async def invoke():
        return handler("")
    assert "管理者を確認できません" in asyncio.run(invoke())
    assert module._job is None
