"""Execute a frozen platform job using the collaborator's unchanged model kernels.

Each worker is an isolated cancellable process. Never write into the delivery or
vendor inputs; all progress, states, metrics and process lines belong to its job.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from pathlib import Path

from common import MODEL, RUNTIME, ROUTING_VERSION, read_json, write_json, clean
from contracts import freeze_input_selection, freeze_routing_selection
from input_products import InputProducts
from routing_versions import RoutingVersions
from routing_adapter import RegisteredRoutingRunner
from state_integrity import assert_state_input_compatibility, validated_parent_state
from state_initialization_adapter import freeze_initialization, anchored_routing
from calibration_search import run_search
from calibration_space import resolve_space, validate_selection, search_summary
import routing_calibration

sys.path[:0] = [str(MODEL / "code"), str(MODEL / "scripts")]

import numpy as np
import pandas as pd
from types import SimpleNamespace
from forecast_products import ForecastProducts
import run_formal_multievent_calibration as kernels
from calibration_platform.algorithms import search
from runoff_platform.config_loader import load_parameter_set
from runoff_platform.data.event_quality import evaluate_event_quality
from runoff_platform.data.pet_forcing import PetProvider
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids
from state_initialization.core import build_uniform_land_state, validate_land_state_arrays
from xaj_routing.control_area import contributing_grid_areas
from xaj_routing.io import read_topology_json


class ModelJob:
    def __init__(self, folder, publish_parameters=True):
        self.folder = Path(folder)
        self.publish_parameters = publish_parameters
        self.request = read_json(self.folder / "request.json")
        self.request, self.bundle = freeze_input_selection(self.request, InputProducts())
        self.request = freeze_routing_selection(self.request, self.bundle)
        self.request = freeze_initialization(self.request, self.bundle)
        self.task = read_json(self.folder / "status.json")
        self.task_id = self.task["taskId"]
        self.period = self.request["period"]
        self.bundle_version = self.bundle["bundleVersionId"]
        self.bundle_digest = self.bundle["manifestSHA256"]
        expected_digest = self.request.get("inputs", {}).get("bundleManifestSHA256")
        if expected_digest is not None and expected_digest != self.bundle_digest:
            raise ValueError("排队后的输入产品摘要已改变；拒绝切换到其他资料")
        self.forcing_version = self.bundle["forcingVersion"]
        self.observation_version = self.bundle["observationVersion"]
        self.input_paths = self.bundle["paths"]
        self.grids = kernels.load_grids(self.input_paths["grids"])
        self.grid_ids = [row["unit_id"] for row in self.grids]
        geometry = read_json(self.input_paths["geometry"])
        by_position = {(f["properties"]["row"], f["properties"]["col"]): f["geometry"]["coordinates"][0] for f in geometry["features"]}
        self.grid_geometry = [{"id": row["unit_id"], "bounds": by_position[(int(row["source_row"]), int(row["source_col"]))], "areaKm2": float(row["area_km2"])} for row in self.grids]
        self.topology = self.input_paths["topology"]
        self.mapping_file = self.input_paths["mapping"]
        # The authoritative v2 topology has the control at the UP reach's end.
        # Do not query the unsplit R0020 reach or average across unrelated outlets.
        control = read_json(MODEL / "data_interface/spatial/xufan_70115800_control_point_snapped_v1.json")
        control["snapped"]["reach_id"] = "R0020_UP"
        self.controls = self.folder / "routing-control.json"
        write_json(self.controls, control)
        units, self.mapping = kernels.build_mapping(self.mapping_file, self.topology, self.grids)
        self.router = RegisteredRoutingRunner(self.topology, units, self.controls)
        self.routing_payload = RoutingVersions().resolve(self.request["routing"]["versionId"])["parameters"]
        self.routing = kernels.read_parameters_json_from_mapping(self.routing_payload)
        self.routing_version = self.request["lineage"]["routingVersionId"]
        self.template = read_json(MODEL / "config/initial_state_land.json")
        flow = pd.read_csv(self.input_paths["flow"])
        self.observed = flow.assign(time=pd.to_datetime(flow.time)).set_index("time")["flow_m3_s"]
        self.precip_dir = self.bundle["precipitationDir"]
        self.params = load_parameter_set(MODEL / "config/parameter_set_land.json")
        overrides = self.request.get("calibration", {}).get("initialParameters", {}) if self.request["runType"] == "calibration" else self.request.get("parameters", {})
        self.params = kernels.candidate_params(self.params, {f"runoff.{key}": value for key, value in overrides.items()})
        self.warnings = [*self.bundle.get("qualityNotes", [])]
        self.freeze_input_provenance()

    def freeze_input_provenance(self):
        warmup = pd.Timedelta(days=float(self.period.get("warmupDays", 7)))
        periods = self.period.get("windows", []) if self.period.get("mode") == "continuous" else self.period.get("events", [])
        if self.request['runType'] == 'forecast':
            self.forecast_rain = ForecastProducts().freeze(self.request)
            write_json(self.folder / 'forecast-rain.json', self.forecast_rain)
            periods = []
        elif self.request["runType"] != "calibration":
            periods = [{"start": self.period["startTime"], "end": self.period["endTime"]}]
        years = set()
        for row in periods:
            if row.get("purpose") == "未使用" or row.get("status") == "inactive":
                continue
            effective_warmup = pd.Timedelta(0) if self.period.get("initialStateVersionId") else warmup
            years.update(range((pd.Timestamp(row["start"]) - effective_warmup).year, pd.Timestamp(row["end"]).year + 1))
        paths = [self.precip_dir / f"nananxi_precip_hourly_1km_{year}.nc" for year in sorted(years)]
        paths += list(self.input_paths.values())
        paths += list((MODEL / "config").glob("*.json"))
        paths += list((MODEL / "code").rglob("*.py")) + [MODEL / "scripts/run_formal_multievent_calibration.py"]
        if self.request['runType'] == 'forecast':
            paths.append(RUNTIME / 'forecast-rain' / f"{self.forecast_rain['versionId']}.json")
        records = []
        for path in dict.fromkeys(paths):
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            try:
                relative = path.relative_to(MODEL).as_posix()
            except ValueError:
                relative = "runtime/" + path.relative_to(RUNTIME).as_posix()
            records.append({"path": relative, "sha256": hasher.hexdigest(), "bytes": path.stat().st_size})
        manifest = {"modelInputVersion": self.bundle["modelInputVersion"], "files": records,
                    "inputBundleVersionId": self.bundle_version, "inputBundleManifestSHA256": self.bundle_digest,
                    "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                    "routingVersionId": self.routing_version,
                    "routingParameters": self.routing_payload,
                    "routingImplementation": {"routing_adapter.py": hashlib.sha256(Path(__file__).with_name('routing_adapter.py').read_bytes()).hexdigest()},
                    "stateInitializationImplementation": hashlib.sha256(Path(__file__).with_name('state_initialization_adapter.py').read_bytes()).hexdigest(),
                    "initialization": self.request.get("initialization"),
                    "originalDeliverySHA256": "31b7d85fae32939152fbce0e174bb0ad075a8f48f34f19cb53c34da2080f31cf",
                    "note": "计算实际使用冻结登记版本；不读取 current/latest，前端静态预览不是数值输入。"}
        parent = self.period.get("initialStateVersionId")
        if self.request["runType"] == "calibration":
            manifest["calibrationImplementation"] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                     for name in ("worker.py", "calibration_search.py", "calibration_space.py", "routing_calibration.py", "contracts.py")}
        if self.request['runType'] == 'forecast':
            manifest['forecastRain'] = self.request['inputs']['forecastPrecipitation']
            manifest['forecastImplementation'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                  for name in ('worker.py', 'forecast_products.py', 'contracts.py')}
        if parent:
            if not str(parent).startswith("STATE-JOB-") or any(mark in parent for mark in ('/', '\\')):
                raise ValueError("父状态版本无效")
            manifest["parentState"] = {"stateVersionId": parent, "sha256": hashlib.sha256((RUNTIME / 'states' / f'{parent}.json').read_bytes()).hexdigest()}
        write_json(self.folder / "input-manifest.json", manifest)
        self.input_digest = hashlib.sha256(__import__('json').dumps(manifest, sort_keys=True).encode()).hexdigest()

    def progress(self, percent, stage):
        write_json(self.folder / "progress.json", {"progress": round(float(percent), 1), "stageLabel": stage})
        print(stage, flush=True)

    def load(self, rows, warmup):
        data = kernels.load_event_data(MODEL, rows, self.grids, self.observed, warmup, continuous=False, precip_dir=self.precip_dir)
        if len(data) != len(rows):
            raise ValueError("部分时段缺少实际出口流量或驱动；拒绝补造资料")
        for event in data:
            if not np.isfinite(event.precip).all():
                raise ValueError(f"{event.event_id} 驱动存在缺测")
        return data

    def hydrograph(self, event, simulated):
        simulated = simulated.loc[event.start:event.end]
        observed = event.observed.reindex(simulated.index)
        is_forecast = self.request['runType'] == 'forecast'
        metrics = {} if is_forecast else kernels.metrics(observed, simulated)
        peak_time = simulated.idxmax()
        areas = contributing_grid_areas(self.mapping_file, read_topology_json(self.topology), "R0020_UP", self.grid_ids)
        rainfall = np.average(event.precip, axis=1, weights=areas)
        rainfall = pd.Series(rainfall, index=event.times).reindex(simulated.index)
        return {
            "forecast": [{"time": t.isoformat(), "value": float(v)} for t, v in simulated.items()],
            "obs": [] if is_forecast else [{"time": t.isoformat(), "value": float(v) if pd.notna(v) else None} for t, v in observed.items()],
            "prev": [], "peak": float(simulated.max()), "peakTime": peak_time.isoformat(),
            "volume": float(simulated.sum() * 3600), "startMark": self.request['issue']['issueTime'] if is_forecast else event.start.isoformat(),
            "metrics": {"nse": metrics.get("NSE"), "peakErr": metrics.get("peak_error_percent"), "timeErrH": metrics.get("peak_time_error_hours"), "volErr": metrics.get("volume_error_percent")},
            "forcing": {"precipitation": [float(v) for v in rainfall], "temperature": [], "components": {},
                        "versionId": self.forecast_rain['versionId'] if is_forecast else self.forcing_version, "bundleVersionId": self.bundle_version,
                        "sourceLabel": self.forecast_rain['sourceLabel'] if is_forecast else "实际登记驱动 · 徐畈控制区面积加权降雨",
                        "qualityNotes": self.bundle.get("qualityNotes", [])},
        }

    def initial_state(self, params):
        return build_uniform_land_state(self.template, len(self.grids), params)

    def route_with_history(self, event, land, routing):
        """Call the unchanged one-hour kernel and persist its actual states.

        No duplicated equations, replay-time recomputation, or synthetic curves.
        Warm-up is computed but excluded from the requested output period.
        """
        count = len(event.times)
        first = int(event.times.searchsorted(event.start))
        fields = list(land) + ["RS", "RI", "RG", "precipitation", "pet", "et_actual", "water_balance_error"]
        history = {field: np.empty((count - first, len(self.grids)), dtype=float) for field in fields}
        components = {field: np.empty_like(event.precip, dtype=float) for field in ("RS", "RI", "RG")}
        for index, time_value in enumerate(event.times):
            rs, ri, rg, land, diagnostics = simulate_xaj_liquid_grids(
                event.precip[index:index + 1], self.params, event.pet_mm_h[index:index + 1], land, return_diagnostics=True)
            for field, values in (("RS", rs), ("RI", ri), ("RG", rg)):
                components[field][index] = values[0]
            if index >= first:
                step = index - first
                for field in land:
                    history[field][step] = land[field]
                for field in ("RS", "RI", "RG"):
                    history[field][step] = components[field][index]
                history["precipitation"][step] = event.precip[index]
                history["pet"][step] = event.pet_mm_h[index]
                for field in ("et_actual", "water_balance_error"):
                    history[field][step] = diagnostics[field][0]
            if index % max(1, count // 20) == 0:
                self.progress(40 + 35 * (index + 1) / count, f"真实逐时网格计算 {index + 1}/{count}")
        self.progress(78, "按冻结拓扑计算坡面与河网汇流")
        np.savez_compressed(self.folder / "routing-runoff.npz", times=np.asarray([time.isoformat() for time in event.times]),
                            **components)
        routing_evidence = {"schemaVersion": "1.0", "taskId": self.task_id, "gridIds": self.grid_ids,
                            "startTime": event.start.isoformat(), "endTime": event.end.isoformat(),
                            "initialRoutingState": routing, "routingParameters": self.routing_payload,
                            "lineage": self.request["lineage"],
                            "arraySHA256": hashlib.sha256((self.folder / "routing-runoff.npz").read_bytes()).hexdigest()}
        write_json(self.folder / "routing-evidence.json", routing_evidence)
        self.routing_evidence = {"available": True, "arraySHA256": routing_evidence["arraySHA256"],
                                 "catalogSHA256": hashlib.sha256((self.folder / "routing-evidence.json").read_bytes()).hexdigest()}
        simulated, final_routing = self.router.run(event.times, components["RS"] @ self.mapping,
                                                  components["RI"] @ self.mapping, components["RG"] @ self.mapping,
                                                  self.routing, routing)
        # Unicode timestamps keep npz loading safe with allow_pickle=False.
        np.savez_compressed(self.folder / "grid-history.npz", times=np.asarray([time.isoformat() for time in event.times[first:]]), **history)
        history_digest = hashlib.sha256((self.folder / "grid-history.npz").read_bytes()).hexdigest()
        metadata = {"schemaVersion": "2.1", "taskId": self.task_id, "context": self.request["context"],
                    "gridIds": self.grid_ids, "gridGeometry": self.grid_geometry, "fields": fields,
                    "times": [time.isoformat() for time in event.times[first:]],
                    "parameters": {key.split(".")[-1]: value for key, value in kernels.resolved_runoff_parameters(self.params).items()},
                    "resultSource": "model-backend", "productionEligible": False, "arraySHA256": history_digest,
                    "provenance": {"inputManifestSHA256": self.input_digest, "forcingVersion": self.forcing_version,
                                   "inputBundleVersionId": self.bundle_version, "inputBundleManifestSHA256": self.bundle_digest,
                                   "petMode": "MONTHLY_CLIMATOLOGY_ASSUMPTION", "parentStateVersionId": self.period.get("initialStateVersionId")}}
        write_json(self.folder / "grid-history.json", metadata)
        self.grid_history = {"available": True, "taskId": self.task_id, "start": metadata["times"][0],
                             "end": metadata["times"][-1], "steps": len(metadata["times"]), "gridCount": len(self.grid_ids), "fields": fields,
                             "arraySHA256": history_digest, "catalogSHA256": hashlib.sha256((self.folder / "grid-history.json").read_bytes()).hexdigest()}
        return simulated, land, final_routing

    def checkpoint(self, land, routing, timestamp, parameters):
        version = f"STATE-{self.task_id}"
        payload = {"schemaVersion": "2.0", "stateVersionId": version, "time": timestamp.isoformat(),
                   "context": self.request["context"], "gridIds": self.grid_ids,
                   "gridGeometry": self.grid_geometry,
                   "land": land, "routing": routing, "parameters": parameters,
                   "sourceTaskId": self.task_id, "resultSource": "model-backend", "productionEligible": False}
        payload["parameterVersionId"] = self.request.get("lineage", {}).get("parameterVersionId")
        payload["initialization"] = getattr(self, "initialization_summary", None)
        payload.update({"parentStateVersionId": self.period.get("initialStateVersionId"),
                        "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                        "inputBundleVersionId": self.bundle_version, "inputBundleManifestSHA256": self.bundle_digest,
                        "routingVersionId": self.routing_version,
                        "routingManifestSHA256": self.request["lineage"]["routingManifestSHA256"],
                        "petMode": "MONTHLY_CLIMATOLOGY_ASSUMPTION", "inputManifestSHA256": self.input_digest})
        write_json(self.folder / "checkpoint.json", payload)
        write_json(RUNTIME / "states" / f"{version}.json", payload)
        return version

    def simulate(self):
        start, end = pd.Timestamp(self.period["startTime"]), pd.Timestamp(self.period["endTime"])
        warmup = int(round(float(self.period.get("warmupDays", 7)) * 24))
        parent = self.period.get("initialStateVersionId")
        land, routing = self.initial_state(self.params), None
        if parent:
            if not str(parent).startswith("STATE-JOB-") or "/" in parent or "\\" in parent:
                raise ValueError("只能使用后端生成的真实状态版本")
            checkpoint = validated_parent_state(RUNTIME / "states" / f"{parent}.json", RUNTIME / "tasks", parent)
            assert_state_input_compatibility(checkpoint, self.request)
            if any(checkpoint["context"].get(k) != self.request["context"].get(k) for k in ("projectId", "resolutionId", "modelConfigId", "contextKey")) or checkpoint["gridIds"] != self.grid_ids:
                raise ValueError("状态版本与案例、尺度或网格不一致")
            if start != pd.Timestamp(checkpoint["time"]) + pd.Timedelta(hours=1):
                raise ValueError("续算开始必须为父状态下一小时，不能跳过或重复计算")
            if checkpoint["parameters"] != kernels.resolved_runoff_parameters(self.params):
                raise ValueError("状态参数与当前参数不一致，需重新初始化")
            land = {k: np.asarray(v, dtype=float) for k, v in checkpoint["land"].items()}
            routing = {k: np.asarray(v, dtype=float) for k, v in checkpoint["routing"].items()}
            validate_land_state_arrays(land, self.params, len(self.grids))
            warmup = 0
        self.progress(10, "读取实际逐小时降雨、流量与状态")
        event = self.load([{"event_id": self.task_id, "year": start.year, "start": start, "end": end, "group": "SIMULATION"}], warmup)[0]
        provider = PetProvider.from_file(MODEL / "config/pet_forcing_assumed_monthly.json")
        event.pet_mm_h = provider.values(event.times)
        self.warnings.append("蒸散发采用逐月给定值。")
        self.progress(40, "计算 110 个有效网格产流和控制断面汇流")
        simulated, final_land, final_routing = self.route_with_history(event, land, routing)
        if not np.isfinite(simulated).all() or (simulated < -1e-9).any():
            raise ValueError("汇流结果出现非有限值或负流量")
        self.initialization_summary = None
        if self.request["runType"] == "stateInitialization":
            strategy = self.request.get("initialization", {}).get("strategy", "FIXED_WARMUP")
            self.initialization_summary = {"strategy": strategy, "warmupHours": warmup,
                                           "stateTime": end.isoformat(),
                                           "warmupControlFlowCms": float(simulated.iloc[-1])}
            if strategy == "OBSERVED_FLOW_ANCHORED":
                anchor = self.request["initialization"]["anchor"]
                final_routing = anchored_routing(self.router, anchor)
                self.initialization_summary.update(anchor=anchor,
                    controlFlowCms=float(final_routing["prev_out"][self.router.control_reach]))
        parameters = kernels.resolved_runoff_parameters(self.params)
        version = self.checkpoint(final_land, final_routing, event.end, parameters)
        hydrograph = self.hydrograph(event, simulated)
        means = {"wu": float(final_land["WU"].mean()), "wl": float(final_land["WL"].mean()), "wd": float(final_land["WD"].mean()), "free": float(final_land["S"].mean()),
                 "soil": float((final_land["WU"] + final_land["WL"] + final_land["WD"]).mean() / self.params.WM)}
        return {"hydrograph": hydrograph, "metrics": hydrograph["metrics"], "endStateVersionId": version,
                "stateVersionId": version, "parentStateVersionId": parent, "means": means,
                "parameters": {k.split(".")[-1]: v for k, v in parameters.items()}, "petMode": "MONTHLY_CLIMATOLOGY_ASSUMPTION", "warnings": self.warnings,
                "stateTime": event.end.isoformat(), "gridCount": len(self.grids), "gridHistory": self.grid_history,
                "initialization": self.initialization_summary,
                "routingEvidence": self.routing_evidence}

    def forecast(self):
        parent = self.period['initialStateVersionId']
        checkpoint = validated_parent_state(RUNTIME / 'states' / f'{parent}.json', RUNTIME / 'tasks', parent)
        assert_state_input_compatibility(checkpoint, self.request)
        issue = pd.Timestamp(self.request['issue']['issueTime'])
        if (pd.Timestamp(checkpoint['time']) != issue
                or any(checkpoint['context'].get(key) != self.request['context'].get(key) for key in ('contextKey', 'projectId', 'resolutionId', 'modelConfigId'))
                or checkpoint['gridIds'] != self.grid_ids
                or checkpoint['parameters'] != kernels.resolved_runoff_parameters(self.params)):
            raise ValueError('起报状态、网格或参数与预报条件不一致')
        self.progress(10, '读取起报状态与已登记降雨情景')
        land = {key: np.asarray(value, dtype=float) for key, value in checkpoint['land'].items()}
        routing = {key: np.asarray(value, dtype=float) for key, value in checkpoint['routing'].items()}
        validate_land_state_arrays(land, self.params, len(self.grids))
        lead = self.request['issue']['leadHours']
        times = pd.DatetimeIndex(self.forecast_rain['times'][:lead])
        precip = np.repeat(np.asarray(self.forecast_rain['hourlyRainfallMm'][:lead], dtype=float)[:, None], len(self.grids), axis=1)
        event = SimpleNamespace(event_id=self.task_id, start=times[0], end=times[-1], times=times,
                                precip=precip, observed=pd.Series(dtype=float))
        event.pet_mm_h = PetProvider.from_file(MODEL / 'config/pet_forcing_assumed_monthly.json').values(times)
        simulated, land, routing = self.route_with_history(event, land, routing)
        if not np.isfinite(simulated).all() or (simulated < -1e-9).any():
            raise ValueError('预报计算出现非有限值或负流量')
        hydrograph = self.hydrograph(event, simulated)
        # Keep the forecast branch inside its task. The next issue uses observed forcing.
        write_json(self.folder / 'forecast-end-state.json', {
            'schemaVersion': '1.0', 'stateRole': 'forecast', 'time': times[-1].isoformat(),
            'sourceTaskId': self.task_id, 'context': self.request['context'], 'parentStateVersionId': parent,
            'land': land, 'routing': routing, 'gridIds': self.grid_ids,
            'parameters': kernels.resolved_runoff_parameters(self.params),
            'forecastRainVersionId': self.forecast_rain['versionId'], 'inputManifestSHA256': self.input_digest})
        return {'runType': 'forecast', 'issue': self.request['issue'], 'hydrograph': hydrograph,
                'metrics': hydrograph['metrics'], 'parameters': self.request['parameters'],
                'parentStateVersionId': parent, 'stateVersionId': None, 'endStateVersionId': None,
                'forecastRain': {'versionId': self.forecast_rain['versionId'], 'name': self.forecast_rain['name'],
                                 'sourceLabel': self.forecast_rain['sourceLabel'], 'totalMm': float(precip[:, 0].sum())},
                'petMode': 'MONTHLY_CLIMATOLOGY_ASSUMPTION', 'gridCount': len(self.grids),
                'gridHistory': self.grid_history, 'routingEvidence': self.routing_evidence,
                'warnings': [*self.warnings, '降雨采用人工给定情景，逐小时均匀分配至有效网格。', '蒸散发采用逐月给定值。']}

    def calibrate(self):
        continuous = self.period.get("mode") == "continuous"
        groups = {"率定": "CALIBRATION", "验证": "VALIDATION", "独立检验": "INDEPENDENT_TEST"}
        rows = [{"event_id": row["id"], "year": pd.Timestamp(row["start"]).year,
                 "start": pd.Timestamp(row["start"]), "end": pd.Timestamp(row["end"]), "group": groups[row["purpose"]]}
                for row in self.period["windows" if continuous else "events"] if row["purpose"] in groups and row.get("status") != "inactive"]
        self.progress(5, "读取率定、验证及独立检验资料")
        events = self.load(rows, int(round(float(self.period.get("warmupDays", 7)) * 24)))
        if continuous:
            provider = PetProvider.from_file(MODEL / "config/pet_forcing_assumed_monthly.json")
            audits = []
            for event in events:
                expected = pd.date_range(event.start, event.end, freq="h")
                if not event.observed.index.is_unique or not event.observed.index.equals(expected):
                    raise ValueError(f"{event.event_id} 出口流量须为完整且唯一的逐小时序列")
                if not np.isfinite(event.observed.to_numpy()).all() or (event.observed < 0).any():
                    raise ValueError(f"{event.event_id} 出口流量存在缺测或负值")
                if self.request["calibration"]["objectives"]["metrics"].get("nse") and event.observed.var() == 0:
                    raise ValueError(f"{event.event_id} 实测流量无变化，不能计算 NSE")
                counts = event.source_station_count
                source_coverage = None
                if counts is not None and np.any(np.asarray(counts) >= 0):
                    source_coverage = float(np.mean(np.asarray(counts) >= 1))
                    if len(counts) != len(event.times) or source_coverage < 1:
                        raise ValueError(f"{event.event_id} 雨量站记录未覆盖完整计算时段（含预热期）")
                event.pet_mm_h = provider.values(event.times)
                audits.append({"periodId": event.event_id, "hours": len(expected), "flowCompleteness": 1.0,
                               "rainCompleteness": 1.0, "sourceStationCoverage": source_coverage,
                               "warmupHours": int(event.times.searchsorted(event.start))})
            write_json(self.folder / "period-quality.json", audits)
            self.warnings.append("蒸散发采用逐月给定值；各时段独立预热，预热段不计入评价。")
        else:
            areas = contributing_grid_areas(self.mapping_file, read_topology_json(self.topology), "R0020_UP", self.grid_ids)
            indexes = np.flatnonzero(areas > 0)
            quality = read_json(MODEL / "config/event_quality_config.json")
            audits = [evaluate_event_quality(event_id=e.event_id, start=e.start, end=e.end, times=e.times,
                      grid_precipitation_mm=e.precip[:, indexes], grid_areas_km2=areas[indexes], observed_flow_cms=e.observed,
                      source_station_count=e.source_station_count, config=quality) for e in events]
            write_json(self.folder / "event-quality.json", audits)
            rejected = [a for a in audits if not a["accepted"]]
            if rejected:
                raise ValueError("所选场次未通过水文资料检查：" + "; ".join(f"{a['event_id']} {','.join(a['exclusion_reasons'])}" for a in rejected[:12]))
            for event in events:
                event.pet_mm_h = np.zeros(len(event.times), dtype=float)
            self.warnings.append("本次洪水场次率定采用零蒸散发。")
        criteria = self.request["calibration"]["objectives"]
        metric_names = {"nse": "NSE", "peak": "PEAK_ERROR", "time": "PEAK_TIME_ERROR", "volume": "VOLUME_ERROR"}
        terms = [{"metric": metric_names[k], "weight": criteria["weights"][k], "scale": 24 if k == "time" else 100} for k, active in criteria["metrics"].items() if active]
        initial_parameters = {key.split('.')[-1]: value for key, value in kernels.resolved_runoff_parameters(self.params).items()}
        validate_selection(self.request['calibration'], initial_parameters)
        routing_search = self.request['calibration'].get('target') == 'routing'
        routing_calibration.validate(self.request['calibration'], self.routing_payload)
        specs = routing_calibration.resolve_space(self.request['calibration']) if routing_search else resolve_space(self.request['calibration'])
        config = self.request["calibration"]["optimizer"]

        def simulate(event, params):
            runoff = self.params if routing_search else params
            routing = kernels.read_parameters_json_from_mapping(params) if routing_search else self.routing
            series = kernels.route_event_fast(event, runoff, routing, self.router, self.mapping, self.initial_state(runoff))[0]
            if not series.index.equals(event.times) or not np.isfinite(series.to_numpy()).all() or (series < 0).any():
                raise ValueError(f"{event.event_id} 模拟流量存在无效值或时间缺口")
            return series

        def objective(params):
            frame = pd.DataFrame([kernels.metrics(e.observed, simulate(e, params).loc[e.start:e.end]) for e in events if e.group == "CALIBRATION"])
            return kernels.objective_loss(frame, terms)

        best, outcome = run_search(specs, config, self.routing_payload if routing_search else self.params, objective,
                                   routing_calibration.candidate_payload if routing_search else kernels.candidate_params,
                                   routing_calibration.resolved if routing_search else kernels.resolved_runoff_parameters, search, self.progress)
        # Preserve the delivered diagnostic report's search-coordinate contract.
        runoff_best = self.params if routing_search else best
        search_values = routing_calibration.resolved(best) if routing_search else kernels.resolved_runoff_parameters(best)
        total_day, groundwater_fraction = kernels.hourly_source_separation_to_daily(runoff_best.KI, runoff_best.KG)
        search_values.update({"runoff.K_total_day": total_day, "runoff.groundwater_fraction": groundwater_fraction})
        outcome["best_parameters"] = {spec["name"]: search_values[spec["name"]] for spec in specs}
        event_metrics, hydrographs = [], {}
        for index, event in enumerate(events):
            self.progress(80 + 19 * (index + 1) / len(events), f"冻结参数独立评价 {index + 1}/{len(events)}")
            series = simulate(event, best)
            event_metrics.append({"eventId": event.event_id, "group": event.group, **kernels.metrics(event.observed, series.loc[event.start:event.end])})
            hydrographs[event.event_id] = self.hydrograph(event, series)
        frame = pd.DataFrame(event_metrics)

        def summary(group):
            rows = frame[frame.group == group]
            result = kernels.summary_for(rows)
            return {**result, "nse": result.get("mean_NSE"), "peakErrorPercent": result.get("mean_abs_peak_error_percent"), "passed": False, "conclusion": "待生产验收"}

        version = f"PARAM-{self.task_id}"
        parameters = {k.split(".")[-1]: v for k, v in kernels.resolved_runoff_parameters(runoff_best).items()}
        result = {"parameterVersionId": version, "bestParameters": parameters, "calibrationMode": self.period["mode"],
                  "evaluationMode": self.request['calibration'].get('evaluationMode', 'automatic'),
                  "periods": self.period.get("windows"), "periodQuality": audits if continuous else None,
                  "statePolicy": "independent-warmup", "optimizationGroups": ["CALIBRATION"],
                  "qualificationPolicy": read_json(MODEL / "config/qualification_config_event.json"),
                  "requestedTolerances": criteria.get("tolerances"),
                  "searchSpace": specs,
                  "searchPlan": routing_calibration.summary(self.request['calibration'], self.routing_payload, best, specs) if routing_search else search_summary(self.request['calibration'], initial_parameters, parameters, specs),
                  "calibrationMetrics": summary("CALIBRATION"), "validationMetrics": summary("VALIDATION"),
                  "independentTest": summary("INDEPENDENT_TEST") if (frame.group == "INDEPENDENT_TEST").any() else None,
                  "eventMetrics": event_metrics, "hydrographs": hydrographs,
                  "hydrograph": next(iter(hydrographs.values())), "optimizer": outcome,
                  "eventSet": self.period.get("eventSet"), "warnings": self.warnings,
                  "petMode": "MONTHLY_CLIMATOLOGY_ASSUMPTION" if continuous else "CONSTANT_ZERO_EXPLICIT_REQUEST"}
        if routing_search:
            RoutingVersions(RUNTIME).validate(best)
            result.update(calibrationTarget='routing', bestRoutingParameters=best)
            if self.publish_parameters:
                candidate = RoutingVersions(RUNTIME).create({'name': f'坡面汇流率定 · {self.task_id[-8:]}',
                    'parentVersionId': self.routing_version, 'parameters': best})
                result['routingCandidate'] = candidate
        parameter_path = RUNTIME / "parameters" / f"{version}.json" if self.publish_parameters else self.folder / "parameter-candidate.json"
        write_json(parameter_path, {"context": self.request["context"], **result,
                   "taskId": self.task_id, "resultSource": "model-backend", "productionEligible": False,
                   "lineage": {**self.request.get("lineage", {}), "inputBundleVersionId": self.bundle_version,
                               "inputBundleManifestSHA256": self.bundle_digest, "inputManifestSHA256": self.input_digest,
                               "forcingVersion": self.forcing_version, "observationVersion": self.observation_version}})
        return result

    def run(self):
        result = self.calibrate() if self.request["runType"] == "calibration" else self.forecast() if self.request['runType'] == 'forecast' else self.simulate()
        result.update({"schemaVersion": "2.0", "taskId": self.task_id, "context": self.request["context"], "contextKey": self.request["context"]["contextKey"],
                       "resultVersionId": f"RESULT-{self.task_id}", "resultSource": "model-backend", "productionEligible": False,
                       "lineage": {**self.request.get("lineage", {}), "resultVersionId": f"RESULT-{self.task_id}",
                                  "inputManifestSHA256": self.input_digest,
                                  "inputBundleVersionId": self.bundle_version, "inputBundleManifestSHA256": self.bundle_digest,
                                  "routingVersionId": self.routing_version,
                                  "forcingVersion": self.forcing_version, "observationVersion": self.observation_version}})
        write_json(self.folder / "result.json", result)
        self.progress(100, "真实模型任务完成；结果与版本已持久化")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    request = read_json(Path(args.task) / "request.json")
    if request['runType'] == 'spatialExport':
        from spatial_products import generate
        from input_products import InputProducts
        generate(Path(args.task), request, InputProducts())
    elif request["runType"] in {"pointQuery", "diagnosticExperiment", "productionWorkflow"}:
        from diagnostic_jobs import run_diagnostic
        run_diagnostic(Path(args.task))
    else:
        ModelJob(args.task).run()
