from dataclasses import replace
import pytest
from hermes_kiokuko.deliveries import prepare, sync_completed
from hermes_kiokuko.errors import KiokukoError


def complete(service, snap, raw):
    context = prepare(service, snap, raw)
    sync_completed(service, snap.session_id, raw, [{'role':'user','content':raw,'api_content':raw+'\n\n'+context}])


@pytest.mark.parametrize('origin', ['cron','unknown','delegation','background_review'])
def test_nonhuman_capture_and_proposal_rejected(service, make_turn, identity, origin):
    raw = 'この設定を覚えて'
    snap = make_turn(raw, who=replace(identity, origin=origin))
    complete(service, snap, raw)
    with pytest.raises(KiokukoError, match='OPTIONAL_CAPTURE_UNAVAILABLE'):
        service.propose(snap, {'claim':raw,'evidence_quote':raw})
    with service.transaction() as db:
        assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 0


@pytest.mark.parametrize('raw', ['> この設定を覚えて', '```\nこの設定を覚えて\n```', '---\nname: skill\n---\nPlease remember this', '「この設定を覚えて」と書いてある', '設定を覚えて'+'あ'*600])
def test_document_fragments_never_passively_capture(service, make_turn, raw):
    complete(service, make_turn(raw), raw)
    with service.transaction() as db:
        assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 0


def test_pending_duplicate_across_turns_but_not_sessions(service, make_turn):
    raw = 'この設定を覚えて'
    for _ in range(2):
        complete(service, make_turn(raw), raw)
    complete(service, make_turn(raw, session='other'), raw)
    with service.transaction() as db:
        assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM memory_entries').fetchone()[0] == 0


def test_real_correction_and_rejected_candidate_can_be_reproposed(service, make_turn):
    raw = 'この設定を修正して'
    snap = make_turn(raw)
    complete(service, snap, raw)
    with service.transaction() as db:
        candidate = db.execute('SELECT id FROM memory_candidates').fetchone()[0]
    service.reject(candidate)
    complete(service, make_turn(raw), raw)
    with service.transaction() as db:
        assert dict(db.execute('SELECT state,count(*) FROM memory_candidates GROUP BY state')) == {'pending':1,'rejected':1}


def test_group_senders_not_deduplicated(service, make_turn, identity):
    raw = 'この設定を覚えて'
    for owner in ['alice','bob']:
        snap = make_turn(raw, who=replace(identity, origin='group_chat', chat_type='group', principal_id=owner))
        complete(service, snap, raw)
    with service.transaction() as db:
        assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 2
