import json
import tempfile
import unittest
from pathlib import Path

from xaj_routing.forcing import (
    find_flood_event,
    resolve_time_window,
    select_netcdf_files,
)


class ForcingTests(unittest.TestCase):
    def test_resolve_time_window_from_flood_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            path.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "id": "xf-31640404",
                                "name": "31640404 flood",
                                "start": "1964-04-04T20:00",
                                "end": "1964-04-15T20:00",
                                "observedPeak": 34.1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            event = find_flood_event(path, "31640404")
            window = resolve_time_window(event_json=path, event_id="xf-31640404")

            self.assertEqual(event["id"], "xf-31640404")
            self.assertEqual(window.start.isoformat(sep=" "), "1964-04-04 20:00:00")
            self.assertEqual(window.end.isoformat(sep=" "), "1964-04-15 20:00:00")
            self.assertEqual(window.observed_peak_cms, 34.1)

    def test_select_netcdf_files_for_multi_year_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for year in [1964, 1965, 1966]:
                (root / f"nananxi_precip_hourly_1km_{year}.nc").write_text(
                    "placeholder",
                    encoding="utf-8",
                )

            paths = select_netcdf_files(
                root,
                start=resolve_time_window("1964-12-31 23:00", "1965-01-01 01:00").start,
                end=resolve_time_window("1964-12-31 23:00", "1965-01-01 01:00").end,
            )

            self.assertEqual([path.name for path in paths], [
                "nananxi_precip_hourly_1km_1964.nc",
                "nananxi_precip_hourly_1km_1965.nc",
            ])


if __name__ == "__main__":
    unittest.main()
