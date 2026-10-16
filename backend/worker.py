"""Execute a frozen platform job using the registered model implementation.

Each worker is an isolated cancellable process. Never write into the delivery or
vendor inputs; all progress, states, metrics and process lines belong to its job.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import time
import json
from collections import OrderedDict
import sys
from pathlib import Path

from common import MODEL, RUNTIME, ROUTING_VERSION, read_json, write_json, clean
from contracts import freeze_input_selection, freeze_routing_selection
from input_products import InputProducts
from routing_versions import RoutingVersions
from routing_adapter import RegisteredRoutingRunner
from state_integrity import assert_state_input_compatibility, validated_parent_state, build_state_driving_identity
from state_initialization_adapter import freeze_initialization, anchored_routing
from calibration_search import run_search
from calibration_objectives import objective_terms
from original_calibration_plan import validate_original_request, source_replay_exclusions
from calibration_space import resolve_space, validate_selection, search_summary
import routing_calibration
import joint_calibration
from calibration_groups import group_windows, scoring_events, continuous_origin
from science_context import IDENTITY_KEYS, ScienceVersions, freeze_science_selection
from observation_quality import evaluate_observation_coverage, masked_volume_metrics

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
    def __init__(self, folder, publish_parameters=False):
        self.job_started = time.perf_counter()
        self.stage_timings = {}
        self.folder = Path(folder)
        self.publish_parameters = publish_parameters
        self.request = read_json(self.folder / "request.json")
        if (self.request.get('calibration') or {}).get('simulationOnly'):
            self.publish_parameters = False
        self.request = validate_original_request(self.request, RUNTIME)
        self.products = InputProducts(runtime_root=RUNTIME)
        self.request, self.bundle = freeze_input_selection(self.request, self.products)
        self.request = freeze_routing_selection(self.request, self.bundle)
        self.request = freeze_initialization(self.request, self.bundle)
        self.request, self.science = freeze_science_selection(self.request, RUNTIME)
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
        if (self.request.get('calibration') or {}).get('optimizer', {}).get('initializationPolicy') != 'SOURCE_INITIAL_CANDIDATES':
            joint_calibration.validate_routing(self.routing_payload)
        self.routing_version = self.request["lineage"]["routingVersionId"]
        self.template = read_json(self.science['paths']['initial_land_state'])
        self.pet_provider = PetProvider.from_file(self.science['paths']['pet_config'])
        flow = pd.read_csv(self.input_paths["flow"])
        self.observed = flow.assign(time=pd.to_datetime(flow.time)).set_index("time")["flow_m3_s"]
        self.observed = pd.to_numeric(self.observed, errors='coerce')
        self.observed = self.observed.where(np.isfinite(self.observed) & (self.observed >= 0))
        self.precip_dir = self.bundle["precipitationDir"]
        self.params = load_parameter_set(MODEL / "config/parameter_set_land.json")
        overrides = self.request.get("calibration", {}).get("initialParameters", {}) if self.request["runType"] == "calibration" else self.request.get("parameters", {})
        self.params = kernels.candidate_params(self.params, {f"runoff.{key}": value for key, value in overrides.items()})
        self.warnings = [*self.bundle.get("qualityNotes", [])]
        self.freeze_input_provenance()
        self.stage_timings['preparationSeconds'] = round(time.perf_counter()-self.job_started, 3)

    def freeze_input_provenance(self):
        warmup = pd.Timedelta(days=float(self.period.get("warmupDays", 7)))
        calibration = self.request.get('calibration') or {}
        policy = calibration.get('statePolicy', self.science['statePolicy'].get('strategy')) if self.request['runType'] == 'calibration' else self.science['statePolicy'].get('strategy')
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
            effective_warmup = pd.Timedelta(0) if self.period.get("initialStateVersionId") or policy == 'continuous-origin' else warmup
            years.update(range((pd.Timestamp(row["start"]) - effective_warmup).year, pd.Timestamp(row["end"]).year + 1))
        if policy == 'continuous-origin' and not self.period.get('initialStateVersionId'):
            origin = calibration.get('continuousOrigin') or self.period.get('continuousOrigin') or self.science['statePolicy'].get('continuousOrigin')
            if not origin or origin == 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP':
                origin = continuous_origin(read_json(self.input_paths['events'])['packages'], int(warmup.total_seconds() / 3600))
            self.continuous_origin = pd.Timestamp(origin)
            first = min(pd.Timestamp(row['start']) for row in periods if row.get('purpose') != '未使用' and row.get('status') != 'inactive')
            if self.continuous_origin > first:
                raise ValueError('连续起点晚于首个评价时刻')
            last = max(pd.Timestamp(row['end']) for row in periods if row.get('purpose') != '未使用' and row.get('status') != 'inactive')
            years.update(range(self.continuous_origin.year, last.year + 1))
        paths = [self.precip_dir / f"nananxi_precip_hourly_1km_{year}.nc" for year in sorted(years)]
        paths += list(self.input_paths.values())
        paths += list((MODEL / "config").glob("*.json"))
        paths += list((MODEL / "code").rglob("*.py")) + [MODEL / "scripts/run_formal_multievent_calibration.py"]
        paths += list(self.science['paths'].values())
        if self.pet_provider.mode == 'HOURLY_CSV':
            paths.append(self.science['paths']['pet_config'].parent / 'pet-data.csv')
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
                    "modelProvenance": read_json(MODEL / 'upgrade-manifest.json') if (MODEL / 'upgrade-manifest.json').is_file() else {'baseDelivery': '20261008', 'upgradeRegistered': False},
                    "science": {key: self.science[key] for key in ('scienceVersionId', *IDENTITY_KEYS, 'petMode', 'statePolicy')},
                    "note": "计算实际使用冻结登记版本；不读取 current/latest，前端静态预览不是数值输入。"}
        parent = self.period.get("initialStateVersionId")
        if self.request["runType"] == "calibration":
            manifest["calibrationImplementation"] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                     for name in ("worker.py", "calibration_search.py", "calibration_space.py", "routing_calibration.py", "joint_calibration.py", "calibration_groups.py", "contracts.py")}
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
        payload = {"progress": round(float(percent), 1), "stageLabel": stage}
        if hasattr(self, 'search_history'):
            payload['searchProgress'] = {'evaluations': len(self.search_history), 'budget': self.request['calibration']['optimizer']['maxEvaluations'], 'history': self.search_history[-200:]}
            elapsed = time.perf_counter() - self.search_started
            completed = len(self.search_history)
            payload['searchProgress'].update(elapsedSeconds=round(elapsed, 2),
                remainingSeconds=round(elapsed / completed * max(0, self.request['calibration']['optimizer']['maxEvaluations'] - completed), 1) if completed else None,
                cacheHits=getattr(self, 'evaluation_cache_hits', 0))
        write_json(self.folder / "progress.json", payload)
        print(stage, flush=True)

    def load(self, rows, warmup):
        if self.request['runType'] != 'calibration' or self.bundle.get('manifest', {}).get('forcingPolicy') == 'STRICT_MISSING' or self.period.get('mode') == 'continuous' or self.request.get('scheme'):
            from strict_forcing import load_hourly_forcing
            data = []
            for row in rows:
                first, last = pd.Timestamp(row['start']), pd.Timestamp(row['end'])
                times, values, counts, audit = load_hourly_forcing(
                    self.bundle, self.grids, first - pd.Timedelta(hours=warmup), last)
                data.append(SimpleNamespace(event_id=row['event_id'], year=first.year, start=first, end=last,
                    times=times, precip=values, observed=self.observed.reindex(pd.date_range(first, last, freq='h')),
                    group=row['group'], segment_start=times[0], source_station_count=counts, forcing_audit=audit))
            return data
        data = kernels.load_event_data(MODEL, rows, self.grids, self.observed, warmup, continuous=False, precip_dir=self.precip_dir)
        if len(data) != len(rows):
            raise ValueError("部分时段缺少实际出口流量或驱动；拒绝补造资料")
        for event in data:
            if not np.isfinite(event.precip).all():
                raise ValueError(f"{event.event_id} 驱动存在缺测")
            event.observed = self.observed.reindex(pd.date_range(event.start, event.end, freq='h'))
        return data

    def hydrograph(self, event, simulated):
        simulated = simulated.loc[event.start:event.end]
        observed = event.observed.reindex(simulated.index)
        is_forecast = self.request['runType'] == 'forecast'
        observation_coverage = None if is_forecast else evaluate_observation_coverage(
            simulated.index, observed, read_json(self.input_paths['quality']))
        metrics = {} if is_forecast else kernels.metrics(observed, simulated)
        if not is_forecast:
            paired = observed.dropna()
            # A visible gap stays a gap. Missing peaks and full-event volumes
            # must not receive a misleading score from the available fragment.
            if not observation_coverage['accepted'] or len(paired) < 3 or paired.var() == 0:
                metrics['NSE'] = None
            if observation_coverage['coverage_fraction'] < 1:
                for name in ('peak_error_percent', 'peak_time_error_hours', 'volume_error_percent'):
                    metrics[name] = None
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
            'volumeEvaluation': {} if is_forecast else masked_volume_metrics(simulated.index, observed, simulated),
            'observationCoverage': observation_coverage,
            "forcing": {"precipitation": [float(v) for v in rainfall], "temperature": [], "components": {},
                        "versionId": self.forecast_rain['versionId'] if is_forecast else self.forcing_version, "bundleVersionId": self.bundle_version,
                        "sourceLabel": self.forecast_rain['sourceLabel'] if is_forecast else "实际登记驱动 · 徐畈控制区面积加权降雨",
                        "qualityNotes": self.bundle.get("qualityNotes", [])},
        }

    def initial_state(self, params):
        return build_uniform_land_state(self.template, len(self.grids), params)

    def route_with_history(self, event, land, routing):
        """Batch the existing kernel; retain every evaluated hour and final state."""
        count = len(event.times)
        first = int(event.times.searchsorted(event.start))
        diagnostic_fields = ['et_actual', 'water_balance_error', 'EU', 'EL', 'ED', 'infiltration', 'net_rainfall', 'pervious_runoff']
        fields = list(land) + ["RS", "RI", "RG", "precipitation", "pet", *diagnostic_fields]
        history = {field: np.empty((count - first, len(self.grids)), dtype=float) for field in fields}
        components = {field: np.empty_like(event.precip, dtype=float) for field in ("RS", "RI", "RG")}
        first_call = True
        limits = {'WU': self.params.WUM, 'WL': self.params.WLM, 'WD': self.params.WDM,
                  'S': self.params.SM, 'FR': 1.0}
        warmup_seconds = runoff_seconds = 0.0
        begin = 0
        last_progress = -max(1, count // 20)
        while begin < count:
            stop = min(begin + 168, first if begin < first else count)
            evaluated = begin >= first
            if first_call or begin - last_progress >= max(1, count // 20):
                self.progress(40 + 35 * begin / count, '首次数值准备与编译缓存加载' if first_call else ('预热产流计算' if not evaluated else f'网格产流计算 {begin}/{count}'))
                last_progress = begin
            call_started = time.perf_counter()
            initial = land
            output = simulate_xaj_liquid_grids(event.precip[begin:stop], self.params, event.pet_mm_h[begin:stop], land, return_diagnostics=True)
            rs, ri, rg, land = output[:4]
            diagnostics = output[4]
            # The former hourly wrapper clips roundoff to physical state bounds
            # before the next hour. End a batch at its first such boundary so
            # the unchanged wrapper performs exactly the same normalization.
            # Never widen numerical tolerances or alter the scientific kernel.
            boundary = np.zeros(stop - begin - 1, dtype=bool)
            for field, upper in limits.items():
                values = diagnostics[field][:-1]
                boundary |= np.any((values < 0) | (values > upper), axis=1)
            if boundary.any():
                length = int(np.flatnonzero(boundary)[0]) + 1
                stop = begin + length
                rs, ri, rg = rs[:length], ri[:length], rg[:length]
                diagnostics = {field: values[:length] for field, values in diagnostics.items()}
                land = {field: diagnostics[field][-1].copy() if field in diagnostics else initial[field].copy() for field in land}
            elapsed = time.perf_counter()-call_started
            if first_call:
                self.stage_timings['runoffFirstCallSeconds'] = round(elapsed,3)
                first_call = False
            if evaluated:
                runoff_seconds += elapsed
            else:
                warmup_seconds += elapsed
            for field, values in (("RS", rs), ("RI", ri), ("RG", rg)):
                components[field][begin:stop] = values
            if evaluated:
                segment = slice(begin-first,stop-first)
                for field in land:
                    if field in diagnostics:
                        history[field][segment] = diagnostics[field]
                    elif np.array_equal(initial[field],land[field]):
                        history[field][segment] = land[field]
                    else:
                        raise ValueError('现有内核未提供完整逐时状态：'+field)
                for field in ("RS", "RI", "RG"):
                    history[field][segment] = components[field][begin:stop]
                history["precipitation"][segment] = event.precip[begin:stop]
                pet = event.pet_mm_h[begin:stop]
                history["pet"][segment] = pet[:,None] if pet.ndim == 1 else pet
                for field in diagnostic_fields:
                    history[field][segment] = diagnostics[field]
            begin = stop
        self.stage_timings.update(warmupSeconds=round(warmup_seconds,3),runoffSeconds=round(runoff_seconds,3))
        self.progress(78, "按冻结拓扑计算坡面与河网汇流")
        persistence_started = time.perf_counter()
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
        persistence_seconds = time.perf_counter()-persistence_started
        routing_started = time.perf_counter()
        simulated, final_routing = self.router.run(event.times, components["RS"] @ self.mapping,
                                                  components["RI"] @ self.mapping, components["RG"] @ self.mapping,
                                                  self.routing, routing)
        self.stage_timings['routingSeconds'] = round(time.perf_counter()-routing_started,3)
        persistence_started = time.perf_counter()
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
                                   "petMode": self.science['petMode'],
                                   **{key: self.science[key] for key in IDENTITY_KEYS},
                                   "parentStateVersionId": self.period.get("initialStateVersionId")}}
        write_json(self.folder / "grid-history.json", metadata)
        self.grid_history = {"available": True, "taskId": self.task_id, "start": metadata["times"][0],
                             "end": metadata["times"][-1], "steps": len(metadata["times"]), "gridCount": len(self.grid_ids), "fields": fields,
                             "arraySHA256": history_digest, "catalogSHA256": hashlib.sha256((self.folder / "grid-history.json").read_bytes()).hexdigest()}
        self.stage_timings['statePersistenceSeconds'] = round(persistence_seconds+time.perf_counter()-persistence_started,3)
        return simulated, land, final_routing

    def checkpoint(self, land, routing, timestamp, parameters):
        version = f"STATE-{self.task_id}"
        payload = {"schemaVersion": "2.0", "stateVersionId": version, "time": timestamp.isoformat(),
                   "context": self.request["context"], "gridIds": self.grid_ids,
                   "gridGeometry": self.grid_geometry,
                   "land": land, "routing": routing, "parameters": parameters,
                   "routingTopology": {"unitIds": self.router.unit_ids,
                                       "reachIds": self.router.reach_ids,
                                       "controlReachId": self.router.control_point.reach_id},
                   "sourceTaskId": self.task_id, "resultSource": "model-backend", "productionEligible": False}
        if getattr(self, 'driving_identity', None) is not None:
            payload['drivingIdentity'] = self.driving_identity
        payload['scienceSnapshot'] = ScienceVersions(RUNTIME).export(self.science['scienceVersionId'])
        payload["parameterVersionId"] = self.request.get("lineage", {}).get("parameterVersionId")
        payload["initialization"] = getattr(self, "initialization_summary", None)
        payload.update({"parentStateVersionId": self.period.get("initialStateVersionId"),
                        "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                        "inputBundleVersionId": self.bundle_version, "inputBundleManifestSHA256": self.bundle_digest,
                        "routingVersionId": self.routing_version,
                        "routingManifestSHA256": self.request["lineage"]["routingManifestSHA256"],
                        "petMode": self.science['petMode'], "scienceVersionId": self.science['scienceVersionId'],
                        **{key: self.science[key] for key in IDENTITY_KEYS}, "inputManifestSHA256": self.input_digest})
        write_json(self.folder / "checkpoint.json", payload)
        write_json(RUNTIME / "states" / f"{version}.json", payload)
        return version

    def simulate(self):
        start, end = pd.Timestamp(self.period["startTime"]), pd.Timestamp(self.period["endTime"])
        warmup = int(round(float(self.period.get("warmupDays", 7)) * 24))
        parent = self.period.get("initialStateVersionId")
        checkpoint = None
        land, routing = self.initial_state(self.params), None
        if parent:
            if not str(parent).startswith("STATE-JOB-") or "/" in parent or "\\" in parent:
                raise ValueError("只能使用后端生成的真实状态版本")
            checkpoint = validated_parent_state(RUNTIME / "states" / f"{parent}.json", RUNTIME / "tasks", parent)
            assert_state_input_compatibility(checkpoint, self.request, self.products)
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
        if not parent and hasattr(self, 'continuous_origin'):
            if self.continuous_origin > start:
                raise ValueError('所选科学条件的连续原点晚于请求时段')
            warmup = int((start - self.continuous_origin).total_seconds() / 3600)
        loading_started = time.perf_counter()
        event = self.load([{"event_id": self.task_id, "year": start.year, "start": start, "end": end, "group": "SIMULATION"}], warmup)[0]
        provider = self.pet_provider
        event.pet_mm_h = provider.values(event.times)
        self.stage_timings['loadingSeconds'] = round(time.perf_counter()-loading_started,3)
        # A legacy parent lacks a reproducible prefix; preserve its strict
        # same-bundle continuation without claiming a verified full trajectory.
        self.driving_identity = build_state_driving_identity(
            self.bundle, self.grids, event.times, event.precip, event.source_station_count,
            parent=checkpoint.get('drivingIdentity') if checkpoint else None, products=self.products
        ) if checkpoint is None or checkpoint.get('drivingIdentity') else None
        self.warnings.append('蒸散发使用冻结科学条件：' + self.science['petMode'])
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
                self.driving_identity['observedFlowAnchor'] = {
                    'time': anchor['time'], 'flowCms': anchor['flowCms']}
        parameters = kernels.resolved_runoff_parameters(self.params)
        saving_started = time.perf_counter()
        version = self.checkpoint(final_land, final_routing, event.end, parameters)
        self.stage_timings['checkpointSeconds'] = round(time.perf_counter()-saving_started,3)
        hydrograph = self.hydrograph(event, simulated)
        means = {"wu": float(final_land["WU"].mean()), "wl": float(final_land["WL"].mean()), "wd": float(final_land["WD"].mean()), "free": float(final_land["S"].mean()),
                 "soil": float((final_land["WU"] + final_land["WL"] + final_land["WD"]).mean() / self.params.WM)}
        return {"hydrograph": hydrograph, "metrics": hydrograph["metrics"], "endStateVersionId": version,
                "stateVersionId": version, "parentStateVersionId": parent, "means": means,
                "parameters": {k.split(".")[-1]: v for k, v in parameters.items()}, "petMode": self.science['petMode'], "warnings": self.warnings,
                "stateTime": event.end.isoformat(), "gridCount": len(self.grids), "gridHistory": self.grid_history,
                "initialization": self.initialization_summary,
                'science': self.request['science'],
                'statePolicy': self.science['statePolicy']['strategy'],
                'continuousOrigin': self.continuous_origin.isoformat() if hasattr(self, 'continuous_origin') else None,
                'actualWarmupHours': warmup,
                'drivingIdentity': self.driving_identity,
                "routingEvidence": self.routing_evidence, 'timings':self.stage_timings}

    def forecast(self):
        parent = self.period['initialStateVersionId']
        checkpoint = validated_parent_state(RUNTIME / 'states' / f'{parent}.json', RUNTIME / 'tasks', parent)
        assert_state_input_compatibility(checkpoint, self.request, self.products)
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
        event.pet_mm_h = self.pet_provider.values(times)
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
                'petMode': self.science['petMode'], 'gridCount': len(self.grids),
                'gridHistory': self.grid_history, 'routingEvidence': self.routing_evidence,
                'science': self.request['science'],
                'warnings': [*self.warnings, '降雨采用人工给定情景，逐小时均匀分配至有效网格。',
                             '蒸散发采用冻结科学配置：' + self.science['petMode']]}

    def calibrate(self):
        preparation_started = time.perf_counter()
        continuous = self.period.get("mode") == "continuous"
        groups = {"率定": "CALIBRATION", "验证": "VALIDATION", "独立检验": "INDEPENDENT_TEST"}
        selected = [row for row in self.period["windows" if continuous else "events"]
                    if row['purpose'] in groups and row.get('status') != 'inactive']
        physical_groups = group_windows(selected)
        union_rows, membership = scoring_events(selected, physical_groups, groups)
        union_rows = [{**row, 'start': pd.Timestamp(row['start']), 'end': pd.Timestamp(row['end'])} for row in union_rows]
        rows = [{"event_id": row["id"], "year": pd.Timestamp(row["start"]).year,
                 "start": pd.Timestamp(row["start"]), "end": pd.Timestamp(row["end"]), "group": groups[row["purpose"]]}
                for row in selected]
        warmup = int(round(float(self.period.get('warmupDays', 7)) * 24))
        state_policy = self.request['calibration'].get('statePolicy', self.science['statePolicy']['strategy'])
        self.progress(5, "读取率定、验证及独立检验资料")
        events = self.load(union_rows, 0 if state_policy == 'continuous-origin' else warmup)
        by_id = {event.event_id: event for event in events}
        views = []
        for row in rows:
            view = copy.copy(by_id[membership[row['event_id']]])
            view.event_id, view.start, view.end, view.group = row['event_id'], row['start'], row['end'], row['group']
            view.observed = self.observed.reindex(pd.date_range(view.start, view.end, freq='h'))
            views.append(view)
        provider = self.pet_provider
        for event in events:
            event.pet_mm_h = provider.values(event.times)
        metrics_requested = self.request['calibration']['objectives']['metrics']
        if metrics_requested.get('nse') or metrics_requested.get('kge'):
            invalid = []
            for event in views:
                sample = event.observed.dropna()
                if len(sample) < 3 or sample.var() == 0 or (metrics_requested.get('kge') and sample.mean() == 0):
                    invalid.append(event.event_id)
            if invalid:
                raise ValueError('所选NSE或KGE指标需要至少3个有效且有变化的实测流量：' + '、'.join(invalid))
        quality = read_json(self.input_paths['quality'])
        if continuous:
            audits = []
            for event in views:
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
                audits.append({"periodId": event.event_id, "hours": len(expected), "flowCompleteness": 1.0,
                               "rainCompleteness": 1.0, "sourceStationCoverage": source_coverage,
                               "warmupHours": int(event.times.searchsorted(event.start)),
                               'observationCoverage': evaluate_observation_coverage(expected, event.observed, quality)})
            write_json(self.folder / "period-quality.json", audits)
        else:
            areas = contributing_grid_areas(self.mapping_file, read_topology_json(self.topology), "R0020_UP", self.grid_ids)
            indexes = np.flatnonzero(areas > 0)
            audits = [evaluate_event_quality(event_id=e.event_id, start=e.start, end=e.end, times=e.times,
                      grid_precipitation_mm=e.precip[:, indexes], grid_areas_km2=areas[indexes], observed_flow_cms=e.observed,
                      source_station_count=e.source_station_count, config=quality) for e in views]
            for audit, event in zip(audits, views):
                coverage = evaluate_observation_coverage(pd.date_range(event.start, event.end, freq='h'), event.observed, quality)
                audit['observation_coverage'] = coverage
                audit['warning_reasons'] = list(dict.fromkeys(audit['warning_reasons'] + coverage['warning_reasons']))
                audit['exclusion_reasons'] = list(dict.fromkeys(audit['exclusion_reasons'] + coverage['exclusion_reasons']))
                audit['accepted'] = bool(audit['accepted'] and coverage['accepted'])
            write_json(self.folder / "event-quality.json", audits)
            replay_only = source_replay_exclusions(self.request, RUNTIME)
            for audit in audits:
                if replay_only and audit.get('event_id') in replay_only and audit['exclusion_reasons'] == ['DURATION_EXCEEDS_MAXIMUM']:
                    audit['sourceSupplementaryReplay'] = True
                    audit['usedForParameterSearch'] = False
            write_json(self.folder / 'event-quality.json', audits)
            rejected = [a for a in audits if not a["accepted"] and not a.get('sourceSupplementaryReplay')]
            if rejected:
                raise ValueError("所选场次未通过水文资料检查：" + "; ".join(f"{a['event_id']} {','.join(a['exclusion_reasons'])}" for a in rejected[:12]))
        self.warnings.append('蒸散发与初态使用不可变科学配置；预热段不参与评价。')
        timeline = None
        if state_policy == 'continuous-origin':
            start, end = min(event.start for event in events), max(event.end for event in events)
            origin = getattr(self, 'continuous_origin', None)
            if origin is None or origin > start:
                raise ValueError('连续率定原点缺失或晚于评价时段')
            timeline = self.load([{'event_id': 'CONTINUOUS-TRAJECTORY', 'year': start.year,
                                  'start': start, 'end': end, 'group': 'TRAJECTORY'}],
                                 int((start - origin).total_seconds() / 3600))[0]
            timeline.pet_mm_h = provider.values(timeline.times)
        self.warnings.append('重叠洪水共享同一状态轨迹；按物理洪水组去重小时后重新计算指标，各组等权。')
        criteria = self.request["calibration"]["objectives"]
        terms = objective_terms(criteria)
        initial_parameters = {key.split('.')[-1]: value for key, value in kernels.resolved_runoff_parameters(self.params).items()}
        joint_search = self.request['calibration'].get('target') == 'joint'
        if not joint_search:
            validate_selection(self.request['calibration'], initial_parameters)
        else:
            joint_calibration.validate(self.request['calibration'], initial_parameters, self.routing_payload)
        routing_search = self.request['calibration'].get('target') == 'routing'
        routing_calibration.validate(self.request['calibration'], self.routing_payload)
        specs = joint_calibration.resolve_space(self.request['calibration']) if joint_search else routing_calibration.resolve_space(self.request['calibration']) if routing_search else resolve_space(self.request['calibration'])
        config = self.request["calibration"]["optimizer"]

        # These caches live within one frozen job. Inputs, network, periods,
        # science and kernel cannot change during their lifetime. Runoff is
        # reused only when every runoff parameter and its initial state is fixed.
        fixed_runoff = {}
        if routing_search:
            self.progress(7, '固定产流参数：准备可复用产流过程（首次编译预热）')
            for event in ([timeline] if timeline is not None else events):
                # Keep the exact float64 path of route_event_fast. The vendor's
                # legacy offline cache intentionally stores float32; use a
                # platform-local container without altering that scientific file.
                rs, ri, rg, land = kernels.vectorized_xaj(event.precip, self.params,
                    np.ones(event.precip.shape[1]), kernels.DEFAULT_TEMP_C, event.pet_mm_h,
                    state=self.initial_state(self.params), return_state=True)
                fixed_runoff[id(event)] = kernels.PreparedRunoff(event, rs @ self.mapping,
                    ri @ self.mapping, rg @ self.mapping, land)
        preparation_seconds = time.perf_counter() - preparation_started

        def simulate(event, params):
            runoff = params['runoff'] if joint_search else self.params if routing_search else params
            routing = kernels.read_parameters_json_from_mapping(params['routing'] if joint_search else params) if (routing_search or joint_search) else self.routing
            joint_calibration.validate_routing(params['routing'] if joint_search else params if routing_search else self.routing_payload)
            series = (kernels.route_prepared_runoff(fixed_runoff[id(event)], routing, self.router)[0] if routing_search else
                      kernels.route_event_fast(event, runoff, routing, self.router, self.mapping, self.initial_state(runoff))[0])
            if not series.index.equals(event.times) or not np.isfinite(series.to_numpy()).all() or (series < 0).any():
                raise ValueError(f"{event.event_id} 模拟流量存在无效值或时间缺口")
            return series

        self.search_history = []
        self.search_started = time.perf_counter()
        self.evaluation_cache_hits = 0
        self.progress(8, '准备数值内核；首次运行正在编译预热')
        evaluation_cache = OrderedDict()
        def objective(params):
            loss = None
            try:
                identity = joint_calibration.resolved(params) if joint_search else routing_calibration.resolved(params) if routing_search else kernels.resolved_runoff_parameters(params)
                key = json.dumps(identity, sort_keys=True, allow_nan=False)
                if key in evaluation_cache:
                    self.evaluation_cache_hits += 1
                    loss = evaluation_cache[key]
                    return loss
                shared = simulate(timeline, params) if timeline is not None else None
                frame = pd.DataFrame([kernels.metrics(e.observed, (shared if shared is not None else simulate(e, params)).loc[e.start:e.end]) for e in events if e.group == "CALIBRATION"])
                loss = kernels.objective_loss(frame, terms)
                evaluation_cache[key] = loss
                if len(evaluation_cache) > 4096:
                    evaluation_cache.popitem(last=False)
                return loss
            finally:
                previous = self.search_history[-1]['bestLoss'] if self.search_history else None
                finite = float(loss) if loss is not None and np.isfinite(loss) else None
                self.search_history.append({'evaluation': len(self.search_history) + 1, 'loss': finite, 'bestLoss': min(previous, finite) if previous is not None and finite is not None else finite if previous is None else previous})

        initial = joint_calibration.initial_candidate(self.params, self.routing_payload) if joint_search else self.routing_payload if routing_search else self.params
        def resolve_runoff(params):
            result = kernels.resolved_runoff_parameters(params)
            daily, fraction = kernels.hourly_source_separation_to_daily(params.KI, params.KG)
            result.update({'runoff.K_total_day': daily, 'runoff.groundwater_fraction': fraction})
            return result
        best, outcome = run_search(specs, config, initial, objective,
                                   joint_calibration.candidate_payload if joint_search else routing_calibration.candidate_payload if routing_search else kernels.candidate_params,
                                   joint_calibration.resolved if joint_search else routing_calibration.resolved if routing_search else resolve_runoff, search, self.progress)
        search_seconds = time.perf_counter() - self.search_started
        evaluation_started = time.perf_counter()
        # Preserve the delivered diagnostic report's search-coordinate contract.
        runoff_best = best['runoff'] if joint_search else self.params if routing_search else best
        search_values = joint_calibration.resolved(best) if joint_search else routing_calibration.resolved(best) if routing_search else kernels.resolved_runoff_parameters(best)
        total_day, groundwater_fraction = kernels.hourly_source_separation_to_daily(runoff_best.KI, runoff_best.KG)
        search_values.update({"runoff.K_total_day": total_day, "runoff.groundwater_fraction": groundwater_fraction})
        outcome["best_parameters"] = {spec["name"]: search_values[spec["name"]] for spec in specs}
        shared = simulate(timeline, best) if timeline is not None else None
        simulations = {event.event_id: shared if shared is not None else simulate(event, best) for event in events}
        group_metrics = [{"groupId": group['id'], 'memberEventIds': group['memberEventIds'], 'group': event.group,
                          'start': event.start.isoformat(), 'end': event.end.isoformat(), 'uniqueHours': group['uniqueHours'],
                          **kernels.metrics(event.observed, simulations[event.event_id].loc[event.start:event.end]),
                          **masked_volume_metrics(pd.date_range(event.start, event.end, freq='h'), event.observed,
                                                  simulations[event.event_id].loc[event.start:event.end])}
                         for group, event in zip(physical_groups, events)]
        event_metrics, hydrographs = [], {}
        for index, event in enumerate(views):
            self.progress(80 + 19 * (index + 1) / len(views), f"冻结参数独立评价 {index + 1}/{len(views)}")
            series = simulations[membership[event.event_id]]
            event_metrics.append({"eventId": event.event_id, "group": event.group,
                                  **kernels.metrics(event.observed, series.loc[event.start:event.end]),
                                  **masked_volume_metrics(pd.date_range(event.start, event.end, freq='h'), event.observed,
                                                         series.loc[event.start:event.end])})
            hydrographs[event.event_id] = self.hydrograph(event, series)
        frame = pd.DataFrame(group_metrics)
        pd.DataFrame(group_metrics).to_csv(self.folder / 'overlap-group-metrics.csv', index=False, encoding='utf-8-sig')
        source_evaluation = None
        if self.request.get('sourcePlan'):
            from original_calibration_plan import original_plan_payload
            original_plan = original_plan_payload(self.request['sourcePlan']['parameterVersionId'], RUNTIME)['plan']
            view_by_id = {event.event_id: event for event in views}
            original_groups = []
            for group in original_plan['overlapGroups']:
                first_view = view_by_id[group['memberEventIds'][0]]
                index = pd.date_range(group['start'], group['end'], freq='h')
                observed = self.observed.reindex(index)
                values = simulations[membership[first_view.event_id]].reindex(index)
                original_groups.append({'groupId': group['id'], 'memberEventIds': group['memberEventIds'],
                                        'group': groups[group['purpose']], 'start': group['start'], 'end': group['end'],
                                        'uniqueHours': group['uniqueHours'], **kernels.metrics(observed, values),
                                        **masked_volume_metrics(index, observed, values)})
            original_ids = set(original_plan['sourceAcceptedEventIds'])
            original_windows = [row for row in event_metrics if row['eventId'] in original_ids]
            original_frame, window_frame = pd.DataFrame(original_groups), pd.DataFrame(original_windows)
            source_evaluation = {'sourceTaskId': original_plan['sourceTaskId'], 'planId': original_plan['planId'],
                                 'acceptedEventIds': original_plan['sourceAcceptedEventIds'],
                                 'supplementaryEventIds': [row['eventId'] for row in event_metrics if row['eventId'] not in original_ids],
                                 'originalGroupMetrics': original_groups, 'originalWindowMetrics': original_windows,
                                 'groupSummary': {name: kernels.summary_for(original_frame[original_frame.group == name]) for name in groups.values()},
                                 'windowSummary': {name: kernels.summary_for(window_frame[window_frame.group == name]) for name in groups.values()},
                                 'meaning': '原 16 场与 7 个物理洪水组分别评价；补算场次不参加原参数寻优。'}

        def summary(group):
            rows = frame[frame.group == group]
            result = kernels.summary_for(rows)
            return {**result, "nse": result.get("mean_NSE"), "peakErrorPercent": result.get("mean_abs_peak_error_percent"), "passed": False, "conclusion": "待生产验收"}

        version = f"PARAM-{self.task_id}"
        parameters = {k.split(".")[-1]: v for k, v in kernels.resolved_runoff_parameters(runoff_best).items()}
        result = {"parameterVersionId": version, "bestParameters": parameters, "calibrationMode": self.period["mode"],
                  "evaluationMode": self.request['calibration'].get('evaluationMode', 'automatic'),
                  'simulationOnly': self.request['calibration'].get('simulationOnly', False),
                  "periods": self.period.get("windows"), "periodQuality": audits if continuous else None,
                  "statePolicy": state_policy, 'actualWarmupHours': int((timeline.start - self.continuous_origin).total_seconds() / 3600) if timeline is not None else warmup,
                  'continuousOrigin': self.continuous_origin.isoformat() if timeline is not None else None,
                  "optimizationGroups": ["CALIBRATION"],
                  'evaluationPolicy': 'EQUAL_PHYSICAL_GROUP_UNIQUE_HOURS', 'overlapGroups': physical_groups,
                  'overlapGroupMetrics': group_metrics,
                  'windowSummary': {group: kernels.summary_for(pd.DataFrame(event_metrics).query('group == @group')) for group in groups.values()},
                  "qualificationPolicy": read_json(MODEL / "config/qualification_config_event.json"),
                  "requestedTolerances": criteria.get("tolerances"),
                  "objectiveTerms": terms,
                  'sourcePlan': self.request.get('sourcePlan'),
                  'sourcePlanEvaluation': source_evaluation,
                  "searchSpace": specs,
                  "searchPlan": joint_calibration.summary(self.request['calibration'], initial, best, specs) if joint_search else routing_calibration.summary(self.request['calibration'], self.routing_payload, best, specs) if routing_search else search_summary(self.request['calibration'], initial_parameters, parameters, specs),
                  "calibrationMetrics": summary("CALIBRATION"), "validationMetrics": summary("VALIDATION"),
                  "independentTest": summary("INDEPENDENT_TEST") if (frame.group == "INDEPENDENT_TEST").any() else None,
                  "eventMetrics": event_metrics, "hydrographs": hydrographs,
                  "hydrograph": next(iter(hydrographs.values())), "optimizer": outcome,
                  "eventSet": self.period.get("eventSet"), "warnings": self.warnings,
                  "petMode": self.science['petMode'],
                  'science': self.request['science'], 'scienceSnapshot': ScienceVersions(RUNTIME).export(self.science['scienceVersionId'])}
        if routing_search or joint_search:
            routing_best = best['routing'] if joint_search else best
            RoutingVersions(RUNTIME).validate(routing_best)
            result.update(calibrationTarget='joint' if joint_search else 'routing', bestRoutingParameters=routing_best)
            if self.publish_parameters:
                candidate = RoutingVersions(RUNTIME).create({'name': f'产汇流联合率定 · {self.task_id[-8:]}' if joint_search else f'坡面汇流率定 · {self.task_id[-8:]}',
                    'parentVersionId': self.routing_version, 'parameters': routing_best})
                result['routingCandidate'] = candidate
        parameter_path = RUNTIME / "parameters" / f"{version}.json" if self.publish_parameters else self.folder / "parameter-candidate.json"
        write_json(parameter_path, {"context": self.request["context"], **result,
                   "taskId": self.task_id, "resultSource": "model-backend", "productionEligible": False,
                   "lineage": {**self.request.get("lineage", {}), "inputBundleVersionId": self.bundle_version,
                               "inputBundleManifestSHA256": self.bundle_digest, "inputManifestSHA256": self.input_digest,
                               "forcingVersion": self.forcing_version, "observationVersion": self.observation_version}})
        result['timings'] = {'preparationSeconds': round(preparation_seconds, 3), 'searchSeconds': round(search_seconds, 3),
                             'finalEvaluationSeconds': round(time.perf_counter() - evaluation_started, 3)}
        result['cache'] = {'evaluationHits': self.evaluation_cache_hits, 'fixedRunoffProcesses': len(fixed_runoff), 'scope': 'frozen-job'}
        try:
            import numba
            from runoff_platform.models import xaj_vectorized
            from calibration_platform.routing_kernel import route_arrays
            compiled = {'runoff': bool(getattr(xaj_vectorized._array_kernel, 'signatures', [])),
                        'routing': bool(getattr(route_arrays, 'signatures', []))}
            result['acceleration'] = {'enabled': all(compiled.values()), 'numbaVersion': numba.__version__, 'compiledKernels': compiled}
        except ImportError:
            result['acceleration'] = {'enabled': False, 'reason': '未安装Numba'}
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
