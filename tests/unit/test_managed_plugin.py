from types import SimpleNamespace

from hermes_kiokuko.slash_update import ManagedSlashUpdate


def test_managed_update_never_installs_with_pip(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('managed updates must not mutate the Python environment')
    monkeypatch.setattr('hermes_kiokuko.slash_update.subprocess.run', forbidden)
    update = ManagedSlashUpdate(SimpleNamespace())
    for action in ('', 'help', 'status', 'retry'):
        assert 'plugins update kiokuko-tools' in update.execute(tmp_path, action)
    assert list(tmp_path.iterdir()) == []
