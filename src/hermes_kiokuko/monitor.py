"""Bounded capture queue, acknowledged runs, and durable extraction jobs."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
import queue
import secrets
import shutil
import threading
import time

from . import runtime
from .errors import KiokukoError
from .filesystem import acquire_lock, file_lock
from .models import TurnSnapshot, canonical, digest, now
from .orca_transport import OrcaProcess, read_trace, run_path, trace_size
from .service import Service
from .store import Store

_managers = {}
_lock = threading.RLock()
QUEUE_EVENTS = 128
QUEUE_BYTES = 16 << 20
RETENTION_BYTES = 1 << 30


@dataclass
class Capture:
    snapshot: TurnSnapshot
    run_id: str = field(default_factory=lambda: 'run_' + secrets.token_hex(16))
    dropped: int = 0
    observation: int = 0
    api_requests: int = 0
    closed: bool = False
    aborted: bool = False
    causes: dict = field(default_factory=dict)


def binding_hash(service, snap):
    return hmac.new(service.store.key, canonical(asdict(snap)).encode(), 'sha256').hexdigest()


def get_manager(service):
    key = str(service.store.home)
    with _lock:
        manager = _managers.get(key)
        if manager is None or manager.stopping.is_set():
            manager = Monitor(service)
            _managers[key] = manager
        return manager


def release_home(home):
    with _lock:
        manager = _managers.pop(str(home), None)
    if manager:
        manager.close()


def complete_turn(service, snap):
    if not service.config['monitor']['enabled']:
        return
    get_manager(service).complete(snap)


class Monitor:
    def __init__(self, parent):
        self.service = Service(Store(parent.store.home), host_guard=parent.host_guard, content_guard=parent.content_guard)
        self.owner = 'owner_' + secrets.token_hex(16)
        self.owner_fd = acquire_lock(self.service.store.directory / (self.owner + '.lock'), exclusive=True)
        self.queue = queue.Queue(QUEUE_EVENTS)
        self.bytes = 0
        self.lock = threading.RLock()
        self.captures = {}
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.process = None
        self.recover()
        self.writer = threading.Thread(target=self._write_loop, daemon=True, name='kiokuko-orca')
        self.extractor = threading.Thread(target=self._extract_loop, daemon=True, name='kiokuko-experiences')
        self.writer.start()
        self.extractor.start()

    def recover(self):
        with self.service.transaction() as db:
            owners = [r[0] for r in db.execute("SELECT DISTINCT owner FROM monitor_runs WHERE state='recording'")]
        for owner in owners:
            if not owner.startswith('owner_') or len(owner) != 38 or any(c not in '0123456789abcdef' for c in owner[6:]):
                continue
            try:
                with file_lock(self.service.store.directory / (owner + '.lock'), exclusive=True, timeout=0):
                    with self.service.transaction(write=True) as db:
                        db.execute("UPDATE monitor_runs SET state='incomplete',error_code='MONITOR_WRITER_INTERRUPTED' WHERE owner=? AND state='recording'", (owner,))
            except KiokukoError:
                pass
        # The extraction lease is a file lock, never a clock-based guess about a live worker.
        try:
            with file_lock(self.service.store.directory / 'experience.lock', exclusive=True, timeout=0):
                with self.service.transaction(write=True) as db:
                    db.execute("UPDATE experience_jobs SET state='pending' WHERE state='running'")
        except KiokukoError:
            pass

    def begin(self, snap, user):
        if snap.origin not in {'cli', 'cli_user', 'dm', 'group_chat'}:
            return
        with self.lock:
            if snap.key in self.captures:
                return
            if len(self.captures) >= QUEUE_EVENTS:
                raise KiokukoError('MONITOR_QUEUE_FULL')
            capture = Capture(snap)
            self.captures[snap.key] = capture
            try:
                self._enqueue(capture, 'open', user)
            except Exception:
                capture.closed = True
                raise

    def next_observation(self, snap):
        with self.lock:
            capture = self.captures.get(snap.key)
            if capture is None or capture.closed or capture.snapshot != snap:
                raise KiokukoError('MONITOR_TURN_MISSING')
            capture.observation += 1
            return capture.observation

    def append(self, snap, kind, actor, value, attrs):
        with self.lock:
            capture = self.captures.get(snap.key)
            if capture is None or capture.closed or capture.snapshot != snap:
                raise KiokukoError('MONITOR_TURN_MISSING')
            event = {'type': kind, 'actor': actor, 'turn': attrs.get('observation', 0),
                     'attrs': attrs, 'payload': value, 'occurredAt': now()}
            self._enqueue(capture, 'append', event)

    def _enqueue(self, capture, operation, value):
        size = len(canonical(value).encode())
        if self.stopping.is_set() or self.bytes + size > QUEUE_BYTES:
            capture.dropped += 1
            raise KiokukoError('MONITOR_QUEUE_FULL')
        try:
            self.queue.put_nowait((capture, operation, value, size))
            self.bytes += size
        except queue.Full:
            capture.dropped += 1
            raise KiokukoError('MONITOR_QUEUE_FULL') from None

    def drop(self, snap, code):
        with self.lock:
            capture = self.captures.get(snap.key)
            if capture:
                capture.dropped += 1
        runtime.record_status(code)

    def complete(self, snap):
        with self.lock:
            capture = self.captures.get(snap.key)
            if not capture or capture.closed:
                return
            capture.closed = True
            try:
                self._enqueue(capture, 'close', {})
            except KiokukoError:
                # A full queue cannot lose the only completion signal. Writer scans closed runs.
                runtime.record_status('MONITOR_QUEUE_FULL')

    def abort(self, snap):
        with self.lock:
            capture = self.captures.get(snap.key)
            if not capture or capture.closed:
                return
            capture.closed = capture.aborted = True
            capture.dropped += 1
            try:
                self._enqueue(capture, 'abort', {})
            except KiokukoError:
                pass  # The writer's closed-run sweep retains this signal.

    def _write(self, capture, operation, value):
        with file_lock(self.service.store.directory / 'monitor-quota.lock', exclusive=True):
            if not self.service.config['monitor']['enabled']:
                capture.dropped += 1
            if operation != 'open':
                with self.service.transaction() as db:
                    row = db.execute('SELECT state FROM monitor_runs WHERE id=?', (capture.run_id,)).fetchone()
                if not row or row[0] != 'recording':
                    if not row:
                        self._failed(capture)
                    if operation in {'close', 'abort'}:
                        if row and row[0] == 'incomplete' and self.process and run_path(self.service.store.directory, capture.run_id).exists():
                            try:
                                self.process.call('close', capture.run_id)
                            except Exception:
                                pass
                        with self.lock:
                            self.captures.pop(capture.snapshot.key, None)
                    return
            if operation in {'open', 'append'}:
                with self.service.transaction() as db:
                    total = db.execute('SELECT COALESCE(sum(bytes),0) FROM monitor_runs').fetchone()[0]
                reserve = len(canonical(value).encode()) * 3 + 65536
                if total + reserve > RETENTION_BYTES:
                    _collect_locked(self.service, reserve=reserve)
                    with self.service.transaction() as db:
                        total = db.execute('SELECT COALESCE(sum(bytes),0) FROM monitor_runs').fetchone()[0]
                    if total + reserve > RETENTION_BYTES:
                        raise KiokukoError('MONITOR_CAPACITY_EXCEEDED')
            self._write_frame(capture, operation, value)
            path = run_path(self.service.store.directory, capture.run_id)
            if path.exists():
                with self.service.transaction(write=True) as db:
                    db.execute('UPDATE monitor_runs SET bytes=?,api_requests=? WHERE id=?', (trace_size(path),capture.api_requests,capture.run_id))

    def _write_frame(self, capture, operation, value):
        snap = capture.snapshot
        if operation == 'open':
            with self.service.transaction(snap, write=True) as db:
                db.execute('INSERT INTO monitor_runs(id,snapshot_json,binding_hash,owner,state,created_at) VALUES (?,?,?,?,?,?)',
                    (capture.run_id, canonical(asdict(snap)), binding_hash(self.service, snap), self.owner, 'recording', now()))
            if self.process is None:
                self.process = OrcaProcess(self.service.store.directory)
            run_path(self.service.store.directory, capture.run_id)
            self.process.call('open', capture.run_id)
            self.process.call('append', capture.run_id, {'type': 'note', 'actor': 'user', 'payload': value})
        elif operation == 'append':
            if self.process is None:
                raise KiokukoError('MONITOR_WRITER_FAILED')
            observation = value['attrs'].get('observation')
            if value['type'] in {'model.response', 'tool.result', 'error'} and observation in capture.causes:
                value['causes'] = [capture.causes[observation]]
            ack = self.process.call('append', capture.run_id, value)
            if value['type'] == 'model.request':
                capture.api_requests += 1
            if value['type'] in {'model.request', 'tool.call'}:
                capture.causes[observation] = ack['seq']
        else:
            if operation != 'abort':
                with self.service.transaction(snap) as db:
                    if not db.execute('SELECT 1 FROM turn_syncs WHERE profile_key=? AND session_id=? AND turn_id=?', snap.key).fetchone():
                        raise KiokukoError('MONITOR_TURN_INCOMPLETE')
            if self.process is None:
                raise KiokukoError('MONITOR_WRITER_FAILED')
            ack = self.process.call('close', capture.run_id)
            with self.service.transaction(None if operation == 'abort' else snap, write=True) as db:
                state = 'incomplete' if capture.dropped or not capture.api_requests else 'complete'
                db.execute("UPDATE monitor_runs SET state=?,events_hash=?,completed_at=?,bytes=?,dropped=? WHERE id=? AND state='recording'",
                    (state, ack['integrity']['events_sha256'], now(), trace_size(run_path(self.service.store.directory, capture.run_id)), capture.dropped, capture.run_id))
                row = db.execute('SELECT state FROM monitor_runs WHERE id=?', (capture.run_id,)).fetchone()
                if row and row[0] == 'complete':
                    db.execute('INSERT OR IGNORE INTO experience_jobs VALUES (?,?,NULL,0,?)', (capture.run_id, 'pending', now()))
            with self.lock:
                self.captures.pop(snap.key, None)
            self.wakeup.set()

    def _failed(self, capture):
        capture.dropped += 1
        try:
            with self.service.transaction(write=True) as db:
                db.execute('INSERT OR IGNORE INTO monitor_runs(id,snapshot_json,binding_hash,owner,state,created_at) VALUES (?,?,?,?,?,?)',
                    (capture.run_id,canonical(asdict(capture.snapshot)),binding_hash(self.service,capture.snapshot),self.owner,'incomplete',now()))
                db.execute("UPDATE monitor_runs SET state='incomplete',dropped=?,error_code='MONITOR_CAPTURE_FAILED' WHERE id=? AND state IN ('recording','incomplete')", (capture.dropped, capture.run_id))
        except Exception:
            pass
        if self.process and self.process.process.poll() is not None:
            self.process.close()
            self.process = None
        runtime.record_status('MONITOR_CAPTURE_FAILED', self.service)

    def _write_loop(self):
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                capture, op, value, size = self.queue.get(timeout=.1)
            except queue.Empty:
                # Recover completion signals rejected by a full queue; no API thread waits for I/O.
                with self.lock:
                    if not self.queue.empty():
                        continue
                    closed = [c for c in self.captures.values() if c.closed]
                for capture in closed:
                    try:
                        self._write(capture, 'abort' if capture.aborted else 'close', {})
                    except Exception:
                        self._failed(capture)
                        with self.lock:
                            self.captures.pop(capture.snapshot.key, None)
                continue
            with self.lock:
                self.bytes -= size
            try:
                self._write(capture, op, value)
            except Exception:
                self._failed(capture)
            finally:
                self.queue.task_done()
        if self.process:
            self.process.close()
        for capture in list(self.captures.values()):
            self._failed(capture)

    def _extract_loop(self):
        from .experiences import process_next
        last_gc = 0
        while not self.stopping.is_set():
            try:
                if self.service.config['monitor']['enabled']:
                    with self.lock:
                        captures = list(self.captures.values())
                    for capture in captures:
                        try:
                            with self.service.transaction(capture.snapshot):
                                pass
                        except KiokukoError as error:
                            if error.code == 'STALE_GENERATION':
                                self.abort(capture.snapshot)
                    if time.monotonic() - last_gc > 60:
                        collect(self.service)
                        last_gc = time.monotonic()
                    if process_next(self.service):
                        continue
                else:
                    with self.lock:
                        for capture in self.captures.values():
                            capture.dropped += 1
                            capture.closed = capture.aborted = True
            except Exception:
                runtime.record_status('MONITOR_EXTRACTION_FAILED')
            self.wakeup.wait(.25)
            self.wakeup.clear()

    def wait_idle(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.service.transaction() as db:
                pending = db.execute("SELECT count(*) FROM experience_jobs WHERE state IN ('pending','running')").fetchone()[0]
            if not self.queue.unfinished_tasks and not pending and not self.captures:
                return True
            self.stopping.wait(.02)
        return False

    def close(self):
        self.stopping.set()
        self.wakeup.set()
        def finish():
            self.writer.join()
            self.extractor.join()
            self.service.store.close()
            os.close(self.owner_fd)
        if threading.current_thread() not in {self.writer, self.extractor}:
            reaper = threading.Thread(target=finish, daemon=True)
            reaper.start()
            reaper.join(1)


def collect(service):
    with file_lock(service.store.directory / 'monitor-quota.lock', exclusive=True):
        _collect_locked(service)


def _collect_locked(service, reserve=0):
    cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with service.transaction() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM monitor_runs WHERE state NOT IN ('purged','missing') ORDER BY created_at")]
    total = 0
    sizes = {}
    for row in rows:
        path = run_path(service.store.directory, row['id'])
        sizes[row['id']] = trace_size(path)
        total += sizes[row['id']]
        if row['state'] != 'recording' and not path.exists():
            _remove_run_locked(service, row['id'], missing=True)
    for row in rows:
        if row['state'] != 'recording' and (row['created_at'] < cutoff or total + reserve > RETENTION_BYTES):
            _remove_run_locked(service, row['id'], missing=True)
            total -= sizes[row['id']]


def remove_run(service, run_id, *, missing=False):
    with file_lock(service.store.directory / 'monitor-quota.lock', exclusive=True):
        _remove_run_locked(service, run_id, missing=missing)


def _remove_run_locked(service, run_id, *, missing=False):
    # Commit invalidation before unlinking: extraction must recheck this state at commit.
    with service.transaction(write=True) as db:
        row = db.execute('SELECT state FROM monitor_runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise KiokukoError('MONITOR_RUN_NOT_FOUND')
        if row[0] == 'recording':
            raise KiokukoError('MONITOR_RUN_ACTIVE')
        db.execute('UPDATE monitor_runs SET state=?,bytes=0,error_code=? WHERE id=?',
            ('missing' if missing else 'purged', 'MONITOR_SOURCE_MISSING' if missing else 'MONITOR_PURGED', run_id))
        db.execute("UPDATE experience_jobs SET state='blocked',error_code='MONITOR_SOURCE_MISSING',updated_at=? WHERE run_id=?", (now(), run_id))
    path = run_path(service.store.directory, run_id)
    if path.exists():
        trace_size(path)
        shutil.rmtree(path)


def status(service):
    with service.transaction() as db:
        runs = dict(db.execute('SELECT state,count(*) FROM monitor_runs GROUP BY state'))
        jobs = dict(db.execute('SELECT state,count(*) FROM experience_jobs GROUP BY state'))
        last = db.execute("SELECT max(updated_at) FROM experience_jobs WHERE state='done'").fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT * FROM monitor_runs')]
    size, missing = 0, 0
    for row in rows:
        path = run_path(service.store.directory, row['id'])
        size += trace_size(path)
        missing += int(row['state'] == 'complete' and not path.exists())
    from .orca_transport import runtime_check
    node = 'not_required'
    if service.config['monitor']['enabled']:
        try:
            runtime_check()
            node = 'ready'
        except KiokukoError as e:
            node = e.code
    return {'enabled': service.config['monitor']['enabled'], 'node': node, 'runs': runs,
            'jobs': jobs, 'last_extraction_success': last, 'bytes': size, 'source_missing': missing,
            'capture_observed': any(row['api_requests'] > 0 for row in rows),
            'api_requests': sum(row['api_requests'] for row in rows),
            'retention_days': 7, 'max_bytes': RETENTION_BYTES}


def end_session(service, messages):
    # Explicit signed host history identifies the closing turn; no mutable-session fallback.
    if service is None:
        return
    users = [m for m in messages or [] if isinstance(m, dict) and m.get('role') == 'user']
    if not users:
        return
    with _lock:
        manager = _managers.get(str(service.store.home))
    if manager is None:
        return
    try:
        from .compaction import signed_snapshot
        with service.transaction() as db:
            snap = signed_snapshot(service,db,users[-1])
        manager.abort(snap)
    except KiokukoError:
        runtime.record_status('MONITOR_END_CONTEXT_MISSING',service)
