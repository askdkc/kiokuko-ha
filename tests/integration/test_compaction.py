import json
from pathlib import Path

import pytest

from hermes_kiokuko.compaction import compact
from hermes_kiokuko.curation import adopt, curate, review_all
from hermes_kiokuko.deliveries import prepare, sync_completed
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.facts import verify_predicate
from hermes_kiokuko.models import ExplicitCommand, Identity
from hermes_kiokuko.operations import entry_review, purge
from hermes_kiokuko.workspace import resolve_workspace


@pytest.fixture
def project(service, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.json").write_text('{"language":"ja","port":8000,"enabled":true}')
    who = Identity("cli", "cli", "profile-owner", "conversation", resolve_workspace(root), "dm")
    snap = service.snapshot("session", "turn", "config.json の設定を確認した。", who, workspace_root=root)
    context = prepare(service, snap, "settings")
    messages = [{"role": "user", "content": "config.json の設定を確認した。",
                 "api_content": "config.json の設定を確認した。\n\n" + context},
                {"role": "assistant", "content": "config.json language=ja, port=8000"}]
    sync_completed(service, "session", messages[0]["content"], messages)
    predicate = {"path": "config.json", "format": "json", "selector": ["language"], "value": "ja"}
    return root, snap, messages, predicate


def capture(service, project, predicates=None, reason="pre_compress"):
    _, _, messages, predicate = project
    return compact(service, messages, reason, extractor=lambda text: predicates if predicates is not None else [predicate])


def test_only_verified_predicates_activate_and_retry_is_idempotent(service, project):
    root, snap, messages, good = project
    bad = [dict(good, value="en"), dict(good, claim="Everything is secure"),
           {"path": "config.json", "format": "json", "selector": ["enabled"], "value": 1},
           dict(good, path="unmentioned.json")]
    result = capture(service, project, [good, *bad])
    assert len(result["accepted"]) == 1 and result["rejected"] == 4
    assert capture(service, project)["replayed"]
    entry = service.get(snap, result["accepted"][0])
    assert entry["epistemic_status"] == "file_verified"
    assert entry["confirmation_kind"] is None
    assert entry["scope_type"] == "principal_workspace"
    assert "ja" in service.search(snap, "language")[0]["claim"]
    assert not capture(service, project, reason="session_end")["accepted"]
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM memory_evidence").fetchone()[0] == 0


def test_stale_files_are_not_reused_and_notify_even_on_continue(service, project):
    root, snap, messages, _ = project
    entry_id = capture(service, project)["accepted"][0]
    prepare(service, snap, "language")
    (root / "config.json").write_text('{"language":"en"}')
    assert not service.search(snap, "language")
    context = prepare(service, snap, "continue", messages)
    assert entry_id in context and "expired" in context
    assert service.get(snap, entry_id)["state"] == "expired"


def test_fact_scope_and_project_ranking(service, project):
    _, snap, _, _ = project
    fact = capture(service, project)["accepted"][0]
    service.explicit(snap, ExplicitCommand("remember", "language note", "principal_workspace"))
    assert service.search(snap, "language")[0]["id"] == fact
    for principal, conversation, workspace, chat_type in [
            ("other", "conversation", snap.workspace_id, "dm"),
            (snap.principal_id, "group", snap.workspace_id, "group"),
            (snap.principal_id, "conversation", "another-project", "dm")]:
        other = service.snapshot(principal + workspace + chat_type, "turn", "query",
                                 Identity("cli", "cli", principal, conversation, workspace, chat_type))
        assert not service.search(other, "language")


def test_delayed_extraction_rewind_and_source_change(service, project):
    root, snap, messages, predicate = project
    def delayed(text):
        service.transition(snap.session_id, rewound=True)
        return [predicate]
    with pytest.raises(KiokukoError, match="STALE_GENERATION"):
        compact(service, messages, "pre_compress", extractor=delayed)
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0


def test_unsigned_and_uncompleted_history_are_never_captured(service, project):
    root, snap, messages, predicate = project
    with pytest.raises(KiokukoError, match="COMPACTION_CONTEXT_UNAVAILABLE"):
        compact(service, [{"role": "user", "content": "config.json"}], "session_end", extractor=lambda _: [predicate])
    with service.transaction(write=True) as db:
        db.execute("DELETE FROM turn_syncs")
    assert not capture(service, project)["accepted"]


def test_purge_cascades_and_capture_cannot_resurrect(service, project):
    _, snap, _, _ = project
    entry_id = capture(service, project)["accepted"][0]
    _, review = entry_review(service, entry_id)
    purge(service, entry_id, review)
    assert not capture(service, project, reason="session_end")["accepted"]
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM verified_facts").fetchone()[0] == 0
        assert db.execute("SELECT entry_id FROM fact_receipts").fetchone()[0] is None


def test_post_compact_uses_summary_but_verifies_actual_file(service, project):
    _, _, messages, predicate = project
    messages.insert(0, {"role": "user", "content": "config.json language is en", "_compressed_summary": True})
    result = compact(service, messages, "post_compress", extractor=lambda text: [dict(predicate, value="en")])
    assert result["accepted"] == [] and result["rejected"] == 1


def test_curation_keyboard_selection_validation_and_share(service, project):
    _, snap, _, _ = project
    source_id = capture(service, project)["accepted"][0]
    answers = iter(["s", "999", "1", "s", "", "s", "share"])
    outputs = []
    result = curate(service, snap, input_fn=lambda _: next(answers), output=outputs.append)
    assert len(result["adopted"]) == 1
    assert any("[ ] 1." in line for line in outputs)
    assert any("[x] 1." in line for line in outputs)
    assert any("全利用者" in line for line in outputs)
    assert any("選択は維持" in line for line in outputs)
    assert service.get(snap, source_id)["scope_type"] == "principal_workspace"
    target = service.get(snap, result["adopted"][0])
    assert target["scope_type"] == "profile" and target["shared_by_admin"] == 1
    assert snap.workspace_id in target["claim"]
    assert not review_all(service, snap, output=lambda _: None)[0]


@pytest.mark.parametrize("answer", ["q", EOFError, KeyboardInterrupt])
def test_curation_cancel_never_shares(service, project, answer):
    _, snap, _, _ = project
    capture(service, project)
    def read(_):
        if isinstance(answer, type):
            raise answer
        return answer
    assert curate(service, snap, input_fn=read, output=lambda _: None)["cancelled"]
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_entries WHERE scope_type='profile'").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["file", "revision", "purge"])
def test_curation_changed_review_is_atomic(service, project, change):
    root, snap, _, _ = project
    capture(service, project)
    items, _ = review_all(service, snap, output=lambda _: None)
    source_id = items[0]["entry"]["id"]
    if change == "file":
        (root / "config.json").write_text('{"language":"en"}')
    elif change == "revision":
        service.explicit(snap, ExplicitCommand("correct", "new content", entry_id=source_id, expected_revision=1))
    else:
        _, review = entry_review(service, source_id)
        purge(service, source_id, review)
    with pytest.raises(KiokukoError):
        adopt(service, snap, items)
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_entries WHERE scope_type='profile'").fetchone()[0] == 0


def test_file_verifier_does_not_follow_symlinks_or_promote_text_assertions(tmp_path):
    (tmp_path / "config.json").write_text('{"a":1}')
    (tmp_path / "link.json").symlink_to(tmp_path / "config.json")
    for path in ["../config.json", "/tmp/config.json", "link.json", ".env", "./config.json"]:
        with pytest.raises(KiokukoError):
            verify_predicate(str(tmp_path), {"path": path, "format": "json", "selector": ["a"], "value": 1})
    (tmp_path / "notes.md").write_text("The moon is cheese.\n")
    fact = verify_predicate(str(tmp_path), {"path": "notes.md", "format": "line", "selector": 1, "value": "The moon is cheese."})
    assert "記載:" in fact["claim"]  # A file-content observation, not endorsement of its assertion.


def test_source_change_during_extraction_rejected(service, project):
    root, _, messages, predicate = project
    def extract(text):
        (root / "config.json").write_text('{"language":"en"}')
        return [predicate]
    result = compact(service, messages, "session_end", extractor=extract)
    assert not result["accepted"] and result["rejected"] == 1


def test_batch_curation_failure_never_partially_shares(service, project):
    root, snap, _, predicate = project
    capture(service, project, [predicate, dict(predicate, selector=["port"], value=8000)])
    items, _ = review_all(service, snap, output=lambda _: None)
    assert len(items) == 2
    (root / "config.json").write_text('{"language":"ja","port":9000}')
    with pytest.raises(KiokukoError):
        adopt(service, snap, items)
    with service.transaction() as db:
        assert db.execute("SELECT count(*) FROM memory_entries WHERE scope_type='profile'").fetchone()[0] == 0


def test_curation_stale_review_refresh_preserves_valid_selection(service, project):
    root, snap, _, _ = project
    capture(service, project)
    outputs, calls = [], iter(["1", "s", "share", "q"])
    def read(prompt):
        answer = next(calls)
        if answer == "share":
            # Predicate remains true but the file changed after display.
            (root / "config.json").write_text('{"language":"ja","port":9000}')
        return answer
    result = curate(service, snap, input_fn=read, output=outputs.append)
    assert result["cancelled"]
    refreshed = next(i for i, text in enumerate(outputs) if "候補を更新します" in text)
    assert any("[x] 1." in line for line in outputs[refreshed:])


def test_secret_keys_are_not_accepted_as_facts(tmp_path):
    (tmp_path / "config.json").write_text('{"api_key":"something-private"}')
    with pytest.raises(KiokukoError, match="SECRET_REJECTED"):
        verify_predicate(str(tmp_path), {"path": "config.json", "format": "json", "selector": ["api_key"], "value": "something-private"})
