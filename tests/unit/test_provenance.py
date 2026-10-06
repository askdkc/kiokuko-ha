import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_kiokuko import provenance

ROOT = Path(__file__).resolve().parents[2]


def test_entry_missing_package_logs_process_and_preserves_exception(tmp_path):
    code = "import runpy; runpy.run_path('__init__.py')['register'](None)"
    import os
    config = tmp_path / 'config.yaml'
    config.write_text('plugins: {enabled: [kiokuko-tools], disabled: []}\n')
    before = config.read_bytes()
    env = dict(os.environ, HERMES_HOME=str(tmp_path))
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code], cwd=ROOT,
                            capture_output=True, text=True, timeout=10, env=env)
    assert result.returncode != 0
    assert 'KIOKUKO_PACKAGE_NOT_IMPORTABLE' in result.stderr
    assert "No module named 'hermes_kiokuko'" in result.stderr
    assert 'search_paths' in result.stderr
    assert str(tmp_path) in result.stderr and config.read_bytes() == before


def test_entry_nested_dependency_is_not_package_absence(monkeypatch, caplog):
    import builtins
    original = builtins.__import__
    error = ModuleNotFoundError('private failure', name='yaml')
    def fail(name, *args, **kwargs):
        if name == 'hermes_kiokuko.plugin_entry':
            raise error
        return original(name, *args, **kwargs)
    entry = runpy.run_path(str(ROOT / '__init__.py'))
    monkeypatch.setattr(builtins, '__import__', fail)
    paths = sys.path[:]
    with pytest.raises(ModuleNotFoundError) as caught:
        entry['register'](None)
    assert caught.value is error and sys.path == paths
    assert 'KIOKUKO_DEPENDENCY_NOT_IMPORTABLE' in caplog.text
    assert 'private failure' not in caplog.text


def test_unknown_pm_is_not_missing_installation(monkeypatch):
    monkeypatch.setattr(provenance, 'pm_environment', lambda: {
        'checked': False, 'selected_environment': None, 'active': None})
    info = provenance.runtime_provenance()
    assert info['restart_required'] is None
    assert info['loaded']['origin'] == 'unknown'
    assert info['observed_in'] == 'current_process'


@pytest.mark.parametrize('active,in_generation', [(False, True), (True, False)])
def test_same_python_stale_selection_or_foreign_package_needs_restart(monkeypatch, active, in_generation):
    monkeypatch.setattr(provenance, 'pm_environment', lambda: {
        'checked': True, 'selected_environment': '/generation/.venv',
        'active': active, 'package_in_generation': in_generation})
    info = provenance.runtime_provenance()
    assert info['restart_required'] is True
    assert info['loaded']['origin'] == ('pm_generation' if in_generation else 'outside_pm_generation')


def test_same_version_changed_code_needs_restart(monkeypatch):
    monkeypatch.setattr(provenance, '_fingerprint', lambda: 'new-code')
    assert provenance.runtime_provenance()['restart_required'] is True


def test_snapshot_path_is_valid_without_matching_directory_plugin_path(monkeypatch, tmp_path):
    monkeypatch.setattr(provenance, 'pm_environment', lambda: {
        'checked': True, 'selected_environment': '/generation/.venv',
        'active': True, 'package_in_generation': True})
    info = provenance.runtime_provenance(SimpleNamespace(path=tmp_path, source='user', version='0.1.16'))
    assert info['restart_required'] is False
    assert info['loaded']['origin'] == 'pm_generation'
    assert info['configured_plugin']['path'] != info['loaded']['package_path']
