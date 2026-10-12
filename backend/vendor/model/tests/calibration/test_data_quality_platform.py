import unittest
from unittest.mock import patch

from data_quality_platform import get_data_quality_catalog, run_data_quality_task
from data_quality_platform.service import DataQualityRequestError, _metadata_exclusions


class DataQualityPlatformTests(unittest.TestCase):
    def test_catalog_exposes_ready_modules_and_dependency(self):
        catalog = get_data_quality_catalog()
        modules = {item["module_id"]: item for item in catalog["modules"]}
        self.assertEqual(modules["DQ-PRECIP-IDW-01"]["status"], "READY")
        self.assertEqual(modules["DQ-EVENT-QC-02"]["depends_on"], ["DQ-PRECIP-IDW-01"])
        self.assertEqual(modules["STATE-INITIALIZATION-03"]["status"], "READY")
        self.assertEqual(
            modules["STATE-INITIALIZATION-03"]["request_schema"],
            "data_interface/state_initialization_schema.json",
        )
        self.assertEqual(modules["RUNOFF-XAJ-04"]["status"], "READY")
        self.assertEqual(modules["ROUTING-05"]["status"], "READY")
        self.assertEqual(modules["MULTI-EVENT-CALIBRATION-06"]["status"], "READY_ASYNC")
        self.assertEqual(
            modules["MULTI-EVENT-CALIBRATION-06"]["request_schema"],
            "data_interface/formal_calibration_task_schema.json",
        )
        self.assertEqual(modules["CAL-WLM-BOUNDARY-07"]["status"], "READY")
        self.assertEqual(modules["CAL-MULTI-SEED-08"]["status"], "READY")
        self.assertEqual(modules["CAL-CONTINUOUS-BALANCE-09"]["status"], "READY")
        self.assertEqual(modules["PRODUCTION-CALIBRATION-10"]["status"], "READY_ASYNC")
        self.assertEqual(
            modules["PRODUCTION-CALIBRATION-10"]["request_schema"],
            "data_interface/production_calibration_workflow_schema.json",
        )

    def test_unknown_module_is_rejected(self):
        with self.assertRaises(DataQualityRequestError):
            run_data_quality_task({"module_id": "UNKNOWN"})

    def test_single_allowed_year_range_accepts_flat_json_array(self):
        item = {
            "start": "2017-01-01T00:00:00",
            "end": "2017-01-02T00:00:00",
            "observedInputs": {"rainCompleteness": 1.0, "riverCompleteness": 1.0},
        }
        self.assertEqual(_metadata_exclusions(item, {"allowed_year_ranges": [2017, 2017]}), [])

    @patch("data_quality_platform.service._run_precipitation_task")
    def test_dispatches_precipitation_module(self, handler):
        handler.return_value = {"status": "SUCCEEDED"}
        result = run_data_quality_task({"module_id": "DQ-PRECIP-IDW-01", "project_root": "."})
        self.assertEqual(result["status"], "SUCCEEDED")
        handler.assert_called_once()

    @patch("data_quality_platform.service._run_event_quality_task")
    def test_dispatches_event_quality_module(self, handler):
        handler.return_value = {"status": "SUCCEEDED"}
        result = run_data_quality_task({"module_id": "DQ-EVENT-QC-02", "project_root": "."})
        self.assertEqual(result["status"], "SUCCEEDED")
        handler.assert_called_once()

    @patch("data_quality_platform.service.run_state_initialization_task")
    def test_dispatches_state_initialization_module(self, handler):
        handler.return_value = {"status": "SUCCEEDED"}
        result = run_data_quality_task(
            {"module_id": "STATE-INITIALIZATION-03", "project_root": "."}
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        handler.assert_called_once()


if __name__ == "__main__":
    unittest.main()
