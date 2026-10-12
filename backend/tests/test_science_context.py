"""Scientific versions bind exact PET/config contents to new state handoff."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL, read_json, write_json
from science_context import ScienceVersions, freeze_science_selection, inherit_state_science, digest_file, relocated_task_science, IDENTITY_KEYS
from science_context import model_implementation_identity
from state_integrity import assert_state_input_compatibility


class ScienceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.registry = ScienceVersions(self.runtime)
        self.state = {"fixed_warmup_hours": 168, "pet_config": "legacy-monthly.json"}
        self.initial = {"representation": "CAPACITY_FRACTION", "WU_fraction": .6, "WL_fraction": .6, "WD_fraction": .6}
        self.pet = {"mode": "MONTHLY_CLIMATOLOGY", "monthly_values_mm_day": [3.] * 12, "production_eligible": False}

    def tearDown(self):
        self.temp.cleanup()

    def register(self, **updates):
        request = {"name": "科学条件", "state_config": self.state, "initial_land_state": self.initial, "pet_config": self.pet}
        request.update(updates)
        return self.registry.register(**request)

    def test_registration_is_content_addressed_and_does_not_select_active(self):
        first, second = self.register(), self.register(name="显示名称可不同")
        self.assertEqual(first["scienceVersionId"], second["scienceVersionId"])
        self.assertFalse(first["productionEligible"])
        self.assertFalse((self.runtime / "current-science.json").exists())
        self.assertTrue(all(path.is_file() for path in first["paths"].values()))

    def test_actual_forcing_loader_is_covered_by_model_implementation_identity(self):
        identity = model_implementation_identity()
        entry = next(row for row in identity['files'] if row['path'] == 'platform/strict_forcing.py')
        self.assertEqual(entry['sha256'], digest_file(Path(__file__).resolve().parents[1] / 'strict_forcing.py'))

    def test_portable_science_snapshot_preserves_values_and_rejects_tampering(self):
        first = self.register()
        portable = self.registry.export(first['scienceVersionId'])
        target = ScienceVersions(self.root / 'target')
        restored = target.import_snapshot(portable)
        self.assertEqual(restored['scienceConfigurationSHA256'], first['scienceConfigurationSHA256'])
        self.assertEqual(restored['configs'], first['configs'])
        tampered = copy.deepcopy(portable)
        tampered['files']['pet-config.json'] = 'e30='
        with self.assertRaisesRegex(ValueError, '摘要'):
            ScienceVersions(self.root / 'other-target').import_snapshot(tampered)
        escaped = copy.deepcopy(portable)
        escaped['files']['../outside.csv'] = 'e30='
        with self.assertRaises(ValueError):
            ScienceVersions(self.root / 'escape-target').import_snapshot(escaped)

    def test_explicit_apply_selects_request_only_and_keeps_continuous_semantics(self):
        registered = self.register(state_policy={'strategy': 'continuous-origin', 'warmupHours': 168,
                                                 'continuousOrigin': 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP'})
        request, descriptor = self.registry.apply_to_request({'runType': 'simulation'}, registered['scienceVersionId'])
        self.assertEqual(descriptor['statePolicy']['strategy'], 'continuous-origin')
        self.assertEqual(request['science']['versionId'], registered['scienceVersionId'])
        self.assertFalse((self.runtime / 'current-science.json').exists())

    def test_monthly_values_initial_state_and_strategy_have_distinct_identities(self):
        first = self.register()
        pet = copy.deepcopy(self.pet); pet["monthly_values_mm_day"][0] += 1
        other = self.register(pet_config=pet)
        self.assertNotEqual(first["petConfigurationSHA256"], other["petConfigurationSHA256"])
        self.assertNotEqual(first["scienceConfigurationSHA256"], other["scienceConfigurationSHA256"])
        initial = {**self.initial, "WU_fraction": .5}
        changed_initial = self.register(initial_land_state=initial)
        self.assertEqual(first["petConfigurationSHA256"], changed_initial["petConfigurationSHA256"])
        self.assertNotEqual(first["scienceConfigurationSHA256"], changed_initial["scienceConfigurationSHA256"])

    def test_real_pet_csv_is_frozen_and_hash_covers_content(self):
        source = self.root / "pet-source"
        source.mkdir()
        csv = source / "forcing.csv"
        csv.write_text("time,pet_mm_h\n2017-06-19T00:00,0.1\n2017-06-19T01:00,0.2\n", encoding="utf-8")
        config = source / "pet.json"
        write_json(config, {"mode": "HOURLY_CSV", "path": "forcing.csv", "production_eligible": True})
        first = self.register(pet_config=config)
        self.assertFalse(first["productionEligible"])
        self.assertEqual(read_json(first["paths"]["pet_config"])["path"], "pet-data.csv")
        csv.write_text("time,pet_mm_h\n2017-06-19T00:00,0.3\n2017-06-19T01:00,0.2\n", encoding="utf-8")
        second = self.register(pet_config=config)
        self.assertNotEqual(first["petConfigurationSHA256"], second["petConfigurationSHA256"])
        self.assertTrue((first["paths"]["pet_config"].parent / "pet-data.csv").read_text().endswith("0.2\n"))
        (first["paths"]["pet_config"].parent / "pet-data.csv").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PET 依赖内容"):
            self.registry.resolve(first["scienceVersionId"])

    def test_missing_external_pet_data_fails_instead_of_assumption_fallback(self):
        config = self.root / "pet.json"
        write_json(config, {"mode": "HOURLY_CSV", "path": "missing.csv"})
        with self.assertRaisesRegex(ValueError, "依赖缺失"):
            self.register(pet_config=config)

    def test_freeze_default_event_zero_and_simulation_monthly_preserve_current_semantics(self):
        event = {"runType": "calibration", "period": {"mode": "event"}, "lineage": {}}
        simulation = {"runType": "simulation", "period": {"warmupDays": 7}, "lineage": {}}
        frozen_event, zero = freeze_science_selection(event, self.runtime)
        frozen_sim, monthly = freeze_science_selection(simulation, self.runtime)
        self.assertEqual(zero["petMode"], "CONSTANT_ZERO_EXPLICIT_REQUEST")
        self.assertEqual(monthly["petMode"], "MONTHLY_CLIMATOLOGY_ASSUMPTION")
        self.assertNotEqual(zero["petConfigurationSHA256"], monthly["petConfigurationSHA256"])
        resumed = {**copy.deepcopy(frozen_sim), "period": {"warmupDays": 0, "initialStateVersionId": "STATE-JOB-next"}}
        _, again = freeze_science_selection(resumed, self.runtime)
        self.assertEqual(monthly["scienceConfigurationSHA256"], again["scienceConfigurationSHA256"])
        self.assertTrue(all(key in frozen_sim["lineage"] for key in IDENTITY_KEYS))
        self.assertNotIn("science", event)

    def test_event_monthly_choice_and_explicit_science_labels_are_checked(self):
        request, monthly = freeze_science_selection({'runType': 'calibration', 'period': {'mode': 'event'},
            'inputs': {'evaporation': {'mode': 'monthly-climatology-assumption'}},
            'calibration': {'statePolicy': 'continuous-origin'}}, self.runtime)
        self.assertEqual(monthly['petMode'], 'MONTHLY_CLIMATOLOGY_ASSUMPTION')
        self.assertEqual(monthly['statePolicy']['strategy'], 'continuous-origin')
        wrong = copy.deepcopy(request)
        wrong['inputs']['evaporation']['mode'] = 'constant-zero'
        with self.assertRaisesRegex(ValueError, '不是场次零蒸散发'):
            freeze_science_selection(wrong, self.runtime)
        with self.assertRaisesRegex(ValueError, '明确选择'):
            freeze_science_selection({'runType': 'simulation', 'inputs': {'evaporation': {'mode': 'science-version'}}}, self.runtime)

    def test_changed_frozen_version_or_implementation_request_rejected(self):
        frozen, descriptor = freeze_science_selection({"runType": "simulation"}, self.runtime)
        altered = copy.deepcopy(frozen); altered["lineage"]["modelImplementationSHA256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "实现或科学配置"):
            freeze_science_selection(altered, self.runtime)
        write_json(descriptor["paths"]["initial_land_state"], {"WU_fraction": 0.9})
        with self.assertRaises(ValueError):
            freeze_science_selection(frozen, self.runtime)

    def test_frontend_null_calibration_simulation_freezes_without_guessing_strategy(self):
        request, descriptor = freeze_science_selection({'runType': 'simulation', 'calibration': None,
            'inputs': {'evaporation': {'mode': 'monthly-climatology-assumption'}}}, self.runtime)
        self.assertEqual(descriptor['statePolicy']['strategy'], 'independent-warmup')
        self.assertEqual(request['calibration'], None)
        again, _ = freeze_science_selection(request, self.runtime)
        self.assertEqual(again['science'], request['science'])

    def test_state_handoff_rejects_legacy_and_any_science_identity_change(self):
        request, _ = freeze_science_selection({"runType": "simulation", "inputs": {}, "lineage": {}}, self.runtime)
        checkpoint = {key: request["lineage"][key] for key in IDENTITY_KEYS}
        assert_state_input_compatibility(checkpoint, request)
        for key in IDENTITY_KEYS:
            changed = {**checkpoint, key: "0" * 64}
            with self.assertRaisesRegex(ValueError, "重新初始化"):
                assert_state_input_compatibility(changed, request)
        with self.assertRaisesRegex(ValueError, "历史状态.*重新初始化"):
            assert_state_input_compatibility({}, request)

    def test_state_handoff_inherits_complete_parent_conditions_instead_of_monthly_default(self):
        source = self.register(state_policy={'strategy': 'continuous-origin', 'warmupHours': 168,
                                             'continuousOrigin': 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP'})
        frozen, _ = self.registry.apply_to_request({'runType': 'simulation'}, source['scienceVersionId'])
        parent = {**frozen['lineage'], 'scienceSnapshot': self.registry.export(source['scienceVersionId'])}
        request = {'runType': 'forecast', 'inputs': {'evaporation': {'mode': 'monthly-climatology-assumption'}}}
        inherited = inherit_state_science(request, parent, self.runtime)
        self.assertEqual(inherited['science'], frozen['science'])
        self.assertEqual(inherited['inputs']['evaporation']['mode'], 'science-version')
        final, descriptor = freeze_science_selection(inherited, self.runtime)
        assert_state_input_compatibility(parent, final)
        self.assertEqual(descriptor['statePolicy']['strategy'], 'continuous-origin')
        self.assertNotIn('science', request)

    def test_state_handoff_rejects_explicit_other_conditions_and_old_implementation(self):
        source = self.register()
        other = self.register(pet_config={**self.pet, 'monthly_values_mm_day': [5.] * 12})
        frozen, _ = self.registry.apply_to_request({'runType': 'simulation'}, source['scienceVersionId'])
        parent = frozen['lineage']
        with self.assertRaisesRegex(ValueError, '沿用父状态'):
            inherit_state_science({'runType': 'forecast', 'science': {'versionId': other['scienceVersionId']}}, parent, self.runtime)
        with self.assertRaisesRegex(ValueError, '模型实现已更新'):
            inherit_state_science({'runType': 'stateContinuation'}, {**parent, 'modelImplementationSHA256': '0' * 64}, self.runtime)
        with self.assertRaisesRegex(ValueError, '历史状态'):
            inherit_state_science({'runType': 'stateContinuation'}, {}, self.runtime)

    def test_state_handoff_recovers_portable_parent_snapshot_in_new_workspace(self):
        source = self.register()
        frozen, _ = self.registry.apply_to_request({'runType': 'simulation'}, source['scienceVersionId'])
        parent = {**frozen['lineage'], 'scienceSnapshot': self.registry.export(source['scienceVersionId'])}
        destination = self.root / 'restored'
        inherited = inherit_state_science({'runType': 'stateContinuation'}, parent, destination)
        descriptor = ScienceVersions(destination).resolve(inherited['science']['versionId'])
        self.assertEqual(descriptor['configs'], source['configs'])
        self.assertEqual(descriptor['scienceConfigurationSHA256'], parent['scienceConfigurationSHA256'])

    def test_continuous_initialization_uses_selected_conditions_and_rejects_fixed_method(self):
        source = self.register(state_policy={'strategy': 'continuous-origin', 'warmupHours': 168,
                                             'continuousOrigin': 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP'})
        request = {'runType': 'stateInitialization', 'science': {'versionId': source['scienceVersionId']},
                   'initialization': {'strategy': 'CONTINUOUS'}, 'inputs': {'evaporation': {'mode': 'science-version'}}}
        frozen, descriptor = freeze_science_selection(request, self.runtime)
        self.assertEqual(descriptor['statePolicy']['strategy'], 'continuous-origin')
        self.assertEqual(frozen['science']['versionId'], source['scienceVersionId'])
        request['initialization']['strategy'] = 'FIXED_WARMUP'
        with self.assertRaisesRegex(ValueError, '初始化方式'):
            freeze_science_selection(request, self.runtime)

    def test_relocated_same_task_snapshots_need_matching_digest(self):
        folder = self.root / "CAL-task"
        write_json(folder / "resolved_request.json", {key: "E:/old/" + key + ".json" for key in ("state_config", "initial_land_state", "pet_config")})
        expected = {}
        for key, config in (("state_config", self.state), ("initial_land_state", self.initial), ("pet_config", self.pet)):
            path = folder / (key + "_snapshot.json")
            write_json(path, config); expected[key] = digest_file(path)
        result = relocated_task_science(folder, expected)
        self.assertTrue(all(path.parent == folder for path in result["paths"].values()))
        invalid = {**expected, "pet_config": "0" * 64}
        with self.assertRaisesRegex(ValueError, "摘要"):
            relocated_task_science(folder, invalid)
        result["paths"]["pet_config"].unlink()
        with self.assertRaisesRegex(ValueError, "不能静默"):
            relocated_task_science(folder, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
