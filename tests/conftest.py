from pathlib import Path
import importlib
import threading
from types import SimpleNamespace
import pytest

from hermes_kiokuko.config import setup
from hermes_kiokuko.models import Identity
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store


@pytest.fixture
def logical_deadline_clock(monkeypatch, request):
    """Control only memory budget clocks, never global thread/HTTP/SDK timers.

    Functional tests exercise actual storage with fixed logical time. Deadline
    tests advance it explicitly; neither kind establishes wall-clock latency.
    Production clock references and budget values remain unchanged.
    """
    class Clock:
        value = 1000.
        lock = threading.Lock()

        def monotonic(self):
            with self.lock:
                return self.value

        def advance(self, seconds):
            assert seconds >= 0
            with self.lock:
                self.value += seconds

    clock = Clock()
    timer = SimpleNamespace(monotonic=clock.monotonic)
    for name in ('store', 'service', 'deliveries', 'task_profiles', 'profile_probe', 'turn_hook'):
        monkeypatch.setattr(importlib.import_module('hermes_kiokuko.' + name), 'time', timer)
    if hasattr(request.module, 'time'):
        monkeypatch.setattr(request.module, 'time', timer)
    return clock


@pytest.fixture
def service(tmp_path):
    home = tmp_path / "profile"
    setup(home)
    store = Store(home, initialize=True)
    yield Service(store)
    store.close()


@pytest.fixture
def identity():
    return Identity("cli", "cli", "profile-owner", "conv-local", "ws-local", "dm")


@pytest.fixture
def make_turn(service, identity):
    counter = iter(range(10000))
    def make(raw="hello", *, session="session", who=None, turn=None):
        return service.snapshot(session, turn or f"turn-{next(counter)}", raw, who or identity)
    return make
