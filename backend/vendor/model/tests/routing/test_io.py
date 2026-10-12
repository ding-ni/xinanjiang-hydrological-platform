import json
import tempfile
import unittest
from pathlib import Path

from xaj_routing.io import read_control_points_json


class IoTests(unittest.TestCase):
    def test_read_control_point_from_snap_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control.json"
            path.write_text(
                json.dumps(
                    {
                        "control_id": "xufan_70115800",
                        "name": "Xufan control",
                        "snapped": {"reach_id": "R0020"},
                    }
                ),
                encoding="utf-8",
            )

            controls = read_control_points_json(path)

            self.assertEqual(len(controls), 1)
            self.assertEqual(controls[0].control_id, "xufan_70115800")
            self.assertEqual(controls[0].name, "Xufan control")
            self.assertEqual(controls[0].reach_id, "R0020")


if __name__ == "__main__":
    unittest.main()
