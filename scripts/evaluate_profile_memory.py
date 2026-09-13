#!/usr/bin/env python3
"""Deterministic resolver evaluation; not a real-model task-success benchmark."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hermes_kiokuko.profile_resolver import Candidate,decide


def evaluate():
    rows=[]
    for mode in ('shadow','suggest','resolve'):
        for complete in (True,False):
            for scenario in ('unique','ambiguous','stale','lexical_only','missing'):
                first=Candidate('source',0,'src/widget.py',100,scenario!='lexical_only',scenario!='stale')
                candidates=[] if scenario=='missing' else [first]
                if scenario=='ambiguous': candidates.append(Candidate('other',0,'tests/widget.py',1,True,True))
                result=decide(candidates,['widget.py'],complete=complete,mode=mode)
                adopted=any(d.action=='adopt' for d in result)
                expected=(mode=='resolve' and complete and scenario=='unique')
                rows.append({'mode':mode,'complete':complete,'scenario':scenario,'adopted':adopted,'expected_adoption':expected,
                             'targets':[d.candidate.target for d in result]})
    errors=sum(r['adopted']!=r['expected_adoption'] for r in rows)
    return {'dataset':'profile-resolver-v1','synthetic':True,'cases':len(rows),'errors':errors,
            'false_adoptions':sum(r['adopted'] and not r['expected_adoption'] for r in rows),
            'note':'Finite decision fixtures only. Scope, delivery and real-model outcomes require separate checks.','results':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/profile-memory-evaluation.json')
    args=parser.parse_args()
    report=evaluate()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    return int(report['errors']>0)


if __name__=='__main__':raise SystemExit(main())
