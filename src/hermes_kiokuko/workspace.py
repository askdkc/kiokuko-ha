from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit

from .models import digest


def resolve_workspace(cwd) -> str | None:
    if not cwd:
        return None
    path = Path(cwd).resolve()
    if not path.is_dir():
        return None
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                                  text=True, timeout=.5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    root = git("rev-parse", "--show-toplevel")
    if not root:
        return "ws_" + digest(str(path))
    remote = git("config", "--get", "remote.origin.url")
    if "://" in remote:
        parsed = urlsplit(remote)
        remote = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    elif "@" in remote:
        remote = remote.split("@", 1)[1]
    # Clone identity includes canonical root. Repo-supplied IDs never grant access.
    return "ws_" + digest(str(Path(root).resolve()) + "\0" + remote.removesuffix(".git"))
