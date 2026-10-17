"""The hook budget includes snapshot time and keeps failures unsigned."""
import pytest


@pytest.mark.parametrize('elapsed,expires', [(.499, False), (.5, True), (.501, True)])
def test_hook_shares_500ms_budget_with_snapshot(host, logical_deadline_clock, monkeypatch, elapsed, expires):
    home, _ = host
    from hermes_kiokuko import runtime
    from hermes_kiokuko.provider import KiokukoMemoryProvider
    from hermes_kiokuko.turn_hook import pre_llm_call
    provider = KiokukoMemoryProvider()
    provider.initialize('deadline', hermes_home=str(home))
    service = runtime.current()
    original = service.snapshot
    def slow_snapshot(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        logical_deadline_clock.advance(elapsed)
        return snapshot
    monkeypatch.setattr(service, 'snapshot', slow_snapshot)
    try:
        result = pre_llm_call(session_id='deadline', turn_id='one', user_message='hello', platform='cli')
        assert ('<!--kiokuko:v1:' not in result['context']) == expires
        if expires:
            assert 'DEADLINE_EXCEEDED' in result['context']
        with service.transaction() as db:
            assert db.execute('SELECT count(*) FROM retrieval_deliveries').fetchone()[0] == (0 if expires else 1)
    finally:
        provider.shutdown()
