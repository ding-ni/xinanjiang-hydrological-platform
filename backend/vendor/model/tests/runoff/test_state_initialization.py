import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from runoff_platform.core.types import GridInfo, LandParams, LandState
from runoff_platform.models.xaj import simulate_land
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids
from state_initialization import (
    StateInitializationError,
    arrays_to_land_records,
    build_observed_flow_anchored_state,
    build_uniform_land_state,
    land_records_to_arrays,
    load_checkpoint,
    run_state_initialization_task,
)
from xaj_routing.models import ControlPoint
from xaj_routing.topology import Topology


class StateInitializationTests(unittest.TestCase):
    def setUp(self):
        self.params = LandParams()
        self.template = {
            "swe": 0.0,
            "snow_liquid": 0.0,
            "WU": 12.0,
            "WL": 55.0,
            "WD": 30.0,
            "S": 5.0,
            "FR": 0.5,
        }

    def test_uniform_state_is_explicit_and_round_trips_by_grid_id(self):
        arrays = build_uniform_land_state(self.template, 2, self.params)
        records = arrays_to_land_records(["G2", "G1"], arrays)
        restored = land_records_to_arrays(records, ["G1", "G2"], self.params)
        self.assertEqual(restored["WU"].tolist(), [12.0, 12.0])
        self.assertEqual(set(restored), {"swe", "snow_liquid", "WU", "WL", "WD", "S", "FR"})

    def test_uniform_state_rejects_values_outside_candidate_capacity(self):
        template = {**self.template, "WL": 81.0}
        with self.assertRaises(StateInitializationError):
            build_uniform_land_state(template, 1, self.params)

    def test_capacity_fraction_state_scales_with_candidate_storage(self):
        template = {
            "representation": "CAPACITY_FRACTION",
            "swe": 0.0,
            "snow_liquid": 0.0,
            "WU_fraction": 0.5,
            "WL_fraction": 0.5,
            "WD_fraction": 0.5,
            "S_fraction": 0.5,
            "FR": 0.5,
        }
        params = LandParams(WM=120.0, WUM=20.0, WLM=80.0, SM=8.0)
        state = build_uniform_land_state(template, 1, params)
        self.assertEqual(state["WD"].tolist(), [10.0])
        self.assertEqual(state["S"].tolist(), [4.0])

    def test_vectorized_warmup_requires_explicit_state(self):
        state = build_uniform_land_state(self.template, 2, self.params)
        rain = np.asarray([[0.0, 1.0], [2.0, 0.0]], dtype=float)
        rs, ri, rg, final_state = simulate_xaj_liquid_grids(
            rain,
            self.params,
            pet_mm_h=0.08,
            initial_state=state,
        )
        self.assertEqual(rs.shape, (2, 2))
        self.assertEqual(ri.shape, (2, 2))
        self.assertEqual(rg.shape, (2, 2))
        self.assertTrue(np.all(final_state["WU"] >= 0.0))

    def test_vectorized_warmup_matches_single_grid_xaj(self):
        times = pd.date_range("2017-01-01", periods=4, freq="h")
        forcing = pd.DataFrame(
            {
                "precipitation": [0.0, 1.0, 5.0, 0.0],
                "temperature": [15.0] * 4,
                "pet": [0.08] * 4,
            },
            index=times,
        )
        scalar_state = LandState(**self.template)
        scalar = simulate_land(
            forcing,
            self.params,
            scalar_state,
            GridInfo(zone_type="NON_GLACIER"),
        )
        vector_state = build_uniform_land_state(self.template, 1, self.params)
        rs, ri, rg, final_state = simulate_xaj_liquid_grids(
            forcing["precipitation"].to_numpy()[:, None],
            self.params,
            pet_mm_h=0.08,
            initial_state=vector_state,
        )
        np.testing.assert_allclose(rs[:, 0], scalar["RS"], atol=2.0e-8)
        np.testing.assert_allclose(ri[:, 0], scalar["RI"], atol=2.0e-8)
        np.testing.assert_allclose(rg[:, 0], scalar["RG"], atol=2.0e-8)
        for field in final_state:
            self.assertAlmostEqual(final_state[field][0], getattr(scalar_state, field))

    def test_observed_anchor_rejects_non_finite_flow(self):
        topology = Topology.from_mapping(
            {
                "reaches": [{"reach_id": "R1", "downstream_reach_id": None}],
                "units": [{"unit_id": "U1", "reach_id": "R1", "area_km2": 1.0}],
            }
        )
        with self.assertRaises(StateInitializationError):
            build_observed_flow_anchored_state(
                topology,
                ControlPoint(control_id="C1", reach_id="R1"),
                float("nan"),
            )

    def test_observed_anchor_closes_at_control_reach(self):
        topology = Topology.from_mapping(
            {
                "reaches": [
                    {"reach_id": "R1", "downstream_reach_id": "R2"},
                    {"reach_id": "R2", "downstream_reach_id": None},
                ],
                "units": [
                    {"unit_id": "U1", "reach_id": "R1", "area_km2": 1.0},
                    {"unit_id": "U2", "reach_id": "R2", "area_km2": 3.0},
                ],
            }
        )
        state = build_observed_flow_anchored_state(
            topology,
            ControlPoint(control_id="C1", reach_id="R2"),
            20.0,
        )
        self.assertAlmostEqual(state.slope_outflow_cms["U1"]["RG"], 5.0)
        self.assertAlmostEqual(state.slope_outflow_cms["U2"]["RG"], 15.0)
        self.assertAlmostEqual(state.reach_outflow_cms["R2"], 20.0)

    def test_checkpoint_strategy_writes_a_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "module_id": "STATE-INITIALIZATION-03",
                        "state_id": "SOURCE",
                        "strategy": "FIXED_WARMUP",
                        "effective_time": "2017-01-01T00:00:00",
                        "land_states": [
                            {"grid_id": "G1", "zone_type": "NON_GLACIER", **self.template}
                        ],
                        "routing_state": {
                            "slope_outflow_cms": {},
                            "reach_inflow_cms": {},
                            "reach_outflow_cms": {},
                        },
                        "diagnostics": {"quality_flags": []},
                        "provenance": {},
                    }
                ),
                encoding="utf-8",
            )
            response = run_state_initialization_task(
                {
                    "module_id": "STATE-INITIALIZATION-03",
                    "task_id": "COPY-1",
                    "project_root": str(root),
                    "strategy": "CHECKPOINT",
                    "source_checkpoint": "source.json",
                    "state_id": "COPY",
                    "output_dir": "out",
                }
            )
            copied = load_checkpoint(response["output_files"]["state_checkpoint"])
            self.assertEqual(copied["state_id"], "COPY")
            self.assertEqual(copied["provenance"]["parent_state_id"], "SOURCE")


if __name__ == "__main__":
    unittest.main()
