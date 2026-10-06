from hermes_kiokuko.diagnostics import diagnose


def test_diagnosis_before_setup_does_not_create_profile(tmp_path):
    home = tmp_path / 'absent'
    info = diagnose(home)
    assert not info['host_ready'] and not info['store_ready']
    assert not home.exists() and info['gateway_loaded'] is None


def test_healthy_database_does_not_hide_failed_host(service, monkeypatch):
    from hermes_kiokuko.errors import KiokukoError
    def reject(*args):
        raise KiokukoError('HOST_CONTRACT_MISMATCH')
    monkeypatch.setattr('hermes_kiokuko.diagnostics.check_host', reject)
    monkeypatch.setattr('hermes_kiokuko.diagnostics.host_contract_report', lambda:{'ok':False,'failed_checks':[{'name':'import:hermes_cli','ok':False}]})
    result = diagnose(service.store.home)
    assert result['database_ok'] is True and result['store_ready'] is True
    assert result['host_ready'] is False and result['ok'] is False
    assert result['host_contract']['failed_checks'][0]['name'] == 'import:hermes_cli'


def test_failed_doctor_exits_nonzero(service, monkeypatch, capsys):
    from hermes_kiokuko.cli import main
    monkeypatch.setattr('hermes_kiokuko.cli.active_home', lambda:service.store.home)
    from hermes_kiokuko.errors import KiokukoError
    def reject(*args):
        raise KiokukoError('HOST_CONTRACT_MISMATCH')
    monkeypatch.setattr('hermes_kiokuko.diagnostics.check_host', reject)
    assert main(['doctor']) == 1
    assert '"ok": false' in capsys.readouterr().out


def test_missing_tools_entrypoint_fails_doctor_with_healthy_store(service, monkeypatch):
    import importlib.metadata
    original = importlib.metadata.entry_points
    monkeypatch.setattr('hermes_kiokuko.diagnostics.check_host', lambda *_: None)
    monkeypatch.setattr(importlib.metadata, 'entry_points',
                        lambda *, group: [] if group == 'hermes_agent.plugins' else original(group=group))
    info = diagnose(service.store.home)
    assert info['database_ok'] is True
    assert info['entrypoints_ready'] is False and info['ok'] is False
    assert 'DIAGNOSTIC_ENTRYPOINTS_UNAVAILABLE' in info['errors']


def test_default_doctor_does_not_load_plugins(service, monkeypatch):
    def unexpected(*_):
        raise AssertionError('default doctor must remain read-only')
    monkeypatch.setattr('hermes_kiokuko.diagnostics.command_registration', unexpected)
    assert diagnose(service.store.home)['command_registration'] == {'checked': False, 'gateway_loaded': None}


def test_pm_runtime_mismatch_fails_doctor_without_claiming_gateway(service, monkeypatch):
    monkeypatch.setattr('hermes_kiokuko.provenance.pm_environment', lambda: {
        'selected_environment': '/selected/venv', 'active': False, 'package_in_generation': False})
    info = diagnose(service.store.home)
    assert 'DIAGNOSTIC_PM_RUNTIME_MISMATCH' in info['errors']
    assert info['ok'] is False and info['gateway_loaded'] is None
