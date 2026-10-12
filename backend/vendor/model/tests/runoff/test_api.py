import json
import unittest
from copy import deepcopy
from pathlib import Path

from runoff_platform_api import get_parameter_catalog, run_calibration_request, run_platform_request


class ApiCompatibilityTests(unittest.TestCase):
    def test_legacy_api_and_frontend_schema(self):
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / "data_interface/examples/example_request_land.json").read_text(encoding="utf-8-sig"))
        response = run_platform_request(payload)
        self.assertEqual(response["status"], "SUCCEEDED")
        self.assertEqual(len(response["hourly"]), len(payload["forcing"]))
        self.assertEqual({item["component_type"] for item in response["runoff_component"]}, {"RS", "RI", "RG"})
        catalog = get_parameter_catalog("NON_GLACIER")
        self.assertIn("parameters", catalog)
        self.assertIn("DIFFERENTIAL_EVOLUTION", catalog["calibration_algorithm_options"])
        self.assertEqual(catalog["defaults"]["source_coefficient_time_step_hours"], 1.0)
        schema = {item["name"]: item for item in catalog["parameters"]}
        self.assertEqual(schema["KI"]["unit"], "1/hour")
        self.assertEqual(schema["KI"]["maximum"], 0.06)
        self.assertEqual(schema["KG"]["maximum"], 0.06)
        self.assertIn("DAILY_TOTAL_AND_SPLIT", catalog["source_calibration_parameterizations"])

    def test_daily_total_and_split_api_matches_direct_hourly_parameters(self):
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / "data_interface/examples/example_request_land.json").read_text(encoding="utf-8-sig"))
        direct = run_platform_request(payload)
        derived_payload = deepcopy(payload)
        derived_payload["params"].pop("KI")
        derived_payload["params"].pop("KG")
        derived_payload["params"]["K_total_day"] = 0.67
        derived_payload["params"]["groundwater_fraction"] = 0.37 / 0.67
        derived = run_platform_request(derived_payload)
        self.assertAlmostEqual(
            direct["summary"]["total_runoff_mm"],
            derived["summary"]["total_runoff_mm"],
            places=12,
        )

    def test_end_to_end_calibration_request(self):
        root = Path(__file__).resolve().parents[2]
        simulation_request = json.loads((root / "data_interface/examples/example_request_land.json").read_text(encoding="utf-8-sig"))
        baseline = run_platform_request(simulation_request)
        observation = [{"time": row["time"], "value": row["total_runoff"]} for row in baseline["hourly"]]
        response = run_calibration_request({
            "simulation_request": simulation_request,
            "observation": observation,
            "simulation_field": "total_runoff",
            "parameter_space": {"parameters": [{"name": "KC", "minimum": 0.8, "maximum": 1.0, "levels": 3}]},
            "calibration_config": {"algorithm": "GRID_SEARCH", "max_evaluations": 3},
            "objective_config": {"method": "WEIGHTED_SUM", "terms": [{"metric": "NSE", "weight": 1.0}]}
        })
        self.assertEqual(response["status"], "SUCCEEDED")
        self.assertEqual(response["evaluations"], 3)


if __name__ == "__main__":
    unittest.main()
