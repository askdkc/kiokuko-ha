import json
import os
from pathlib import Path
import subprocess
import venv

import pytest

from hermes_kiokuko.filesystem import acquire_lock
from hermes_kiokuko.slash_update import UpdateJob, perform_update, update_environment


def test_worker_uses_captured_python_and_profile_in_real_subprocess(tmp_path, monkeypatch):
    """Fake pip inside a disposable venv: exercise spawning, never contact PyPI."""
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "bin" / "python3"
    site = Path(subprocess.check_output([str(python), "-I", "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
    pip = site / "pip"
    pip.mkdir()
    (pip / "__init__.py").write_text("")
    (pip / "__main__.py").write_text('''import json, os, sys
from pathlib import Path
Path("invocation.json").write_text(json.dumps({"args":sys.argv[1:], "home":os.environ["HERMES_HOME"],
    "redirect":os.environ.get("PIP_TARGET"), "config":os.environ["PIP_CONFIG_FILE"]}))
metadata = Path(__file__).parent.parent / "hermes_kiokuko-9.9.dist-info"
metadata.mkdir()
(metadata / "METADATA").write_text("Metadata-Version: 2.1\\nName: hermes-kiokuko\\nVersion: 9.9\\n")
''')
    home = tmp_path / "profiles" / "main"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("untouched")
    monkeypatch.setenv("PIP_TARGET", "/wrong-environment")
    monkeypatch.setenv("HERMES_HOME", "/wrong-profile")
    monkeypatch.setenv("PYTHONPATH", "/wrong-path")
    env = update_environment(home)
    assert "PYTHONPATH" not in env
    job = UpdateJob(home, str(python), "0.1.1")
    lock = environment / ".kiokuko-update.lock"
    perform_update(job, env, acquire_lock(lock, exclusive=True))
    assert (job.state, job.version) == ("complete", "9.9")
    seen = json.loads((home / "invocation.json").read_text())
    assert seen["home"] == str(home) and seen["redirect"] is None
    assert seen["config"] == os.devnull
    assert seen["args"] == ["install", "--upgrade", "--no-input", "--disable-pip-version-check",
                            "--progress-bar", "off", "--only-binary=:all:",
                            "--index-url", "https://pypi.org/simple", "hermes-kiokuko"]
    assert (home / "config.yaml").read_text() == "untouched"
    os.close(acquire_lock(lock, exclusive=True, timeout=0))


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_failure_is_not_success_and_releases_venv_lock(tmp_path, monkeypatch, failure):
    import hermes_kiokuko.slash_update as module
    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 180)
        if failure == "missing":
            raise FileNotFoundError()
        return subprocess.CompletedProcess(args[0], 1)
    monkeypatch.setattr(module.subprocess, "run", run)
    job = UpdateJob(tmp_path, "unused-python", "0.1.1")
    lock = tmp_path / "update.lock"
    perform_update(job, {}, acquire_lock(lock, exclusive=True))
    assert job.state == "failed" and job.version == ""
    os.close(acquire_lock(lock, exclusive=True, timeout=0))


@pytest.mark.parametrize('version', ['0.1.11', '', 'invalid version'])
def test_noop_and_invalid_version_results_are_explicit(tmp_path, monkeypatch, version):
    import hermes_kiokuko.slash_update as module
    from types import SimpleNamespace
    monkeypatch.setattr(module.subprocess, 'run',
                        lambda argv, **kw: SimpleNamespace(returncode=0, stdout=version))
    job = UpdateJob(tmp_path, 'unused-python', '0.1.11')
    lock = tmp_path / 'update.lock'
    perform_update(job, {}, acquire_lock(lock, exclusive=True))
    assert job.state == ('complete' if version == '0.1.11' else 'failed')
    assert ('再起動' in module.describe(job)) if job.state == 'complete' else job.error == 'UPDATE_VERSION_UNAVAILABLE'
    os.close(acquire_lock(lock, exclusive=True, timeout=0))


def test_thread_start_failure_releases_lock_and_status_remains_readonly(tmp_path, monkeypatch):
    import hermes_kiokuko.slash_update as module
    monkeypatch.setattr(module, 'check_host', lambda home: None)
    monkeypatch.setattr(module, '_job', None)
    monkeypatch.setattr(module.sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(module.threading.Thread, 'start',
                        lambda self: (_ for _ in ()).throw(RuntimeError('start failed')))
    handler = module.SlashUpdate(None)
    assert 'UPDATE_UNAVAILABLE' in handler.execute(tmp_path, '')
    os.close(acquire_lock(tmp_path / '.kiokuko-update.lock', exclusive=True, timeout=0))
    assert 'まだ更新' in handler.execute(tmp_path, 'status')


@pytest.mark.parametrize('state', ['running', 'complete', 'failed'])
@pytest.mark.parametrize('action', ['', 'status', 'help', 'retry'])
def test_job_transitions_only_retry_failed_starts_worker(tmp_path, monkeypatch, state, action):
    import hermes_kiokuko.slash_update as module
    monkeypatch.setattr(module, 'check_host', lambda home: None)
    monkeypatch.setattr(module.sys, 'prefix', str(tmp_path))
    job = UpdateJob(tmp_path, 'unused-python', '0.1.11', state=state, version='0.1.11')
    monkeypatch.setattr(module, '_job', job)
    calls = []
    # Record the startup but close its real lock without launching a thread.
    def start(thread):
        calls.append(thread)
        os.close(thread._args[2])
    monkeypatch.setattr(module.threading.Thread, 'start', start)
    module.SlashUpdate(None).execute(tmp_path, action)
    assert len(calls) == int(state == 'failed' and action == 'retry')
    if not calls:
        assert module._job is job


def test_gateway_checks_host_before_status_but_cli_operation_does_not(tmp_path, monkeypatch):
    import hermes_kiokuko.slash_update as module
    import hermes_kiokuko.gateway_commands as gateway
    from hermes_kiokuko.errors import KiokukoError
    monkeypatch.setattr(module, '_job', None)
    def unsupported(home):
        raise KiokukoError('UNSUPPORTED_HERMES')
    monkeypatch.setattr(gateway, 'check_host', unsupported)
    handler = module.SlashUpdate(None)
    assert 'まだ更新' in handler.execute(tmp_path, 'status')
    with pytest.raises(KiokukoError, match='UNSUPPORTED_HERMES'):
        gateway.GatewayCommands._execute(handler, tmp_path, 'status')
