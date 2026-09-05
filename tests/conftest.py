from pathlib import Path
import pytest

from hermes_kiokuko.config import setup
from hermes_kiokuko.models import Identity
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store


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
