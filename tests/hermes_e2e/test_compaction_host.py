"""Audited host compression boundary and auxiliary extraction adapter."""
import json
import threading
from types import SimpleNamespace

import pytest


def test_real_compression_boundary_and_session_end_use_snapshot_root(host, tmp_path, monkeypatch):
    home, manager = host
    from agent.memory_manager import MemoryManager
    from agent.conversation_compression import _pre_compress_memory_context
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.turn_hook import pre_llm_call
    from hermes_kiokuko import runtime
    from hermes_kiokuko.compaction import extract_predicates
    import agent.auxiliary_client as auxiliary
    import agent.runtime_cwd as cwd
    root = tmp_path / "project"
    root.mkdir()
    (root / "settings.toml").write_text('[app]\nname="kiokuko"\nport=8000\n')
    monkeypatch.setattr(cwd, "resolve_agent_cwd", lambda: str(root))
    observed = []
    values = [{"path": "settings.toml", "format": "toml", "selector": ["app", "name"], "value": "kiokuko"}]
    def call(**kwargs):
        observed.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(values)))])
    # Keep the host API signature intact for the compatibility gate.
    import functools
    call = functools.wraps(auxiliary.call_llm)(call)
    monkeypatch.setattr(auxiliary, "call_llm", call)
    provider = KiokukoMemoryProvider()
    provider.initialize("old", hermes_home=str(home))
    mm = MemoryManager()
    mm._providers.append(provider)
    raw = "settings.toml を確認した"
    context = pre_llm_call(session_id="old", turn_id="one", user_message=raw, platform="cli")["context"]
    messages = [{"role": "user", "content": raw, "api_content": raw + "\n\n" + context},
                {"role": "assistant", "content": "app.name is kiokuko"}]
    mm.sync_all(raw, "app.name is kiokuko", session_id="old", messages=messages)
    assert mm.flush_pending(timeout=5)
    agent = SimpleNamespace(_memory_manager=mm)
    returned = _pre_compress_memory_context(agent, messages, False)
    assert "file-verified" in returned
    # Moving the live agent does not redirect an old session's capture.
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(cwd, "resolve_agent_cwd", lambda: str(other))
    provider.on_session_switch("new")
    values[:] = [{"path": "settings.toml", "format": "toml", "selector": ["app", "port"], "value": 8000}]
    mm.on_session_end(messages)
    with runtime.current().transaction() as db:
        rows = db.execute("SELECT * FROM memory_entries").fetchall()
        assert len(rows) == 2
        assert len({r["workspace_id"] for r in rows}) == 1
        assert {r[0] for r in db.execute("SELECT session_id FROM verified_facts")} == {"old"}
    assert len(observed) == 2 and all(r["task"] == "compression" for r in observed)
    mm.shutdown_all()


def test_extraction_timeout_late_worker_has_no_storage_rights(host, monkeypatch):
    import agent.auxiliary_client as auxiliary
    import hermes_kiokuko.compaction as compaction
    from hermes_kiokuko.errors import KiokukoError
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def call(**kwargs):
        started.set()
        release.wait(3)
        finished.set()
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="[]"))])
    monkeypatch.setattr(auxiliary, "call_llm", call)
    monkeypatch.setattr(compaction, "EXTRACTION_TIMEOUT", .03)
    try:
        with pytest.raises(KiokukoError, match="COMPACTION_TIMEOUT"):
            compaction.extract_predicates("settings.toml")
        assert started.is_set()
        with pytest.raises(KiokukoError, match="COMPACTION_BUSY"):
            compaction.extract_predicates("settings.toml")
    finally:
        release.set()
        assert finished.wait(3)


def test_installed_curation_command_with_real_line_input(host, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from hermes_kiokuko.facts import store_verified
    from hermes_kiokuko.models import Identity
    from hermes_kiokuko.service import Service
    from hermes_kiokuko.store import Store
    from hermes_kiokuko.workspace import resolve_workspace
    home, _ = host
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.json").write_text('{"language":"ja"}')
    store = Store(home, initialize=True)
    try:
        service = Service(store)
        identity = Identity("cli", "cli", "profile-owner", "conversation", resolve_workspace(root), "dm")
        snap = service.snapshot("session", "turn", "config.json", identity, workspace_root=root)
        store_verified(service, snap, [{"path": "config.json", "format": "json", "selector": ["language"], "value": "ja"}], "test-receipt")
        command = Path(sys.executable).with_name("kioku-curation")
        result = subprocess.run([str(command)], input="999\n1\ns\n\ns\nshare\n", text=True,
                                capture_output=True, cwd=root, env=dict(os.environ, HERMES_HOME=str(home)), timeout=20)
        assert result.returncode == 0, result.stderr
        assert "[ ] 1." in result.stdout and "[x] 1." in result.stdout
        assert "全利用者" in result.stdout and "1 件をGlobal記憶へ採用" in result.stdout
        with service.transaction() as db:
            assert db.execute("SELECT count(*) FROM memory_entries WHERE scope_type='profile'").fetchone()[0] == 1
    finally:
        store.close()
