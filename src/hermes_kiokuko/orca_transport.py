"""Pinned Orca subprocess and strictly checked, managed trace reads."""
import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time

from .errors import KiokukoError
from .filesystem import checked_file, private_directory
from .models import canonical, new_id
from ._vendor.orca_trace import TraceReader

ASSETS = Path(__file__).with_name('orca')
RUN_PATTERN = re.compile(r'run_[0-9a-f]{32}\Z')
MAX_EVENT = 1 << 20


@lru_cache(maxsize=8)
def _node_probe(node, binary_identity, bundle_digest):
    """Load the actual writer without opening a trace or inheriting credentials.

    Cache by executable identity and verified bundle, never version metadata.
    The hello operation does not use its root argument or touch the filesystem.
    """
    frame = {'version': 1, 'id': 'kiokuko-runtime-probe', 'op': 'hello'}
    try:
        result = subprocess.run([node, str(ASSETS / 'bridge.mjs'), os.devnull],
            input=canonical(frame) + '\n', capture_output=True, text=True,
            timeout=3, check=True, env={'PATH': os.defpath})
        return json.loads(result.stdout) == {
            'version': 1, 'id': frame['id'], 'ok': True, 'protocol': 1}
    except (ValueError, OSError, subprocess.SubprocessError):
        return False


def runtime_check():
    node = shutil.which('node')
    if not node:
        raise KiokukoError('MONITOR_NODE_MISSING')
    pin = json.loads((ASSETS / 'pin.json').read_text())
    if hashlib.sha256((ASSETS / 'bridge.mjs').read_bytes()).hexdigest() != pin['bundle_sha256']:
        raise KiokukoError('MONITOR_BUNDLE_MISMATCH')
    try:
        executable = Path(node).resolve()
        info = executable.stat()
        identity = (str(executable), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not _node_probe(node, identity, pin['bundle_sha256']):
            raise ValueError()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise KiokukoError('MONITOR_NODE_UNSUPPORTED') from None
    return node


def run_path(directory, run_id):
    if not RUN_PATTERN.fullmatch(run_id):
        raise KiokukoError('MONITOR_INVALID_RUN')
    root = directory / 'traces'
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise KiokukoError('UNSAFE_PATH')
    path = root / run_id
    if path.is_symlink():
        raise KiokukoError('UNSAFE_PATH')
    return path


def trace_size(path):
    if not path.exists():
        return 0
    total = 0
    # Only the explicitly managed run directory; never follow links.
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs:
            if (Path(root) / name).is_symlink():
                raise KiokukoError('UNSAFE_PATH')
        for name in files:
            file = Path(root) / name
            checked_file(file)
            total += file.stat().st_size
    return total


def read_trace(directory, row):
    path = run_path(directory, row['id'])
    if not path.exists():
        raise KiokukoError('MONITOR_SOURCE_MISSING')
    trace_size(path)  # Validate every ancestor/blob before the upstream reader opens it.
    try:
        reader = TraceReader.open(path)
        ok, expected, _ = reader.verify_integrity()
        if not ok or expected != row['events_hash'] or reader.manifest.run_id != row['id']:
            raise KiokukoError('MONITOR_INTEGRITY_FAILED')
        output = []
        for event in reader.stream():
            if event.blob:
                data = reader.blob(event.blob, verify=True)
                if len(data) > MAX_EVENT + 65536:
                    raise KiokukoError('MONITOR_PAYLOAD_LIMIT')
                payload = json.loads(data)
            else:
                payload = event.payload
            output.append((event, payload))
        if reader.problems() or not output or output[0][0].type != 'run.start' or output[-1][0].type != 'run.end':
            raise KiokukoError('MONITOR_INCOMPLETE')
        return output
    except KiokukoError:
        raise
    except Exception:
        raise KiokukoError('MONITOR_INVALID_TRACE') from None


class OrcaProcess:
    def __init__(self, directory):
        self.root = directory / 'traces'
        private_directory(self.root)
        # No provider credentials, NODE_OPTIONS, preload hooks or inherited env in the writer.
        self.process = subprocess.Popen([runtime_check(), str(ASSETS / 'bridge.mjs'), str(self.root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'PATH': os.defpath}, bufsize=0)
        self.buffer = b''
        self.call('hello')

    def call(self, op, run=None, event=None):
        frame = {'version': 1, 'id': new_id('op'), 'op': op}
        if run:
            frame['run'] = run
        if event is not None:
            frame['event'] = event
        data = (canonical(frame) + '\n').encode()
        deadline = time.monotonic() + 5
        try:
            with selectors.DefaultSelector() as selector:
                fd = self.process.stdin.fileno()
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_WRITE)
                while data:
                    if not selector.select(max(0, deadline-time.monotonic())):
                        raise TimeoutError()
                    size = os.write(fd, data)
                    data = data[size:]
                selector.unregister(fd)
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while b'\n' not in self.buffer:
                    if not selector.select(max(0, deadline-time.monotonic())):
                        raise TimeoutError()
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        raise EOFError()
                    self.buffer += chunk
                    if len(self.buffer) > 65536:
                        raise ValueError()
            line, self.buffer = self.buffer.split(b'\n', 1)
            ack = json.loads(line)
            if ack.get('version') != 1 or ack.get('id') != frame['id'] or not ack.get('ok'):
                raise ValueError()
            return ack
        except Exception:
            self.close()
            raise KiokukoError('MONITOR_WRITER_FAILED') from None

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        self.process.stdin.close()
        self.process.stdout.close()
