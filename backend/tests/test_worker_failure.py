"""Frozen worker failures must retain the actionable exception, not the bootloader banner."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import worker_error_message


class WorkerFailureTests(unittest.TestCase):
    def test_frozen_import_error_is_not_hidden_by_pyi_banner(self):
        lines = ['Traceback (most recent call last):', '  File "worker.py", line 1',
                 "ModuleNotFoundError: No module named 'shapely.ops'",
                 "[PYI-34300:ERROR] Failed to execute script 'app_entry' due to unhandled exception!"]
        self.assertEqual(worker_error_message(lines, 1), lines[-2])

    def test_chained_exception_prefers_last_exception(self):
        self.assertEqual(worker_error_message(['KeyError: old', 'ValueError: current', ''], 1),
                         'ValueError: current')

    def test_empty_or_only_bootloader_output_keeps_exit_code(self):
        for lines in ([], ['[PYI-1:ERROR] bootloader failed']):
            self.assertEqual(worker_error_message(lines, 3), '模型进程退出：3')

    def test_non_python_failure_keeps_last_meaningful_line(self):
        self.assertEqual(worker_error_message(['starting', 'invalid input', ''], 2), 'invalid input')


if __name__ == '__main__':
    unittest.main()
