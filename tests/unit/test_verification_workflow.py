"""Verification must consume the selected current host and this build's output."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('verification_support', ROOT / 'scripts/verification_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


@pytest.fixture
def current_host(tmp_path):
    root = tmp_path / 'current-host'
    files = {
        'pm/__init__.py': '',
        'pm/environment.py': 'class PythonEnvironment:\n def __init__(self, *, uv, python, destination, cache, env, output, no_config): pass\n',
        'pm/workspace.py': 'def lock_and_sync(plugins, excludes, *, root, source, seed_lock, environment): pass\n',
        'hermes_cli/__init__.py': '__version__ = "99.123.4"\n',
        'hermes_cli/main.py': '',
        'hermes_cli/_launchers.py': 'def runtime_command(root, *, code): pass\n',
        'hermes_cli/plugins.py': 'def get_plugin_manager(): pass\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


@pytest.mark.parametrize('script', ['verify_native_plugin.py', 'verify_pm_lifecycle.py'])
def test_current_host_accepts_new_identity_and_rejects_missing_api(current_host, script):
    command = [sys.executable, str(ROOT / 'scripts' / script), '--host', str(current_host), '--check-host-only']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['ok'] and report['historical_fixture'] is None
    # The same future identity with a missing required keyword must fail.
    (current_host / 'pm/workspace.py').write_text('def lock_and_sync(plugins, excludes, *, root, source, seed_lock): pass\n')
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and 'pm.workspace.lock_and_sync' in result.stderr


def test_historical_identity_is_opt_in_and_still_enforced(current_host, tmp_path, monkeypatch):
    root = tmp_path / 'fixture-catalog'
    pin = root / 'tests/hermes_e2e/pin.json'
    pin.parent.mkdir(parents=True)
    path = current_host / 'hermes_cli/__init__.py'
    pin.write_text(json.dumps({'fixtures': {'historical': {'files': {
        'hermes_cli/__init__.py': hashlib.sha256(path.read_bytes()).hexdigest()}}}}))
    monkeypatch.setattr(support, 'ROOT', root)
    assert support.verify_host(current_host, 'historical')['ok']
    path.write_text('__version__ = "100.0.0"\n')
    assert support.verify_host(current_host)['ok']
    with pytest.raises(ValueError, match='Historical fixture source mismatch'):
        support.verify_host(current_host, 'historical')


@pytest.fixture
def build_environment(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    version = __import__('tomllib').loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    (root / 'pyproject.toml').write_text(f'[project]\nversion = "{version}"\n')
    monkeypatch.setattr(support, 'ROOT', root)
    output = root / 'dist'
    output.mkdir()
    (output / 'old.whl').write_bytes(b'stale wheel must never be chosen')
    (output / 'old.tar.gz').write_bytes(b'stale sdist must never be chosen')
    return output, version


def produce_build(command, **kwargs):
    directory = Path(command[command.index('--outdir') + 1])
    version = __import__('tomllib').loads((support.ROOT / 'pyproject.toml').read_text())['project']['version']
    metadata = f'Name: hermes-kiokuko\nVersion: {version}\n'.encode()
    with zipfile.ZipFile(directory / 'current.whl', 'w') as archive:
        archive.writestr(f'hermes_kiokuko-{version}.dist-info/METADATA', metadata)
    with tarfile.open(directory / 'current.tar.gz', 'w:gz') as archive:
        member = tarfile.TarInfo(f'hermes_kiokuko-{version}/PKG-INFO')
        member.size = len(metadata)
        archive.addfile(member, io.BytesIO(metadata))


def test_only_current_build_artifacts_are_selected_and_verified(build_environment, monkeypatch):
    output, version = build_environment
    monkeypatch.setattr(support.subprocess, 'run', produce_build)
    manifest = support.build_artifacts(output)
    assert json.loads(manifest.read_text())['version'] == version
    wheel = support.artifact_path(manifest, 'wheel')
    assert wheel.name == 'current.whl' and wheel.parent != output
    assert support.artifact_path(manifest, 'sdist').name == 'current.tar.gz'
    wheel.write_bytes(b'changed after build')
    with pytest.raises(ValueError, match='digest changed'):
        support.artifact_path(manifest, 'wheel')


def test_failed_or_incomplete_build_never_falls_back_to_old_artifact(build_environment, monkeypatch):
    output, _ = build_environment
    monkeypatch.setattr(support.subprocess, 'run', lambda *a, **k: None)
    with pytest.raises(ValueError, match='Expected one current-build wheel'):
        support.build_artifacts(output)
