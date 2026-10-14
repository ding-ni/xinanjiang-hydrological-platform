"""Fixed simulations retain model numerics while separating evaluation gaps."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import read_json, write_json
from contracts import normalize_request
from test_unified_backend import simulation
from test_continuous_calibration import continuous_request


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Isolated real model regression')
class FixedSimulationWorkflowTests(unittest.TestCase):
    def test_missing_evaluation_does_not_change_flow_or_state_and_no_search_runs(self):
        import numpy as np
        import pandas as pd
        from worker import ModelJob, kernels
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            def make(name):
                folder = Path(temp) / 'tasks' / name
                request = simulation('2017-06-19T00:00', '2017-06-19T23:00')
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': name})
                return ModelJob(folder)
            first = make('JOB-fixed-reference')
            event = first.load([{'event_id': 'REFERENCE', 'start': pd.Timestamp('2017-06-19T00:00'),
                                 'end': pd.Timestamp('2017-06-19T23:00'), 'group': 'SIMULATION'}], 24)[0]
            event.pet_mm_h = first.pet_provider.values(event.times)
            expected_flow, expected_land, expected_routing = kernels.route_event_fast(
                event, first.params, first.routing, first.router, first.mapping, first.initial_state(first.params))
            with patch('worker.run_search', side_effect=AssertionError('Fixed simulation invoked parameter search')):
                reference = first.simulate()
                second = make('JOB-fixed-no-observations')
                second.observed[:] = np.nan
                missing = second.simulate()
            np.testing.assert_allclose([row['value'] for row in reference['hydrograph']['forecast']],
                                       expected_flow.loc[event.start:event.end].values, rtol=1e-12, atol=1e-12)
            self.assertEqual(missing['hydrograph']['forecast'], reference['hydrograph']['forecast'])
            self.assertTrue(all(row['value'] is None for row in missing['hydrograph']['obs']))
            self.assertTrue(all(value is None for value in missing['metrics'].values()))
            self.assertEqual(missing['drivingIdentity'], reference['drivingIdentity'])
            checkpoint = read_json(second.folder / 'checkpoint.json')
            for field in expected_land:
                np.testing.assert_allclose(checkpoint['land'][field], expected_land[field], rtol=1e-12, atol=1e-12)
            for field in expected_routing:
                np.testing.assert_allclose(checkpoint['routing'][field], expected_routing[field], rtol=1e-12, atol=1e-12)
            self.assertTrue((second.folder / 'grid-history.npz').is_file())
            self.assertTrue(missing['gridHistory']['available'])
            self.assertEqual(checkpoint['drivingIdentity'], missing['drivingIdentity'])
            self.assertEqual(checkpoint['routingTopology']['unitIds'], second.router.unit_ids)
            self.assertEqual(checkpoint['routingTopology']['reachIds'], second.router.reach_ids)
            self.assertEqual(len(checkpoint['routingTopology']['unitIds']), len(checkpoint['routing']['slope_state']))
            self.assertEqual(len(checkpoint['routingTopology']['reachIds']), len(checkpoint['routing']['prev_out']))

    def test_calibration_candidates_stay_in_task_until_named_save(self):
        from worker import ModelJob
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            request = continuous_request(False)
            folder = Path(temp) / 'tasks/JOB-unpublished-candidate'
            write_json(folder / 'request.json', normalize_request(request, case_catalog()))
            write_json(folder / 'status.json', {'taskId': folder.name})
            job = ModelJob(folder)
            result = job.calibrate()
            self.assertEqual(result['optimizer']['evaluations'], 4)
            candidate = read_json(folder / 'parameter-candidate.json')
            self.assertEqual(candidate['bestParameters'], result['bestParameters'])
            self.assertFalse((Path(temp) / 'parameters' / (result['parameterVersionId'] + '.json')).exists())

    def test_future_observations_do_not_change_issue_state_or_hindcast_branch(self):
        import numpy as np
        import pandas as pd
        from worker import ModelJob
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            origin, issue = '2017-06-18T11:00:00', '2017-06-19T11:00:00'
            def make(name, start, end, parent=None, change_future=False, science=None):
                request = simulation(start, end)
                if parent:
                    request['period'].update(initialStateVersionId=parent, warmupDays=0)
                    request['science'] = copy.deepcopy(science)
                    request['inputs']['evaporation'] = {'mode': 'science-version'}
                else:
                    request['period']['continuousOrigin'] = origin
                folder = Path(temp) / 'tasks' / name
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': name, 'status': 'running'})
                job = ModelJob(folder)
                if change_future:
                    job.observed.loc[job.observed.index > pd.Timestamp(issue)] *= 100
                job.run()
                write_json(folder / 'status.json', {'taskId': name, 'status': 'completed'})
                return read_json(folder / 'result.json'), read_json(folder / 'checkpoint.json')
            with patch('worker.run_search', side_effect=AssertionError('Historical forecast invoked parameter search')):
                base, base_state = make('JOB-base-unchanged', issue, issue)
                changed_base, changed_state = make('JOB-base-future-flow-changed', issue, issue, change_future=True)
                forecast, final = make('JOB-branch-unchanged', '2017-06-19T12:00:00', '2017-06-19T15:00:00',
                    base['endStateVersionId'], science=base['science'])
                changed_forecast, changed_final = make('JOB-branch-future-flow-changed', '2017-06-19T12:00:00',
                    '2017-06-19T15:00:00', changed_base['endStateVersionId'], change_future=True, science=changed_base['science'])
            self.assertEqual(base['hydrograph'], changed_base['hydrograph'])
            self.assertEqual(forecast['hydrograph']['forecast'], changed_forecast['hydrograph']['forecast'])
            self.assertNotEqual(forecast['hydrograph']['obs'], changed_forecast['hydrograph']['obs'])
            self.assertNotEqual(forecast['metrics'], changed_forecast['metrics'])
            for unchanged, changed in ((base_state, changed_state), (final, changed_final)):
                self.assertEqual(unchanged['drivingIdentity'], changed['drivingIdentity'])
                for field in unchanged['land']:
                    np.testing.assert_array_equal(unchanged['land'][field], changed['land'][field])
                for field in unchanged['routing']:
                    np.testing.assert_array_equal(unchanged['routing'][field], changed['routing'][field])

    def test_multiyear_annual_blocks_keep_land_and_routing_state(self):
        import numpy as np
        import pandas as pd
        from types import SimpleNamespace
        from worker import ModelJob, kernels
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            folder = Path(temp) / 'tasks/JOB-block-reference'
            write_json(folder / 'request.json', normalize_request(simulation(), case_catalog()))
            write_json(folder / 'status.json', {'taskId': folder.name})
            job = ModelJob(folder)
            times = pd.date_range('2023-12-31T22:00', '2025-01-01T02:00', freq='h')
            position = np.arange(len(times))
            rainfall = ((position % 72) < 5).astype(float)[:, None] * np.linspace(.2, 1.2, len(job.grids))[None, :]
            pet = job.pet_provider.values(times)
            def event(index):
                return SimpleNamespace(times=times[index], precip=rainfall[index], pet_mm_h=pet[index])
            whole, land_all, routing_all = kernels.route_event_fast(
                event(np.arange(len(times))), job.params, job.routing, job.router,
                job.mapping, job.initial_state(job.params))
            land, routing, processes = job.initial_state(job.params), None, []
            for year in sorted(set(times.year)):
                block, land, routing = kernels.route_event_fast(event(np.flatnonzero(times.year == year)),
                    job.params, job.routing, job.router, job.mapping, land, routing)
                processes.append(block)
            joined = pd.concat(processes)
            self.assertTrue(joined.index.equals(times))
            np.testing.assert_allclose(joined.values, whole.values, rtol=1e-12, atol=1e-12)
            for field in land_all:
                np.testing.assert_allclose(land[field], land_all[field], rtol=1e-12, atol=1e-12)
            for field in routing_all:
                np.testing.assert_allclose(routing[field], routing_all[field], rtol=1e-12, atol=1e-12)


if __name__ == '__main__':
    unittest.main()
