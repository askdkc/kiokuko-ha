"""All host imports happen after binding an isolated Hermes profile."""
import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "hermes-profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    # Never inherit a gateway sender, provider credential or runtime mode from the shell.
    import os
    for name in list(os.environ):
        if name.startswith("HERMES_SESSION_") or name.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            monkeypatch.delenv(name)
    from hermes_kiokuko.config import setup
    setup(home)
    from hermes_kiokuko.config import read_yaml, write_yaml
    cfg = read_yaml(home / "config.yaml")
    cfg["auxiliary"] = {"title_generation": {"enabled": False}}
    cfg["model"] = {"context_length": 128000}
    write_yaml(home / "config.yaml", cfg)
    hermes = pytest.importorskip("hermes_cli")
    root = Path(hermes.__file__).resolve().parent.parent
    manifest = json.loads((Path(__file__).with_name("pin.json")).read_text())
    for path, checksum in manifest["files"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == checksum, f"Unaudited host source: {path}"
    from gateway import session_context
    session_context.reset_session_vars()
    from hermes_cli.plugins import _reset_plugin_managers_for_tests, get_plugin_manager
    _reset_plugin_managers_for_tests()
    manager = get_plugin_manager()
    manager.discover_and_load()
    assert manager._plugins["kiokuko-tools"].enabled
    try:
        yield home, manager
    finally:
        from hermes_kiokuko import runtime
        for token in list(runtime._owners):
            runtime.release(token)
        _reset_plugin_managers_for_tests()
        session_context.reset_session_vars()
