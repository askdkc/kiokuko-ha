"""Production budget and rollback boundaries, independent of OS scheduling."""
import importlib

import pytest

from hermes_kiokuko.config import load_config, write_yaml
from hermes_kiokuko.deliveries import prepare, sync_completed
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.models import ExplicitCommand, Identity
from hermes_kiokuko.task_profiles import capture_completed
from hermes_kiokuko.workspace import resolve_workspace


def advance_at_final_validation(service, monkeypatch, clock, elapsed):
    original = service.check_snapshot
    calls = 0
    def checked(db, snapshot):
        nonlocal calls
        original(db, snapshot)
        calls += 1
        if calls == 2:
            clock.advance(elapsed)
    monkeypatch.setattr(service, 'check_snapshot', checked)


def test_clock_is_local_to_memory_deadlines(logical_deadline_clock):
    stdlib = importlib.import_module('time')
    service = importlib.import_module('hermes_kiokuko.service')
    assert service.time is not stdlib
    assert service.time.monotonic() == 1000.
    logical_deadline_clock.advance(.2)
    assert service.time.monotonic() == 1000.2
    assert stdlib.monotonic is not service.time.monotonic


@pytest.mark.parametrize('elapsed,expires', [(.149, False), (.15, True), (.151, True)])
def test_search_150ms_budget(service, make_turn, logical_deadline_clock, monkeypatch, elapsed, expires):
    snap = make_turn('alpha')
    entry = service.explicit(snap, ExplicitCommand('remember', 'alpha', 'principal'))
    advance_at_final_validation(service, monkeypatch, logical_deadline_clock, elapsed)
    if expires:
        with pytest.raises(KiokukoError, match='^DEADLINE_EXCEEDED$'):
            service.search(snap, 'alpha')
    else:
        assert service.search(snap, 'alpha')[0]['id'] == entry['entry_id']


@pytest.mark.parametrize('elapsed,expires', [(.149, False), (.15, True), (.151, True)])
def test_capture_150ms_budget_rolls_back_all_projection_writes(
        service, tmp_path, logical_deadline_clock, monkeypatch, elapsed, expires):
    cfg = load_config(service.store.home)
    cfg['task_profile_memory']['mode'] = 'resolve'
    write_yaml(service.store.directory / 'config.yaml', cfg)
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'widget.py').write_text('pass\n')
    who = Identity('cli', 'cli', 'profile-owner', 'conversation', resolve_workspace(root), 'dm')
    raw = 'Inspect `widget.py`'
    snap = service.snapshot('source', 'turn', raw, who, workspace_root=root)
    context = prepare(service, snap, raw)
    messages = [{'role': 'user', 'content': raw, 'api_content': raw + '\n\n' + context}]
    sync_completed(service, snap.session_id, raw, messages)
    advance_at_final_validation(service, monkeypatch, logical_deadline_clock, elapsed)
    if expires:
        with pytest.raises(KiokukoError, match='^DEADLINE_EXCEEDED$'):
            capture_completed(service, snap, messages)
    else:
        assert capture_completed(service, snap, messages)
    with service.transaction() as db:
        for table in ('task_profiles', 'task_profile_receipts', 'task_profile_documents'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == (0 if expires else 1)
