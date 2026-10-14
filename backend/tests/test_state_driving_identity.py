"""Continuation follows its numerical driving trajectory, not a bundle label."""
import copy
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_products import InputProducts
from common import ROUTING_VERSION
from science_context import IDENTITY_KEYS
from strict_forcing import load_hourly_forcing
from state_integrity import build_state_driving_identity, assert_state_input_compatibility, verify_state_driving_identity
from test_input_service import InputFixture


class StateDrivingIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.fixture = InputFixture(root / 'original')
        self.changed_fixture = InputFixture(root / 'extended')
        self.products = InputProducts(self.fixture.root, root / 'runtime')
        self.parent = self.products.resolve()
        self.extended = InputProducts(self.changed_fixture.root, root / 'extended-runtime').resolve()
        self.extended['bundleVersionId'] = 'INPUT-extended'
        self.extended['manifestSHA256'] = 'b' * 64
        with self.parent['paths']['grids'].open(encoding='utf-8-sig', newline='') as stream:
            self.grids = list(csv.DictReader(stream))
        times, values, counts, _ = load_hourly_forcing(self.parent, self.grids, self.fixture.times[0], self.fixture.times[3])
        identity = build_state_driving_identity(self.parent, self.grids, times, values, counts, products=self.products)
        science = {key: 'a' * 64 for key in IDENTITY_KEYS}
        self.checkpoint = {'time': self.fixture.times[3].isoformat(),
                           'gridIds': [row['unit_id'] for row in self.grids],
                           'inputBundleVersionId': self.parent['bundleVersionId'],
                           'inputBundleManifestSHA256': self.parent['manifestSHA256'],
                           'forcingVersion': self.parent['forcingVersion'],
                           'observationVersion': self.parent['observationVersion'],
                           'routingVersionId': ROUTING_VERSION, 'routingManifestSHA256': 'r' * 64,
                           'petMode': 'MONTHLY_CLIMATOLOGY_ASSUMPTION', 'drivingIdentity': identity, **science}
        self.request = {'inputs': {'bundleVersionId': self.extended['bundleVersionId'],
                                    'bundleManifestSHA256': self.extended['manifestSHA256']},
                        'lineage': {'forcingVersion': 'extended-source', 'observationVersion': 'other-evaluation',
                                    'routingVersionId': ROUTING_VERSION, 'routingManifestSHA256': 'r' * 64,
                                    'petMode': self.checkpoint['petMode'], **science}}
        self.registry = SimpleNamespace(resolve=lambda version: self.extended)

    def tearDown(self):
        self.temporary.cleanup()

    def test_new_bundle_same_prefix_and_changed_observations_remain_compatible(self):
        self.changed_fixture.flows = [999.] * 6
        self.changed_fixture.write_flow()
        assert_state_input_compatibility(self.checkpoint, self.request, self.registry)
        times, values, counts, _ = load_hourly_forcing(self.extended, self.grids, self.fixture.times[4], self.fixture.times[-1])
        identity = build_state_driving_identity(self.extended, self.grids, times, values, counts,
                                                parent=self.checkpoint['drivingIdentity'], products=self.registry)
        self.assertEqual(identity['start'], self.fixture.times[0].isoformat())
        self.assertEqual(identity['end'], self.fixture.times[-1].isoformat())
        self.assertEqual(len(identity['segments']), 2)
        checkpoint = {**self.checkpoint, 'time': identity['end'], 'drivingIdentity': identity}
        self.assertTrue(verify_state_driving_identity(checkpoint, self.request, self.registry))

    def test_changed_prefix_rejected_but_future_rain_can_change(self):
        self.changed_fixture.precip[5, 1, 0] += 50
        self.changed_fixture.write_precipitation()
        assert_state_input_compatibility(self.checkpoint, self.request, self.registry)
        self.changed_fixture.precip[2, 1, 0] += .5
        self.changed_fixture.write_precipitation()
        with self.assertRaisesRegex(ValueError, '已演算降雨发生变化'):
            assert_state_input_compatibility(self.checkpoint, self.request, self.registry)

    def test_changed_station_rules_grid_or_missing_trajectory_rejected(self):
        for mutation, pattern in [
            (lambda cp: cp['drivingIdentity'].update(sourceSHA256='d' * 64), '降雨来源'),
            (lambda cp: cp['drivingIdentity'].update(gridSHA256='d' * 64), '网格'),
            (lambda cp: cp.pop('drivingIdentity'), '缺少逐时驱动'),
            (lambda cp: cp['drivingIdentity'].update(end=self.fixture.times[2].isoformat()), '状态时刻'),
        ]:
            checkpoint = copy.deepcopy(self.checkpoint)
            mutation(checkpoint)
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                assert_state_input_compatibility(checkpoint, self.request, self.registry)

    def test_model_science_and_routing_guards_survive_bundle_extension(self):
        for key in (*IDENTITY_KEYS, 'routingVersionId', 'routingManifestSHA256', 'petMode'):
            changed = copy.deepcopy(self.request)
            changed['lineage'][key] = 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                assert_state_input_compatibility(self.checkpoint, changed, self.registry)

    def test_only_dynamic_observation_anchor_is_checked(self):
        self.checkpoint['drivingIdentity']['observedFlowAnchor'] = {
            'time': self.fixture.times[3].isoformat(), 'flowCms': self.fixture.flows[3]}
        assert_state_input_compatibility(self.checkpoint, self.request, self.registry)
        self.changed_fixture.flows[3] = 999
        self.changed_fixture.write_flow()
        with self.assertRaisesRegex(ValueError, '起算实测流量已改变'):
            assert_state_input_compatibility(self.checkpoint, self.request, self.registry)

    def test_cannot_append_gap_or_overlap_to_saved_trajectory(self):
        for start in (self.fixture.times[3], self.fixture.times[5]):
            times, values, counts, _ = load_hourly_forcing(self.extended, self.grids, start, self.fixture.times[-1])
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, '未连续衔接'):
                build_state_driving_identity(self.extended, self.grids, times, values, counts,
                                             parent=self.checkpoint['drivingIdentity'], products=self.registry)


if __name__ == '__main__':
    unittest.main()
