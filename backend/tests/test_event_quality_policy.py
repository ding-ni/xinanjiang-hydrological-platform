"""Runoff/rain ratios are descriptive; missing driving data still rejects events."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL
sys.path.insert(0, str(MODEL / 'code'))
import numpy as np
import pandas as pd
from strict_forcing import evaluate_event_quality


class EventQualityPolicyTests(unittest.TestCase):
    def setUp(self):
        times = pd.date_range('2022-03-17T09:00', periods=48, freq='h')
        self.arguments = dict(event_id='HIGH-RATIO', start=times[0], end=times[-1],
                              times=times, grid_precipitation_mm=np.ones((48, 1)),
                              grid_areas_km2=np.ones(1), observed_flow_cms=pd.Series(100., index=times),
                              source_station_count=np.ones(48),
                              config={'basin_area_km2': 1., 'rules': {
                                  'maximum_observed_runoff_to_precipitation_ratio': 1.2}})

    def test_high_ratio_keeps_statistics_and_historical_rejection(self):
        result = evaluate_event_quality(**self.arguments)
        original = evaluate_event_quality(**self.arguments, legacy=True)
        self.assertAlmostEqual(result['observed_runoff_to_precipitation_ratio'], 360.)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['exclusion_reasons'], [])
        self.assertFalse(original['accepted'])
        self.assertEqual(original['exclusion_reasons'], ['RUNOFF_PRECIPITATION_RATIO_EXCEEDS_MAXIMUM'])
        for key in original.keys() - {'accepted', 'exclusion_reasons'}:
            self.assertEqual(result[key], original[key])

    def test_high_ratio_does_not_override_other_rejections(self):
        self.arguments['grid_precipitation_mm'][0, 0] = np.nan
        self.arguments['source_station_count'][1] = 0
        self.arguments['observed_flow_cms'].iloc[1:] = np.nan
        result = evaluate_event_quality(**self.arguments)
        self.assertFalse(result['accepted'])
        self.assertCountEqual(result['exclusion_reasons'], [
            'PRECIPITATION_INCOMPLETE', 'SOURCE_STATIONS_BELOW_MINIMUM', 'FLOW_RECORD_TOO_SHORT'])
