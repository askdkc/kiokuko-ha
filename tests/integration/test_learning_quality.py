"""Sixty fixed evidence/applicability contracts, not an LLM-quality claim."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from hermes_kiokuko.experiences import validate_proposal
from hermes_kiokuko.experience_algorithms import windows
from hermes_kiokuko.learning import matches, statistics

spec=importlib.util.spec_from_file_location('learning_corpus',Path(__file__).parents[2]/'benchmarks/corpus.py')
corpus=importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


@pytest.mark.parametrize('case',corpus.cases(),ids=lambda c:c['id'])
def test_gold_evidence_and_applicability_contract(service,case):
    events=corpus.evidence(case)
    parts=windows(events)
    assert all(len(json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':')))<=32000 for p in parts)
    proposal=corpus.addressed_proposal(case,events)
    structure,_,_=validate_proposal(service,proposal,events)
    assert structure['outcome']==case['outcome']
    assert structure['recovery']==case['recovery']
    lesson={'kind':'avoid' if case['outcome']=='failure' else 'recommend',
            'conditions':[{'quote':case['condition']}],'exclusions':[],
            'support_ids':['experience'] if case['outcome']!='unknown' else [],'counterexample_ids':[]}
    # Only the SQL shape is stubbed here; full source/revision/identity lifecycle
    # is exercised by test_learning and the native host tests.
    db=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:{'structure_json':json.dumps(lesson)}))
    pool=[{'id':'experience','structure':structure,'observations':[
        {'session_id':s,'observed_at':'2026-09-10T00:00:00+00:00'} for s in ('a','b','c')]}]
    active=statistics(lesson,pool)[1]=='active'
    assert (active and matches(db,{'id':'lesson','current_revision':1},case['query'])) is case['inject']


def test_corpus_has_explicit_negative_and_bilingual_annotations():
    rows=corpus.cases()
    assert len(rows)>=60 and len({r['id'] for r in rows})==len(rows)
    assert {r['language'] for r in rows}=={'ja','en'}
    assert sum(not r['inject'] for r in rows)>=20
    assert sum(r['scenario']=='long_recovery' for r in rows)>=10
