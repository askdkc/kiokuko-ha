#!/usr/bin/env python3
"""Compare frozen and new extraction on public fixtures; never edit a live store.

Model outputs need human labels before reporting semantic precision or rollout
acceptance. --score refuses incomplete labels; fixtures alone cannot certify auto.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'benchmarks'))
from corpus import cases, evidence
import legacy_extraction as legacy
from hermes_kiokuko.config import setup
from hermes_kiokuko.experiences import extract_model, PROMPT, validate_proposal
from hermes_kiokuko.experience_algorithms import windows, select_prepared, verify_ref
from hermes_kiokuko.models import canonical, now
from hermes_kiokuko import learning
from types import SimpleNamespace
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store
from hermes_kiokuko.model_job import model_job


def score(report):
    if report.get('dataset') != 'learning_cases-v1' or report.get('real_model') is not True:
        raise ValueError('A real-model report for learning_cases-v1 is required.')
    gold={c['id']:c for c in cases()}
    summaries={}
    for variant in ('legacy','new'):
        records=[r for r in report['results'] if r['variant']==variant]
        labels=[r['review'] for r in records]
        if len(records) != len(gold) or {r['case_id'] for r in records} != set(gold):
            raise ValueError('The acceptance gate requires all 60 cases for each variant.')
        if not records or any(v is None for label in labels for v in label.values()):
            raise ValueError('Complete every review label before scoring; automated checks do not establish semantic correctness.')
        required={'extracted_count','grounded_relevant_count','unsupported_success_count','duplicate_count','recovery_preserved','lesson_offered','lesson_applicable'}
        for record in records:
            label=record['review']
            if record['gold'] != gold[record['case_id']] or set(label)!=required:
                raise ValueError('Dataset or review fields differ from the fixed corpus.')
            for key in ('extracted_count','grounded_relevant_count','unsupported_success_count','duplicate_count'):
                if type(label[key]) is not int or not 0 <= label[key] <= 3:
                    raise ValueError('Review counts must be integers between 0 and 3.')
            if any(type(label[k]) is not bool for k in ('recovery_preserved','lesson_offered','lesson_applicable')):
                raise ValueError('Review decisions must be booleans.')
            if any(label[k]>label['extracted_count'] for k in ('grounded_relevant_count','unsupported_success_count','duplicate_count')):
                raise ValueError('Review counts exceed accepted extraction count.')
            if label['extracted_count'] != len(record['accepted']):
                raise ValueError('Extracted count must match the accepted outputs.')
            if label['lesson_offered'] != bool(record.get('lesson') and record['lesson']['offered']):
                raise ValueError('Do not edit the observed lesson delivery decision.')
        predicted=sum(label['extracted_count'] for label in labels)
        correct=sum(label['grounded_relevant_count'] for label in labels)
        offered=sum(label['lesson_offered'] for label in labels)
        applicable=sum(label['lesson_offered'] and label['lesson_applicable'] for label in labels)
        negatives=[r for r in records if not r['gold']['inject']]
        false_injections=sum(r['review']['lesson_offered'] for r in negatives)
        long=[r for r in records if r['gold']['scenario']=='long_recovery']
        tokens=[r['usage'] for r in records]
        summaries[variant]={
            'extraction_precision':correct/predicted if predicted else None,
            'lesson_precision':applicable/offered if offered else None,
            'false_injection_rate':false_injections/len(negatives) if negatives else None,
            'long_recovery_recall':sum(r['review']['recovery_preserved'] for r in long)/len(long) if long else None,
            'unsupported_success':sum(label['unsupported_success_count'] for label in labels),
            'duplicates':sum(label['duplicate_count'] for label in labels),
            'calls':sum(r['calls'] for r in records),
            'p95_seconds':sorted(r['seconds'] for r in records)[max(0,int(len(records)*.95)-1)],
            'input_tokens':sum(u['input_tokens'] for u in tokens) if all(u['input_tokens'] is not None for u in tokens) else None,
            'output_tokens':sum(u['output_tokens'] for u in tokens) if all(u['output_tokens'] is not None for u in tokens) else None}
    old,new=summaries['legacy'],summaries['new']
    values=(old['extraction_precision'],new['extraction_precision'],new['lesson_precision'],new['false_injection_rate'],old['long_recovery_recall'],new['long_recovery_recall'])
    passed=(all(v is not None for v in values) and new['unsupported_success']==0 and
            new['extraction_precision']>=old['extraction_precision'] and new['lesson_precision']>=.95 and
            new['false_injection_rate']<=.05 and new['long_recovery_recall']>old['long_recovery_recall'])
    return {'metrics':summaries,'semantic_gate_passed':passed,
            'auto_ready':bool(passed and report.get('boundary_suite_passed') is True),
            'note':'Scope isolation, deletion and host-path tests must separately pass. Human review labels are not model-generated.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,help='Explicit Hermes profile for the configured auxiliary model (read only).')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--score',type=Path,help='Report with human-filled review labels.')
    parser.add_argument('--limit',type=int,default=60)
    args=parser.parse_args()
    if args.score:
        print(json.dumps(score(json.loads(args.score.read_text())),ensure_ascii=False,indent=2));return
    if not args.home or not args.output:
        parser.error('--home and --output are required; model calls may be billed.')
    if not 1 <= args.limit <= len(cases()):parser.error('--limit must be between 1 and 60.')
    if args.output.exists():parser.error('Refusing to overwrite an existing report.')
    os.environ['HERMES_HOME']=str(args.home.resolve())
    results=[]
    with tempfile.TemporaryDirectory(prefix='kiokuko-eval-') as directory:
        home=Path(directory);setup(home);store=Store(home,initialize=True);service=Service(store)
        try:
            for case in cases()[:args.limit]:
                raw=evidence(case)
                for variant in ('legacy','new'):
                    parts=list(legacy.chunks(raw)) if variant=='legacy' else windows(raw)
                    prepared,proposals,usage,errors=[],[],[],[]
                    started=time.monotonic()
                    calls=0
                    for part in parts:
                        metric={}
                        try:
                            with model_job(service) as job:
                                calls+=1
                                response=job.call(lambda:extract_model(args.home.resolve(),part,prompt=legacy.PROMPT if variant=='legacy' else PROMPT,metrics=metric))
                            if not isinstance(response,list):raise ValueError('Expected an array')
                            proposals.extend(response)
                            for proposal in response:
                                try:
                                    if variant=='new':
                                        for ref in proposal.get('evidence',[]):verify_ref(ref,part)
                                    prepared.append((legacy.validate_proposal if variant=='legacy' else validate_proposal)(service,proposal,raw))
                                except Exception as error:errors.append(getattr(error,'code',type(error).__name__))
                        except Exception as error:errors.append(getattr(error,'code',type(error).__name__))
                        usage.append(metric)
                    selected=prepared[:3] if variant=='legacy' else select_prepared(prepared)
                    lesson_result=None
                    if variant=='new' and selected:
                        # Three explicitly synthetic sessions exercise the admission
                        # threshold; these are not observations of independent real users.
                        structure=selected[0][0]
                        if structure.get('schema_version')==2:
                            pool=[{'id':f"fixture-{case['id']}-{i}",'revision':1,'scope':['principal_workspace','fixture',None,'fixture'],
                                   'structure':structure,'allowed':learning.allowed_values(structure),
                                   'observations':[{'run_id':f'fixture-{i}','session_id':f'fixture-{i}','generation':1,'observed_at':now()}]}
                                  for i in range(3)]
                            payload={'version':learning.VERSION,'family':'fixture','experiences':pool,
                                     'required_conditions':structure['conditions'],'previous':None,'previous_revision':None}
                            metric={}
                            try:
                                with model_job(service) as job:
                                    calls+=1
                                    proposed=job.call(lambda:extract_model(args.home.resolve(),payload,prompt=learning.PROMPT,metrics=metric))
                                if proposed is not None:
                                    checked=learning.validate_draft(service,proposed,payload)
                                    stats,lifecycle=learning.statistics(checked,pool)
                                    db=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:{'structure_json':canonical(checked)}))
                                    offered=lifecycle=='active' and learning.matches(db,{'id':'fixture','current_revision':1},case['query'])
                                    lesson_result={'draft':checked,'state':lifecycle,'offered':offered,'stats':stats}
                            except Exception as error:
                                errors.append(getattr(error,'code',type(error).__name__))
                            usage.append(metric)
                    results.append({'case_id' :case['id'],'variant':variant,'gold':case,'calls':calls,
                        'seconds':time.monotonic()-started,
                        'usage':{k:sum(u[k] for u in usage) if usage and all(u.get(k) is not None for u in usage) else None for k in ('input_tokens','output_tokens')},
                        'errors':errors,'accepted':[s for s,_,_ in selected],'lesson':lesson_result,
                        'review':{'extracted_count':None,'grounded_relevant_count':None,'unsupported_success_count':None,
                                  'duplicate_count':None,'recovery_preserved':None,'lesson_offered':bool(lesson_result and lesson_result['offered']),'lesson_applicable':None}})
                    report={'dataset':'learning_cases-v1','baseline':'a99c1de','boundary_suite_passed':None,
                            'real_model':True,'results':results,
                            'lesson_evaluation':'Synthetic three-session admission probe using extracted evidence; not independent production outcomes. Human review of relevance and grounding is required.'}
                    # Each checkpoint replaces only this newly created report.
                    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                    print(f"{case['id']} {variant}: {len(selected)} accepted, {len(errors)} rejected/errors",flush=True)
        finally:store.close()


if __name__=='__main__':main()
