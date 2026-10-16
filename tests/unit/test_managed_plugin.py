from types import SimpleNamespace

from hermes_kiokuko.slash_update import ManagedSlashUpdate


def test_managed_update_never_installs_with_pip(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('managed updates must not mutate the Python environment')
    monkeypatch.setattr('hermes_kiokuko.slash_update.subprocess.run', forbidden)
    update = ManagedSlashUpdate(SimpleNamespace())
    for action in ('', 'help', 'status', 'retry'):
        reply = update.execute(tmp_path, action)
        assert 'plugins list' in reply
        assert 'plugins update kiokuko-tools' not in reply
    assert list(tmp_path.iterdir()) == []


def test_managed_update_uses_download_directory_not_manifest_display_name(tmp_path):
    directory = tmp_path / 'plugins' / 'custom' / 'kiokuko-ha'
    directory.mkdir(parents=True)
    manifest = SimpleNamespace(name='kiokuko-tools', key='kiokuko-tools',
                               source='user', path=directory, version='test')
    reply = ManagedSlashUpdate(SimpleNamespace(manifest=manifest)).execute(tmp_path, '')
    assert 'plugins update custom/kiokuko-ha' in reply
    assert 'plugins update kiokuko-tools' not in reply


def test_managed_update_does_not_guess_a_download_key_from_pm_snapshot(tmp_path):
    manifest = SimpleNamespace(name='kiokuko-tools', key='kiokuko-tools', source='user',
                               path=tmp_path / 'generation' / 'snapshot', version='test')
    reply = ManagedSlashUpdate(SimpleNamespace(manifest=manifest)).execute(tmp_path, '')
    assert 'plugins list' in reply and 'plugins update kiokuko-tools' not in reply
