"""Compare observable results with the frozen pre-optimization implementation."""
import importlib.util
from pathlib import Path

import pytest

from hermes_kiokuko.models import ExplicitCommand, Identity
from hermes_kiokuko.retrieval import search

spec = importlib.util.spec_from_file_location('legacy_retrieval', Path(__file__).parents[2] / 'benchmarks/legacy_retrieval.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


@pytest.mark.parametrize('fts', [True, False])
@pytest.mark.parametrize('query', ['', '日本', '型', 'Widget', 'alpha beta', 'no-match', '"quoted"'])
def test_results_equal_across_scope_rank_and_eligibility(service, make_turn, fts, query):
    for i in range(90):
        who = Identity('cli','cli','profile-owner' if i % 3 else 'someone-else', 'conv', 'ws-local', 'dm')
        snap = make_turn(f'entry {i}', session=f's-{i}', who=who)
        result = service.explicit(snap, ExplicitCommand('remember', ['日本語の型 Widget', 'alpha beta', 'unrelated'][i%3] + f' {i}', 'principal_workspace'))
        with service.transaction(write=True) as db:
            db.execute('UPDATE memory_entries SET pinned=?,authority=?,confidence=?,valid_until=? WHERE id=?',
                       (int(i%13==0),70+i%30,.9 if i%7 else .5,'2000-01-01' if i%11==0 else None,result['entry_id']))
    snap = make_turn(query, session='search')
    with service.transaction(write=True) as db:
        if not fts: db.execute("UPDATE store_metadata SET value='0' WHERE key='fts'")
        actual = search(db,snap,query,service.config)
        expected = legacy.search(db,snap,query,service.config)
        assert [(e['id'],e['current_revision'],e['lexical_score']) for e in actual] == [(e['id'],e['current_revision'],e['lexical_score']) for e in expected]
