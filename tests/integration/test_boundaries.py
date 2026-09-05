from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import struct
import threading
import time

import pytest

from hermes_kiokuko.deliveries import prepare, record_manual_read, sync_completed
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.models import ExplicitCommand, Identity, now
from hermes_kiokuko.operations import entry_review, purge, share
from hermes_kiokuko.projections import commit_vector_result
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store


def _open_store_in_process(home):
    store = Store(home, initialize=True)
    try:
        with store.transaction() as db:
            return store.profile_key, store.db_id, db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    finally:
        store.close()


def test_concurrent_processes_initialize_one_identity(tmp_path):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_open_store_in_process, [tmp_path / "shared"] * 4))
    assert len(set(results)) == 1
    assert results[0][2] == 2


def test_late_worker_cannot_resurrect_purged_vector(service, make_turn):
    snap = make_turn()
    entry = service.explicit(snap, ExplicitCommand("remember", "pending embedding", "principal"))
    with service.transaction(write=True) as db:
        db.execute("INSERT INTO embedding_profiles VALUES ('model','hash',2)")
        db.execute("INSERT INTO jobs(id,kind,dedupe_key,entry_id,entry_revision,state,available_at,lease_id,lease_expires_at,created_at,updated_at) VALUES ('job','embed','dedupe',?,1,'leased',?,'lease','2999-01-01',?,?)",
                   (entry["entry_id"], now(), now(), now()))
    started, released = threading.Event(), threading.Event()
    def worker():
        started.set()
        assert released.wait(5)
        return commit_vector_result(service, "job", "lease", "model", struct.pack('<ff', .1, .2))
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(worker)
        assert started.wait(5)
        _, token = entry_review(service, entry["entry_id"])
        purge(service, entry["entry_id"], token)
        released.set()
        with pytest.raises(KiokukoError, match="STALE_JOB"):
            future.result(5)
    with service.transaction() as db:
        assert not db.execute("SELECT * FROM memory_vectors").fetchall()
        assert not db.execute("SELECT * FROM jobs").fetchall()
        assert not db.execute("SELECT * FROM memory_entries").fetchall()


def test_history_respects_revision_scope_after_sharing(service, make_turn):
    a = make_turn(session="A", who=Identity("telegram", "dm", "A", "A", "workspace", "dm"))
    b = make_turn(session="B", who=Identity("telegram", "dm", "B", "B", "workspace", "dm"))
    entry = service.explicit(a, ExplicitCommand("remember", "private old text", "principal"))
    update = make_turn("correct", session="A", who=Identity("telegram", "dm", "A", "A", "workspace", "dm"))
    service.explicit(update, ExplicitCommand("correct", "approved shared text", entry_id=entry["entry_id"], expected_revision=1))
    _, token = entry_review(service, entry["entry_id"])
    share(service, entry["entry_id"], 2, token, "profile")
    history = service.get(b, entry["entry_id"], history=True)
    assert [row["revision"] for row in history] == [3]
    assert "private old text" not in str(history)


def test_manual_read_is_corrected_even_without_auto_delivery(service, make_turn):
    snap = make_turn()
    entry = service.explicit(snap, ExplicitCommand("remember", "manual old", "principal"))
    result = service.get(snap, entry["entry_id"])
    record_manual_read(service, snap, result)
    service.explicit(make_turn("correct"), ExplicitCommand("correct", "manual new", entry_id=entry["entry_id"], expected_revision=1))
    assert "KIOKUKO CORRECTION" in prepare(service, make_turn("yes"), "yes")


def test_large_invalidation_set_uses_fence(service, make_turn):
    read = make_turn("read")
    entries = []
    for index in range(50):
        saved = service.explicit(make_turn(str(index)), ExplicitCommand("remember", f"value {index}", "principal"))
        entries.append(service.get(read, saved["entry_id"]))
    record_manual_read(service, read, entries)
    service.explicit(make_turn("save anchor"), ExplicitCommand("remember", "stable anchor", "principal"))
    original = make_turn("stable anchor")
    old_context = prepare(service, original, "stable anchor")
    old_history = [{"role": "user", "content": "stable anchor", "api_content": "stable anchor\n\n" + old_context}]
    for entry in entries:
        service.explicit(make_turn("forget"), ExplicitCommand("forget", entry_id=entry["id"], expected_revision=1))
    context = prepare(service, make_turn("continue"), "continue", old_history, deadline=time.monotonic()+2)
    assert "All Kiokuko entries" in context and len(context) <= 2200
    history = old_history + [{"role": "user", "content": "continue", "api_content": "continue\n\n" + context}]
    # The fence invalidated the anchor's old context too; do not suppress a fresh recall.
    assert "stable anchor" in prepare(service, make_turn("stable anchor"), "stable anchor", history)


def test_passive_capture_completed_only_and_idempotent(service, make_turn):
    raw = "この設定を覚えて"
    snapshot = make_turn(raw)
    context = prepare(service, snapshot, raw)
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_candidates").fetchone()[0] == 0
    rows = [{"role": "user", "content": raw, "api_content": raw+'\n\n'+context}]
    sync_completed(service, snapshot.session_id, raw, rows)
    sync_completed(service, snapshot.session_id, raw, rows)
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_candidates").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0


def test_profile_isolation(service, make_turn, tmp_path):
    first = service.explicit(make_turn(), ExplicitCommand("remember", "profile one", "principal"))
    second_store = Store(tmp_path / "another", initialize=True)
    try:
        second = Service(second_store)
        snapshot = second.snapshot("session", "turn", "profile one", Identity("cli", "cli", "profile-owner", "conv", None, "dm"))
        assert second.search(snapshot, "profile one") == []
        with pytest.raises(KiokukoError, match="ENTRY_UNAVAILABLE"):
            second.get(snapshot, first["entry_id"])
    finally:
        second_store.close()


def test_no_fts_falls_back_to_ngrams(tmp_path, monkeypatch):
    original = sqlite3.connect
    class WithoutFTS(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.lstrip().startswith("CREATE VIRTUAL TABLE"):
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=WithoutFTS))
    store = Store(tmp_path / "without-fts", initialize=True)
    try:
        service = Service(store)
        snap = service.snapshot("s", "t", "日本語", Identity("cli", "cli", "profile-owner", "conv", None, "dm"))
        service.explicit(snap, ExplicitCommand("remember", "日本語で返答する", "principal"))
        assert service.search(snap, "日本語")
        with service.transaction() as db:
            assert db.execute("SELECT value FROM store_metadata WHERE key='fts'").fetchone()[0] == '0'
    finally:
        store.close()


def test_conflicting_approved_subjects_do_not_auto_inject(service, make_turn):
    snapshot = make_turn()
    for claim in ("Use PostgreSQL", "Use SQLite"):
        pending = service.propose(snapshot, {"claim": claim, "subject_key": "database"})
        _, token = service.candidate_review(pending["id"])
        service.approve(pending["id"], token)
    assert service.search(snapshot, "database") == []
    assert len(service.search(snapshot, conflicts=True)) == 2
