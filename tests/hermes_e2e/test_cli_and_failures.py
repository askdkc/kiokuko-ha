import argparse
import threading
import pytest


def test_real_cli_parser_and_approval(host):
    home, _ = host
    from hermes_kiokuko.cli import setup_parser, execute
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    args = parser.parse_args(["remember", "--scope", "principal", "--text", "CLI raw value", "--operation-id", "cli-op"])
    args.func = execute  # Hermes installs the callback on Namespace; never serialize it.
    saved = execute(args, home)
    assert execute(args, home) == saved
    status = execute(parser.parse_args(["status"]), home)
    assert status["recent_operations"][0]["entry_id"] == saved["entry_id"]


def test_cli_confirmation_binds_claim_revision_and_purge(host):
    import json
    from hermes_kiokuko.cli import setup_parser, execute, cli_snapshot
    from hermes_kiokuko.errors import KiokukoError
    from hermes_kiokuko.service import Service
    from hermes_kiokuko.store import Store
    home, _ = host
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    saved = execute(parser.parse_args(["remember", "--scope", "principal", "--text", "old value"]), home)
    store = Store(home)
    try:
        service = Service(store)
        candidate = service.propose(cli_snapshot(service, "new value"), {
            "action": "correct", "entry_id": saved["entry_id"], "expected_revision": 1,
            "claim": "new value", "evidence_quote": "new value"})
        args = parser.parse_args(["approve", candidate["id"]])
        displayed = []
        with pytest.raises(KiokukoError, match="CANCELLED"):
            execute(args, home, input_fn=lambda _: "no", output=displayed.append)
        review = json.loads(displayed[-1])
        assert review["candidate"]["proposed_claim"] == "new value"
        assert review["candidate"]["expected_revision"] == 1
        assert review["target"]["claim"] == "old value"
        assert review["evidence"][0]["quote_verified"] == 0
        assert service.candidate_review(candidate["id"])[0]["candidate"]["state"] == "pending"
        approved = execute(args, home, input_fn=lambda _: candidate["id"], output=displayed.append)
        assert approved == {"entry_id": saved["entry_id"], "revision": 2}
        result = execute(parser.parse_args(["purge", saved["entry_id"]]), home,
                         input_fn=lambda _: saved["entry_id"], output=displayed.append)
        assert result["purged"]
        assert "physical disk erasure" in json.loads(displayed[-1])["scope"]
        with service.transaction() as db:
            assert db.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM memory_candidates").fetchone()[0] == 0
    finally:
        store.close()


def test_cli_workspace_link_changes_only_future_snapshots(host, tmp_path):
    from hermes_kiokuko.cli import setup_parser, execute, cli_snapshot
    from hermes_kiokuko.models import Identity
    from hermes_kiokuko.service import Service
    from hermes_kiokuko.store import Store
    from hermes_kiokuko.workspace import resolve_workspace
    home, _ = host
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    execute(parser.parse_args(["remember", "--scope", "principal", "--text", "workspace setup"]), home)
    store = Store(home)
    try:
        service = Service(store)
        target = cli_snapshot(service, "target")
        clone = tmp_path / "clone"
        clone.mkdir()
        identity = Identity("cli", "cli", "profile-owner", "clone", resolve_workspace(clone), "dm")
        before = service.snapshot("clone", "before", "before", identity)
        execute(parser.parse_args(["workspace-link", str(clone), "--workspace-id", target.workspace_id]),
                home, input_fn=lambda _: target.workspace_id, output=lambda _: None)
        after = service.snapshot("clone", "after", "after", identity)
        assert after.workspace_id == target.workspace_id != before.workspace_id
        assert service.get_snapshot("clone", "before") == before
    finally:
        store.close()


def test_hook_timeout_skip_and_late_completion_stays_prepared(host, monkeypatch):
    home, manager = host
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko import runtime
    import hermes_cli.plugins as plugins
    p = KiokukoMemoryProvider()
    p.initialize("s", hermes_home=str(home))
    original = manager._hooks["pre_llm_call"][0]
    ready, release, finished = threading.Event(), threading.Event(), threading.Event()
    def delayed(**kwargs):
        result = original(**kwargs)
        ready.set()
        try:
            assert release.wait(5)
            return result
        finally:
            finished.set()
    manager._hooks["pre_llm_call"][0] = delayed
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: .05)
    kwargs = dict(session_id="s", turn_id="t", user_message="hello", platform="cli")
    try:
        assert manager.invoke_hook("pre_llm_call", **kwargs) == []
        assert ready.wait(3)
        assert manager.invoke_hook("pre_llm_call", **{**kwargs, "turn_id": "second"}) == []
    finally:
        release.set()
        assert finished.wait(3)
    with runtime.current().transaction() as db:
        assert [row[0] for row in db.execute("SELECT state FROM retrieval_deliveries")] == ["prepared"]
        assert db.execute("SELECT count(*) FROM turn_snapshots").fetchone()[0] == 1


def test_version_and_native_gate_cover_tools_and_provider(host, monkeypatch):
    home, _ = host
    import hermes_cli
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.compatibility import check_host, surface_is_compatible_and_selected
    from hermes_kiokuko.errors import KiokukoError
    from hermes_kiokuko.config import read_yaml, write_yaml
    for version in ("0.20.9", "0.22.0", "unknown"):
        monkeypatch.setattr(hermes_cli, "__version__", version)
        assert not surface_is_compatible_and_selected()
        assert not KiokukoMemoryProvider().is_available()
    monkeypatch.setattr(hermes_cli, "__version__", "0.21.0")
    cfg = read_yaml(home / "config.yaml")
    cfg["memory"]["user_profile_enabled"] = True
    write_yaml(home / "config.yaml", cfg)
    with pytest.raises(KiokukoError, match="NATIVE_MEMORY_CONFIG"):
        check_host(home)
    assert not KiokukoMemoryProvider().is_available()
