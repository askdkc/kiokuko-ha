from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3

import pytest

from hermes_kiokuko.config import load_config, read_yaml, setup, write_yaml
from hermes_kiokuko.deliveries import prepare, sync_completed
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.models import ExplicitCommand, now
from hermes_kiokuko.operations import backup, entry_review, purge, restore, verify
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store


def test_setup_preserves_native_files_and_auxiliary(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "memories").mkdir()
    for name in ("MEMORY.md", "USER.md"):
        (home / "memories" / name).write_bytes(b"existing-user-owned-data\n")
    write_yaml(home / "config.yaml", {"auxiliary": {"custom": "preserve"}, "plugins": {"enabled": ["other"]}})
    setup(home)
    cfg = read_yaml(home / "config.yaml")
    assert cfg["memory"] == {"provider": "kiokuko", "memory_enabled": False, "user_profile_enabled": False}
    assert cfg["auxiliary"] == {"custom": "preserve"}
    assert cfg["plugins"]["enabled"] == ["other", "kiokuko-tools"]
    for name in ("MEMORY.md", "USER.md"):
        assert (home / "memories" / name).read_bytes() == b"existing-user-owned-data\n"


def test_purge_ownership_successor_and_retry(service, make_turn):
    snap = make_turn()
    original = service.explicit(snap, ExplicitCommand("remember", "PURGE_SENTINEL", "principal"))
    successor = service.explicit(make_turn("next"), ExplicitCommand("remember", "SURVIVOR", "principal"))
    candidate = service.propose(make_turn("candidate"), {"claim": "candidate content", "evidence_quote": "candidate"})
    _, approval = service.candidate_review(candidate["id"])
    promoted = service.approve(candidate["id"], approval)
    context = prepare(service, snap, "PURGE_SENTINEL")
    with service.transaction(write=True) as db:
        db.execute("UPDATE memory_entries SET supersedes_id=? WHERE id=?", (original["entry_id"], successor["entry_id"]))
        # Simulate an accepted candidate promoted to the target, with its own evidence.
        db.execute("UPDATE memory_candidates SET promoted_entry_id=? WHERE id=?", (original["entry_id"], candidate["id"]))
        delivery = db.execute("SELECT id FROM retrieval_deliveries WHERE turn_id=?", (snap.turn_id,)).fetchone()[0]
    service.feedback(snap, original["entry_id"], 1, delivery, "helpful")
    _, token = entry_review(service, original["entry_id"])
    purge(service, original["entry_id"], token)
    with service.transaction() as db:
        for table in ("memory_evidence", "memory_revisions", "memory_search_documents", "memory_ngrams", "memory_vectors", "retrieval_feedback", "retrieval_delivery_entries"):
            assert db.execute(f"SELECT count(*) FROM {table} WHERE entry_id=?", (original["entry_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM memory_candidates WHERE id=?", (candidate["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM memory_evidence WHERE candidate_id=?", (candidate["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT claim,supersedes_id FROM memory_entries WHERE id=?", (successor["entry_id"],)).fetchone()[:] == ("SURVIVOR", None)
        assert db.execute("SELECT rendered_sha256 FROM retrieval_deliveries WHERE id=?", (delivery,)).fetchone()[0] is None
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM memory_fts WHERE entry_id=?", (original["entry_id"],)).fetchone()[0] == 0
    with pytest.raises(KiokukoError, match="PURGED_OPERATION"):
        service.explicit(snap, ExplicitCommand("remember", "PURGE_SENTINEL", "principal"))
    with pytest.raises(KiokukoError, match="SYNC_CONTEXT_UNAVAILABLE"):
        sync_completed(service, snap.session_id, "hello", [{"role": "user", "content": "hello", "api_content": "hello\n\n" + context}])
    assert "CORRECTION" in prepare(service, make_turn("continue"), "continue")


def test_purge_rolls_back(service, make_turn, monkeypatch):
    snap = make_turn()
    saved = service.explicit(snap, ExplicitCommand("remember", "survives rollback", "principal"))
    prepare(service, snap, "rollback")
    _, token = entry_review(service, saved["entry_id"])
    real = service._invalidate
    def fail(*args):
        real(*args)
        raise RuntimeError("simulated disk failure")
    monkeypatch.setattr(service, "_invalidate", fail)
    with pytest.raises(RuntimeError):
        purge(service, saved["entry_id"], token)
    assert service.get(snap, saved["entry_id"])["claim"] == "survives rollback"
    with service.transaction() as db:
        assert not db.execute("SELECT * FROM memory_tombstones").fetchall()
        assert db.execute("SELECT count(*) FROM memory_fts").fetchone()[0] == 1


def test_evidence_xor_and_missing_revision(service):
    with service.transaction(write=True) as db:
        for values in ((None, None, None), ("missing", 1, None), (None, None, "missing"), ("both", 1, "both")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO memory_evidence(id,entry_id,entry_revision,candidate_id,source_role,source_kind,observed_at) VALUES ('bad',?,?,?,'user','test',?)", (*values, now()))


def test_backup_restore_live_holder_and_profile_key(service, make_turn, tmp_path):
    snap = make_turn()
    entry = service.explicit(snap, ExplicitCommand("remember", "backup value", "principal"))
    tmp_path.chmod(0o755)
    destination = tmp_path / "backup"
    backup(service, destination)
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert destination.stat().st_mode & 0o777 == 0o700
    with pytest.raises(KiokukoError, match="LIVE_HOLDER"):
        restore(service.store.home, destination)
    service.explicit(make_turn("changed"), ExplicitCommand("correct", "after backup", entry_id=entry["entry_id"], expected_revision=1))
    home = service.store.home
    service.store.close()
    restore(home, destination)
    reopened = Store(home)
    try:
        fresh = Service(reopened)
        assert fresh.get(snap, entry["entry_id"])["claim"] == "backup value"
        assert verify(fresh)["ok"]
    finally:
        reopened.close()


@pytest.mark.parametrize("damage,code", [("UPDATE schema_migrations SET checksum='bad'", "CHECKSUM_MISMATCH"),
                                         ("CREATE TABLE alien(data TEXT)", "SCHEMA_MISMATCH")])
def test_mismatch_does_not_repair(service, damage, code):
    db = sqlite3.connect(service.store.path)
    db.execute(damage)
    db.commit()
    db.close()
    before = service.store.path.read_bytes()
    with pytest.raises(KiokukoError, match=code):
        verify(service)
    assert service.store.path.read_bytes() == before


def test_corrupt_database_not_reset(tmp_path):
    setup(tmp_path)
    directory = tmp_path / "kiokuko"
    from hermes_kiokuko.filesystem import atomic_write
    atomic_write(directory / "identity.key", b"x" * 32)
    atomic_write(directory / "kiokuko.db", b"corrupt sentinel")
    with pytest.raises(KiokukoError):
        Store(tmp_path, initialize=True)
    assert (directory / "kiokuko.db").read_bytes() == b"corrupt sentinel"


def test_model_auto_promotion_config_cannot_be_enabled(service):
    path = service.store.directory / "config.yaml"
    cfg = read_yaml(path)
    cfg["promotion"]["from_model_proposal"] = True
    write_yaml(path, cfg)
    with pytest.raises(KiokukoError, match="UNSUPPORTED_CONFIG"):
        load_config(service.store.home)
