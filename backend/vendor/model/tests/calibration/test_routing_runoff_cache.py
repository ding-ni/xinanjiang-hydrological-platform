import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_formal_multievent_calibration as formal  # noqa: E402


class _RecordingRouter:
    def __init__(self):
        self.calls = []

    def run(self, times, unit_rs, unit_ri, unit_rg, routing, state):
        self.calls.append(
            {
                "routing": routing,
                "rs": unit_rs.copy(),
                "ri": unit_ri.copy(),
                "rg": unit_rg.copy(),
            }
        )
        return pd.Series(np.sum(unit_rs + unit_ri + unit_rg, axis=1), index=times), {
            "routing": routing
        }


class RoutingRunoffCacheTests(unittest.TestCase):
    def test_two_routing_candidates_reuse_one_xaj_run(self):
        times = pd.date_range("2020-01-01", periods=3, freq="h")
        event = formal.EventData(
            event_id="E1",
            year=2020,
            start=times[0],
            end=times[-1],
            times=times,
            precip=np.ones((3, 2), dtype=float),
            observed=pd.Series([1.0, 2.0, 3.0], index=times),
            group="CALIBRATION",
            pet_mm_h=np.zeros(3, dtype=float),
        )
        rs = np.asarray([[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]])
        ri = rs * 0.5
        rg = rs * 0.25
        final_land = {"WU": np.asarray([1.0, 1.0])}
        mapping = np.asarray([[0.25], [0.75]])

        with patch.object(
            formal,
            "vectorized_xaj",
            return_value=(rs, ri, rg, final_land),
        ) as xaj:
            prepared = formal.prepare_runoff_event(
                event,
                params=object(),
                mapping=mapping,
                land_state={"initial": True},
            )
            router = _RecordingRouter()
            first, _, _ = formal.route_prepared_runoff(
                prepared, "ROUTING_A", router
            )
            second, _, _ = formal.route_prepared_runoff(
                prepared, "ROUTING_B", router
            )

        self.assertEqual(xaj.call_count, 1)
        self.assertEqual(len(router.calls), 2)
        np.testing.assert_array_equal(router.calls[0]["rs"], router.calls[1]["rs"])
        np.testing.assert_array_equal(first.to_numpy(), second.to_numpy())
        self.assertEqual(router.calls[0]["routing"], "ROUTING_A")
        self.assertEqual(router.calls[1]["routing"], "ROUTING_B")


if __name__ == "__main__":
    unittest.main()
