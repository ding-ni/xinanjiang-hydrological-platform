import unittest

from calibration_platform.qualification import (
    load_qualification_config,
    qualification_definition,
    qualify_event,
)


class EventQualificationTests(unittest.TestCase):
    def test_single_nse_threshold_is_the_only_qualification_flag(self):
        config = load_qualification_config()
        self.assertTrue(qualify_event({"NSE": 0.5}, config))
        self.assertFalse(qualify_event({"NSE": 0.4999}, config))
        self.assertFalse(qualify_event({"NSE": None}, config))
        self.assertFalse(config["strict_combined_rate_enabled"])

    def test_peak_and_volume_errors_do_not_change_ordinary_qualification(self):
        config = load_qualification_config()
        poor_errors = {
            "NSE": 0.7,
            "peak_error_percent": -90.0,
            "peak_time_error_hours": 24.0,
            "volume_error_percent": -80.0,
        }
        self.assertTrue(qualify_event(poor_errors, config))
        definition = qualification_definition(config)
        self.assertEqual(definition["metric"], "NSE")
        self.assertEqual(definition["minimum"], 0.5)
        self.assertFalse(definition["strict_combined_rate_enabled"])


if __name__ == "__main__":
    unittest.main()
