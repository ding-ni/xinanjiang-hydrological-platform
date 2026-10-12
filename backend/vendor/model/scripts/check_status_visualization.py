"""Check that the handoff package can serve the new status visualizations.

This is a read-only smoke check. It does not run calibration or rewrite model
results. Use it from the ``model`` directory after installing requirements.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    args = parser.parse_args()
    checks = {
        "frontend/index.html": ROOT / "frontend" / "index.html",
        "frontend/state-explorer.js": ROOT / "frontend" / "state-explorer.js",
        "frontend/flood-rainfall-check.js": ROOT / "frontend" / "flood-rainfall-check.js",
        "frontend/components/grid-value-map.js": ROOT / "frontend" / "components" / "grid-value-map.js",
        "code/integration/web_api.py": ROOT / "code" / "integration" / "web_api.py",
        "code/calibration_platform/http_adapter.py": ROOT / "code" / "calibration_platform" / "http_adapter.py",
        "data_interface/grid_state_schema.json": ROOT / "data_interface" / "grid_state_schema.json",
        "data_interface/parameter_editor_schema.json": ROOT / "data_interface" / "parameter_editor_schema.json",
    }
    failed = [name for name, path in checks.items() if not path.is_file()]
    if failed:
        print("文件检查失败：" + ", ".join(failed))
        return 1
    base = args.base_url.rstrip("/")
    overview = get_json(base + "/api/v1/overview")
    run_id = overview.get("run_id")
    if not run_id:
        raise RuntimeError("/api/v1/overview 未返回 run_id")
    units = get_json(base + f"/api/v1/runs/{run_id}/grid-state/units")
    state = get_json(base + f"/api/v1/runs/{run_id}/grid-state")
    rainfall = get_json(base + "/api/v1/floods/31640404/areal-rainfall")
    geometry = get_json(base + "/api/v1/grid-geometry")
    grid_rainfall = get_json(base + "/api/v1/floods/31640404/grid-rainfall?time=1964-04-04T20%3A00")
    parameters = get_json(base + "/api/v1/parameters?zone_type=NON_GLACIER")
    if not units.get("units") or not state.get("records") or not rainfall.get("records") or not parameters.get("parameters"):
        raise RuntimeError("新增接口返回为空，无法显示诊断数据")
    geometry_ids = {item["properties"]["unit_id"] for item in geometry["features"]}
    rainfall_ids = {item["unit_id"] for item in grid_rainfall["cells"]}
    if geometry_ids != rainfall_ids or len(geometry_ids) != len(units["units"]):
        raise RuntimeError("地图边界、降雨网格和产流网格 ID 不一致")
    print("状态可视化自检通过")
    print(f"run_id: {run_id}")
    print(f"网格数: {len(units['units'])}")
    print(f"状态记录数: {state['record_count']}")
    print(f"洪水面雨量记录数: {rainfall['record_count']}")
    print(f"可编辑参数数: {len(parameters['parameters'])}")
    print(f"地图边界/逐格降雨: {len(geometry_ids)} / {len(rainfall_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
