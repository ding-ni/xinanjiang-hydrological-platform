"""INTEGRATION-PIPELINE: one-call conservative adapter and routing workflow."""

from __future__ import annotations

import json
from pathlib import Path

from xaj_routing.engine import RoutingEngine
from xaj_routing.control_split import split_topology_at_control_points
from xaj_routing.raster_formats import read_esri_ascii_grid
from xaj_routing.spatial import build_grid_topology, valid_mask
from xaj_routing.io import (
    read_control_points_json,
    read_parameters_json,
    read_runoff_csv,
    read_state_json,
    read_topology_json,
    write_component_flows_csv,
    write_control_flows_csv,
    write_json,
    write_outlet_flows_csv,
    write_reach_flows_csv,
)

from .runoff_adapter import aggregate_runoff_to_reach_segments
from .plotting import write_hydrograph
from .spatial_mapping import build_grid_to_unit_weights
from .validation import compare_control_flow


def _control_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict) and "control_points" in payload:
        return list(payload["control_points"])
    if isinstance(payload, dict) and "controls" in payload:
        return list(payload["controls"])
    return [payload] if isinstance(payload, dict) else []


def _prepare_formal_topology(
    config: dict,
    resolve,
    controls: list[dict],
    output_dir: Path,
) -> Path:
    """Ensure a run with controls always uses a persisted split topology."""
    configured_path = resolve(config["topology"])
    payload = json.loads(configured_path.read_text(encoding="utf-8-sig"))
    if not controls or payload.get("control_nodes"):
        return configured_path

    grid = read_esri_ascii_grid(resolve(config["dem_ascii"]))
    base = build_grid_topology(
        grid.elevation,
        grid.cellsize,
        float(config.get("stream_threshold_km2", 1.0)),
        basin_id=payload.get("basin_id", "nan_an"),
        spatial_version=payload.get("spatial_version", "spatial_v1"),
        nodata=grid.nodata,
    )
    split = split_topology_at_control_points(
        base,
        valid_mask(grid.elevation, grid.nodata),
        controls,
    )
    derived_path = output_dir / "formal_control_split_topology.json"
    derived_payload = split.to_mapping(include_grids=False)
    derived_payload.update(
        {
            "topology_status": "FORMAL_CONTROL_SPLIT",
            "source_topology": str(configured_path).replace("\\", "/"),
            "source_ascii": str(resolve(config["dem_ascii"])).replace("\\", "/"),
            "stream_threshold_km2": float(config.get("stream_threshold_km2", 1.0)),
        }
    )
    write_json(derived_path, derived_payload)
    return derived_path


def run_integrated_case(config_path: str | Path) -> dict:
    config_path = Path(config_path).resolve()
    root = config_path.parent.parent
    config = json.loads(config_path.read_text(encoding="utf-8"))
    resolve = lambda value: (root / value).resolve()
    output_dir = resolve(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    control_payload = json.loads(resolve(config["control_points"]).read_text(encoding="utf-8-sig"))
    controls = _control_rows(control_payload)
    topology_path = _prepare_formal_topology(config, resolve, controls, output_dir)

    mapping_path = output_dir / "grid_to_unit_weight.csv"
    mapping_summary = build_grid_to_unit_weights(
        resolve(config["dem_ascii"]),
        topology_path,
        resolve(config["runoff_grid_geojson"]),
        resolve(config["grid_static"]),
        mapping_path,
        float(config.get("stream_threshold_km2", 1.0)),
        controls=controls,
    )
    routing_input_path = output_dir / "runoff_by_routing_unit.csv"
    adapter_summary = aggregate_runoff_to_reach_segments(
        resolve(config["runoff_wide"]),
        mapping_path,
        topology_path,
        routing_input_path,
    )

    engine = RoutingEngine(
        topology=read_topology_json(resolve(config["topology"])),
        parameters=read_parameters_json(resolve(config["routing_parameters"])),
        initial_state=read_state_json(
            resolve(config["initial_routing_state"]) if config.get("initial_routing_state") else None
        ),
        control_points=read_control_points_json(resolve(config["control_points"])),
    )
    result = engine.run(read_runoff_csv(routing_input_path))
    run_id = config.get("run_id", "integrated_run")
    write_component_flows_csv(output_dir / "component_flow.csv", result.component_flows, run_id)
    write_reach_flows_csv(output_dir / "reach_flow.csv", result.reach_flows, run_id)
    write_outlet_flows_csv(output_dir / "outlet_flow.csv", result.outlet_flows, run_id)
    write_control_flows_csv(output_dir / "control_flow.csv", result.control_flows, run_id)
    write_json(output_dir / "final_routing_state.json", result.final_state.to_mapping())

    structural = {
        "component_flow_rows": len(result.component_flows),
        "reach_flow_rows": len(result.reach_flows),
        "outlet_flow_rows": len(result.outlet_flows),
        "control_flow_rows": len(result.control_flows),
        "negative_flow_count": sum(
            row.outflow_cms < 0.0 for row in result.reach_flows
        ),
    }
    metrics = compare_control_flow(resolve(config["observed_control_flow"]), output_dir / "control_flow.csv")
    write_hydrograph(
        resolve(config["observed_control_flow"]),
        output_dir / "control_flow.csv",
        output_dir / "outlet_flow.csv",
        output_dir / "integrated_hydrograph.png",
    )
    report = {
        "status": "TECHNICAL_CHAIN_PASSED_NOT_CALIBRATED",
        "routing_method": {
            "name": "REACH_SEGMENT",
            "source_runoff_resolution_m": 1000.0,
            "routing_dem_resolution_m": mapping_summary["routing_dem_resolution_m"],
            "spatial_mapping": "D8_FLOW_PATH_AREA_OVERLAP",
            "slope_routing": "LINEAR_RESERVOIR",
            "channel_routing": "MUSKINGUM",
            "input_components": [
                "RS",
                "RI",
                "RG",
                "glacier_fast",
                "glacier_slow",
                "glacier_base",
            ],
        },
        "mapping": mapping_summary,
        "adapter": adapter_summary,
        "routing_structure": structural,
        "control_section_diagnostic": metrics,
        "limitations": [
            "2017 is a report calibration-period event, not an independent validation period.",
            "Temperature and PET in the runoff forcing are assumed values.",
            "Runoff-grid area and 30 m routing-DEM area use different boundary discretizations.",
            "Routing parameters and initial routing states require joint calibration or warm-up.",
        ],
    }
    write_json(output_dir / "integration_validation_report.json", report)
    return report
