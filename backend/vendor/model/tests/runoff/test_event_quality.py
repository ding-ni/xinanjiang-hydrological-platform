import unittest

import numpy as np
import pandas as pd

from runoff_platform.data.event_quality import evaluate_event_quality


CONFIG = {
    "basin_area_km2": 10.0,
    "rules": {
        "maximum_duration_hours": 10,
        "minimum_paired_flow_hours": 2,
        "minimum_precipitation_valid_fraction": 1.0,
        "minimum_source_stations": 1,
        "minimum_source_station_hour_fraction": 1.0,
        "minimum_warmup_event_rain_mm": 1.0,
        "maximum_observed_runoff_to_precipitation_ratio": 1.2,
    },
}


def evaluate(precipitation, flow, *, end_hour=2, station_count=None):
    times = pd.date_range("2020-01-01", periods=len(precipitation), freq="h")
    observed = pd.Series(flow, index=times[: len(flow)])
    return evaluate_event_quality(
        event_id="E1",
        start=times[0],
        end=times[end_hour],
        times=times,
        grid_precipitation_mm=np.asarray(precipitation, dtype=float)[:, None],
        grid_areas_km2=np.asarray([1.0]),
        observed_flow_cms=observed,
        source_station_count=np.asarray(station_count) if station_count is not None else None,
        config=CONFIG,
    )


class EventQualityTests(unittest.TestCase):
    def test_zero_rain_flow_event_is_rejected(self):
        result = evaluate([0.0, 0.0, 0.0], [10.0, 12.0, 8.0], station_count=[1, 1, 1])
        self.assertFalse(result["accepted"])
        self.assertIn("WARMUP_EVENT_RAIN_BELOW_MINIMUM", result["exclusion_reasons"])

    def test_physically_plausible_event_is_accepted(self):
        result = evaluate([10.0, 10.0, 10.0], [10.0, 12.0, 8.0], station_count=[2, 2, 2])
        self.assertTrue(result["accepted"])

    def test_excessive_runoff_ratio_is_rejected(self):
        result = evaluate([1.0, 0.0, 0.0], [100.0, 100.0, 100.0], station_count=[1, 1, 1])
        self.assertIn("RUNOFF_PRECIPITATION_RATIO_EXCEEDS_MAXIMUM", result["exclusion_reasons"])

    def test_duration_limit_is_configurable(self):
        precipitation = [10.0] * 12
        result = evaluate(precipitation, [1.0] * 12, end_hour=11, station_count=[2] * 12)
        self.assertIn("DURATION_EXCEEDS_MAXIMUM", result["exclusion_reasons"])

    def test_source_station_count_can_be_required(self):
        config = {**CONFIG, "rules": {**CONFIG["rules"], "require_source_station_count": True}}
        times = pd.date_range("2020-01-01", periods=3, freq="h")
        result = evaluate_event_quality(
            event_id="E1",
            start=times[0],
            end=times[-1],
            times=times,
            grid_precipitation_mm=np.asarray([[10.0], [10.0], [10.0]]),
            grid_areas_km2=np.asarray([1.0]),
            observed_flow_cms=pd.Series([1.0, 1.0, 1.0], index=times),
            source_station_count=None,
            config=config,
        )
        self.assertIn("SOURCE_STATION_COUNT_UNAVAILABLE", result["exclusion_reasons"])


if __name__ == "__main__":
    unittest.main()
