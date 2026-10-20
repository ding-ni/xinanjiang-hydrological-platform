"""Register the 20261012 frozen CAL and manual-edit candidates separately.

This imports evidence into the existing tasks/parameters/routing registries.
It never selects a candidate or overwrites source/default configuration files.
The default destination is an isolated review workspace, not daily runtime.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from common import MODEL, ROUTING_VERSION, now, read_json, write_json
from catalog import PARAMETERS
from routing_versions import RoutingVersions, payload_digest
from input_products import InputProducts
from science_context import ScienceVersions, digest_file, digest_json, model_implementation_identity, relocated_task_science
from original_calibration_plan import freeze_source_plan, SOURCE_QC

TASK = "CAL-20261011-104708-95c5368f"
SOURCE_TARGET_POLICY = 'LEGACY_CLOSED_RING_VERTEX_MEAN'
SOURCE_PRECIPITATION_DTYPE = 'float64'
SCIENCE_HASHES = {"state_config": "17383900e96167c76985fe68f755a202a4a14cc4ecf95e5d93d65b4d31e4a815",
                  "initial_land_state": "e44aa9235d2a964a627f70a3b08a14a340fca65cd6764754133938bb16dc078f",
                  "pet_config": "f7315a0bafcdcb1947aaa089873858048f93f20e05c95aa0939729c3cd935540"}
CONTEXT = {"projectId": "nananxi-xufan", "resolutionId": "1km", "modelConfigId": "nananxi-xufan-1km",
           "contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km"}


def preserve_json(path, value):
    if path.exists():
        previous = read_json(path)
        if previous != value:
            raise ValueError("导入目标已有不同记录，拒绝覆盖：" + str(path))
        return
    write_json(path, value)


def parameters(values):
    result = {key: values.get(key, values.get("runoff." + key)) for key in PARAMETERS}
    for key, value in result.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("候选缺少完整有限参数：" + key)
    if result["WM"] <= result["WUM"] + result["WLM"] or result["KI"] + result["KG"] >= 1:
        raise ValueError("导入候选不满足产流物理约束")
    return result


def register_routing(runtime, payload, name, provenance):
    registry = RoutingVersions(runtime)
    # The old and new payload share fields/reach identities; create no new
    # server or writable default. Keep the immutable id content-addressed.
    registry.validate(payload)
    digest = payload_digest(payload)
    version = "ROUTING-IMPORT-" + digest
    row = {"routingVersionId": version, "name": name, "createdAt": None, "parentVersionId": ROUTING_VERSION,
           "parameters": payload, "parametersSHA256": digest, "productionEligible": False, "importProvenance": provenance}
    if (runtime / 'routing-versions' / (version + '.json')).exists():
        existing = registry.resolve(version)
        if existing['parameters'] != payload:
            raise ValueError('同一汇流内容编号已有不同参数，拒绝覆盖')
        return existing
    preserve_json(runtime / "routing-versions" / (version + ".json"), row)
    return registry.resolve(version)


def register_search_reference(runtime, payload, provenance):
    registry = RoutingVersions(runtime)
    registry.validate(payload, allow_search_reference=True)
    digest = payload_digest(payload)
    version = 'ROUTING-SOURCE-REFERENCE-' + digest
    row = {'routingVersionId': version, 'name': '同事原率定的河道 K 基准', 'parameters': payload,
           'parametersSHA256': digest, 'createdAt': None, 'parentVersionId': ROUTING_VERSION,
           'searchReferenceOnly': True, 'productionEligible': False, 'importProvenance': provenance}
    preserve_json(runtime / 'routing-versions' / (version + '.json'), row)
    return registry.resolve(version)


def matching_source_input(runtime, source_hash, products=None):
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        raise ValueError('来源率定缺少可核验的 MDB 内容摘要')
    source_hash = source_hash.lower()
    products = products or InputProducts(runtime_root=runtime)
    matches = []
    for public in products.list_versions()['versions']:
        manifest_source = public.get('sourceOrigin') or {}
        if public.get('operation') == 'fixedBaseline' and public.get('sourceId') != 'SRC-MDB-' + source_hash and manifest_source.get('databaseSHA256') != source_hash:
            continue
        descriptor = products.resolve(public['bundleVersionId'])
        manifest = descriptor['manifest']
        registered_hash = manifest.get('databaseSHA256') or (manifest.get('sourceOrigin') or {}).get('databaseSHA256')
        if manifest.get('sourceId') != 'SRC-MDB-' + source_hash and registered_hash != source_hash:
            continue
        target_policy = manifest.get('targetCoordinatePolicy') or manifest.get('spatialTargetPolicy') or manifest.get('targetPolicy') or manifest.get('summary', {}).get('spatialTargetPolicy') or manifest.get('summary', {}).get('targetPolicy')
        dtype = manifest.get('forcingValueDtype') or manifest.get('precipitationDtype') or manifest.get('precipitationPrecision') or manifest.get('summary', {}).get('precipitationDtype')
        if target_policy != SOURCE_TARGET_POLICY or dtype != SOURCE_PRECIPITATION_DTYPE:
            # The corrected polygon/NC center is a different scientific forcing
            # product. Never attach the old fitted metrics to that definition.
            continue
        origin = manifest.get('summary', {}).get('continuousOrigin')
        if not isinstance(origin, str) or not origin:
            raise ValueError('MDB 输入缺少冻结的连续原点摘要，不能应用候选')
        matches.append(descriptor)
    original_qc = [item for item in matches if item['manifest'].get('sourceQcPolicy') == SOURCE_QC]
    if original_qc:
        matches = original_qc
    if len(matches) > 1:
        raise ValueError('同一 MDB 来源登记了多个输入版本，请先在独立工作区明确用于候选重评价的版本')
    return matches[0] if matches else None


def import_candidates(source_root, runtime_root, expected_science=None):
    source, runtime = Path(source_root).resolve(), Path(runtime_root).resolve()
    if not source.is_dir() or runtime == source or runtime.is_relative_to(source):
        raise ValueError("候选导入工作区必须与原交付目录分开")
    folder = source / "validation/platform_calibration_tasks" / TASK
    relocated = relocated_task_science(folder, expected_science or SCIENCE_HASHES)
    report = read_json(folder / "result.json")
    status = read_json(folder / "status.json")
    if status.get("status") != "SUCCEEDED" or report.get("task_id") != TASK:
        raise ValueError("来源率定任务未成功或编号不符")
    resolved = read_json(folder / "output/resolved_runoff_parameter_set.json")
    frozen_values = parameters(resolved["parameters"])
    if frozen_values != parameters(report["resolved_runoff_parameter_set"]):
        raise ValueError("候选小时参数与来源率定结果不一致")
    manual_path = source / "data_interface/runoff_2017/xaj_parameter_set.json"
    manual_values = parameters(read_json(manual_path)["land_parameters"])
    evidence = {"sourceTaskId": TASK, "files": {"result.json": digest_file(folder / "result.json"),
                "resolved_runoff_parameter_set.json": digest_file(folder / "output/resolved_runoff_parameter_set.json"),
                "manual_parameter_set.json": digest_file(manual_path)}, "scienceSnapshots": relocated["evidence"],
                "sourceMDBSHA256": relocated["request"].get("source_sha256")}
    evidence['sourceInputDefinition'] = {'spatialTargetPolicy': SOURCE_TARGET_POLICY,
        'precipitationDtype': SOURCE_PRECIPITATION_DTYPE,
        'targetDefinition': 'Mean of every vertex in the closed polygon ring, including the duplicated first vertex.',
        'scientificLimitation': 'Source IDW targets are offset from true grid centers; retained only to reproduce the original fitted trajectory.'}
    matching_input = matching_source_input(runtime, evidence['sourceMDBSHA256'])
    input_reference = ({'requiredInputBundleVersionId': matching_input['bundleVersionId'],
                        'inputBundleVersionId': matching_input['bundleVersionId'],
                        'continuousOrigin': matching_input['manifest']['summary']['continuousOrigin'],
                        'requiresInputRegistration': False} if matching_input else
                       {'requiredInputBundleVersionId': None, 'inputBundleVersionId': None,
                        'continuousOrigin': None, 'requiresInputRegistration': True})
    input_reference.update(requiredSpatialTargetPolicy=SOURCE_TARGET_POLICY, requiredPrecipitationDtype=SOURCE_PRECIPITATION_DTYPE)
    policy = {"strategy": "continuous-origin" if relocated["request"].get("state_strategy") == "CONTINUOUS" else "independent-warmup",
              "warmupHours": int(read_json(relocated["paths"]["state_config"])["fixed_warmup_hours"]),
              "continuousOrigin": relocated["request"].get("simulation_contract", {}).get("continuous_origin", "FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP")}
    scientific_registry = ScienceVersions(runtime)
    science = scientific_registry.register(name="20261011 率定冻结的月 PET、初态和连续策略", **relocated["paths"],
                                           state_policy=policy, provenance=evidence)
    # The current manual candidate has its own scientific context. The actual
    # package happens to use the same settings; do not infer that for all future
    # edits or silently use the frozen calibration context when it differs.
    active_contract = read_json(source / 'config/simulation_contract.json')
    active_paths = {}
    active_evidence = {}
    for key in ('state_config', 'initial_land_state', 'pet_config'):
        reference = active_contract.get(key)
        if not isinstance(reference, str) or not reference:
            raise ValueError('人工修改方案的科学契约缺少配置引用：' + key)
        path = Path(reference)
        path = path.resolve() if path.is_absolute() else (source / path).resolve()
        if not path.is_relative_to(source) or path.is_symlink() or not path.is_file():
            raise ValueError('人工修改方案科学依赖缺失或超出交付包：' + key)
        active_paths[key] = path
        active_evidence[key] = {'relativePath': path.relative_to(source).as_posix(), 'sha256': digest_file(path)}
    active_policy = {'strategy': 'continuous-origin' if active_contract.get('default_state_strategy') == 'CONTINUOUS' else 'independent-warmup',
                     'warmupHours': int(read_json(active_paths['state_config'])['fixed_warmup_hours'])}
    if active_policy['strategy'] == 'continuous-origin':
        active_policy['continuousOrigin'] = active_contract.get('continuous_origin', 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP')
    manual_science = scientific_registry.register(name='20261012 人工修改方案的当前科学条件', **active_paths,
                                                  state_policy=active_policy, provenance={'activeContractSHA256': digest_file(source / 'config/simulation_contract.json'),
                                                                                         'scienceDependencies': active_evidence})
    base_routing = read_json(folder / "routing_parameter_snapshot.json")
    search_reference = register_search_reference(runtime, base_routing, evidence)
    source_plan = freeze_source_plan(folder, {"versionId": science["scienceVersionId"],
                                           "configurationSHA256": science["scienceConfigurationSHA256"],
                                           "petConfigurationSHA256": science["petConfigurationSHA256"]},
                                     search_reference['routingVersionId'])
    search = report["common_parameter_set"]
    frozen_routing = copy.deepcopy(base_routing)
    for component in ("RS", "RI", "RG"):
        frozen_routing["default_slope_k_hours"][component] = search["routing." + component + "_k_hours"]
    for reach in frozen_routing["reach_muskingum"].values():
        reach["k_hours"] *= search["routing.muskingum_k_scale"]
        reach["x"] = search["routing.muskingum_x"]
    frozen_routing.setdefault("metadata", {})["muskingum_x"] = search["routing.muskingum_x"]
    manual_routing_path = source / "config/routing_parameters_control_split_v2.json"
    manual_routing = read_json(manual_routing_path)
    implementation = model_implementation_identity()["modelImplementationSHA256"]
    imports = []
    for kind, values, routing, candidate_science in (("frozen-calibration", frozen_values, frozen_routing, science),
                                                    ("manual-edit", manual_values, manual_routing, manual_science)):
        provenance = {**evidence, "candidateKind": kind,
                      "routingSHA256": digest_json(routing), "manualRoutingFileSHA256": digest_file(manual_routing_path) if kind == "manual-edit" else None}
        routing_record = register_routing(runtime, routing, "20261012 " + kind, provenance)
        identity = digest_json({"parameters": values, "science": candidate_science["scienceConfigurationSHA256"], "routing": routing_record["parametersSHA256"],
                                'requiredInput': input_reference,
                                 "targetModelImplementationSHA256": implementation, "source": provenance,
                                 'originalCalibrationPlanSHA256': source_plan['planSHA256']})
        task_id = "JOB-import-20261012-" + identity[:24]
        parameter_id = "PARAM-" + task_id
        lineage = {"scienceVersionId": candidate_science["scienceVersionId"], "scienceConfigurationSHA256": candidate_science["scienceConfigurationSHA256"],
                   "petConfigurationSHA256": candidate_science["petConfigurationSHA256"], "modelImplementationSHA256": implementation,
                   "routingVersionId": routing_record["routingVersionId"], "parameterVersionId": parameter_id}
        if matching_input:
            lineage.update(inputBundleVersionId=matching_input['bundleVersionId'], inputBundleManifestSHA256=matching_input['manifestSHA256'],
                           forcingVersion=matching_input['forcingVersion'], observationVersion=matching_input['observationVersion'])
        selection = {"versionId": candidate_science["scienceVersionId"], "configurationSHA256": candidate_science["scienceConfigurationSHA256"],
                     "petConfigurationSHA256": candidate_science["petConfigurationSHA256"]}
        note = "来源冻结率定候选；请用登记输入重新评价，尚无生产资格。" if kind == "frozen-calibration" else "来源当前人工修改方案；KC 与冻结最优值不同，不继承冻结率定评价指标。"
        source_target_warning = '源率定IDW把闭合多边形重复首点计入顶点均值，目标位置偏离真实网格中心；本候选重放保留该来源定义，真实中心方案须重新率定。'
        row = {"schemaVersion": "2.0", "taskId": task_id, "resultVersionId": "RESULT-" + task_id,
               'name': '20261011冻结率定候选 · 原方法重放' if kind == 'frozen-calibration' else '20261011人工修改候选 · 原方法重放',
               "parameterVersionId": parameter_id, "context": CONTEXT, "contextKey": CONTEXT["contextKey"],
               "bestParameters": values, "parameters": values, "science": selection, "lineage": lineage,
               "scienceSnapshot": scientific_registry.export(candidate_science['scienceVersionId']),
               "statePolicy": candidate_science['statePolicy']['strategy'], "petMode": candidate_science['petMode'],
               'actualWarmupHours': candidate_science['statePolicy']['warmupHours'], **input_reference,
               'sourceMDBSHA256': evidence['sourceMDBSHA256'],
               "routingCandidate": routing_record, "bestRoutingParameters": routing, "resultSource": "model-backend",
               "productionEligible": False, "status": "candidate", "candidateKind": kind,
               "importProvenance": provenance, "warnings": [note, source_target_warning, '尚无同MDB、旧顶点均值目标及float64的精确重放输入，请先登记来源重放产品；当前真实中心产品不继承源指标。'] if not matching_input else [note, source_target_warning],
               "requiresReevaluation": True, 'sourceRequiresReevaluation': True,
               'originalCalibrationPlan': source_plan, 'originalPlanAvailable': True,
               'metricProvenance': 'ORIGINAL_DELIVERY_SAVED_METRICS_NOT_RECALCULATED_BY_CURRENT_IMPLEMENTATION',
               "calibrationMetrics": report.get("groups", {}).get("CALIBRATION") if kind == "frozen-calibration" else None,
               "validationMetrics": report.get("groups", {}).get("VALIDATION") if kind == "frozen-calibration" else None,
               "independentTest": report.get("groups", {}).get("INDEPENDENT_TEST") if kind == "frozen-calibration" else None}
        job = runtime / "tasks" / task_id
        preserve_json(job / "request.json", {"runType": "candidateImport", "context": CONTEXT, "parameters": values, "science": selection,
                                              **input_reference, 'statePolicy': row['statePolicy'], 'actualWarmupHours': row['actualWarmupHours'],
                                              'inputs': {'bundleVersionId': matching_input['bundleVersionId'], 'bundleManifestSHA256': matching_input['manifestSHA256']} if matching_input else {},
                                              "routing": {"versionId": routing_record["routingVersionId"]}, "lineage": lineage,
                                               'originalCalibrationPlan': source_plan,
                                              "importProvenance": provenance})
        preserve_json(job / "result.json", row)
        preserve_json(job / "status.json", {"taskId": task_id, "runType": "candidateImport", "runTypeLabel": "20261012候选导入",
                                            "status": "completed", "stageLabel": "候选已登记，等待按资料重新评价", "resultSource": "model-backend",
                                            "resultVersionId": row["resultVersionId"], "contextKey": CONTEXT["contextKey"], "lineage": lineage,
                                            "createdAt": None, "productionEligible": False})
        preserve_json(runtime / "parameters" / (parameter_id + ".json"), row)
        imports.append({"candidateKind": kind, "taskId": task_id, "parameterVersionId": parameter_id,
                        "KC": values["KC"], "scienceVersionId": candidate_science["scienceVersionId"], "routingVersionId": routing_record["routingVersionId"],
                        **input_reference})
    summary = {"operation": "candidateImport", "source": str(source), "runtime": str(runtime), "activeSelectionChanged": False,
               "productionEligible": False, "candidates": imports}
    preserve_json(runtime / "imports" / ("20261012-candidates-" + digest_json(imports)[:24] + ".json"), summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Verified historical source model directory")
    parser.add_argument("--runtime", type=Path, required=True, help="Explicit destination workspace")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(import_candidates(args.source, args.runtime), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
