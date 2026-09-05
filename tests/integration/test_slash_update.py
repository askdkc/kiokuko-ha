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
