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
