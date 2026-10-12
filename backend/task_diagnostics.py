"""Read saved numerical diagnostics only; never rebuild a graph from means."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from common import read_json, safe_task, clean
from contracts import timestamp


class TaskDiagnostics:
    def __init__(self, manager):
        self.manager = manager

    def _saved(self, task_id):
        import numpy as np
        result = self.manager.result(task_id)  # success-only, including archived tasks
        folder = safe_task(self.manager.root, task_id)
        if not (folder / "grid-history.json").is_file():
            raise FileNotFoundError("该作业未保存逐时网格诊断；旧末状态不能用于重建过程，请创建新模拟任务。")
        metadata = read_json(folder / "grid-history.json")
        checkpoint = read_json(folder / "checkpoint.json")
        request = read_json(folder / "request.json")
        manifest = result.get("gridHistory", {})
        digest = result.get("lineage", {}).get("inputManifestSHA256")
        if (metadata.get("taskId") != task_id or result.get("taskId") != task_id
                or checkpoint.get("sourceTaskId") != task_id
                or metadata.get("context") != result.get("context")
                or metadata.get("context") != checkpoint.get("context")
                or metadata.get("context") != request.get("context")
                or metadata.get("resultSource") != "model-backend"
                or not digest or metadata.get("provenance", {}).get("inputManifestSHA256") != digest
                or checkpoint.get("inputManifestSHA256") != digest
                or checkpoint.get("stateVersionId") != result.get("stateVersionId")):
            raise RuntimeError("逐时诊断与成功任务、检查点或冻结输入不一致，拒绝展示错配产物")
        if checkpoint.get("inputBundleVersionId") is not None:
            for key in ("inputBundleVersionId", "inputBundleManifestSHA256"):
                if (metadata.get("provenance", {}).get(key) != checkpoint.get(key)
                        or result.get("lineage", {}).get(key) != checkpoint.get(key)):
                    raise RuntimeError("逐时诊断的输入产品血缘与成功任务不一致")
            frozen_inputs = request.get("inputs", {})
            if (frozen_inputs.get("bundleVersionId") is not None
                    and (frozen_inputs.get("bundleVersionId") != checkpoint["inputBundleVersionId"]
                         or frozen_inputs.get("bundleManifestSHA256") != checkpoint["inputBundleManifestSHA256"])):
                raise RuntimeError("逐时诊断与冻结请求的输入产品不一致")
        array_path = folder / "grid-history.npz"
        array_digest = hashlib.sha256(array_path.read_bytes()).hexdigest()
        if (not manifest.get("arraySHA256") or manifest.get("arraySHA256") != array_digest
                or metadata.get("arraySHA256") != array_digest
                or manifest.get("catalogSHA256") != hashlib.sha256((folder / "grid-history.json").read_bytes()).hexdigest()):
            raise RuntimeError("逐时诊断文件哈希与成功结果登记不一致")
        times, grid_ids, fields = metadata.get("times", []), metadata.get("gridIds", []), metadata.get("fields", [])
        geometry = metadata.get("gridGeometry", [])
        if (not times or not grid_ids or not fields or len(set(grid_ids)) != len(grid_ids)
                or len(set(fields)) != len(fields) or not {"WU", "WL", "WD", "S", "RS", "RI", "RG"}.issubset(fields)
                or grid_ids != checkpoint.get("gridIds") or geometry != checkpoint.get("gridGeometry")
                or [cell.get("id") for cell in geometry] != grid_ids
                or manifest.get("steps") != len(times) or manifest.get("gridCount") != len(grid_ids)
                or times[-1] != checkpoint.get("time")
                or manifest.get("start") != times[0] or manifest.get("end") != times[-1]
                or times != [row.get("time") for row in result.get("hydrograph", {}).get("forecast", [])]):
            raise RuntimeError("逐时诊断的时间轴、网格或字段与计算结果不一致")
        parsed_times = [timestamp(value) for value in times]
        if any(right - left != timedelta(hours=1) for left, right in zip(parsed_times, parsed_times[1:])):
            raise RuntimeError("保存状态的时间轴不是连续整点")
        with np.load(array_path, allow_pickle=False) as archive:
            if set(archive.files) != {"times", *fields} or archive["times"].tolist() != times:
                raise RuntimeError("诊断数组与目录的时间轴或字段不一致")
            history = {name: archive[name].copy() for name in fields}
        for name, values in history.items():
            if values.shape != (len(times), len(grid_ids)) or values.dtype.kind not in "fiu" or not np.isfinite(values).all():
                raise RuntimeError("诊断数组形状或数值无效：" + name)
        for name, values in checkpoint.get("land", {}).items():
            if name not in history or not np.array_equal(history[name][-1], np.asarray(values)):
                raise RuntimeError("保存历史末状态与完整检查点不一致：" + name)
        return folder, metadata, {"times": np.asarray(times), **history}

    def catalog(self, task_id):
        return self._saved(task_id)[1]

    def state(self, task_id, time_value=None, unit_id=None):
        import numpy as np
        if time_value is not None and unit_id is not None:
            raise ValueError("time 和 unitId 不能同时查询")
        _, metadata, history = self._saved(task_id)
        if history:
            times = history["times"].tolist()
            if unit_id:
                if unit_id not in metadata["gridIds"]:
                    raise KeyError("网格未登记到该任务")
                index = metadata["gridIds"].index(unit_id)
                return clean({"taskId": task_id, "unitId": unit_id, "times": times,
                              "series": {name: history[name][:, index].tolist() for name in metadata["fields"]},
                              "resultSource": "model-backend", "context": metadata["context"],
                              "provenance": metadata["provenance"], "productionEligible": False})
            value = str(time_value or times[-1]).replace(" ", "T")
            # Accept equivalent whole-hour display forms, but never interpolate.
            value = timestamp(value).isoformat()
            if value not in times:
                raise ValueError("所选时刻不在已保存的计算时段内；不插值或外推状态。")
            step = times.index(value)
            cells = []
            for index, geometry in enumerate(metadata["gridGeometry"]):
                cell = {**geometry, **{name: float(history[name][step, index]) for name in metadata["fields"]}}
                cell.update({"wu": cell["WU"], "wl": cell["WL"], "wd": cell["WD"],
                             "freeWater": cell["S"], "soil": (cell["WU"] + cell["WL"] + cell["WD"]) / metadata["parameters"]["WM"],
                             "runoff": cell["RS"] + cell["RI"] + cell["RG"]})
                cells.append(cell)
            return clean({"taskId": task_id, "time": value, "cells": cells,
                          "means": {name: float(history[field][step].mean()) for name, field in {"wu": "WU", "wl": "WL", "wd": "WD", "free": "S"}.items()},
                          "resultSource": "model-backend", "context": metadata["context"],
                          "provenance": metadata["provenance"], "productionEligible": False})

    def event_quality(self, task_id):
        self.manager.get(task_id)
        path = safe_task(self.manager.root, task_id) / "event-quality.json"
        if not path.is_file():
            raise FileNotFoundError("该任务尚无场次质控记录")
        return {"taskId": task_id, "audits": read_json(path), "resultSource": "model-backend", "productionEligible": False}
