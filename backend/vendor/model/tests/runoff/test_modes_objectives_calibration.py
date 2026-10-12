import unittest

import pandas as pd

from runoff_platform.calibration import CalibrationEngine
from runoff_platform.data import ContinuousDataProvider, EventDataProvider, EventWindow
from runoff_platform.objectives import CompositeObjective
from runoff_platform.core.types import GridInfo, LandParams, LandState, RunContext
from runoff_platform.simulation import ContinuousSimulationRunner, EventSimulationRunner


def hourly_forcing():
    index = pd.date_range("2020-01-01", periods=12, freq="h")
    return pd.DataFrame({"precipitation": [0, 0, 2, 8, 20, 4, 0, 0, 0, 0, 0, 0], "temperature": [5.0] * 12, "pet": [0.05] * 12}, index=index)


class PlatformExtensionTests(unittest.TestCase):
    def test_continuous_and_event_modes(self):
        forcing = hourly_forcing()
        prepared = ContinuousDataProvider().from_frame(forcing)
        result = ContinuousSimulationRunner().run(prepared, LandParams(), LandState(swe=0), GridInfo(zone_type="NON_GLACIER"), RunContext(simulation_mode="CONTINUOUS"))
        self.assertEqual(len(result), len(forcing))
        event = EventWindow("E1", forcing.index[4], forcing.index[8], 3)
        event_data = EventDataProvider().prepare(forcing, event)
        event_result, _ = EventSimulationRunner().run(event_data, LandParams(), LandState(swe=0), GridInfo(zone_type="NON_GLACIER"), RunContext(simulation_mode="EVENT"))
        self.assertEqual(event_result.index[0], event.start)
        self.assertEqual(event_result.index[-1], event.end)

    def test_objective_weights_are_configurable(self):
        observed = pd.Series([1.0, 2.0, 8.0, 2.0])
        simulated = pd.Series([1.0, 2.0, 4.0, 4.0])
        first = CompositeObjective([{"metric": "NSE", "weight": 0.9}, {"metric": "PEAK_ERROR", "weight": 0.1}]).evaluate(observed, simulated)
        second = CompositeObjective([{"metric": "NSE", "weight": 0.1}, {"metric": "PEAK_ERROR", "weight": 0.9}]).evaluate(observed, simulated)
        self.assertNotEqual(first["score"], second["score"])

    def test_all_calibration_algorithms(self):
        space = {"parameters": [{"name": "x", "minimum": -5, "maximum": 5, "levels": 11}]}
        for algorithm in ["GRID_SEARCH", "RANDOM_SEARCH", "DIFFERENTIAL_EVOLUTION"]:
            with self.subTest(algorithm=algorithm):
                result = CalibrationEngine().run(space, lambda values: (values["x"] - 1.5) ** 2, {"algorithm": algorithm, "max_evaluations": 40, "population_size": 8, "generations": 5, "seed": 2})
                self.assertGreater(result.evaluations, 0)
                self.assertLess(result.best_loss, 1.0)


if __name__ == "__main__":
    unittest.main()
