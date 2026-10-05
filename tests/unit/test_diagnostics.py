from hermes_kiokuko.diagnostics import diagnose


def test_diagnosis_before_setup_does_not_create_profile(tmp_path):
    home = tmp_path / 'absent'
    info = diagnose(home)
    assert not info['host_ready'] and not info['store_ready']
    assert not home.exists() and info['gateway_loaded'] is None
