"""Candidate migration keeps frozen and manual parameters separate and inactive."""
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL, read_json, write_json
from science_context import digest_file
from input_products import file_sha256
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_input_version_contracts import InputVersionFixture

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/import_20261012_candidates.py'
SPEC = importlib.util.spec_from_file_location('candidate_import_probe', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CandidateImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source-model'
        self.runtime = self.root / 'separate-runtime'
        self.task = self.source / 'validation/platform_calibration_tasks' / MODULE.TASK
        self.params = {'KC': 0.5411732734801327, 'WM': 161., 'WUM': 9., 'WLM': 40., 'B': .6, 'C': .05,
                       'SM': 21., 'EX': 2., 'KI': .089, 'KG': .0018, 'IM': .016}
        self.routing = read_json(MODEL / 'config/routing_parameters_control_split_v2.json')
        for reach in self.routing['reach_muskingum'].values():
            reach.update(k_hours=1., x=.2)
        write_json(self.task / 'routing_parameter_snapshot.json', self.routing)
        search = {'routing.RS_k_hours': .05, 'routing.RI_k_hours': .5, 'routing.RG_k_hours': 37.,
                  'routing.muskingum_k_scale': 1.2, 'routing.muskingum_x': .1}
        write_json(self.task / 'result.json', {'task_id': MODULE.TASK, 'resolved_runoff_parameter_set': {'runoff.' + k: v for k, v in self.params.items()},
                                              'common_parameter_set': search, 'groups': {'CALIBRATION': {'mean_NSE': .7}}})
        write_json(self.task / 'status.json', {'status': 'SUCCEEDED'})
        write_json(self.task / 'output/resolved_runoff_parameter_set.json', {'parameters': self.params})
        self.manual = {**self.params, 'KC': .540001}
        write_json(self.source / 'data_interface/runoff_2017/xaj_parameter_set.json', {'land_parameters': self.manual})
        write_json(self.source / 'config/routing_parameters_control_split_v2.json', self.routing)
        request = {'state_strategy': 'CONTINUOUS', 'source_sha256': 'a' * 64, 'task_id': MODULE.TASK,
                   'profile_id': 'NANAN_JOINT_REFERENCE_MDB', 'calibration_scope': 'RUNOFF_ROUTING_JOINT',
                   'algorithm': 'DIFFERENTIAL_EVOLUTION', 'max_evaluations': 960, 'population_size': 48,
                   'generations': 19, 'differential_weight': .8, 'crossover_probability': .7,
                   'split_seed': 42, 'search_seed': 20260820, 'calibration_ratio': .667, 'validation_ratio': .201}
        self.hashes = {}
        for key, relative in (('state_config', 'state_initialization_config.json'), ('initial_land_state', 'initial_state_land.json'), ('pet_config', 'pet_forcing_assumed_monthly.json')):
            file = self.task / (key + '_snapshot.json')
            write_json(file, read_json(MODEL / 'config' / relative))
            self.hashes[key] = digest_file(file)
            request[key] = 'E:/old/workspace/' + file.name
        write_json(self.task / 'resolved_request.json', request)
        write_json(self.task / 'request.json', request)
        import joint_calibration
        write_json(self.task / 'parameter_space_snapshot.json', {'parameters': joint_calibration.resolve_space({'jointProfile': 'reference-16'})})
        write_json(self.task / 'objective_config_snapshot.json', {'method': 'WEIGHTED_SUM', 'terms': [
            {'metric': 'NSE', 'weight': .3}, {'metric': 'KGE', 'weight': .2},
            {'metric': 'PBIAS', 'weight': .2}, {'metric': 'PEAK_ERROR', 'weight': .2},
            {'metric': 'PEAK_TIME_ERROR', 'weight': .1}]})
        write_json(self.task / 'event_quality_config_snapshot.json', read_json(MODEL / 'config/event_quality_config.json'))
        write_json(self.task / 'base_parameter_snapshot.json', {'parameters': {**self.params, 'KI': .03, 'KG': .006}})
        write_json(self.task / 'initial_candidates.json', [])
        write_json(self.task / 'qualification_config_snapshot.json', read_json(MODEL / 'config/qualification_config_event.json'))
        write_json(self.task / 'data_review_config_snapshot.json', {})
        (self.task / 'output/event_metrics.csv').write_text(
            'event_id,start,end,group\nA,2021-08-11T10:00:00,2021-08-20T10:00:00,CALIBRATION\n'
            'B,2022-03-17T19:00:00,2022-04-01T19:00:00,VALIDATION\n', encoding='utf-8')
        (self.task / 'output/event_quality.csv').write_text(
            'event_id,start,end,accepted\nA,2021-08-11T10:00:00,2021-08-20T10:00:00,True\n'
            'B,2022-03-17T19:00:00,2022-04-01T19:00:00,True\n', encoding='utf-8')
        (self.task / 'output/overlap_group_metrics.csv').write_text('event_id,group\n', encoding='utf-8')
        write_json(self.source / 'config/simulation_contract.json', {'default_state_strategy': 'CONTINUOUS',
            'continuous_origin': 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP',
            **{key: (self.task / (key + '_snapshot.json')).relative_to(self.source).as_posix() for key in self.hashes}})
        self.original = {str(path.relative_to(self.source)): digest_file(path) for path in self.source.rglob('*') if path.is_file()}

    def tearDown(self):
        self.assertEqual(self.original, {str(path.relative_to(self.source)): digest_file(path) for path in self.source.rglob('*') if path.is_file()})
        self.temp.cleanup()

    def test_import_is_separate_idempotent_inactive_and_manual_has_no_frozen_metrics(self):
        result = MODULE.import_candidates(self.source, self.runtime, self.hashes)
        self.assertFalse(result['activeSelectionChanged'])
        self.assertEqual([row['KC'] for row in result['candidates']], [.5411732734801327, .540001])
        self.assertEqual(result, MODULE.import_candidates(self.source, self.runtime, self.hashes))
        self.assertEqual(len(list((self.runtime / 'parameters').glob('*.json'))), 2)
        frozen, manual = [read_json(self.runtime / 'parameters' / (r['parameterVersionId'] + '.json')) for r in result['candidates']]
        self.assertEqual(frozen['science'], manual['science'])
        self.assertEqual(frozen['statePolicy'], 'continuous-origin')
        self.assertEqual(frozen['name'], '20261011冻结率定候选 · 原方法重放')
        self.assertEqual(manual['name'], '20261011人工修改候选 · 原方法重放')
        self.assertEqual(manual['statePolicy'], 'continuous-origin')
        self.assertEqual(frozen['petMode'], 'MONTHLY_CLIMATOLOGY_ASSUMPTION')
        self.assertEqual(frozen['scienceSnapshot']['manifest']['scienceVersionId'], frozen['science']['versionId'])
        self.assertEqual(frozen['scienceSnapshot']['manifest']['statePolicy']['strategy'], frozen['statePolicy'])
        self.assertFalse(frozen['productionEligible'])
        self.assertEqual(frozen['calibrationMetrics']['mean_NSE'], .7)
        self.assertIsNone(manual['calibrationMetrics'])
        self.assertTrue(manual['requiresReevaluation'])
        self.assertTrue(manual['sourceRequiresReevaluation'])
        self.assertTrue(frozen['requiresInputRegistration'])
        self.assertIsNone(frozen['inputBundleVersionId'])
        self.assertEqual(frozen['sourceMDBSHA256'], 'a' * 64)
        for selection in ('current-science.json', 'current-parameters.json', 'active-source.json'):
            self.assertFalse((self.runtime / selection).exists())

    def test_missing_or_changed_snapshot_fails_before_candidate_registration(self):
        altered = {**self.hashes, 'pet_config': '0' * 64}
        with self.assertRaisesRegex(ValueError, '摘要'):
            MODULE.import_candidates(self.source, self.runtime, altered)
        self.assertFalse((self.runtime / 'parameters').exists())

    def test_manual_candidate_uses_current_contract_and_never_borrows_old_pet(self):
        pet = read_json(self.task / 'pet_config_snapshot.json')
        pet['monthly_values_mm_day'][0] += 1
        path = self.source / 'config/manual-pet.json'
        write_json(path, pet)
        contract = read_json(self.source / 'config/simulation_contract.json')
        contract['pet_config'] = path.relative_to(self.source).as_posix()
        write_json(self.source / 'config/simulation_contract.json', contract)
        self.original = {str(p.relative_to(self.source)): digest_file(p) for p in self.source.rglob('*') if p.is_file()}
        result = MODULE.import_candidates(self.source, self.runtime, self.hashes)
        self.assertNotEqual(result['candidates'][0]['scienceVersionId'], result['candidates'][1]['scienceVersionId'])

    def test_exact_registered_mdb_source_binds_input_lineage_and_origin(self):
        fixture = InputVersionFixture(self.root / 'registered-source')
        product = fixture.finished_product('mdb-candidate')
        manifest = product['manifest']
        manifest.update(sourceId='SRC-MDB-' + 'a' * 64, sourceOrigin={'type': 'mdb-snapshot', 'databaseSHA256': 'a' * 64},
                        summary={'continuousOrigin': '2020-12-25T00:00:00'}, targetCoordinatePolicy='LEGACY_CLOSED_RING_VERTEX_MEAN',
                        forcingValueDtype='float64')
        write_json(product['manifestPath'], manifest)
        write_json(product['task'] / 'result.json', {**product['result'], 'manifestSHA256': file_sha256(product['manifestPath'])})
        fixture.products.register_task(product['taskId'])
        with patch.object(MODULE, 'InputProducts', return_value=fixture.products):
            imported = MODULE.import_candidates(self.source, fixture.runtime, self.hashes)
        for candidate in imported['candidates']:
            row = read_json(fixture.runtime / 'parameters' / (candidate['parameterVersionId'] + '.json'))
            self.assertEqual(row['inputBundleVersionId'], product['versionId'])
            self.assertEqual(row['requiredInputBundleVersionId'], product['versionId'])
            self.assertEqual(row['continuousOrigin'], '2020-12-25T00:00:00')
            self.assertEqual(row['actualWarmupHours'], 168)
            self.assertFalse(row['requiresInputRegistration'])
            resolved = fixture.products.resolve(product['versionId'])
            self.assertEqual(row['lineage']['inputBundleManifestSHA256'], resolved['manifestSHA256'])
            self.assertEqual(row['lineage']['forcingVersion'], resolved['forcingVersion'])
            request = read_json(fixture.runtime / 'tasks' / row['taskId'] / 'request.json')
            self.assertEqual(request['inputs']['bundleVersionId'], product['versionId'])
            self.assertEqual(request['continuousOrigin'], row['continuousOrigin'])

    def test_matching_also_reads_database_hash_in_full_registered_manifest(self):
        descriptor = {'bundleVersionId': 'INPUT-direct-hash', 'manifest': {'sourceId': 'SRC-custom',
            'databaseSHA256': 'a' * 64, 'summary': {'continuousOrigin': '2020-12-25T00:00:00'},
            'targetPolicy': 'LEGACY_CLOSED_RING_VERTEX_MEAN', 'precipitationDtype': 'float64'}}
        products = SimpleNamespace(list_versions=lambda: {'versions': [{'bundleVersionId': 'INPUT-direct-hash', 'sourceId': 'SRC-custom'}]},
                                   resolve=lambda version: descriptor)
        self.assertEqual(MODULE.matching_source_input(self.runtime, 'a' * 64, products), descriptor)

    def test_same_mdb_correct_center_product_is_not_a_source_replay_match(self):
        legacy = {'bundleVersionId': 'INPUT-legacy', 'manifest': {'sourceId': 'SRC-MDB-' + 'a' * 64,
            'summary': {'continuousOrigin': '2020-12-25T00:00:00'}, 'targetCoordinatePolicy': 'LEGACY_CLOSED_RING_VERTEX_MEAN',
            'forcingValueDtype': 'float64'}}
        corrected = copy.deepcopy(legacy)
        corrected['bundleVersionId'] = 'INPUT-centers'
        corrected['manifest']['targetCoordinatePolicy'] = 'NC_GRID_AXIS_CENTRE'
        corrected['manifest']['forcingValueDtype'] = 'float32'
        versions = {row['bundleVersionId']: row for row in (corrected, legacy)}
        products = SimpleNamespace(list_versions=lambda: {'versions': [{'bundleVersionId': key} for key in versions]},
                                   resolve=lambda version: versions[version])
        self.assertEqual(MODULE.matching_source_input(self.runtime, 'a' * 64, products)['bundleVersionId'], 'INPUT-legacy')
        versions.pop('INPUT-legacy')
        self.assertIsNone(MODULE.matching_source_input(self.runtime, 'a' * 64, products))

    def test_existing_identical_routing_remains_immutable_when_input_binding_is_added(self):
        first = MODULE.register_routing(self.runtime, self.routing, '原候选', {'source': 'earlier import'})
        path = self.runtime / 'routing-versions' / (first['routingVersionId'] + '.json')
        before = path.read_bytes()
        second = MODULE.register_routing(self.runtime, self.routing, '精确重放候选', {'source': 'legacy profile import'})
        self.assertEqual(first['routingVersionId'], second['routingVersionId'])
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
