"""Immutable routing configuration registry; never overwrite vendor defaults."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from pathlib import Path
from common import MODEL, RUNTIME, ROUTING_VERSION, now, read_json, write_json, safe_task


def payload_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


class RoutingVersions:
    def __init__(self, runtime_root=None):
        self.root = Path(runtime_root or RUNTIME) / "routing-versions"

    def resolve(self, version=None):
        version = version or ROUTING_VERSION
        if version == ROUTING_VERSION:
            path = MODEL / "config/routing_parameters_control_split_v2.json"
            payload = read_json(path)
            return {"routingVersionId": version, "name": "控制切分 v2 参考汇流", "parameters": payload,
                    "parametersSHA256": payload_digest(payload), "fileSHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "productionEligible": False, "createdAt": None, "parentVersionId": None}
        if not str(version).startswith("ROUTING-"):
            raise ValueError("汇流版本未登记")
        row = read_json(safe_task(self.root, version).with_suffix(".json"))
        if row.get("routingVersionId") != version or row.get("parametersSHA256") != payload_digest(row["parameters"]):
            raise ValueError("汇流版本摘要不一致")
        self.validate(row["parameters"], allow_search_reference=row.get('searchReferenceOnly') is True)
        return row

    def list(self):
        return {"versions": [self.resolve(), *[self.resolve(p.stem) for p in sorted(self.root.glob("*.json"))]]}

    def validate(self, payload, allow_search_reference=False):
        reference = self.resolve()["parameters"]
        if not isinstance(payload, dict) or set(payload) != set(reference):
            raise ValueError("汇流参数字段必须与参考配置一致")
        if payload["time_step_hours"] != 1 or isinstance(payload["time_step_hours"], bool):
            raise ValueError("仅支持逐小时汇流")
        slope = payload["default_slope_k_hours"]
        reaches = payload["reach_muskingum"]
        if not isinstance(slope, dict) or set(slope) != set(reference["default_slope_k_hours"]):
            raise ValueError("坡面分量清单不一致")
        if not isinstance(reaches, dict) or set(reaches) != set(reference["reach_muskingum"]):
            raise ValueError("河段清单必须与控制切分拓扑一致")
        def numeric(value, low, high):
            return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high
        if any(not numeric(value, 0.05, 720) for value in slope.values()):
            raise ValueError("坡面 K 必须在 0.05–720 小时之间")
        overrides = payload["unit_component_slope_k_hours"]
        units = {row['unit_id'] for row in read_json(MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json')['units']}
        if not isinstance(overrides, dict) or set(overrides) - units:
            raise ValueError("逐单元坡面参数必须引用控制切分拓扑中的汇流单元")
        for unit, values in overrides.items():
            if not isinstance(values, dict) or not values or set(values) - {'RS', 'RI', 'RG'}:
                raise ValueError(f"单元 {unit} 仅支持非空的地表、壤中、地下径流 K 参数")
            if any(not numeric(value, 0.05, 720) for value in values.values()):
                raise ValueError(f"单元 {unit} 坡面 K 必须在 0.05–720 小时之间")
        for name, row in reaches.items():
            if not isinstance(row, dict) or set(row) != {"k_hours", "x"}:
                raise ValueError(f"河段 {name} 参数字段错误")
            k, x = row["k_hours"], row["x"]
            if not numeric(k, 0.5, 720) or not numeric(x, 0, 0.5) or (not allow_search_reference and not 2 * k * x <= 1 <= 2 * k * (1 - x)):
                raise ValueError(f"河段 {name} 不满足逐小时 Muskingum 稳定条件 2KX ≤ 1 ≤ 2K(1-X)")

    def create(self, request):
        if not isinstance(request, dict) or set(request) - {"name", "parentVersionId", "parameters"}:
            raise ValueError("汇流登记请求无效")
        name = request.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("请提供 1–120 字符的汇流版本名称")
        parent = self.resolve(request.get("parentVersionId"))
        payload = copy.deepcopy(request.get("parameters"))
        self.validate(payload)
        version = "ROUTING-" + uuid.uuid4().hex
        row = {"routingVersionId": version, "name": name.strip(), "createdAt": now(),
               "parentVersionId": parent["routingVersionId"], "parameters": payload,
               "parametersSHA256": payload_digest(payload), "productionEligible": False}
        write_json(self.root / f"{version}.json", row)
        return row
