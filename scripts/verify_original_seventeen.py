"""Verify all 17 delivered flood windows with one fixed-parameter platform job.

The original 16 accepted windows and seven physical groups retain their saved
evaluation scope. The later 17-window benchmark uses common observed/our/XAJ
reference hours. This acceptance does not run a new parameter search.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "后端文件/南安溪产流汇流模型_20261012终结版/南安溪产流汇流模型_20261012终结版"
DEFAULT_OUTPUT = ROOT / "开发产物/同事方案完整接入验收_20261012/17场原方案重放"
SOURCE_TASK = "CAL-20261011-104708-95c5368f"
FLOW_TOLERANCE = 1e-6
METRIC_TOLERANCE = 1e-8
VOLUME_TOLERANCE = 1e-5
METRIC_FIELDS = (
    "paired_hours", "NSE", "KGE", "PBIAS_percent", "observed_peak_m3_s",
    "simulated_peak_m3_s", "peak_error_percent", "peak_time_error_hours",
    "volume_error_percent", "qualified",
)
ACTIVE_STATUSES = {"queued", "running", "cancelling"}


def jsonable(value):
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return jsonable(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_number(actual, expected, tolerance):
    actual, expected = jsonable(actual), jsonable(expected)
    if actual is None or expected is None:
        return actual is expected, None
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected, None
    difference = abs(float(actual) - float(expected))
    return math.isfinite(difference) and difference <= tolerance, difference


class Report:
    def __init__(self, output, runtime, source):
        self.output = Path(output)
        self.path = self.output / "17场原方案重放验收.json"
        self.value = {
            "schemaVersion": "1.0", "status": "initializing", "stage": "preflight",
            "startedAt": datetime.now(timezone.utc).isoformat(), "taskId": None,
            "runtime": str(runtime), "source": str(source), "passed": False,
            "productionEligible": False,
            "scope": "ORIGINAL_17_FIXED_REPLAY_ORIGINAL_16_WINDOWS_AND_7_GROUPS_NO_NEW_SEARCH",
            "tolerances": {"flowM3s": FLOW_TOLERANCE, "metrics": METRIC_TOLERANCE,
                           "maskedVolumeM3": VOLUME_TOLERANCE},
            "checks": [], "errors": [], "windows": [], "metricComparisons": [],
            "sourceFilesBefore": [], "sourceFilesAfter": [],
            "artifacts": {"report": str(self.path)},
        }
        self.save()

    def save(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.value["updatedAt"] = datetime.now(timezone.utc).isoformat()
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(jsonable(self.value), ensure_ascii=False, indent=2,
                                        allow_nan=False), encoding="utf-8")
        temporary.replace(self.path)

    def check(self, name, actual, expected=True, *, tolerance=None, required=False):
        actual, expected = jsonable(actual), jsonable(expected)
        passed, difference = (same_number(actual, expected, tolerance) if tolerance is not None
                              else (actual == expected, None))
        row = {"check": name, "passed": passed, "actual": actual, "expected": expected}
        if tolerance is not None:
            row.update(absoluteError=difference, tolerance=tolerance)
        self.value["checks"].append(row)
        if not passed:
            self.value["errors"].append(row)
            if required:
                self.save()
                raise ValueError("验收条件不符：" + name)
        return passed

    def metrics(self, scope, identifier, actual, expected, fields=METRIC_FIELDS):
        differences = {}
        passed = True
        for key in fields:
            left, right = jsonable(actual.get(key)), jsonable(expected.get(key))
            tolerance = (VOLUME_TOLERANCE if key.endswith("_volume_m3")
                         else METRIC_TOLERANCE)
            if isinstance(left, (str, bool)) or isinstance(right, (str, bool)):
                matched, error = left == right, None
            else:
                matched, error = same_number(left, right, tolerance)
            matched = matched and key in actual and key in expected
            differences[key] = {"actual": left, "expected": right,
                                "absoluteError": error, "passed": matched, "tolerance": tolerance}
            passed = passed and matched
        row = {"scope": scope, "id": identifier, "passed": passed, "metrics": differences}
        self.value["metricComparisons"].append(row)
        if not passed:
            self.value["errors"].append({"check": scope, "id": identifier, "metrics": differences})
        return passed


def independent_metrics(index, observed, simulated, common_mask=None):
    """Recalculate the source formulas without calling platform/kernel metrics."""
    import numpy as np

    obs, sim = np.asarray(observed, dtype=float), np.asarray(simulated, dtype=float)
    paired = np.isfinite(obs) & np.isfinite(sim)
    if common_mask is not None:
        paired &= np.asarray(common_mask, dtype=bool)
    obs, sim = obs[paired], sim[paired]
    times = index[paired]
    values = {key: None for key in METRIC_FIELDS}
    values.update(paired_hours=int(paired.sum()), qualified=False)
    if not len(obs):
        return values
    denominator = float(np.sum((obs - np.mean(obs)) ** 2))
    nse = 1.0 - float(np.sum((obs - sim) ** 2)) / denominator if denominator > 0 else None
    std_obs, std_sim = float(np.std(obs)), float(np.std(sim))
    mean_obs = float(np.mean(obs))
    correlation = float(np.corrcoef(obs, sim)[0, 1]) if std_obs > 0 and std_sim > 0 else 0.0
    kge = (1.0 - math.sqrt((correlation - 1.0) ** 2 + (std_sim / std_obs - 1.0) ** 2
                           + (float(np.mean(sim)) / mean_obs - 1.0) ** 2)
           if std_obs > 0 and mean_obs != 0 else None)
    obs_peak, sim_peak = float(np.max(obs)), float(np.max(sim))
    obs_sum, sim_sum = float(np.sum(obs)), float(np.sum(sim))
    volume_error = 100.0 * (sim_sum - obs_sum) / obs_sum if obs_sum != 0 else None
    values.update(
        NSE=nse, KGE=kge, PBIAS_percent=volume_error,
        observed_peak_m3_s=obs_peak, simulated_peak_m3_s=sim_peak,
        peak_error_percent=100.0 * (sim_peak - obs_peak) / obs_peak if obs_peak != 0 else None,
        peak_time_error_hours=abs((times[int(np.argmax(sim))] - times[int(np.argmax(obs))]).total_seconds()) / 3600.0,
        volume_error_percent=volume_error, qualified=nse is not None and nse >= 0.5,
    )
    return values


def independent_volumes(index, observed, simulated):
    import numpy as np

    obs, sim = np.asarray(observed, dtype=float), np.asarray(simulated, dtype=float)
    paired = np.isfinite(obs) & np.isfinite(sim) & (obs >= 0) & (sim >= 0)
    obs_sum = math.fsum(float(item) for item in obs[paired]) * 3600.0
    sim_sum = math.fsum(float(item) for item in sim[paired]) * 3600.0
    complete = bool(paired.all())
    complete_obs = bool((np.isfinite(obs) & (obs >= 0)).all())
    times = index[paired]
    return {
        "paired_hours": int(paired.sum()), "window_hours": len(index),
        "paired_fraction": float(paired.sum()) / len(index),
        "volume_scope": "COMPLETE_EVENT" if complete else "COMMON_VALID_HOURS_ONLY",
        "masked_observed_volume_m3": obs_sum if paired.any() else None,
        "masked_simulated_volume_m3": sim_sum if paired.any() else None,
        "masked_volume_bias_percent": 100.0 * (sim_sum - obs_sum) / obs_sum if obs_sum > 0 else None,
        "complete_observed_volume_m3": math.fsum(float(item) for item in obs) * 3600.0 if complete_obs else None,
        "full_volume_error_available": complete,
        "paired_start": times[0].isoformat() if len(times) else None,
        "paired_end": times[-1].isoformat() if len(times) else None,
        "missing_value_policy": "NO_ZERO_FILL_NO_INTERPOLATION_NO_FULL_VOLUME_EXTRAPOLATION",
    }


def source_paths(source, calibration, reference, plan):
    names = set(plan["sourceFiles"]) | {
        "state_config_snapshot.json", "initial_land_state_snapshot.json", "pet_config_snapshot.json",
        "result.json", "output/formal_multi_event_report.json", "xufan3.20261004.mdb",
    }
    paths = [(calibration / name).resolve() for name in sorted(names)]
    paths += [reference / "17场三方案过程数据.csv", reference / "17场逐场对比指标.csv"]
    paths += [source / "空间可视化人工调参版/model/config/parameter_set_land.json",
              source / "空间可视化人工调参版/model/config/routing_parameters_control_split_v2.json"]
    if any(not path.is_relative_to(source) or path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("来源证据缺失、包含链接或超出指定交付目录")
    return paths


def file_records(paths, source):
    return [{"path": str(path), "relativePath": path.relative_to(source).as_posix(),
             "sha256": sha256(path), "bytes": path.stat().st_size} for path in paths]


def select_candidate(runtime, parameter_id, current_identity, report):
    from common import safe_task
    from original_calibration_plan import validate_plan

    if parameter_id:
        records = [read_json(safe_task(runtime / "parameters", parameter_id).with_suffix(".json"))]
    else:
        records = [read_json(path) for path in sorted((runtime / "parameters").glob("*.json"))]
    matches = [row for row in records if row.get("candidateKind") == "frozen-calibration"
               and isinstance(row.get("originalCalibrationPlan"), dict)
               and row.get("lineage", {}).get("modelImplementationSHA256") == current_identity
               and not row.get("requiresInputRegistration")]
    report.check("exactly_one_current_frozen_candidate", len(matches), 1, required=True)
    candidate = matches[0]
    if parameter_id:
        report.check("explicit_parameter_id", candidate["parameterVersionId"], parameter_id, required=True)
    plan = validate_plan(candidate["originalCalibrationPlan"])
    report.check("original_source_task", plan["sourceTaskId"], SOURCE_TASK, required=True)
    report.check("candidate_production_eligible", candidate.get("productionEligible"), False, required=True)
    return candidate, plan


def preflight(args, report):
    import pandas as pd
    from input_products import InputProducts
    from original_calibration_plan import build_original_request, source_input
    from routing_versions import RoutingVersions
    from science_context import ScienceVersions, model_implementation_identity

    identity = model_implementation_identity()
    candidate, plan = select_candidate(args.runtime, args.parameter, identity["modelImplementationSHA256"], report)
    report.value.update(parameterVersionId=candidate["parameterVersionId"], sourcePlan=plan,
                        modelImplementationBefore=identity)
    model_source = args.source / "空间可视化人工调参版/model"
    calibration = model_source / "validation/platform_calibration_tasks" / plan["sourceTaskId"]
    reference = args.source / "最新对比结果/20261011_17场洪水三方案对比(最优）"
    paths = source_paths(args.source, calibration, reference, plan)
    report.value["sourceFilesBefore"] = file_records(paths, args.source)
    for name, digest in plan["sourceFiles"].items():
        report.check("sealed_source_file:" + name, sha256(calibration / name), digest, required=True)
    report.check("source_mdb_sha256", sha256(calibration / "xufan3.20261004.mdb"),
                 plan["sourceMDBSHA256"], required=True)
    source_result = read_json(calibration / "result.json")
    original_parameters = {key.split(".")[-1]: value
                           for key, value in source_result["resolved_runoff_parameter_set"].items()}
    report.check("original_frozen_runoff_parameters", candidate["bestParameters"], original_parameters, required=True)
    original_routing = copy.deepcopy(read_json(calibration / "routing_parameter_snapshot.json"))
    search = source_result["common_parameter_set"]
    for component in ("RS", "RI", "RG"):
        original_routing["default_slope_k_hours"][component] = search["routing." + component + "_k_hours"]
    for reach in original_routing["reach_muskingum"].values():
        reach["k_hours"] *= search["routing.muskingum_k_scale"]
        reach["x"] = search["routing.muskingum_x"]
    original_routing.setdefault("metadata", {})["muskingum_x"] = search["routing.muskingum_x"]
    routing = RoutingVersions(args.runtime).resolve(candidate["routingCandidate"]["routingVersionId"])
    report.check("original_frozen_routing_parameters", routing["parameters"], original_routing, required=True)
    report.check("candidate_original_science", candidate["science"], plan["science"], required=True)
    science = ScienceVersions(args.runtime).resolve(candidate["science"]["versionId"])
    for key, name in (("state_config", "state_config_snapshot.json"),
                      ("initial_land_state", "initial_land_state_snapshot.json"),
                      ("pet_config", "pet_config_snapshot.json")):
        expected = read_json(calibration / name)
        if key == "state_config" and "pet_config" in expected:
            expected["pet_config"] = "pet-config.json"
        report.check("original_science_contents:" + key, read_json(science["paths"][key]), expected, required=True)
    products = InputProducts(runtime_root=args.runtime)
    bundle = source_input(plan, products)
    manifest = bundle["manifest"]
    report.check("candidate_input_bundle", candidate.get("requiredInputBundleVersionId"),
                 bundle["bundleVersionId"], required=True)
    report.check("candidate_input_digest", candidate.get("lineage", {}).get("inputBundleManifestSHA256"),
                 bundle["manifestSHA256"], required=True)
    for key, expected in (("sourceQcPolicy", "SOURCE_ORIGINAL_20261011"),
                          ("targetCoordinatePolicy", "LEGACY_CLOSED_RING_VERTEX_MEAN"),
                          ("forcingValueDtype", "float64"),
                          ("eventQualityConfigurationSHA256", plan["qcConfigurationSHA256"])):
        report.check("source_input:" + key, manifest.get(key), expected, required=True)
    report.check("source_input_mdb", manifest.get("sourceOrigin", {}).get("databaseSHA256"),
                 plan["sourceMDBSHA256"], required=True)
    report.check("source_qc_bytes", sha256(bundle["paths"]["quality"]), plan["qcConfigurationSHA256"], required=True)
    report.check("source_input_accepted_ids", sorted(manifest.get("summary", {}).get("acceptedEventIds", [])),
                 sorted(plan["sourceAcceptedEventIds"]), required=True)
    report.value["input"] = {key: jsonable(bundle[key]) for key in
                             ("bundleVersionId", "manifestSHA256", "manifestPath", "forcingVersion", "observationVersion")}
    request = build_original_request(candidate["parameterVersionId"], args.runtime, operation="fixed-replay", products=products)
    report.check("fixed_parameter_algorithm", request["calibration"]["optimizer"]["algorithm"], "FIXED_PARAMETERS", required=True)
    report.check("single_evaluation_requested", request["calibration"]["optimizer"]["maxEvaluations"], 1, required=True)
    report.check("source_window_count", len(plan["sourceEventWindows"]), 17, required=True)
    report.check("original_accepted_count", len(plan["sourceAcceptedEventIds"]), 16, required=True)
    report.check("original_group_count", len(plan["overlapGroups"]), 7, required=True)
    report.check("supplementary_only_31230701", plan["sourceExcludedEventIds"], ["xf-31230701"], required=True)
    report.check("partial_observation_31230901_accepted", "xf-31230901" in plan["sourceAcceptedEventIds"], required=True)
    report.check("original_warmup_hours", plan["actualWarmupHours"], 168, required=True)
    report.check("original_continuous_origin", plan["continuousOrigin"], "2020-12-25T00:00:00", required=True)
    report.value["originalAcceptance"] = {"acceptedEventIds": plan["sourceAcceptedEventIds"],
                                          "supplementaryEventIds": plan["sourceExcludedEventIds"],
                                          "physicalGroups": plan["overlapGroups"]}
    frames = {
        "process": pd.read_csv(reference / "17场三方案过程数据.csv", dtype={"flood_code": str}, float_precision="round_trip"),
        "benchmark": pd.read_csv(reference / "17场逐场对比指标.csv", dtype={"flood_code": str}, float_precision="round_trip"),
        "windows": pd.read_csv(calibration / "output/event_metrics.csv", dtype={"event_id": str}, float_precision="round_trip"),
        "groups": pd.read_csv(calibration / "output/overlap_group_metrics.csv", dtype={"event_id": str}, float_precision="round_trip"),
    }
    frames["process"]["time"] = pd.to_datetime(frames["process"]["time"])
    report.check("source_process_naive_time", frames["process"]["time"].dt.tz is None, required=True)
    report.check("source_benchmark_models", sorted(frames["benchmark"]["model"].unique().tolist()),
                 sorted(["我们方案", "新安江参考"]), required=True)
    benchmark = frames["benchmark"].query("model == '我们方案'")
    report.check("seventeen_benchmark_rows", len(benchmark), 17, required=True)
    report.check("seventeen_benchmark_unique", int(benchmark["flood_code"].nunique()), 17, required=True)
    frames["benchmark"] = benchmark
    report.check("source_accepted_window_ids", sorted(frames["windows"]["event_id"].tolist()),
                 sorted(plan["sourceAcceptedEventIds"]), required=True)
    report.check("source_original_group_count", len(frames["groups"]), 7, required=True)
    qualification = read_json(calibration / "qualification_config_snapshot.json")
    for key, expected in (("method", "NSE_THRESHOLD"), ("metric", "NSE"), ("minimum", 0.5),
                          ("strict_combined_rate_enabled", False)):
        report.check("source_qualification:" + key, qualification.get(key), expected, required=True)
    active = []
    for path in sorted((args.runtime / "tasks").glob("*/status.json")):
        status = read_json(path)
        if status.get("status") in ACTIVE_STATUSES:
            active.append({"taskId": status.get("taskId"), "status": status["status"]})
    report.check("runtime_has_no_other_active_task", active, [], required=True)
    request_path = report.output / "固定参数重放请求.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    report.value["artifacts"]["submittedRequest"] = str(request_path)
    report.save()
    return candidate, plan, bundle, science, request, frames, paths


def compare_result(result, candidate, plan, bundle, science, frames, report, task_folder):
    import numpy as np
    import pandas as pd

    report.value.update(stage="comparing_hydrographs_and_metrics", resultVersionId=result.get("resultVersionId"))
    report.save()
    marker = {"parameterVersionId": candidate["parameterVersionId"], "planId": plan["planId"],
              "planSHA256": plan["planSHA256"], "operation": "fixed-replay"}
    report.check("result_source_plan", result.get("sourcePlan"), marker)
    report.check("result_production_eligible", result.get("productionEligible"), False)
    report.check("result_frozen_runoff_parameters", result.get("bestParameters"), candidate["bestParameters"])
    report.check("result_science", result.get("science"), candidate["science"])
    report.check("result_science_snapshot", result.get("scienceSnapshot"), candidate["scienceSnapshot"])
    report.check("result_state_policy", result.get("statePolicy"), "continuous-origin")
    report.check("result_continuous_origin", result.get("continuousOrigin"), plan["continuousOrigin"])
    report.check("result_actual_warmup", result.get("actualWarmupHours"), plan["actualWarmupHours"])
    report.check("result_pet_mode", result.get("petMode"), science["petMode"])
    lineage = result.get("lineage", {})
    expected_lineage = {
        "originalCalibrationPlanId": plan["planId"], "originalCalibrationPlanSHA256": plan["planSHA256"],
        "modelImplementationSHA256": candidate["lineage"]["modelImplementationSHA256"],
        "scienceVersionId": candidate["science"]["versionId"],
        "scienceConfigurationSHA256": candidate["science"]["configurationSHA256"],
        "petConfigurationSHA256": candidate["science"]["petConfigurationSHA256"],
        "inputBundleVersionId": bundle["bundleVersionId"], "inputBundleManifestSHA256": bundle["manifestSHA256"],
        "forcingVersion": bundle["forcingVersion"], "observationVersion": bundle["observationVersion"],
        "routingVersionId": candidate["routingCandidate"]["routingVersionId"],
    }
    for key, expected in expected_lineage.items():
        report.check("result_lineage:" + key, lineage.get(key), expected)
    provenance = read_json(task_folder / "input-manifest.json")
    report.check("result_frozen_routing_contents", provenance.get("routingParameters"), candidate["bestRoutingParameters"])
    optimizer = result.get("optimizer", {})
    report.check("fixed_algorithm_executed", optimizer.get("algorithm"), "FIXED_PARAMETERS")
    report.check("one_evaluation_executed", optimizer.get("evaluations"), 1)
    report.check("one_history_record", len(optimizer.get("history", [])), 1)
    report.check("no_optimizer_search_stages", optimizer.get("stages", []), [])
    report.check("nse_qualification_threshold", result.get("qualificationPolicy", {}).get("minimum"), 0.5)
    report.check("nse_qualification_single_metric", result.get("qualificationPolicy", {}).get("metric"), "NSE")
    report.check("combined_qualification_disabled", result.get("qualificationPolicy", {}).get("strict_combined_rate_enabled"), False)
    evaluation = result.get("sourcePlanEvaluation") or {}
    report.check("evaluation_source_task", evaluation.get("sourceTaskId"), plan["sourceTaskId"])
    report.check("evaluation_plan_id", evaluation.get("planId"), plan["planId"])
    report.check("evaluation_original_accepted_ids", evaluation.get("acceptedEventIds"), plan["sourceAcceptedEventIds"])
    report.check("evaluation_supplementary_ids", evaluation.get("supplementaryEventIds"), plan["sourceExcludedEventIds"])
    expected_ids = sorted(row["id"] for row in plan["sourceEventWindows"])
    hydrographs = result.get("hydrographs", {})
    report.check("seventeen_hydrograph_ids", sorted(hydrographs), expected_ids)
    event_rows = {row["eventId"]: row for row in result.get("eventMetrics", [])}
    report.check("seventeen_event_metric_ids", sorted(event_rows), expected_ids)
    report.check("seventeen_event_metrics_without_duplicates", len(result.get("eventMetrics", [])), 17)
    windows = {row["eventId"]: row for row in evaluation.get("originalWindowMetrics", [])}
    report.check("original_window_metric_ids", sorted(windows), sorted(plan["sourceAcceptedEventIds"]))
    report.check("original_window_metrics_without_duplicates", len(evaluation.get("originalWindowMetrics", [])), 16)
    source_windows = {row["event_id"]: row for row in frames["windows"].to_dict("records")}
    benchmark = {row["flood_code"]: row for row in frames["benchmark"].to_dict("records")}
    original_purposes = {row["id"]: row["purpose"] for row in plan["sourceEvents"]}
    purpose_names = {"率定": "CALIBRATION", "验证": "VALIDATION", "独立检验": "INDEPENDENT_TEST"}
    hourly_rows, merged_rows = [], []
    for window in plan["sourceEventWindows"]:
        identifier, code = window["id"], window["id"].removeprefix("xf-")
        expected_axis = pd.date_range(window["start"], window["end"], freq="h")
        source = frames["process"].loc[frames["process"]["flood_code"] == code].set_index("time")
        source_ok = report.check("source_window_hourly_axis:" + identifier, bool(source.index.equals(expected_axis)))
        graph = hydrographs.get(identifier) or {}
        forecast, observed = graph.get("forecast", []), graph.get("obs", [])
        forecast_axis = pd.DatetimeIndex(pd.to_datetime([row["time"] for row in forecast]))
        observed_axis = pd.DatetimeIndex(pd.to_datetime([row["time"] for row in observed]))
        forecast_ok = report.check("forecast_local_hourly_axis:" + identifier, bool(forecast_axis.equals(expected_axis)))
        observed_ok = report.check("observation_local_hourly_axis:" + identifier, bool(observed_axis.equals(expected_axis)))
        report.check("no_temperature_forcing:" + identifier, graph.get("forcing", {}).get("temperature"), [])
        report.check("hydrograph_forcing_bundle:" + identifier, graph.get("forcing", {}).get("bundleVersionId"), bundle["bundleVersionId"])
        if not (source_ok and forecast_ok and observed_ok):
            report.value["windows"].append({"eventId": identifier, "passed": False, "reason": "HOURLY_AXIS_OR_WINDOW_LENGTH_MISMATCH"})
            report.save()
            continue
        sim = np.asarray([row["value"] for row in forecast], dtype=float)
        obs = np.asarray([row["value"] for row in observed], dtype=float)
        source_sim = source["our_simulated_m3_s"].to_numpy(dtype=float)
        source_obs = source["observed_m3_s"].to_numpy(dtype=float)
        finite_sim = report.check("simulation_all_hours_finite:" + identifier, bool(np.isfinite(sim).all()))
        finite_source = report.check("source_simulation_all_hours_finite:" + identifier, bool(np.isfinite(source_sim).all()))
        source_mask, obs_mask = np.isfinite(source_obs), np.isfinite(obs)
        mask_equal = report.check("source_observation_missing_mask:" + identifier, bool(np.array_equal(source_mask, obs_mask)))
        mismatches = np.flatnonzero(source_mask != obs_mask)
        obs_difference = float(np.max(np.abs(obs[source_mask] - source_obs[source_mask]))) if source_mask.any() and mask_equal else None
        report.check("source_observation_values:" + identifier, obs_difference, 0.0, tolerance=FLOW_TOLERANCE)
        difference = np.abs(sim - source_sim)
        max_difference = float(np.max(difference)) if finite_sim and finite_source else None
        flow_equal = report.check("source_simulated_flow:" + identifier, max_difference, 0.0, tolerance=FLOW_TOLERANCE)
        common = source_mask & np.isfinite(source_sim) & np.isfinite(source["xaj_simulated_m3_s"].to_numpy(dtype=float))
        metrics = independent_metrics(expected_axis, obs, sim)
        source_metrics = independent_metrics(expected_axis, source_obs, source_sim)
        report.metrics("all_17_independent_obs_sim_metrics", identifier, event_rows.get(identifier, {}), metrics)
        volume = independent_volumes(expected_axis, obs, sim)
        report.metrics("all_17_masked_volumes", identifier, graph.get("volumeEvaluation", {}), volume, tuple(volume))
        report.metrics("all_17_event_row_masked_volumes", identifier, event_rows.get(identifier, {}), volume, tuple(volume))
        common_metrics = independent_metrics(expected_axis, obs, sim, common)
        report.metrics("original_17_common_hour_benchmark", identifier, common_metrics, benchmark.get(code, {}))
        report.metrics("source_17_common_hour_self_check", identifier,
                       independent_metrics(expected_axis, source_obs, source_sim, common), benchmark.get(code, {}))
        if identifier in source_windows:
            report.metrics("original_16_window_metrics", identifier, windows.get(identifier, {}), source_windows[identifier])
            report.metrics("original_16_independent_window_metrics", identifier, metrics, source_windows[identifier])
            report.metrics("source_16_window_self_check", identifier, source_metrics, source_windows[identifier])
            expected_group = purpose_names[original_purposes[identifier]]
            report.check("original_window_group:" + identifier, windows.get(identifier, {}).get("group"), expected_group)
        if identifier in source_windows:
            report.check("benchmark_split:" + identifier, event_rows.get(identifier, {}).get("group"), benchmark.get(code, {}).get("split"))
        else:
            # The supplementary benchmark label describes search participation,
            # while its replay group describes the continuous physical flood.
            report.check("supplementary_benchmark_not_calibrated:" + identifier,
                         benchmark.get(code, {}).get("split"), "NOT_CALIBRATED_QC_LENGTH_721H")
        row = {"eventId": identifier, "windowHours": len(expected_axis), "pairedObservationHours": int(obs_mask.sum()),
               "missingObservationHours": int((~obs_mask).sum()), "benchmarkCommonHours": int(common.sum()),
               "observationMaskMatched": mask_equal,
               "maskMismatchTimes": [expected_axis[int(index)].isoformat() for index in mismatches[:50]],
               "maskMismatchCount": len(mismatches), "maximumObservationDifferenceM3s": obs_difference,
               "maximumSimulationDifferenceM3s": max_difference,
               "maximumDifferenceTime": expected_axis[int(np.argmax(difference))].isoformat() if finite_sim and finite_source else None,
               "sourceSupplementaryReplay": identifier in plan["sourceExcludedEventIds"],
               "usedForOriginalParameterSearch": identifier in plan["sourceAcceptedEventIds"],
               "benchmarkSplit": benchmark.get(code, {}).get("split"),
               "replayPhysicalGroupPurpose": event_rows.get(identifier, {}).get("group"),
               "independentMetrics": metrics, "benchmarkCommonHourMetrics": common_metrics,
               "maskedVolumeEvaluation": volume, "flowAndObservationMatched": flow_equal and mask_equal}
        report.value["windows"].append(row)
        for index, stamp in enumerate(expected_axis):
            hourly_rows.append({"event_id": identifier, "time": stamp.isoformat(),
                                "source_observed_m3_s": source_obs[index], "actual_observed_m3_s": obs[index],
                                "source_simulated_m3_s": source_sim[index], "actual_simulated_m3_s": sim[index],
                                "absolute_difference_m3_s": difference[index],
                                "source_observation_missing": not bool(source_mask[index]),
                                "actual_observation_missing": not bool(obs_mask[index]), "benchmark_common_hour": bool(common[index])})
            merged_rows.append({"time": stamp, "obs": obs[index], "sim": sim[index]})
        report.save()
    special = {row["eventId"]: row for row in report.value["windows"]}
    report.check("721_hour_supplementary_length", special.get("xf-31230701", {}).get("windowHours"), 721)
    report.check("697_hour_partial_flow_window", special.get("xf-31230901", {}).get("windowHours"), 697)
    report.check("421_available_partial_flow_hours", special.get("xf-31230901", {}).get("pairedObservationHours"), 421)
    groups = {row["groupId"]: row for row in evaluation.get("originalGroupMetrics", [])}
    report.check("original_group_metric_ids", sorted(groups), sorted(row["id"] for row in plan["overlapGroups"]))
    report.check("original_group_metrics_without_duplicates", len(evaluation.get("originalGroupMetrics", [])), 7)
    source_groups = {row["event_id"]: row for row in frames["groups"].to_dict("records")}
    if merged_rows:
        merged = pd.DataFrame(merged_rows).set_index("time")
        inconsistent = merged.groupby(level=0)[["obs", "sim"]].nunique(dropna=False).gt(1).any().any()
        report.check("overlapping_hours_same_shared_state", not bool(inconsistent))
        merged = merged.loc[~merged.index.duplicated()].sort_index()
        for group in plan["overlapGroups"]:
            identifier = group["id"]
            row, expected = groups.get(identifier, {}), source_groups.get(identifier, {})
            report.check("original_group_members:" + identifier, row.get("memberEventIds"), group["memberEventIds"])
            report.check("source_group_members:" + identifier, str(expected.get("member_event_ids", "")).split(";"), group["memberEventIds"])
            report.check("original_group_unique_hours:" + identifier, row.get("uniqueHours"), group["uniqueHours"])
            report.check("original_group_purpose:" + identifier, row.get("group"), purpose_names[group["purpose"]])
            for key in ("start", "end"):
                report.check("original_group_" + key + ":" + identifier, row.get(key), group[key])
                report.check("source_group_" + key + ":" + identifier, expected.get(key), group[key])
            report.metrics("original_7_group_metrics", identifier, row, expected)
            axis = pd.date_range(group["start"], group["end"], freq="h")
            frame = merged.reindex(axis)
            independent = independent_metrics(axis, frame["obs"].to_numpy(), frame["sim"].to_numpy())
            report.metrics("original_7_independent_group_metrics", identifier, independent, expected)
            volumes = independent_volumes(axis, frame["obs"].to_numpy(), frame["sim"].to_numpy())
            report.metrics("original_7_masked_group_volumes", identifier, row, volumes, tuple(volumes))
    hourly_path = report.output / "逐时重放差异.csv"
    pd.DataFrame(hourly_rows).to_csv(hourly_path, index=False, encoding="utf-8-sig")
    metric_path = report.output / "逐指标验收差异.csv"
    metric_rows = [{"scope": row["scope"], "id": row["id"], "metric": key, **values}
                   for row in report.value["metricComparisons"] for key, values in row["metrics"].items()]
    pd.DataFrame(metric_rows).to_csv(metric_path, index=False, encoding="utf-8-sig")
    report.value["artifacts"].update(hourlyDifferences=str(hourly_path), metricDifferences=str(metric_path))
    report.value["summary"] = {
        "windowCount": len(report.value["windows"]), "originalAcceptedWindowCount": len(windows),
        "originalPhysicalGroupCount": len(groups), "supplementaryEventIds": plan["sourceExcludedEventIds"],
        "maximumSimulationDifferenceM3s": max((row["maximumSimulationDifferenceM3s"]
                                               for row in report.value["windows"]
                                               if row.get("maximumSimulationDifferenceM3s") is not None), default=None),
        "allHydrographsMatched": len(report.value["windows"]) == 17 and all(
            row.get("flowAndObservationMatched") for row in report.value["windows"]),
        "failedCheckCount": len(report.value["errors"]),
        "allMetricComparisonsMatched": all(row["passed"] for row in report.value["metricComparisons"]),
        "benchmarkEvaluationScope": "FINITE_SOURCE_OBS_AND_OUR_AND_XAJ_REFERENCE_COMMON_HOURS",
        "originalCalibrationEvaluationScope": "FINITE_OBS_AND_OUR_PAIRS_ORIGINAL_16_AND_7_GROUPS",
        "productionEligible": False, "newOptimizationPerformed": False,
    }
    report.save()


def run(args):
    if args.output.is_relative_to(args.source) or args.runtime.is_relative_to(args.source):
        raise ValueError("验收输出与运行工作区不能写入原交付包")
    os.environ["XAJ_RUNTIME_DIR"] = str(args.runtime)
    sys.path.insert(0, str(ROOT / "backend"))
    report = Report(args.output, args.runtime, args.source)
    manager, task_id, paths = None, None, []
    started = time.monotonic()
    try:
        if not args.runtime.is_dir():
            raise ValueError("指定运行工作区不存在")
        candidate, plan, bundle, science, request, frames, paths = preflight(args, report)
        from server import TaskManager
        manager = TaskManager(args.runtime / "tasks")
        if time.monotonic() - started >= args.timeout:
            raise TimeoutError("提交前验收已超过限定时长")
        submitted = manager.submit(request)
        task_id = submitted["taskId"]
        report.value.update(taskId=task_id, status="running", stage="fixed_parameter_replay")
        task_folder = args.runtime / "tasks" / task_id
        report.value["artifacts"].update(taskFolder=str(task_folder), result=str(task_folder / "result.json"),
                                          taskLog=str(task_folder / "task.log"), inputManifest=str(task_folder / "input-manifest.json"))
        report.save()
        print(json.dumps({"stage": "submitted", "taskId": task_id, "parameter": candidate["parameterVersionId"],
                          "windowCount": 17, "algorithm": "FIXED_PARAMETERS"}, ensure_ascii=False), flush=True)
        last_progress = 0.0
        while True:
            status = manager.get(task_id)
            elapsed = time.monotonic() - started
            if elapsed - last_progress >= 20 or last_progress == 0 or status["status"] not in ACTIVE_STATUSES:
                last_progress = elapsed
                report.value.update(taskStatus=status["status"], elapsedSeconds=round(elapsed, 2),
                                    taskProgress=status.get("progress"), taskStage=status.get("stageLabel"))
                report.save()
                print(json.dumps({"taskId": task_id, "status": status["status"], "progress": status.get("progress"),
                                  "stage": status.get("stageLabel"), "elapsedSeconds": round(elapsed, 1)}, ensure_ascii=False), flush=True)
            if status["status"] == "completed":
                break
            if status["status"] not in ACTIVE_STATUSES:
                report.value["taskFailureStatus"] = status
                raise RuntimeError(status.get("errorMessage") or "固定参数任务未成功完成")
            if elapsed >= args.timeout:
                manager.cancel(task_id)
                raise TimeoutError(f"固定参数重放超过 {args.timeout} 秒时限；本验收任务已请求停止")
            time.sleep(min(1.0, max(0.05, args.timeout - elapsed)))
        result = manager.result(task_id)
        report.value["artifacts"]["resultSHA256"] = sha256(task_folder / "result.json")
        compare_result(result, candidate, plan, bundle, science, frames, report, task_folder)
        if time.monotonic() - started > args.timeout:
            raise TimeoutError("验收比较超过限定总时长")
        from science_context import model_implementation_identity
        current = model_implementation_identity()
        report.value["modelImplementationAfter"] = current
        report.check("model_implementation_unchanged", current, report.value["modelImplementationBefore"])
        report.value["status"] = "completed"
    except Exception as error:
        report.value.update(status="failed", failureStage=report.value["stage"],
                            failureDetails={"type": type(error).__name__, "message": str(error),
                                                              "traceback": traceback.format_exc()})
        report.value["errors"].append({"check": "execution", "error": str(error)})
        print(json.dumps({"status": "failed", "stage": report.value["stage"], "taskId": task_id,
                          "error": str(error)}, ensure_ascii=False), flush=True)
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as error:
                report.value["errors"].append({"check": "manager_close", "error": str(error)})
        paths = paths or [Path(row["path"]) for row in report.value["sourceFilesBefore"]]
        if paths:
            try:
                report.value["sourceFilesAfter"] = file_records(paths, args.source)
                report.check("source_files_unchanged", report.value["sourceFilesAfter"], report.value["sourceFilesBefore"])
            except Exception as error:
                report.value["errors"].append({"check": "source_hashes_after", "error": str(error)})
        if "summary" in report.value:
            report.value["summary"]["failedCheckCount"] = len(report.value["errors"])
        report.value.update(passed=report.value["status"] == "completed" and not report.value["errors"],
                            finishedAt=datetime.now(timezone.utc).isoformat(), elapsedSeconds=round(time.monotonic() - started, 2),
                            stage="finished")
        report.save()
        print(json.dumps({"passed": report.value["passed"], "taskId": task_id, "report": str(report.path),
                          "errors": len(report.value["errors"]), "summary": report.value.get("summary")}, ensure_ascii=False), flush=True)
    return 0 if report.value["passed"] else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime-20261012", help="升级版运行工作区")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="原交付包内层目录；只读")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="验收报告和逐小时、逐指标差异输出目录")
    parser.add_argument("--parameter", help="冻结率定候选 ID；省略时要求恰有一个匹配当前实现的候选")
    parser.add_argument("--timeout", type=int, default=1200, help="从预检开始的最长秒数，范围 1–1200")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 1200:
        parser.error("--timeout 必须在 1 到 1200 秒之间")
    args.runtime, args.source, args.output = args.runtime.resolve(), args.source.resolve(), args.output.resolve()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
