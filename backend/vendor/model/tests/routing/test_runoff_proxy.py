import csv
import json
import tempfile
import unittest
from pathlib import Path

from xaj_routing.runoff_proxy import generate_basin_average_runoff_proxy


class RunoffProxyTests(unittest.TestCase):
    def test_generate_basin_average_runoff_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rainfall = root / "rainfall.csv"
            topology = root / "topology.json"
            out_csv = root / "runoff.csv"
            rainfall.write_text(
                "\n".join(
                    [
                        "time,grid_id,precip_mm",
                        "2026-01-01 00:00:00,G1,2",
                        "2026-01-01 00:00:00,G2,4",
                        "2026-01-01 01:00:00,G1,0",
                        "2026-01-01 01:00:00,G2,2",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            topology.write_text(
                json.dumps(
                    {
                        "units": [
                            {"unit_id": "U1", "area_km2": 1.0, "reach_id": "R1"},
                            {"unit_id": "U2", "area_km2": 2.0, "reach_id": "R2"},
                        ],
                        "reaches": [
                            {"reach_id": "R1", "downstream_reach_id": "R2"},
                            {"reach_id": "R2"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = generate_basin_average_runoff_proxy(
                rainfall_csv=rainfall,
                topology_json=topology,
                out_csv=out_csv,
                runoff_coefficient=0.5,
                rs_fraction=0.5,
                ri_fraction=0.3,
                rg_fraction=0.2,
            )

            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(manifest["time_count"], 2)
            self.assertEqual(manifest["unit_count"], 2)
            self.assertEqual(manifest["row_count"], 4)
            self.assertEqual(rows[0]["unit_id"], "U1")
            self.assertEqual(rows[0]["RS"], "0.750000")
            self.assertEqual(rows[0]["RI"], "0.450000")
            self.assertEqual(rows[0]["RG"], "0.300000")
            self.assertEqual(rows[0]["glacier_fast"], "0.000000")


if __name__ == "__main__":
    unittest.main()
