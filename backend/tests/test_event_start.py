import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import write_json
from contracts import normalize_request
from event_start import validate_event_start
from test_unified_backend import calibration


class EventStartTests(unittest.TestCase):
    def test_new_event_request_rejects_continuous_origin_in_either_location_and_parent_state(self):
        request = calibration()
        for target, values in [('calibration', {'statePolicy': 'continuous-origin'}),
                               ('calibration', {'continuousOrigin': '2010-01-01T00:00'}),
                               ('period', {'continuousOrigin': '2010-01-01T00:00'}),
                               ('period', {'initialStateVersionId': 'STATE-old'})]:
            changed = copy.deepcopy(request)
            changed[target].update(values)
            with self.subTest(target=target, values=values), self.assertRaisesRegex(ValueError, '逐场独立预热'):
                normalize_request(changed, case_catalog())
        self.assertEqual(normalize_request(request, case_catalog())['period']['mode'], 'event')

    def test_history_replay_checks_completed_server_record_and_actual_conditions(self):
        request = calibration()
        request['calibration'].update(statePolicy='continuous-origin', continuousOrigin='2010-01-01T00:00')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'tasks/JOB-source'
            write_json(folder / 'request.json', request)
            write_json(folder / 'status.json', {'status': 'completed'})
            replay = copy.deepcopy(request)
            replay['lineage']['replayOfTaskId'] = 'JOB-source'
            validate_event_start(replay, root)
            for field in ('continuousOrigin', 'initialParameters'):
                changed = copy.deepcopy(replay)
                changed['calibration'][field] = 'changed'
                with self.assertRaisesRegex(ValueError, '逐场独立预热'):
                    validate_event_start(changed, root)
            write_json(folder / 'status.json', {'status': 'failed'})
            with self.assertRaisesRegex(ValueError, '逐场独立预热'):
                validate_event_start(replay, root)


if __name__ == '__main__':
    unittest.main()
