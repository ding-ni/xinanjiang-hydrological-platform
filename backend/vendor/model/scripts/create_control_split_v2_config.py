# -*- coding: utf-8 -*-
"""Create versioned integrated-run configuration for formal control splitting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    config_dir = root / "config"
    old_params_path = config_dir / "routing_parameters_2017_first_pass.json"
    params_path = config_dir / "routing_parameters_control_split_v2.json"
    config_path = config_dir / "integrated_2017_control_split_v2.json"

    params = json.loads(old_params_path.read_text(encoding="utf-8-sig"))
    reaches = params.setdefault("reach_muskingum", {})
    original = reaches.pop("R0020", {"k_hours": 1.0, "x": 0.2})
    reaches["R0020_UP"] = dict(original)
    reaches["R0020_DOWN"] = dict(original)
    params["metadata"] = {
        **params.get("metadata", {}),
        "source": "formal_control_split_v2_initial_parameters",
        "topology": "data_interface/spatial/nan_an_topology_control_split_v2.json",
        "control_section_routing": "control output is R0020_UP outflow",
    }
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    config = {
        "run_id": "nan_an_xufan_2017_integrated_control_split_v2",
        "routing_method": "REACH_SEGMENT",
        "routing_dem_resolution_m": 30.0,
        "source_runoff_grid_resolution_m": 1000.0,
        "slope_routing_method": "LINEAR_RESERVOIR",
        "channel_routing_method": "MUSKINGUM",
        "mapping_method": "D8_FLOW_PATH_AREA_OVERLAP",
        "stream_threshold_km2": 1.0,
        "dem_ascii": "data_interface/spatial/nan_an_dem_utm50_30m_clipped.asc",
        "topology": "data_interface/spatial/nan_an_topology_control_split_v2.json",
        "control_points": "data_interface/spatial/xufan_70115800_control_point_snapped_v1.json",
        "routing_parameters": "config/routing_parameters_control_split_v2.json",
        "initial_routing_state": None,
        "runoff_grid_geojson": "data_interface/spatial/南安溪1公里有效格点.geojson",
        "grid_static": "data_interface/runoff_2017/grid_static.csv",
        "runoff_wide": "data_interface/runoff_2017/runoff_components_wide.csv",
        "observed_control_flow": "data_interface/runoff_2017/observed_outlet_flow.csv",
        "output_dir": "validation/integrated_2017_control_split_v2",
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"config": str(config_path), "parameters": str(params_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
