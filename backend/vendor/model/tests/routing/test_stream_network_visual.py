import tempfile
import unittest
from pathlib import Path

from xaj_routing.stream_network_visual import write_dem_stream_network_visualization


class StreamNetworkVisualTests(unittest.TestCase):
    def test_write_dem_stream_network_visualization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ascii_path = root / "dem.asc"
            out_html = root / "stream.html"
            out_png = root / "stream.png"
            summary_out = root / "summary.json"
            ascii_path.write_text(
                "\n".join(
                    [
                        "ncols 5",
                        "nrows 5",
                        "xllcorner 0",
                        "yllcorner 0",
                        "cellsize 30",
                        "NODATA_value -9999",
                        "90 80 70 60 50",
                        "80 70 60 50 40",
                        "70 60 50 40 30",
                        "60 50 40 30 20",
                        "50 40 30 20 10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = write_dem_stream_network_visualization(
                ascii_path=ascii_path,
                out_html=out_html,
                out_png=out_png,
                summary_out=summary_out,
                cell_size_m=30,
                stream_threshold_km2=0.003,
            )

            self.assertTrue(out_html.exists())
            self.assertTrue(out_png.exists())
            self.assertTrue(summary_out.exists())
            self.assertGreater(summary["stream_cell_count"], 0)
            self.assertIn("DEM 提取河网结果", out_html.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
