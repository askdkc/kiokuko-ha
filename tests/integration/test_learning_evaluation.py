"""The reporting gate must never label incomplete/synthetic reviews as acceptance."""
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('evaluate_learning',Path(__file__).parents[2]/'scripts/evaluate_learning.py')
evaluator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def annotated_report():
    records=[]
    for variant in ('legacy','new'):
        for case in evaluator.cases():
            offered=variant=='new' and case['inject']
            records.append({'case_id':case['id'],'gold':case,'variant':variant,
                'accepted':[{'fixture':True}],'lesson':{'offered':offered} if variant=='new' else None,
                'calls':1,'seconds':.1,'usage':{'input_tokens':None,'output_tokens':None},
                'review':{'extracted_count':1,'grounded_relevant_count':1,
                          'unsupported_success_count':0,'duplicate_count':0,
                          'recovery_preserved':variant=='new','lesson_offered':offered,'lesson_applicable':case['inject']}})
    # This is deliberately constructed test data, not an actual quality report.
    return {'dataset':'learning_cases-v1','real_model':True,'boundary_suite_passed':None,'results':records}


def test_semantic_labels_alone_do_not_certify_boundary_suite():
    report=annotated_report()
    result=evaluator.score(report)
    assert result['semantic_gate_passed'] and not result['auto_ready']
    assert result['metrics']['new']['input_tokens'] is None
    report['boundary_suite_passed']=True
    assert evaluator.score(report)['auto_ready']


@pytest.mark.parametrize('change', ['incomplete','synthetic','duplicate_case','bad_count','bool_count','gold_change','delivery_change','negative_count'])
def test_incomplete_or_tampered_report_refused(change):
    report=annotated_report()
    record=report['results'][-1]
    if change=='incomplete':record['review']['grounded_relevant_count']=None
    elif change=='synthetic':report['real_model']=False
    elif change=='duplicate_case':report['results'].append(record)
    elif change=='bad_count':record['review']['grounded_relevant_count']=2
    elif change=='bool_count':record['review']['extracted_count']=True
    elif change=='gold_change':record['gold']={**record['gold'],'inject':True}
    elif change=='delivery_change':record['review']['lesson_offered']=True
    elif change=='negative_count':record['review']['unsupported_success_count']=-1
    with pytest.raises(ValueError):evaluator.score(report)
