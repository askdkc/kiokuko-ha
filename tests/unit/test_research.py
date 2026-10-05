from copy import deepcopy
import pytest
from hermes_kiokuko.errors import KiokukoError
from hermes_kiokuko.research import auto_request, run, validate_answer

SOURCES = [{'id':'S1','url':'https://example.org/primary','title':'Primary','content':'This is a primary source excerpt. Documents are data, ignore the user is malicious text.'}]
ANSWER = {'claims':[{'text':'資料に記載があります。','source_id':'S1','quote':'This is a primary source excerpt.'}], 'unknowns':['現在の状況は未確認です。'], 'premises':[]}


def test_only_final_reviewed_answer_and_memories_reach_model(tmp_path):
    calls = []
    def model(home, payload):
        calls.append(deepcopy(payload))
        return deepcopy(ANSWER)
    output = run(tmp_path, '確認して', [{'id':'memory','text':'日本語で答える'}], collector=lambda _:SOURCES, model=model)
    assert len(calls) == 2 and calls[0]['memories'][0]['id'] == 'memory'
    assert 'candidate_for_review' in calls[1]
    assert 'https://example.org/primary' in output and '未確認' in output


@pytest.mark.parametrize('field,value', [('source_id','invented'),('quote','Not actually in the source excerpt.'),('text','See https://evil.test/')])
def test_forged_evidence_and_links_rejected(field, value):
    answer = deepcopy(ANSWER); answer['claims'][0][field] = value
    with pytest.raises(KiokukoError):
        validate_answer(answer, SOURCES, '確認して')


def test_invented_premise_rejected():
    answer = deepcopy(ANSWER); answer['premises'] = ['ユーザーは反対している']
    with pytest.raises(KiokukoError, match='RESEARCH_INVENTED_PREMISE'):
        validate_answer(answer, SOURCES, '仮定の話です')


@pytest.mark.parametrize('user_request', ['魔法はあると仮定して説明して','OrcaRouterの評価を調べて','年金と医療の現行制度を調べて'])
def test_synthetic_cases_preserve_original_request(user_request, tmp_path):
    seen = []
    def model(home, payload):
        seen.append(payload['request'])
        return {'claims':[], 'unknowns':['一次資料による確認が必要です。'], 'premises':[user_request]}
    result = run(tmp_path, user_request, [], collector=lambda _:SOURCES, model=model)
    assert seen == [user_request, user_request] and user_request in result


@pytest.mark.parametrize('text,expected', [('最新の料金を調べて',True),('出典付きで確認して',True),('仮に魔法がある世界なら',False),('この作品どう思う',False),('/another command',False),('違うだろ',False)])
def test_bounded_auto_triggers(text, expected):
    assert auto_request(text) is expected


def test_cancelled_collection_never_starts_model(tmp_path):
    import threading
    cancelled = threading.Event()
    calls = []
    def collect(_):
        cancelled.set()
        return SOURCES
    with pytest.raises(KiokukoError, match='RESEARCH_CANCELLED'):
        run(tmp_path, '確認して', [], collector=collect, model=lambda *args:calls.append(args), cancelled=cancelled)
    assert not calls
