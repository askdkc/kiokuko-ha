"""Synthetic, public execution fixtures with explicitly annotated outcomes.

These validate evidence mechanics. They do not certify free-form model reasoning.
"""
import json
from pathlib import Path

from hermes_kiokuko.models import canonical


def cases():
    return json.loads(Path(__file__).with_name('learning_cases.json').read_text())


def evidence(case):
    result=[]
    def add(kind,text,observation=None):
        result.append({'run_id':case['id'],'seq':len(result)+1,'type':kind,
                       'actor':'user' if kind=='note' else 'agent' if kind=='tool.call' else 'tool',
                       'tool':'terminal' if kind.startswith('tool.') else '',
                       'observation':observation,'text':text})
    if case['scenario']=='long_recovery':
        for i in range(4):add('note',f'Unrelated earlier task {i}: '+('context '*1300))
    add('note',case['condition'])
    multiple=case['scenario'] in {'recovery','long_recovery','different_check','different_cwd'}
    call={'command':case['target']}
    if case['scenario']=='different_cwd':call['cwd']='project-a'
    add('tool.call',canonical(call),1)
    payload={'exit_code':1 if multiple or case['scenario']=='failure' else 0,'stdout':'check completed'}
    if case['scenario']=='missing_exit':payload.pop('exit_code')
    if case['scenario']=='boolean_exit':payload['exit_code']=False
    if case['scenario']=='string_exit':payload['exit_code']='0'
    if case['scenario']=='conflicting_exit':payload['returncode']=1
    add('tool.result',canonical(payload),1)
    if multiple:
        call={'command':case['target'] if case['scenario']!='different_check' else 'echo unrelated check'}
        if case['scenario']=='different_cwd':call['cwd']='project-b'
        add('tool.call',canonical(call),2)
        add('tool.result',canonical({'exit_code':0,'stdout':'check completed'}),2)
    return result


def addressed_proposal(case,events):
    calls=[e for e in events if e['type']=='tool.call']
    results=[e for e in events if e['type']=='tool.result']
    note=next(e for e in events if e['type']=='note' and e['text']==case['condition'])
    refs=[{'seq':note['seq'],'quote':case['condition'],'start':0},
          {'seq':calls[0]['seq'],'quote':case['target'],'start':calls[0]['text'].index(case['target'])},
          {'seq':results[-1]['seq'],'quote':'check completed','start':results[-1]['text'].index('check completed')}]
    return {'situation':case['condition'],'observation':'check completed','action':case['target'],
            'outcome':'success','inference':'結果の因果関係は未確認' if case['language']=='ja' else 'Causality is unverified.',
            'span':{'start':note['seq'],'end':results[-1]['seq']},'evidence':refs,
            'conditions':[{'value':case['condition'],'evidence':0}],
            'field_evidence':{'situation':[0],'action':[1],'observation':[2]},
            'attempts':[{'call_seq':c['seq'],'result_seq':r['seq'],'target':json.loads(c['text'])['command']} for c,r in zip(calls,results)]}
