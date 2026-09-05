"""Update the running Hermes venv on explicit local human command."""
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import threading

from . import __version__
from .compatibility import check_host
from .errors import KiokukoError
from .filesystem import acquire_lock
from .slash_curation import cli_binding

PACKAGE = "hermes-kiokuko"
_lock = threading.Lock()
_job = None


@dataclass
class UpdateJob:
    home: Path
    python: str
    previous: str
    state: str = "running"
    version: str = ""
    error: str = ""


def update_environment(home):
    # Keep transport/OS settings, but prevent pip flags and Python path variables
    # from redirecting this explicit PyPI update to another prefix or package.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PIP_", "PYTHON", "HERMES_SESSION_"))}
    env.update(HERMES_HOME=str(home), PIP_CONFIG_FILE=os.devnull)
    return env


def perform_update(job, env, lock_fd):
    try:
        result = subprocess.run(
            [job.python, "-I", "-m", "pip", "install", "--upgrade", "--no-input",
             "--disable-pip-version-check", "--progress-bar", "off", "--only-binary=:all:",
             "--index-url", "https://pypi.org/simple", PACKAGE],
            env=env, cwd=str(job.home), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        if result.returncode:
            raise KiokukoError("PIP_INSTALL_FAILED")
        result = subprocess.run(
            [job.python, "-I", "-c",
             "from importlib.metadata import version; print(version('hermes-kiokuko'))"],
            env=env, cwd=str(job.home), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=15, check=True)
        version = result.stdout.strip()
        if not version or len(version) > 80 or not all(c.isalnum() or c in ".+-!" for c in version):
            raise KiokukoError("UPDATE_VERSION_UNAVAILABLE")
        with _lock:
            job.version, job.state = version, "complete"
    except subprocess.TimeoutExpired:
        with _lock:
            job.error, job.state = "UPDATE_TIMEOUT", "failed"
    except (KiokukoError, OSError, subprocess.SubprocessError) as error:
        with _lock:
            job.error = error.code if isinstance(error, KiokukoError) else "UPDATE_FAILED"
            job.state = "failed"
    finally:
        os.close(lock_fd)


def describe(job):
    scope = (f"profile: {job.home}\nPython: {job.python}\n"
             "パッケージは、このPython環境を共有するすべてのprofileで更新されます。"
             "profileの設定・記憶DBは変更しません。")
    if job.state == "running":
        return f"{PACKAGE} をPyPIから更新中です。\n{scope}\n結果: /kiokuko-update status"
    if job.state == "complete":
        return (f"更新処理が完了しました。インストール済み: {job.version}（このprocessの読込時: {job.previous}）\n{scope}\n"
                "実行中のコードは切り替わりません。この環境を使うHermesを再起動してください。")
    return (f"更新に失敗しました ({job.error})。\n{scope}\n"
            "部分的にインストールされた可能性があるため、成功とは扱いません。"
            "再試行: /kiokuko-update retry\n"
            "詳細確認はHermesを終了し、同じPythonで -m pip install --upgrade hermes-kiokuko を実行してください。")


class SlashUpdate:
    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, raw_args):
        global _job
        try:
            home, session = cli_binding(self.ctx)
            # Reject delegated/background contexts too, without opening a DB or
            # taking a mutable latest-turn snapshot to identify the caller.
            from agent.delegation_context import is_delegated_child_context
            from tools.skill_provenance import get_current_write_origin
            from .identity import bound_values
            bound = bound_values()
            if is_delegated_child_context() or get_current_write_origin() == "background_review" or \
                    (bound.get("ID") and bound["ID"] != session):
                raise KiokukoError("CURATION_CLI_REQUIRED")
            action = raw_args.strip()
            if action not in {"", "status", "retry", "help"}:
                return "操作: /kiokuko-update / 状態: /kiokuko-update status / 再試行: /kiokuko-update retry"
            if action == "help":
                return ("/kiokuko-update で現在のHermes用Python環境の hermes-kiokuko をPyPIから更新します。\n"
                        "同じPython環境を共有するprofileにも適用されます。設定・記憶DBは変更しません。\n"
                        "結果確認: /kiokuko-update status / 失敗後の再試行: /kiokuko-update retry\n"
                        "完了後にHermesを再起動してください。")
            with _lock:
                if _job is not None and (_job.state == "running" or action != "retry" or _job.state != "failed"):
                    return describe(_job)
                if action == "status":
                    return "このprocessではまだ更新を実行していません。開始: /kiokuko-update"
                check_host(home)
                if sys.prefix == sys.base_prefix:
                    return "Hermesの仮想環境を確認できません。Hermes用venvから起動してください。system Pythonは更新しません。"
                # All profiles and processes sharing this venv use the same lock.
                lock_fd = acquire_lock(Path(sys.prefix) / ".kiokuko-update.lock", exclusive=True, timeout=0)
                job = UpdateJob(home, sys.executable, __version__)
                env = update_environment(home)
                try:
                    thread = threading.Thread(target=perform_update, args=(job, env, lock_fd),
                                              name="kiokuko-update", daemon=False)
                    thread.start()
                except Exception:
                    os.close(lock_fd)
                    raise
                _job = job
                return describe(job)
        except KiokukoError as error:
            if error.code == "CURATION_CLI_REQUIRED":
                return "更新はHermesの対話CLIで実行してください。この呼出経路では管理者を確認できません。"
            return f"更新を開始できませんでした ({error.code})。別の更新処理やprofile設定を確認してください。"
        except (OSError, ValueError, AttributeError, ImportError, RuntimeError):
            return "更新を開始できませんでした (UPDATE_UNAVAILABLE)。HermesのPython環境と権限を確認してください。"
