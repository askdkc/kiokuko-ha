from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import threading

import pytest

from hermes_kiokuko.deliveries import prepare, sync_completed
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.models import ExplicitCommand, Identity
from hermes_kiokuko.operations import entry_review, purge, share


def history(raw, context):
    return [{"role": "user", "content": raw, "api_content": raw + "\n\n" + context}]


def test_users_dm_group_and_admin_share(service, make_turn):
    a = make_turn(session="A", who=Identity("telegram", "dm", "A", "dm-A", "workspace", "dm"))
    b = make_turn(session="B", who=Identity("telegram", "dm", "B", "dm-B", "workspace", "dm"))
    g = make_turn(session="G", who=Identity("telegram", "group_chat", "A", "group", "workspace", "group"))
    entry = service.explicit(a, ExplicitCommand("remember", "A only", "principal"))
    assert service.search(a, "only")
    assert service.search(b, "only") == []
    assert service.search(g, "only") == []
    for snapshot in (b, g):
        with pytest.raises(KiokukoError, match="ENTRY_UNAVAILABLE"):
            service.get(snapshot, entry["entry_id"], history=True)
    group = service.propose(g, {"claim": "group approved", "scope": "conversation"})
    _, approval = service.candidate_review(group["id"])
    service.approve(group["id"], approval)
    assert [r["claim"] for r in service.search(g, "group")] == ["group approved"]
    assert service.search(a, "group") == []
    _, token = entry_review(service, entry["entry_id"])
    share(service, entry["entry_id"], 1, token, "workspace", "workspace")
    assert service.search(b, "only")
    assert service.search(g, "only")


def test_quote_match_does_not_endorse_claim(service, make_turn):
    raw = "PostgreSQLへの移行案は却下した"
    snap = make_turn(raw)
    context = prepare(service, snap, raw)
    proposed = service.propose(snap, {"claim": "PostgreSQLを採用している", "evidence_quote": "PostgreSQL"})
    sync_completed(service, snap.session_id, raw, history(raw, context))
    review, token = service.candidate_review(proposed["id"])
    assert review["evidence"][0]["quote_verified"] == 1
    assert review["candidate"]["state"] == "pending"
    assert service.search(snap, "PostgreSQL") == []
    service.approve(proposed["id"], token)
    assert service.search(snap, "PostgreSQL")[0]["confirmation_kind"] == "cli_approved"


def test_approval_race(service, make_turn):
    candidate = service.propose(make_turn(), {"claim": "first claim"})
    _, token = service.candidate_review(candidate["id"])
    with service.transaction(write=True) as db:
        db.execute("UPDATE memory_candidates SET proposed_claim='different' WHERE id=?", (candidate["id"],))
    with pytest.raises(KiokukoError, match="APPROVAL_CHANGED"):
        service.approve(candidate["id"], token)


@pytest.mark.parametrize("query", ["continue", "yes", "unrelated weather", ""])
def test_correction_independent_of_query(service, make_turn, query):
    first = make_turn("original")
    saved = service.explicit(first, ExplicitCommand("remember", "old Japanese", "principal"))
    context = prepare(service, first, "Japanese")
    rows = history("original", context)
    sync_completed(service, first.session_id, "original", rows)
    service.explicit(make_turn("correct"), ExplicitCommand("correct", "new English", entry_id=saved["entry_id"], expected_revision=1))
    later = make_turn(query)
    corrected = prepare(service, later, query, rows)
    assert "KIOKUKO CORRECTION" in corrected
    assert "new English" in corrected and "old Japanese" not in corrected
    # Prepared-only notices remain repeatable.
    assert "KIOKUKO CORRECTION" in prepare(service, make_turn("continue"), "continue", rows)
    service.transition("branch", parent_session_id=first.session_id)
    assert "KIOKUKO CORRECTION" in prepare(service, make_turn(session="branch"), "continue", [])
    assert "KIOKUKO CORRECTION" not in prepare(service, make_turn(session="reset"), "continue", [])


def test_prepared_not_observed_and_forgery(service, make_turn):
    snap = make_turn("raw")
    context = prepare(service, snap, "raw")
    with service.transaction() as db:
        assert db.execute("SELECT state FROM retrieval_deliveries").fetchone()[0] == "prepared"
    with pytest.raises(KiokukoError, match="SYNC_CONTEXT_UNAVAILABLE"):
        sync_completed(service, snap.session_id, "raw", history("other raw", context))
    with pytest.raises(KiokukoError, match="SYNC_CONTEXT_UNAVAILABLE"):
        sync_completed(service, snap.session_id, "raw", history("raw", context.replace("Historical", "Forged")))
    sync_completed(service, snap.session_id, "raw", history("raw", context))
    with service.transaction() as db:
        assert db.execute("SELECT state FROM retrieval_deliveries").fetchone()[0] == "observed_in_history"
    old = history("raw", context) + [{"role": "user", "content": "new no marker"}]
    with pytest.raises(KiokukoError, match="SYNC_CONTEXT_UNAVAILABLE"):
        sync_completed(service, snap.session_id, "new no marker", old)


def test_rewind_and_snapshot_immutability(service, make_turn, identity):
    old = make_turn("old", turn="old")
    candidate = service.propose(old, {"claim": "pending"})
    newcwd = service.snapshot("session", "old", "old", replace(identity, workspace_id="ws-other"))
    assert newcwd.workspace_id == old.workspace_id
    assert make_turn(who=replace(identity, workspace_id="ws-other")).workspace_id == "ws-other"
    with pytest.raises(KiokukoError, match="TURN_CONTEXT_CONFLICT"):
        service.snapshot("session", "old", "different", identity)
    service.transition("session", rewound=True)
    with pytest.raises(KiokukoError, match="STALE_GENERATION"):
        service.propose(old, {"claim": "late"})
    assert service.candidate_review(candidate["id"])[0]["candidate"]["state"] == "invalidated_by_rewind"


def test_same_revision_concurrent_writers(service, make_turn):
    saved = service.explicit(make_turn(), ExplicitCommand("remember", "initial", "principal"))
    turns = [make_turn("one"), make_turn("two")]
    barrier = threading.Barrier(2)
    def change(index):
        barrier.wait()
        try:
            return service.explicit(turns[index], ExplicitCommand("correct", str(index), entry_id=saved["entry_id"], expected_revision=1))
        except KiokukoError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(change, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "REVISION_CONFLICT" in results


def test_delayed_old_session_has_own_ledger(service, make_turn):
    old = make_turn(session="old", who=Identity("telegram", "dm", "A", "A", "ws-a", "dm"))
    new = make_turn(session="new", who=Identity("telegram", "dm", "B", "B", "ws-b", "dm"))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda snap: service.propose(snap, {"claim": snap.principal_id, "scope": "principal_workspace"}), [new, old]))
    reviews = [service.candidate_review(row["id"])[0]["candidate"] for row in results]
    assert {(r["session_id"], r["principal_id"], r["workspace_id"]) for r in reviews} == {("old", "A", "ws-a"), ("new", "B", "ws-b")}


def test_same_turn_retry_after_purge_cannot_replay_old_context(service, make_turn):
    snap = make_turn()
    saved = service.explicit(snap, ExplicitCommand("remember", "purge me", "principal"))
    old = prepare(service, snap, "purge")
    _, token = entry_review(service, saved["entry_id"])
    purge(service, saved["entry_id"], token)
    retry = prepare(service, snap, "purge")
    assert retry != old and "purge me" not in retry
    assert "KIOKUKO CORRECTION" in retry
