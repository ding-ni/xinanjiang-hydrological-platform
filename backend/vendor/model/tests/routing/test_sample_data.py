import csv
import json
import tempfile
import unittest
from pathlib import Path

from xaj_routing.sample_data import (
    generate_routing_parameters_json,
    generate_synthetic_runoff_csv,
)


class SampleDataTests(unittest.TestCase):
    def test_generate_synthetic_runoff_matches_topology_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            topology = tmp_path / "topology.json"
            runoff = tmp_path / "runoff.csv"
            topology.write_text(
                json.dumps(
                    {
                        "units": [
                            {"unit_id": "U1", "area_km2": 1.0, "zone_type": "non_glacier"},
                            {"unit_id": "U2", "area_km2": 2.0, "zone_type": "glacier"},
                        ],
                        "reaches": [{"reach_id": "R1"}],
                    }
                ),
                encoding="utf-8",
            )

            summary = generate_synthetic_runoff_csv(
                topology_path=topology,
                out_path=runoff,
                start_time="2026-08-03 00:00:00",
                periods=3,
                peak_rs_mm=4.0,
            )

            with runoff.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(summary["row_count"], 6)
            self.assertEqual(len(rows), 6)
            self.assertEqual(
                list(rows[0]),
                [
                    "unit_id",
                    "time",
                    "RS",
                    "RI",
                    "RG",
                    "glacier_fast",
                    "glacier_slow",
                    "glacier_base",
                ],
            )
            self.assertEqual(rows[0]["unit_id"], "U1")
            self.assertEqual(rows[1]["unit_id"], "U2")

    def test_generate_routing_parameters_has_one_entry_per_reach(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            topology = tmp_path / "topology.json"
            parameters = tmp_path / "parameters.json"
            topology.write_text(
                json.dumps(
                    {
                        "units": [{"unit_id": "U1", "area_km2": 1.0, "reach_id": "R1"}],
                        "reaches": [
                            {"reach_id": "R1", "length_km": 3.0},
                            {"reach_id": "R2", "length_km": 6.0},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = generate_routing_parameters_json(
                topology_path=topology,
                out_path=parameters,
                time_step_hours=1.0,
                default_channel_velocity_kmh=3.0,
            )

            data = json.loads(parameters.read_text(encoding="utf-8"))
            self.assertEqual(summary["reach_count"], 2)
            self.assertEqual(sorted(data["reach_muskingum"]), ["R1", "R2"])
            self.assertEqual(data["reach_muskingum"]["R1"]["k_hours"], 1.0)
            self.assertEqual(data["reach_muskingum"]["R2"]["k_hours"], 2.0)


if __name__ == "__main__":
    unittest.main()
