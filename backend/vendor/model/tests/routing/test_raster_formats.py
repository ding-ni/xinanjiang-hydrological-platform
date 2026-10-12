import tempfile
import unittest
from pathlib import Path

from xaj_routing.raster_formats import clip_ascii_grid_by_polygons, read_esri_ascii_grid
from xaj_routing.vector_formats import PolygonShape


class RasterFormatTests(unittest.TestCase):
    def test_read_esri_ascii_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dem.asc"
            path.write_text(
                "\n".join(
                    [
                        "ncols 3",
                        "nrows 2",
                        "xllcorner 100",
                        "yllcorner 30",
                        "cellsize 0.5",
                        "NODATA_value -9999",
                        "1 2 3",
                        "4 5 -9999",
                    ]
                ),
                encoding="utf-8",
            )

            grid = read_esri_ascii_grid(path)

            self.assertEqual(grid.ncols, 3)
            self.assertEqual(grid.nrows, 2)
            self.assertEqual(grid.nodata, -9999)
            self.assertEqual(grid.elevation.shape, (2, 3))
            self.assertEqual(grid.extent["xmax"], 101.5)

    def test_clip_ascii_grid_by_polygon_crops_to_inside_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dem.asc"
            path.write_text(
                "\n".join(
                    [
                        "ncols 4",
                        "nrows 4",
                        "xllcorner 0",
                        "yllcorner 0",
                        "cellsize 10",
                        "NODATA_value -9999",
                        "1 2 3 4",
                        "5 6 7 8",
                        "9 10 11 12",
                        "13 14 15 16",
                    ]
                ),
                encoding="utf-8",
            )
            grid = read_esri_ascii_grid(path)
            polygon = PolygonShape(
                parts=(
                    (
                        (10.0, 10.0),
                        (30.0, 10.0),
                        (30.0, 30.0),
                        (10.0, 30.0),
                        (10.0, 10.0),
                    ),
                ),
                bbox=(10.0, 10.0, 30.0, 30.0),
                attributes={},
            )

            clipped, summary = clip_ascii_grid_by_polygons(grid, [polygon], output_nodata=-9999)

            self.assertEqual((clipped.nrows, clipped.ncols), (2, 2))
            self.assertEqual(clipped.xllcorner, 10.0)
            self.assertEqual(clipped.yllcorner, 10.0)
            self.assertEqual(summary["inside_cell_count"], 4)
            self.assertEqual(clipped.elevation.tolist(), [[6.0, 7.0], [10.0, 11.0]])


if __name__ == "__main__":
    unittest.main()
