"""Compare original hourly and batched history calls on identical real inputs.

This is an isolated scientific regression, not a production result or a change
to the delivered equations. The sole variant is route_with_history from Git.
"""
import argparse
import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time
import types
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace',required=True)
parser.add_argument('--variant',choices=['hourly','batch'],required=True)
parser.add_argument('--case',choices=['single','events','calibration','original'],default='single')
parser.add_argument('--reference',default='8316a6f')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1];runtime=Path(args.workspace).resolve()
if not runtime.is_relative_to(root/'开发产物'):raise ValueError('Use an isolated workspace under 开发产物')
os.environ['XAJ_RUNTIME_DIR']=str(runtime);os.environ['PYTHONUTF8']='1'
sys.stdout.reconfigure(encoding='utf-8')
sys.path[:0]=[str(root/'backend'),str(root/'backend/tests')]
import worker
import numpy as np
from common import read_json,write_json
from contracts import normalize_request
from catalog import case_catalog
from test_task_history_real import simulation_request
from test_unified_backend import calibration
from original_calibration_plan import original_plan_payload
if args.variant=='hourly':
    source=subprocess.check_output(['git','show',args.reference+':backend/worker.py'],cwd=root).decode('utf-8')
    tree=ast.parse(source);cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='ModelJob')
    method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='route_with_history')
    scope=dict(worker.__dict__);exec(compile(textwrap.dedent(ast.get_source_segment(source,method)),'<original-route-with-history>','exec'),scope)
    worker.ModelJob.route_with_history=scope['route_with_history']
if args.case=='original':
    candidates=[read_json(p) for p in (runtime/'parameters').glob('*.json')]
    candidate=next(row for row in candidates if row.get('originalCalibrationPlan') and row.get('candidateKind')=='frozen-calibration')
    request=original_plan_payload(candidate['parameterVersionId'],runtime,operation='fixed-replay')['request']
    requests=[request]
elif args.case=='calibration':
    request=calibration();request['inputs']['evaporation']={'mode':'monthly-climatology-assumption'}
    request['calibration']['optimizer'].update(algorithm='DIFFERENTIAL_EVOLUTION',maxEvaluations=120,populationSize=12,seed=42,strategy='joint')
    requests=[normalize_request(request,case_catalog())]
else:
    requests=[simulation_request()]
    if args.case=='events':requests.append(simulation_request(start='2017-06-19T00:00',end='2017-06-21T23:00'))
report={'case':args.case,'variant':args.variant,'scope':'ModelJob constructor + frozen input reads + complete numeric run + history/checkpoint/result persistence; input preparation measured separately','firstRunPolicy':'new process; shared on-disk compiler cache may already exist; not an empty-cache comparison','runs':[]}
for repeat in range(2):
    started=time.perf_counter();outputs=[];tasks=[]
    for index,request in enumerate(requests):
        task_id=f'JOB-benchmark-{args.case}-{args.variant}-{repeat}-{index}'
        folder=runtime/'tasks'/task_id
        write_json(folder/'request.json',request)
        write_json(folder/'status.json',{'taskId':task_id,'status':'running','runConfig':request})
        job=worker.ModelJob(folder);job.run()
        write_json(folder/'status.json',{'taskId':task_id,'status':'completed','runConfig':job.request})
        result=read_json(folder/'result.json');outputs.append(result);tasks.append(task_id)
        if request.get('runType')=='calibration':
            from hydrological_operations import HydrologicalOperations
            from science_context import ScienceVersions
            from input_products import InputProducts
            from types import SimpleNamespace
            operations=object.__new__(HydrologicalOperations)
            operations.manager=SimpleNamespace(root=runtime/'tasks',runtime_root=runtime,science_versions=ScienceVersions(runtime),products=InputProducts(runtime_root=runtime))
            operations.cancelled=set()
            child_count=[0]
            def child(_identifier,child_request,_stage,_role):
                child_count[0]+=1
                child_id=task_id+'-state-'+str(child_count[0]);child_folder=runtime/'tasks'/child_id
                write_json(child_folder/'request.json',child_request)
                write_json(child_folder/'status.json',{'taskId':child_id,'status':'running','runConfig':child_request})
                child_job=worker.ModelJob(child_folder);child_job.run()
                write_json(child_folder/'status.json',{'taskId':child_id,'status':'completed','runConfig':child_job.request})
                tasks.append(child_id)
                return read_json(child_folder/'result.json')
            operations.child=child
            finalized=operations.final_states('OP-benchmark',result,job.request)
            outputs[-1]=finalized
    report['runs'].append({'cache':'process-first-call' if repeat==0 else 'warm-in-process','seconds':round(time.perf_counter()-started,4),
        'tasks':tasks,'timings':[row.get('timings') for row in outputs]})
    write_json(runtime/f'benchmark-{args.case}-{args.variant}.json',report)
print(json.dumps(report,ensure_ascii=False),flush=True)
