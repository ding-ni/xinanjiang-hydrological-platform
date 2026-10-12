"""Routing, diagnostic contracts and scoped report exports."""
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from contracts import freeze_input_selection, freeze_routing_selection
from diagnostic_jobs import normalize_diagnostic, spatial_payload
from input_products import InputProducts
from routing_versions import RoutingVersions
from server import TaskManager, ThreadingHTTPServer, handler
from test_unified_backend import calibration, simulation


def workflow_request():
    return {'runType': 'productionWorkflow', 'baseRequest': calibration(),
            'wlmUpperBounds': [80, 120, 160], 'seeds': [42, 101, 2026], 'warmupYear': 2014,
            'yearGroups': {'CALIBRATION': [2015], 'VALIDATION': [2016], 'INDEPENDENT_TEST': [2017]}}


class DiagnosticContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch('server.ThreadPoolExecutor'):
            self.manager = TaskManager(self.root / 'tasks')
        self.versions = self.manager.routing_versions

    def create_version(self):
        payload = copy.deepcopy(self.versions.resolve()['parameters'])
        payload['default_slope_k_hours']['RS'] += 1
        return self.versions.create({'name': 'Test routing', 'parameters': payload})

    def test_versions_are_unique_and_do_not_mutate_parent(self):
        reference = self.versions.resolve()
        first, second = self.create_version(), self.create_version()
        self.assertNotEqual(first['routingVersionId'], second['routingVersionId'])
        self.assertEqual(self.versions.resolve(), reference)
        self.assertEqual(len(self.versions.list()['versions']), 3)
        self.assertFalse(first['productionEligible'])

    def test_reject_unknown_overrides_bad_schema_and_unstable_coefficients(self):
        mutations = [lambda p: p.update(time_step_hours=True),
                     lambda p: p['default_slope_k_hours'].update(RS=float('nan')),
                     lambda p: p.update(unit_component_slope_k_hours={'unit': {'RS': 1}}),
                     lambda p: p['reach_muskingum'].pop(next(iter(p['reach_muskingum']))),
                     lambda p: p['reach_muskingum'][next(iter(p['reach_muskingum']))].update(k_hours=5, x=0.3)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(self.versions.resolve()['parameters'])
                mutation(payload)
                with self.assertRaises(ValueError):
                    self.versions.create({'name': 'Invalid', 'parameters': payload})
        with self.assertRaises(ValueError):
            self.versions.resolve('ROUTING-../../outside')

    def test_registered_partial_unit_overrides_are_validated_and_frozen(self):
        product = spatial_payload(self.manager.products.resolve())
        unit = product['topology']['units'][0]['unit_id']
        payload = copy.deepcopy(self.versions.resolve()['parameters'])
        payload['unit_component_slope_k_hours'] = {unit: {'RS': 8, 'RI': 36}}
        row = self.versions.create({'name': 'Unit slope', 'parameters': payload})
        frozen, bundle = freeze_input_selection(simulation(), self.manager.products)
        frozen['routing'] = {'versionId': row['routingVersionId']}
        frozen = freeze_routing_selection(frozen, bundle, self.versions)
        self.assertEqual(frozen['routing']['parametersSHA256'], row['parametersSHA256'])
        payload['unit_component_slope_k_hours'][unit]['RS'] = 10
        self.assertEqual(self.versions.resolve(row['routingVersionId'])['parameters']['unit_component_slope_k_hours'][unit]['RS'], 8)
        for invalid in ({}, {'SNOW_RS': 2}, {'RS': True}, {'RS': None}, {'RS': float('inf')}, {'RG': 0}, {'RI': 721}, []):
            payload['unit_component_slope_k_hours'] = {unit: invalid}
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.versions.create({'name': 'Invalid unit', 'parameters': payload})

    def test_freeze_selected_version_and_reject_changed_hash_or_inline_parameters(self):
        version = self.create_version()
        request, bundle = freeze_input_selection(simulation(), self.manager.products)
        reference = freeze_routing_selection(copy.deepcopy(request), bundle, self.versions)
        request['routing'] = {'versionId': version['routingVersionId']}
        frozen = freeze_routing_selection(request, bundle, self.versions)
        self.assertEqual(frozen['routing']['parametersSHA256'], version['parametersSHA256'])
        self.assertNotEqual(frozen['lineage']['routingManifestSHA256'], reference['lineage']['routingManifestSHA256'])
        changed = copy.deepcopy(frozen)
        changed['routing']['parametersSHA256'] = '0' * 64
        with self.assertRaises(ValueError):
            freeze_routing_selection(changed, bundle, self.versions)
        changed = copy.deepcopy(frozen)
        changed['routing']['parameters'] = version['parameters']
        with self.assertRaises(ValueError):
            freeze_routing_selection(changed, bundle, self.versions)
        path = self.versions.root / f"{version['routingVersionId']}.json"
        row = read_json(path)
        row['parameters']['default_slope_k_hours']['RS'] += 1
        write_json(path, row)
        with self.assertRaises(ValueError):
            self.versions.resolve(version['routingVersionId'])

    def test_experiment_freezes_same_split_and_requires_independent_test(self):
        base = calibration()
        valid = normalize_diagnostic({'runType': 'diagnosticExperiment', 'mode': 'multiSeed', 'values': [42, 101, 2026], 'baseRequest': base}, self.manager)
        self.assertEqual(valid['baseRequest']['period']['events'], base['period']['events'])
        self.assertEqual(valid['routing']['versionId'], 'control-split-v2')
        bad = copy.deepcopy(base)
        for row in bad['period']['events']:
            if row['purpose'] == '独立检验':
                row['purpose'] = '验证'
        with self.assertRaisesRegex(ValueError, '独立检验'):
            normalize_diagnostic({'runType': 'diagnosticExperiment', 'mode': 'multiSeed', 'values': [42, 101, 2026], 'baseRequest': bad}, self.manager)

    def test_reject_bad_experiment_values_and_absent_point_evidence(self):
        for mode, values in [('multiSeed', [1, 1, 2]), ('multiSeed', [True, 2, 3]), ('multiSeed', [1, 2]), ('wlmBoundary', [80, 80.0]), ('wlmBoundary', [80, float('nan')]), ('wlmBoundary', [59, 120])]:
            with self.subTest(mode=mode, values=values), self.assertRaises(ValueError):
                normalize_diagnostic({'runType': 'diagnosticExperiment', 'mode': mode, 'values': values, 'baseRequest': calibration()}, self.manager)
        task = self.manager.submit(simulation())
        write_json(self.manager.root / task['taskId'] / 'result.json', {})
        self.manager.update(task['taskId'], status='completed')
        with self.assertRaisesRegex(ValueError, '完整产流'):
            normalize_diagnostic({'runType': 'pointQuery', 'sourceTaskId': task['taskId'], 'point': {'lon': 119.5, 'lat': 30.1, 'channelWidthM': 20}}, self.manager)

    def test_experiment_clone_retains_source_without_mutating_base(self):
        original = self.manager.submit({'runType': 'diagnosticExperiment', 'mode': 'multiSeed',
                                        'values': [42, 101, 2026], 'baseRequest': calibration()})
        cloned = self.manager.clone(original['taskId'])
        self.assertEqual(cloned['lineage']['sourceTaskId'], original['taskId'])
        self.assertEqual(cloned['runConfig']['baseRequest'], original['runConfig']['baseRequest'])
        self.assertNotIn('sourceTaskId', original['lineage'])

    def test_spatial_products_use_frozen_control_split(self):
        product = spatial_payload(self.manager.products.resolve())
        self.assertEqual(len(product['topology']['reaches']), 32)
        self.assertEqual(len(product['geometry']['features']), 110)
        self.assertEqual(product['control']['snapped']['reach_id'], 'R0020_UP')
        self.assertEqual(product['control']['originalSnappedReachId'], 'R0020')
        grid_routing = product['gridRouting']
        grid_ids = {row['id'] for row in grid_routing['grids']}
        reach_ids = {row['id'] for row in grid_routing['reaches']}
        self.assertEqual(grid_routing['schemaVersion'], '1.0')
        self.assertEqual(grid_routing['summary']['gridCount'], 110)
        self.assertEqual(grid_routing['summary']['mappingRowCount'], 236)
        self.assertEqual(grid_routing['summary']['reachCount'], 32)
        self.assertEqual(grid_routing['summary']['d8EdgeCount'] + grid_routing['summary']['localTerminalCount'], 110)
        self.assertEqual(len(grid_ids), 110)
        for row in grid_routing['grids']:
            self.assertTrue(row['mapping']['links'])
            self.assertAlmostEqual(row['mapping']['totalFraction'], 1.0, places=6)
            if row['downstreamGridId']:
                self.assertIn(row['downstreamGridId'], grid_ids)
            for link in row['mapping']['links']:
                self.assertIn(link['reachId'], reach_ids)

    def test_workflow_freezes_implementation_and_clone_lineage(self):
        request = normalize_diagnostic(workflow_request(), self.manager)
        self.assertIn('run_continuous_water_balance.py', request['workflowEvidence'])
        self.assertIn('workflow_jobs.py', request['workflowEvidence'])
        request['lineage']['sourceTaskId'] = 'JOB-original-workflow'
        self.assertEqual(normalize_diagnostic(request, self.manager)['lineage']['sourceTaskId'], 'JOB-original-workflow')
        for field in ('workflowConfigSHA256', 'workflowEvidence'):
            changed = copy.deepcopy(request)
            changed[field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ValueError):
                normalize_diagnostic(changed, self.manager)

    def test_workflow_rejects_invalid_year_groups_and_missing_inputs(self):
        mutations = [lambda r: r['yearGroups'].pop('INDEPENDENT_TEST'),
                     lambda r: r['yearGroups'].update(VALIDATION=[]),
                     lambda r: r['yearGroups'].update(VALIDATION=[2015]),
                     lambda r: r['yearGroups'].update(VALIDATION=[True]),
                     lambda r: r['yearGroups'].update(VALIDATION=['2016']),
                     lambda r: r.update(warmupYear=True),
                     lambda r: r.update(warmupYear=2015),
                     lambda r: r.update(warmupYear=2000),
                     lambda r: r.update(unknown='external path'),
                     lambda r: r.update(seeds=[1, 2])]
        for mutation in mutations:
            request = workflow_request()
            mutation(request)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                normalize_diagnostic(request, self.manager)
        original = self.manager.products.resolve
        def missing(version=None):
            bundle = original(version)
            return {**bundle, 'precipitationDir': self.root / 'absent-precipitation'}
        with patch.object(self.manager.products, 'resolve', side_effect=missing), self.assertRaisesRegex(ValueError, '缺少连续年份'):
            normalize_diagnostic(workflow_request(), self.manager)

    def test_all_missing_annual_observations_remain_missing_not_zero(self):
        from workflow_jobs import annual_frame
        from common import clean
        from worker import kernels
        from run_continuous_water_balance import _group_summary
        rows = [{'year': 2015, 'observed_coverage': 0.0, 'climate_balance_residual_using_observed_runoff_mm': None}]
        report = clean(_group_summary(annual_frame(rows)))
        for key in ('mean_NSE', 'mean_KGE', 'mean_volume_error_percent', 'maximum_abs_climate_balance_residual_mm'):
            self.assertIsNone(report[key])
        self.assertEqual(report['mean_observed_coverage'], 0.0)

    def test_workflow_stage_dependency_and_nonproduction_candidate(self):
        from workflow_jobs import run_workflow
        request = normalize_diagnostic(workflow_request(), self.manager)
        params = request['baseRequest']['calibration']['initialParameters']
        metrics = {'mean_NSE': 0.8, 'mean_abs_volume_error_percent': 5, 'qualified_rate_percent': 100}
        boundary = {'experiment': {'evaluation': {'selected_upper_bound': 120, 'boundary_resolved': True}}, 'bestParameters': params}
        stability = {'experiment': {'evaluation': {'stable': True, 'status': 'PASSED'}}, 'bestParameters': params,
                     'calibrationMetrics': metrics, 'validationMetrics': metrics, 'independentTest': metrics}
        continuous = {'pet': {'mode': 'assumed', 'production_eligible': False}, 'minimum_observed_coverage': 1,
                      'maximum_abs_internal_balance_error_mm': 0,
                      'groups': {name: {'mean_NSE': 0.8, 'mean_volume_error_percent': 5} for name in request['yearGroups']}}
        calls = []
        def experiment(folder, stage, publish_candidate=True):
            calls.append(copy.deepcopy(stage))
            self.assertFalse(publish_candidate)
            return boundary if len(calls) == 1 else stability
        with patch('workflow_jobs.run_experiment', side_effect=experiment), patch('workflow_jobs.continuous_balance', return_value=continuous), patch('workflow_jobs.RUNTIME', self.root):
            result = run_workflow(self.root / 'JOB-workflow-unit', request)
        self.assertEqual(calls[1]['baseRequest']['calibration']['wlmUpperBound'], 120)
        self.assertEqual(calls[1]['baseRequest']['calibration']['initialParameters'], params)
        self.assertEqual(result['workflow']['gate']['failed_check_ids'], ['PET_PROVENANCE'])
        candidate = read_json(self.root / 'parameters/PARAM-JOB-workflow-unit.json')
        self.assertFalse(candidate['productionEligible'])

    def test_http_routing_spatial_and_report_allowlist(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.manager, self.root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}/api/model/'
        def fetch(path, payload=None, origin=None):
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            request = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload else None, headers=headers)
            with urllib.request.urlopen(request) as response:
                return response.read()
        try:
            self.assertEqual(len(json.loads(fetch('routing-versions'))['versions']), 1)
            spatial = json.loads(fetch('spatial-products'))
            self.assertEqual(len(spatial['topology']['reaches']), 32)
            self.assertEqual(spatial['gridRouting']['summary']['gridCount'], 110)
            self.assertEqual(spatial['gridRouting']['summary']['mappingRowCount'], 236)
            with self.assertRaises(urllib.error.HTTPError) as denied:
                fetch('routing-versions', {'name': 'Invalid origin', 'parameters': self.versions.resolve()['parameters']}, 'https://untrusted.invalid')
            self.assertEqual(denied.exception.code, 400)
            task = self.manager.submit(simulation())
            with self.assertRaises(urllib.error.HTTPError) as pending:
                fetch(f"tasks/{task['taskId']}/report-package")
            self.assertEqual(pending.exception.code, 409)
            folder = self.manager.root / task['taskId']
            write_json(folder / 'result.json', {'productionEligible': False})
            write_json(folder / 'secret.json', {'secret': 'not exported'})
            write_json(folder / 'JOB-boundary/JOB-experiment-1/result.json', {'diagnostic': True})
            write_json(folder / 'JOB-boundary/JOB-experiment-1/secret.json', {'secret': 'not exported'})
            write_json(folder / 'unrelated/result.json', {'secret': 'not exported'})
            write_json(folder / 'continuous-balance/continuous_water_balance_report.json', {'annual': []})
            self.manager.update(task['taskId'], status='completed')
            with zipfile.ZipFile(io.BytesIO(fetch(f"tasks/{task['taskId']}/report-package"))) as package:
                self.assertEqual(set(package.namelist()), {'request.json', 'result.json', 'status.json', 'JOB-boundary/JOB-experiment-1/result.json', 'continuous-balance/continuous_water_balance_report.json'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
