#!/usr/bin/env python3
"""Measure a disposable synthetic store, never a user profile or chat archive."""
import argparse
import json
import math
from pathlib import Path
import platform
import resource
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'benchmarks')]
from hermes_kiokuko.config import setup,load_config,write_yaml
from hermes_kiokuko.service import Service
from hermes_kiokuko.store import Store
from hermes_kiokuko.retrieval import search
from hermes_kiokuko.deliveries import prepare
from profile_corpus import populate
import legacy_retrieval


def summary(values):
    return {'p50_ms':statistics.median(values),'p95_ms':sorted(values)[math.ceil(len(values)*.95)-1]}


def run(entries=10000,profiles=1000,samples=10):
    report={'synthetic':True,'seed':0,'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,
            'platform':platform.system()+' '+platform.machine(),'entries':entries,'profiles':profiles,'samples':samples,
            'cache':'first sample reported separately; later samples warm, OS cache not flushed','search':{},'prepare':{}}
    with tempfile.TemporaryDirectory(prefix='kiokuko-profile-bench-') as directory:
        base=Path(directory)
        setup(base/'home')
        store=Store(base/'home',initialize=True)
        try:
            service=Service(store)
            who=populate(service,base/'workspace',entries,profiles)
            snap=service.snapshot('reader','search','needle',who,workspace_root=base/'workspace')
            for name,fn in [('legacy',legacy_retrieval.search),('scoped',search)]:
                durations=[]
                counts=[]
                results=[]
                for _ in range(samples):
                    with service.transaction() as db:
                        statements=[]
                        db.set_trace_callback(lambda sql:statements.append(sql.split()[0]))
                        start=time.perf_counter()
                        result=fn(db,snap,'needle',service.config)
                        durations.append((time.perf_counter()-start)*1000)
                        counts.append(len(statements))
                        results=[r['id'] for r in result]
                report['search'][name]={**summary(durations),'first_ms':durations[0],'sql_count':counts[-1],'result_count':len(results)}
                if name=='legacy': baseline=results
                else: assert results==baseline,'Search results changed'
            for mode in ('off','shadow','suggest','resolve'):
                config=load_config(store.home)
                config['task_profile_memory']['mode']=mode
                write_yaml(store.directory/'config.yaml',config)
                timings=[]
                rendered=[]
                for i in range(samples):
                    query='Inspect `needle.py`'
                    snap=service.snapshot(f'reader-{mode}',str(i),query,who,workspace_root=base/'workspace')
                    metrics={}
                    context=prepare(service,snap,query,deadline=time.monotonic()+2,metrics=metrics)
                    timings.append(metrics)
                    rendered.append('Resolved target reference:' in context)
                    if mode == 'resolve':
                        assert rendered[-1], 'Synthetic exact target did not reach the delivery'
                report['prepare'][mode]={**summary([m['total_ms'] for m in timings]),'first_ms':timings[0]['total_ms'],
                    'last_stages':timings[-1],'resolved_count':sum(rendered)}
            report['max_rss_platform_units']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        finally:store.close()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entries',type=int,default=10000)
    parser.add_argument('--profiles',type=int,default=1000)
    parser.add_argument('--samples',type=int,default=10)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/profile-memory-benchmark.json')
    args=parser.parse_args()
    if not(1<=args.entries<=100000 and 1<=args.profiles<=10000 and 2<=args.samples<=100):
        parser.error('entries 1..100000, profiles 1..10000, samples 2..100 required')
    result=run(args.entries,args.profiles,args.samples)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
