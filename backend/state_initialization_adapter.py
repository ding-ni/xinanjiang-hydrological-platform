"""Bind the delivered observed-flow anchor to registered inputs and routing."""
from __future__ import annotations

import hashlib


def resolve_anchor(request, bundle):
    import pandas as pd
    from contracts import timestamp
    time = timestamp(request["period"]["endTime"])
    path = bundle["paths"]["flow"]
    frame = pd.read_csv(path, usecols=["time", "flow_m3_s"])
    values = frame.loc[pd.to_datetime(frame.time) == time, "flow_m3_s"]
    if len(values) != 1:
        raise ValueError("状态时刻须有唯一的徐畈实测流量记录")
    value = float(values.iloc[0])
    import math
    if not math.isfinite(value) or value < 0:
        raise ValueError("状态时刻的徐畈流量缺测或小于零")
    return {"time": time.isoformat(), "flowCms": value, "stationId": "70115800",
            "controlReachId": "R0020_UP", "observationVersion": bundle["observationVersion"],
            "observationSHA256": hashlib.sha256(path.read_bytes()).hexdigest()}


def freeze_initialization(request, bundle):
    selection = request.get("initialization", {})
    if selection.get("strategy") != "OBSERVED_FLOW_ANCHORED":
        return request
    anchor = resolve_anchor(request, bundle)
    if "anchor" in selection and selection["anchor"] != anchor:
        raise ValueError("流量锚定记录与当前资料版本不一致")
    request["initialization"] = {"strategy": "OBSERVED_FLOW_ANCHORED", "anchor": anchor}
    return request


def anchored_routing(runner, anchor):
    from state_initialization.routing import build_observed_flow_anchored_state
    state = build_observed_flow_anchored_state(runner.topology, runner.control_point, anchor["flowCms"])
    return runner.standard_state_to_fast(state)
