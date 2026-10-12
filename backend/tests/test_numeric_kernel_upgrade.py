"""Independent legacy XAJ references and native/fast routing regressions."""
import copy
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL, read_json
from routing_adapter import RegisteredRoutingRunner
from run_formal_multievent_calibration import FastRoutingRunner
from runoff_platform.core.types import LandParams
from runoff_platform.core.numeric_compilation import numeric_cache_directory, optional_njit
from runoff_platform.models import xaj_vectorized as xaj
from xaj_routing.engine import RoutingEngine
from xaj_routing.io import read_parameters_json, read_control_points_json
from xaj_routing.models import RunoffRecord, RoutingState


class XajUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.rain = np.zeros((48, 2), dtype=float)
        self.rain[5:11, 0] = [2, 4, 10, 50, 30, 10]
        self.rain[18:28, 0] = 12
        self.rain[4:9, 1] = [20, 60, 10, 5, 3]
        self.rain[30:33, 1] = 80
        self.pet = np.tile(np.array([.12, .24]), (48, 1))
        self.state = {field: np.full(2, value, dtype=float) for field, value in
                      zip(xaj.LAND_STATE_FIELDS, (0, 0, 12, 55, 30, 5, .5))}

    def test_outputs_match_frozen_20261008_reference_in_three_parameter_regimes(self):
        reference = read_json(Path(__file__).parent / 'fixtures/xaj_liquid_20261008.json')
        self.assertEqual(reference['referenceSha256'],
                         '372341fd6bce1efd468b2c5e670e86afd7fbae6b298180d3149acac34a8933be')
        for case in reference['cases']:
            with self.subTest(case=case['name']):
                params = LandParams(**case['parameters'])
                actual = xaj.simulate_xaj_liquid_grids(self.rain, params, self.pet, self.state)
                for index, name in enumerate(('RS', 'RI', 'RG')):
                    np.testing.assert_allclose(actual[index], case[name], rtol=1e-7, atol=1e-8)
                for name in self.state:
                    np.testing.assert_allclose(actual[3][name], case['finalState'][name], rtol=1e-12, atol=1e-12)

    def test_diagnostics_close_water_balance_and_reconstruct_state_without_mutating_inputs(self):
        params = LandParams()
        saved_state = copy.deepcopy(self.state)
        saved_rain, saved_pet = self.rain.copy(), self.pet.copy()
        actual = xaj.simulate_xaj_liquid_grids(self.rain, params, self.pet, self.state, True)
        self.assertIs(type(actual[3]), dict)
        self.assertIs(type(actual[4]), dict)
        checkpoint = copy.deepcopy(actual[3])
        json.dumps({name: values.tolist() for name, values in checkpoint.items()}, allow_nan=False)
        trace = actual[4]
        self.assertEqual(set(trace), {'et_actual', 'water_balance_error', 'EU', 'EL', 'ED',
                                     'WU', 'WL', 'WD', 'S', 'FR', 'infiltration',
                                     'net_rainfall', 'pervious_runoff'})
        for name, values in trace.items():
            self.assertEqual(values.shape, self.rain.shape, name)
            self.assertEqual(values.dtype, np.float64, name)
            self.assertTrue(np.isfinite(values).all(), name)
        self.assertLess(np.max(np.abs(trace['water_balance_error'])), 1e-10)
        np.testing.assert_allclose(trace['EU'] + trace['EL'] + trace['ED'], trace['et_actual'], atol=1e-12)
        np.testing.assert_allclose(trace['infiltration'] + trace['pervious_runoff'],
                                   trace['net_rainfall'] * (1-params.IM), atol=1e-12)
        for name in ('WU', 'WL', 'WD', 'S', 'FR'):
            np.testing.assert_array_equal(trace[name][-1], actual[3][name])
        for name in self.state:
            np.testing.assert_array_equal(self.state[name], saved_state[name])
        np.testing.assert_array_equal(self.rain, saved_rain)
        np.testing.assert_array_equal(self.pet, saved_pet)

    def test_xaj_split_continuation_matches_whole_including_diagnostics(self):
        params = LandParams()
        whole = xaj.simulate_xaj_liquid_grids(self.rain, params, self.pet, self.state, True)
        first = xaj.simulate_xaj_liquid_grids(self.rain[:19], params, self.pet[:19], self.state, True)
        parent = copy.deepcopy(first[3])
        second = xaj.simulate_xaj_liquid_grids(self.rain[19:], params, self.pet[19:], parent, True)
        for index in range(3):
            np.testing.assert_array_equal(np.concatenate((first[index], second[index])), whole[index])
        for name in whole[3]:
            np.testing.assert_array_equal(second[3][name], whole[3][name])
            np.testing.assert_array_equal(parent[name], first[3][name])
        for name in whole[4]:
            np.testing.assert_allclose(np.concatenate((first[4][name], second[4][name])),
                                       whole[4][name], rtol=1e-12, atol=1e-12)

    def test_roundoff_is_normalized_but_actual_state_overflow_is_rejected(self):
        params = LandParams()
        state = copy.deepcopy(self.state)
        state['WU'][0] = -1.4e-17
        state['WL'][0] = params.WLM + 1e-12
        normalized = xaj._copy_and_validate_state(state, params, 2)
        self.assertEqual(normalized['WU'][0], 0)
        self.assertEqual(normalized['WL'][0], params.WLM)
        for value in (-1e-6, params.WUM+1e-6, float('nan')):
            state['WU'][0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                xaj._copy_and_validate_state(state, params, 2)

    def test_active_xaj_kernel_matches_python_kernel(self):
        params = LandParams()
        values = tuple(getattr(params, name) for name in
                       ('KC', 'WUM', 'WLM', 'WDM', 'WM', 'C', 'B', 'SM', 'EX', 'KI', 'KG', 'IM'))
        state = tuple(self.state[name].copy() for name in xaj.LAND_STATE_FIELDS)
        slow = xaj._python_array_kernel(self.rain, self.pet, tuple(a.copy() for a in state), values, True)
        active = xaj._array_kernel(self.rain, self.pet, tuple(a.copy() for a in state), values, True)
        for index in range(3):
            np.testing.assert_allclose(slow[index], active[index], rtol=1e-7, atol=1e-8)
        for index in (3, 4):
            for key in slow[index]:
                np.testing.assert_allclose(slow[index][key], active[index][key], rtol=1e-12, atol=1e-12)


class RoutingKernelUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.topology = MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json'
        self.controls = MODEL / 'data_interface/spatial/xufan_70115800_control_point_snapped_v1.json'
        self.units = [row['unit_id'] for row in read_json(self.topology)['units']]
        self.runner = FastRoutingRunner(self.topology, self.units, self.controls)
        self.routing = read_parameters_json(MODEL / 'config/routing_parameters_control_split_v2.json')
        self.times = pd.date_range('2024-01-01', periods=72, freq='h')
        rng = np.random.default_rng(32)
        self.arrays = [rng.gamma(.5, 2, (len(self.times), len(self.units))) for _ in range(3)]

    def native(self, initial=None):
        records = [RunoffRecord(unit_id=unit, time=time.to_pydatetime(),
                               RS=float(self.arrays[0][t, u]), RI=float(self.arrays[1][t, u]),
                               RG=float(self.arrays[2][t, u]))
                   for t, time in enumerate(self.times) for u, unit in enumerate(self.units)]
        return RoutingEngine(self.runner.topology, self.routing, initial_state=initial,
                             control_points=read_control_points_json(self.controls)).run(records)

    def assert_native_parity(self):
        initial = RoutingState(slope_outflow_cms={self.units[0]: {'RS': 1, 'RG': 2}},
                               reach_inflow_cms={self.runner.reach_ids[0]: 3},
                               reach_outflow_cms={self.runner.reach_ids[0]: 2})
        reference = self.native(copy.deepcopy(initial))
        fast, final = self.runner.run(self.times, *self.arrays, self.routing,
                                      self.runner.standard_state_to_fast(initial))
        np.testing.assert_allclose(fast, [row.outflow_cms for row in reference.control_flows], rtol=1e-12, atol=1e-10)
        expected_state = self.runner.standard_state_to_fast(reference.final_state)
        for name in final:
            np.testing.assert_allclose(final[name], expected_state[name], rtol=1e-12, atol=1e-10)

    def test_global_k_and_control_split_match_native_engine(self):
        self.assertEqual(self.runner.control_point.reach_id, 'R0020_UP')
        self.assert_native_parity()

    def test_partial_per_unit_per_component_k_matches_native_engine(self):
        upstream = next(unit for unit in self.units
                        if self.runner.topology.units[unit].reach_id == self.runner.control_point.reach_id)
        self.routing.unit_component_slope_k_hours.update({
            upstream: {'RS': 10}, self.units[0]: {'RI': 40, 'RG': 70}})
        self.assert_native_parity()
        self.assertEqual(self.routing.slope_k_seconds(upstream, 'RI'),
                         self.routing.default_slope_k_hours['RI'] * 3600)

    def test_split_continuation_preserves_parent_and_matches_whole(self):
        self.routing.unit_component_slope_k_hours.update({self.units[0]: {'RS': 10, 'RG': 70}})
        whole, final = self.runner.run(self.times, *self.arrays, self.routing)
        first, parent = self.runner.run(self.times[:30], *(a[:30] for a in self.arrays), self.routing)
        saved = copy.deepcopy(parent)
        tail, resumed = self.runner.run(self.times[30:], *(a[30:] for a in self.arrays), self.routing, parent)
        np.testing.assert_array_equal(np.concatenate((first, tail)), whole)
        for name in final:
            np.testing.assert_array_equal(final[name], resumed[name])
            np.testing.assert_array_equal(parent[name], saved[name])
        registered = RegisteredRoutingRunner(self.topology, self.units, self.controls)
        adapted, adapted_state = registered.run(self.times, *self.arrays, self.routing)
        np.testing.assert_array_equal(adapted, whole)
        for name in final:
            np.testing.assert_array_equal(adapted_state[name], final[name])

    def test_nonhour_config_and_timestamps_are_rejected(self):
        self.routing = replace(self.routing, time_step_hours=.5)
        with self.assertRaisesRegex(ValueError, '1-hour'):
            self.runner.run(self.times, *self.arrays, self.routing)
        self.routing = replace(self.routing, time_step_hours=1)
        irregular = self.times.to_list()
        irregular[5] += pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, '1-hour'):
            self.runner.run(pd.DatetimeIndex(irregular), *self.arrays, self.routing)

    def test_invalid_unit_override_and_array_shape_are_rejected_before_kernel(self):
        self.routing.unit_component_slope_k_hours.update({'outside': {'RS': 10}})
        with self.assertRaisesRegex(ValueError, 'unknown routing units'):
            self.runner.run(self.times, *self.arrays, self.routing)
        self.routing.unit_component_slope_k_hours.clear()
        with self.assertRaisesRegex(ValueError, 'shape'):
            self.runner.run(self.times, self.arrays[0][:1], *self.arrays[1:], self.routing)
        state = {'slope_state': np.zeros((1, 3)), 'prev_in': np.zeros(1), 'prev_out': np.zeros(1)}
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            self.runner.run(self.times, *self.arrays, self.routing, state)


class OptionalCompilationTests(unittest.TestCase):
    def test_missing_numba_keeps_original_python_callable(self):
        def kernel(value):
            return value + 1
        with patch.dict(sys.modules, {'numba': None}):
            self.assertIs(optional_njit(kernel), kernel)

    def test_cache_follows_runtime_and_rejects_vendor_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'XAJ_RUNTIME_DIR': directory}):
                self.assertEqual(numeric_cache_directory(), Path(directory).resolve() / 'numeric-cache')
        with patch.dict(os.environ, {'XAJ_RUNTIME_DIR': str(MODEL / 'generated')}):
            with self.assertRaisesRegex(ValueError, 'source directory'):
                numeric_cache_directory()


if __name__ == '__main__':
    print('active XAJ branch:', 'NUMBA' if hasattr(xaj._array_kernel, 'signatures') else 'PYTHON')
    unittest.main(verbosity=2)
