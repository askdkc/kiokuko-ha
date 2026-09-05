"""Profile readiness, with independent ownership for concurrently active agents."""
from collections import Counter
from pathlib import Path
import threading

from .compatibility import active_home, check_host
from .errors import KiokukoError
from .models import new_id
from .security import host_scan
from .service import Service
from .store import Store

_lock = threading.RLock()
_owners = {}
_status = Counter()


def acquire(home):
    home = Path(home).resolve()
    check_host(home)
    store = Store(home, initialize=True)
    try:
        service = Service(store, host_guard=check_host, content_guard=host_scan)
        token = new_id("owner")
        with _lock:
            _owners[token] = service
        return token, service
    except BaseException:
        store.close()
        raise


def release(token):
    with _lock:
        service = _owners.pop(token, None)
    if service:
        service.store.close()


def current():
    home = active_home()
    check_host(home)
    with _lock:
        services = [s for s in _owners.values() if s.store.home == home and s.store.holder is not None]
    if not services:
        raise KiokukoError("PROVIDER_NOT_READY")
    return services[0]


def record_status(code, service=None):
    # No raw exception, claim, user content or path enters the status stream.
    with _lock:
        _status[code] += 1
    if service:
        try:
            service.store.status(code)
        except (KiokukoError, OSError):
            pass


def status_counts():
    with _lock:
        return dict(_status)
