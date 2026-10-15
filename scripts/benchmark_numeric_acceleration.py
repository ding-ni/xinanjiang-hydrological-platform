"""Same-data Python/JIT process, score and final-state benchmark; no search."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', required=True)
parser.add_argument('--request', required=True)
parser.add_argument('--output', default='开发产物/numeric-acceptance')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
os.environ['XAJ_RUNTIME_DIR'] = str(Path(args.workspace).resolve())
sys.path.insert(0, str(root / 'backend'))
from common import read_json, write_json, clean
from worker import ModelJob, kernels
from calibration_groups import group_windows
from runoff_platform.models import xaj_vectorized as xaj
from calibration_platform.routing_kernel import python_route_arrays
import numpy as np
import pandas as pd
import numba

out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=True)
request = read_json(Path(args.request))
request.pop('scheme', None)
for key in ('sourceBundleVersionId', 'preparationTaskId'):
    request['inputs'].pop(key, None)
folder = out / 'JOB-benchmark'
write_json(folder / 'request.json', request)
write_json(folder / 'status.json', {'taskId': folder.name})
job = ModelJob(folder)
groups = group_windows(request['period']['events'])
origin = pd.Timestamp(request['calibration']['continuousOrigin'])
report = {'requestFile': str(Path(args.request).resolve()), 'inputVersion':job.bundle_version,
          'numbaVersion':numba.__version__, 'numpyVersion':np.__version__, 'conditions':[],
          'scope':'forward runoff/routing only; excludes IO, optimizer and state-history persistence; unchanged parameters and science'}
for name, windows, continuous in (
    ('single-event', groups[:1], False), ('independent-events', groups, False),
    ('original-continuous-origin', [{'id':'trajectory','start':min(g['start'] for g in groups), 'end':max(g['end'] for g in groups)}], True)):
    prepared_at = time.perf_counter()
    events = []
    for window in windows:
        start, end = pd.Timestamp(window['start']), pd.Timestamp(window['end'])
        warmup = int((start-origin).total_seconds()/3600) if continuous else round(request['period']['warmupDays']*24)
        event = job.load([{'event_id':window['id'], 'year':start.year, 'start':start, 'end':end, 'group':'CALIBRATION'}], warmup)[0]
        event.pet_mm_h = job.pet_provider.values(event.times)
        events.append(event)
    loading = time.perf_counter()-prepared_at
    def run():
        return [kernels.route_event_fast(event,job.params,job.routing,job.router,job.mapping,job.initial_state(job.params)) for event in events]
    with patch.object(xaj,'_array_kernel',xaj._python_array_kernel), patch.object(kernels,'route_arrays',python_route_arrays):
        started=time.perf_counter(); reference=run(); python_seconds=time.perf_counter()-started
    # New dispatchers guarantee that this timing includes compilation, even if
    # another worker has already populated the application's on-disk cache.
    with patch.object(xaj,'_array_kernel',numba.njit(cache=False)(xaj._python_array_kernel)), patch.object(kernels,'route_arrays',numba.njit(cache=False)(python_route_arrays)):
        started=time.perf_counter(); cold=run(); cold_seconds=time.perf_counter()-started
        started=time.perf_counter(); warm=run(); warm_seconds=time.perf_counter()-started
    max_flow=max_land=max_routing=0.
    for event, expected, actual in zip(events,reference,warm):
        np.testing.assert_allclose(actual[0],expected[0],rtol=1e-10,atol=1e-9)
        max_flow=max(max_flow,float(np.max(np.abs(actual[0].to_numpy()-expected[0].to_numpy()))))
        for key in expected[1]:
            np.testing.assert_allclose(actual[1][key],expected[1][key],rtol=1e-10,atol=1e-9)
            max_land=max(max_land,float(np.max(np.abs(actual[1][key]-expected[1][key]))))
        for key in expected[2]:
            np.testing.assert_allclose(actual[2][key],expected[2][key],rtol=1e-10,atol=1e-9)
            max_routing=max(max_routing,float(np.max(np.abs(actual[2][key]-expected[2][key]))))
        first_metrics=kernels.metrics(event.observed,expected[0].loc[event.start:event.end])
        second_metrics=kernels.metrics(event.observed,actual[0].loc[event.start:event.end])
        for key,value in first_metrics.items():
            if isinstance(value,(float,int)) and np.isfinite(value):
                np.testing.assert_allclose(second_metrics[key],value,rtol=1e-10,atol=1e-9)
    row={'condition':name,'start':str(events[0].times[0]),'end':str(events[-1].times[-1]),
         'processes':len(events),'drivingHours':sum(len(e.times) for e in events),'gridCount':len(job.grids),
         'preparationSeconds':loading,'pythonSeconds':python_seconds,'jitFirstCallSeconds':cold_seconds,'jitWarmSeconds':warm_seconds,
         'maxFlowDifference':max_flow,'maxLandStateDifference':max_land,'maxRoutingStateDifference':max_routing,'metricsMatch':True}
    report['conditions'].append(row)
    write_json(out/'benchmark.json',clean(report))
    print(json.dumps(clean(row)),flush=True)
