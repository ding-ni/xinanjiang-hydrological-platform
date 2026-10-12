import csv
import tempfile
import unittest
from pathlib import Path

from integration.runoff_adapter import aggregate_runoff_to_reach_segments


class IntegrationAdapterTests(unittest.TestCase):
    def test_area_weighted_adapter_preserves_component_volume(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "mapping.csv").write_text(
                "grid_id,unit_id,reach_id,raw_overlap_area_km2,adjusted_overlap_area_km2,source_fraction\n"
                "G1,U1,R1,0.25,0.25,0.25\nG1,U2,R2,0.75,0.75,0.75\n",
                encoding="utf-8",
            )
            (root / "runoff.csv").write_text(
                "time,unit_id,RS_mm,RI_mm,RG_mm,glacier_fast_mm,glacier_slow_mm,glacier_base_mm\n"
                "2026-01-01 00:00:00,G1,4,2,1,0.5,0.25,0.1\n",
                encoding="utf-8",
            )
            (root / "topology.json").write_text(
                '{"units":[{"unit_id":"U1","area_km2":0.5},'
                '{"unit_id":"U2","area_km2":1.5}]}',
                encoding="utf-8",
            )

            summary = aggregate_runoff_to_reach_segments(
                root / "runoff.csv",
                root / "mapping.csv",
                root / "topology.json",
                root / "output.csv",
            )

            self.assertLess(summary["max_component_volume_error_mm_km2"], 1.0e-12)
            self.assertEqual(summary["routing_method"], "REACH_SEGMENT")
            with (root / "output.csv").open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
