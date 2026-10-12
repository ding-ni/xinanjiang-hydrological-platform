import csv
import tempfile
import unittest
from pathlib import Path

from xaj_routing.flow_compare import (
    compare_flow_series,
    read_flow_series,
    write_aligned_flow_csv,
    write_flow_compare_html,
)


class FlowCompareTests(unittest.TestCase):
    def test_compare_flow_series_aligns_by_exact_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sim_path = root / "sim.csv"
            obs_path = root / "obs.csv"
            self._write_csv(
                sim_path,
                ["time", "outflow_cms"],
                [
                    ["2026-01-01 00:00:00", "1.0"],
                    ["2026-01-01 01:00:00", "2.0"],
                    ["2026-01-01 02:00:00", "3.0"],
                ],
            )
            self._write_csv(
                obs_path,
                ["time", "flow_cms"],
                [
                    ["2026-01-01 01:00:00", "1.5"],
                    ["2026-01-01 02:00:00", "2.5"],
                    ["2026-01-01 03:00:00", "9.0"],
                ],
            )

            simulated = read_flow_series(sim_path, source_label="sim")
            observed = read_flow_series(obs_path, source_label="obs")
            aligned_rows, metrics = compare_flow_series(simulated, observed)

            self.assertEqual(metrics["simulated_count"], 3)
            self.assertEqual(metrics["observed_count"], 3)
            self.assertEqual(metrics["aligned_count"], 2)
            self.assertEqual(aligned_rows[0]["time"].isoformat(sep=" "), "2026-01-01 01:00:00")
            self.assertAlmostEqual(metrics["mae_cms"], 0.5)
            self.assertAlmostEqual(metrics["rmse_cms"], 0.5)

    def test_write_outputs_for_no_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sim_path = root / "sim.csv"
            obs_path = root / "obs.csv"
            out_csv = root / "aligned.csv"
            out_html = root / "compare.html"
            self._write_csv(
                sim_path,
                ["time", "outflow_cms"],
                [["2026-01-01 00:00:00", "1.0"]],
            )
            self._write_csv(
                obs_path,
                ["time", "flow_cms"],
                [["2025-01-01 00:00:00", "1.5"]],
            )

            simulated = read_flow_series(sim_path, source_label="sim")
            observed = read_flow_series(obs_path, source_label="obs")
            aligned_rows, metrics = compare_flow_series(simulated, observed)
            write_aligned_flow_csv(out_csv, aligned_rows)
            write_flow_compare_html(
                out_html,
                simulated=simulated,
                observed=observed,
                metrics=metrics,
            )

            self.assertEqual(metrics["aligned_count"], 0)
            self.assertEqual(out_csv.read_text(encoding="utf-8").splitlines()[0], "time,sim_flow_cms,obs_flow_cms,difference_cms")
            self.assertIn("模拟流量", out_html.read_text(encoding="utf-8"))

    def test_duplicate_timestamps_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sim.csv"
            self._write_csv(
                path,
                ["time", "reach_id", "outflow_cms"],
                [
                    ["2026-01-01 00:00:00", "R001", "1.0"],
                    ["2026-01-01 00:00:00", "R002", "2.0"],
                ],
            )

            with self.assertRaisesRegex(ValueError, "duplicate timestamp"):
                read_flow_series(path, source_label="sim")

            filtered = read_flow_series(
                path,
                filters={"reach_id": "R002"},
                source_label="sim",
            )
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0].flow_cms, 2.0)

    @staticmethod
    def _write_csv(path, fieldnames, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(fieldnames)
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
