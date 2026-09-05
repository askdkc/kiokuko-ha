"""Private files, atomic writes and advisory locks (macOS/Linux)."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

from .errors import KiokukoError


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise KiokukoError("UNSAFE_PATH")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise KiokukoError("UNSAFE_OWNER")
    os.chmod(path, 0o700)


def checked_file(path: Path) -> None:
    if path.is_symlink():
        raise KiokukoError("UNSAFE_PATH")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise KiokukoError("UNSAFE_OWNER")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise KiokukoError("UNSAFE_PERMISSIONS")


def local_filesystem(path: Path) -> None:
    if sys.platform == "darwin":
        # statvfs strips Darwin's MNT_LOCAL flag; read it from statfs instead.
        import ctypes
        class StatFS(ctypes.Structure):
            _fields_ = [("bsize", ctypes.c_uint32), ("iosize", ctypes.c_int32),
                        ("counts", ctypes.c_uint64 * 5), ("fsid", ctypes.c_int32 * 2),
                        ("owner", ctypes.c_uint32), ("type", ctypes.c_uint32),
                        ("flags", ctypes.c_uint32), ("subtype", ctypes.c_uint32),
                        ("names_and_reserved", ctypes.c_char * 2096)]
        info = StatFS()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.statfs(os.fsencode(path), ctypes.byref(info)) != 0:
            raise KiokukoError("FILESYSTEM_UNKNOWN")
        if not info.flags & 0x00001000:
            raise KiokukoError("NETWORK_FILESYSTEM")
    elif sys.platform.startswith("linux"):
        try:
            kind = subprocess.run(["stat", "-f", "-c", "%T", str(path)],
                                  capture_output=True, text=True, timeout=2, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise KiokukoError("FILESYSTEM_UNKNOWN") from None
        if kind in {"nfs", "nfs4", "cifs", "smb2", "smb3", "fuse.sshfs", "9p"}:
            raise KiokukoError("NETWORK_FILESYSTEM")
    else:
        raise KiokukoError("UNSUPPORTED_PLATFORM")


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise KiokukoError("UNSAFE_PATH")
    fd, temp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def acquire_lock(path: Path, *, exclusive=False, timeout=2.5):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + timeout
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        while True:
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise KiokukoError("LIVE_HOLDER" if exclusive else "STORE_BUSY") from None
                time.sleep(0.01)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def file_lock(path: Path, **kwargs):
    fd = acquire_lock(path, **kwargs)
    try:
        yield
    finally:
        os.close(fd)
