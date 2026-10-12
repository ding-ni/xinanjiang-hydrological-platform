"""Unit K bindings must match the original engine and preserve continuation."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL, read_json, write_json
from routing_adapter import RegisteredRoutingRunner, inherited_parameters
from routing_versions import RoutingVersions
from run_formal_multievent_calibration import FastRoutingRunner
from xaj_routing.engine import RoutingEngine
from xaj_routing.models import RoutingParameters, RunoffRecord
import numpy as np
import pandas as pd


class RoutingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json'
        self.units = [row['unit_id'] for row in read_json(self.path)['units']]
        control = read_json(MODEL / 'data_interface/spatial/xufan_70115800_control_point_snapped_v1.json')
        control['snapped']['reach_id'] = 'R0020_UP'
        controls = Path(self.temp.name) / 'control.json'
        write_json(controls, control)
        self.runner = RegisteredRoutingRunner(self.path, self.units, controls)
        self.original = FastRoutingRunner(self.path, self.units, controls)
        self.payload = RoutingVersions().resolve()['parameters']
        self.times = pd.date_range('2017-06-20', periods=48, freq='h')
        generator = np.random.default_rng(51)
        self.depths = [generator.uniform(0, 4, (len(self.times), len(self.units))) for _ in range(3)]

    def test_default_path_is_bit_exact_to_delivered_fast_runner(self):
        routing = RoutingParameters.from_mapping(self.payload)
        actual, state = self.runner.run(self.times, *self.depths, routing)
        expected, reference = self.original.run(self.times, *self.depths, routing)
        np.testing.assert_array_equal(actual, expected)
        for key in state:
            np.testing.assert_array_equal(state[key], reference[key])

    def test_partial_unit_constants_match_original_engine_and_change_flow(self):
        self.payload['unit_component_slope_k_hours'] = {unit: {'RS': 15 + index, 'RG': 70}
                                                        for index, unit in enumerate(self.units)}
        routing = RoutingParameters.from_mapping(self.payload)
        before = copy.deepcopy(self.payload)
        actual, state = self.runner.run(self.times, *self.depths, routing)
        records = [RunoffRecord(unit_id=unit, time=time.to_pydatetime(),
                               RS=float(self.depths[0][step, index]), RI=float(self.depths[1][step, index]),
                               RG=float(self.depths[2][step, index]))
                   for step, time in enumerate(self.times) for index, unit in enumerate(self.units)]
        original = RoutingEngine(self.runner.topology, routing, control_points=[self.runner.control_point]).run(records)
        expected = [row.outflow_cms for row in original.control_flows]
        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-10)
        for index, unit in enumerate(self.units):
            for component, name in enumerate(('RS', 'RI', 'RG')):
                self.assertAlmostEqual(state['slope_state'][index, component], original.final_state.slope_outflow_cms[unit][name], places=10)
        self.assertEqual(self.payload, before)
        baseline, _ = self.runner.run(self.times, *self.depths, RoutingParameters.from_mapping(RoutingVersions().resolve()['parameters']))
        self.assertGreater(float(np.max(np.abs(actual - baseline))), 1)
        self.assertEqual(routing.slope_k_seconds(self.units[0], 'RI'), self.payload['default_slope_k_hours']['RI'] * 3600)

    def test_split_continuation_equals_whole_and_does_not_mutate_parent(self):
        self.payload['unit_component_slope_k_hours'] = {self.units[0]: {'RS': 9, 'RI': 36}}
        routing = RoutingParameters.from_mapping(self.payload)
        whole, whole_state = self.runner.run(self.times, *self.depths, routing)
        first, parent = self.runner.run(self.times[:24], *[values[:24] for values in self.depths], routing)
        saved = copy.deepcopy(parent)
        second, final = self.runner.run(self.times[24:], *[values[24:] for values in self.depths], routing, parent)
        np.testing.assert_array_equal(np.concatenate([first, second]), whole)
        for key in parent:
            np.testing.assert_array_equal(parent[key], saved[key])
            np.testing.assert_array_equal(final[key], whole_state[key])

    def test_unknown_units_rejected_at_kernel_boundary(self):
        self.payload['unit_component_slope_k_hours'] = {'outside': {'RS': 5}}
        with self.assertRaises(ValueError):
            self.runner.run(self.times, *self.depths, RoutingParameters.from_mapping(self.payload))

    def test_point_split_inherits_partial_k_and_reach_parameters_by_cells(self):
        payload = {'default_slope_k_hours': {'RS': 1, 'RI': 2, 'RG': 3},
                   'unit_component_slope_k_hours': {'U_A': {'RS': 9}},
                   'reach_muskingum': {'A': {'k_hours': 1, 'x': 0.2}, 'B': {'k_hours': 2, 'x': 0.1}}}
        units = [{'unit_id': 'U_A', 'reach_id': 'A'}, {'unit_id': 'U_B', 'reach_id': 'B'}]
        targets = [{'unit_id': 'U_A1', 'reach_id': 'A1'}, {'unit_id': 'U_A2', 'reach_id': 'A2'}, units[1]]
        original = {(0, 0): 'A', (0, 1): 'A', (1, 0): 'B', (1, 1): None}
        split = {(0, 0): 'A1', (0, 1): 'A2', (1, 0): 'B', (1, 1): None}
        before = copy.deepcopy(payload)
        bound = inherited_parameters(payload, units, targets, original, split)
        self.assertEqual(bound['unit_component_slope_k_hours'], {'U_A1': {'RS': 9}, 'U_A2': {'RS': 9}})
        self.assertEqual(bound['reach_muskingum']['A2'], payload['reach_muskingum']['A'])
        self.assertEqual(payload, before)
        split[(0, 0)] = 'B'
        with self.assertRaises(ValueError):
            inherited_parameters(payload, units, targets, original, split)

    def test_point_split_rejects_new_or_lost_catchment_cells(self):
        payload = {'unit_component_slope_k_hours': {}, 'reach_muskingum': {'A': {'k_hours': 1, 'x': 0.2}}}
        units = [{'unit_id': 'U_A', 'reach_id': 'A'}]
        for original, split in [({(0, 0): None}, {(0, 0): 'A'}),
                                ({(0, 0): 'A'}, {(0, 0): None})]:
            with self.subTest(original=original, split=split), self.assertRaises(ValueError):
                inherited_parameters(payload, units, units, original, split)


if __name__ == '__main__':
    unittest.main(verbosity=2)
