import re
from dataclasses import replace

import pytest

from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.facts import store_verified
from hermes_kiokuko.models import Identity, ExplicitCommand
from hermes_kiokuko.slash_curation import SlashCuration
from hermes_kiokuko.workspace import resolve_workspace


@pytest.fixture
def review(service, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.json").write_text('{"language":"ja","port":8000}')
    identity = Identity("cli", "cli", "profile-owner", "conversation", resolve_workspace(root), "dm")
    snap = service.snapshot("session", "source", "config.json", identity, workspace_root=root)
    facts = [{"path": "config.json", "format": "json", "selector": [key], "value": value}
             for key, value in [("language", "ja"), ("port", 8000)]]
    store_verified(service, snap, facts, "test-slash")
    command = SlashCuration(None)
    return root, snap, command


def count_shared(service):
    with service.transaction() as db:
        return db.execute("SELECT count(*) FROM memory_entries WHERE scope_type='profile'").fetchone()[0]


def confirm_args(output):
    return re.search(r"/kioku-curation (confirm [a-f0-9]+)", output)[1]


def test_selection_confirmation_and_duplicate(service, review):
    _, snap, command = review
    run = lambda raw: command.execute(service, snap, raw)
    assert "[ ] 1." in run("")
    assert "番号" in run("share")
    assert "[x] 1." in run("select 1")
    assert "選択は維持" in run("select 999")
    old = confirm_args(run("share"))
    run("all")
    assert "無効" in run(old)
    approval = confirm_args(run("share"))
    assert count_shared(service) == 0
    assert "2 件をGlobal記憶へ採用" in run(approval)
    assert "候補が未表示" in run(approval)
    assert count_shared(service) == 2


@pytest.mark.parametrize("change", ["file", "revision", "purge"])
def test_changed_evidence_rolls_back_entire_batch(service, review, change):
    root, snap, command = review
    command.execute(service, snap, "")
    command.execute(service, snap, "all")
    approval = confirm_args(command.execute(service, snap, "share"))
    entry_id = command.review.items[-1]["entry"]["id"]
    if change == "file":
        (root / "config.json").write_text('{"language":"ja","port":9000}')
    elif change == "revision":
        service.explicit(snap, ExplicitCommand("correct", "changed", entry_id=entry_id, expected_revision=1))
    else:
        from hermes_kiokuko.operations import entry_review, purge
        purge(service, entry_id, entry_review(service, entry_id)[1])
    assert "共有は0件" in command.execute(service, snap, approval)
    assert count_shared(service) == 0


@pytest.mark.parametrize("change", ["session_id", "workspace_id", "principal_id", "profile_key", "session_generation", "expiry", "cancel"])
def test_old_confirmation_cannot_cross_context(service, review, change):
    _, snap, command = review
    command.execute(service, snap, "")
    command.execute(service, snap, "all")
    approval = confirm_args(command.execute(service, snap, "share"))
    if change == "expiry":
        command.review.expires = 0
    elif change == "cancel":
        assert "共有せず" in command.execute(service, snap, "cancel")
    else:
        snap = replace(snap, **{change: 99 if change == "session_generation" else "other"})
    assert "候補が未表示" in command.execute(service, snap, approval)
    assert count_shared(service) == 0


def test_busy_preserves_selection_and_requires_new_confirmation(service, review, monkeypatch):
    import hermes_kiokuko.slash_curation as module
    _, snap, command = review
    command.execute(service, snap, "")
    command.execute(service, snap, "select 1")
    approval = confirm_args(command.execute(service, snap, "share"))
    def busy(*args):
        raise KiokukoError("STORE_BUSY")
    monkeypatch.setattr(module, "adopt", busy)
    assert "選択は維持" in command.execute(service, snap, approval)
    assert "無効" in command.execute(service, snap, approval)
    assert command.review.selected == {1}
    assert count_shared(service) == 0
