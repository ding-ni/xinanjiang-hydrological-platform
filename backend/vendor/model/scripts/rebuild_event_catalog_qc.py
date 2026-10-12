"""Rebuild the platform flood-event catalog from QC forcing and flow data."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from runoff_platform.data.event_catalog import (  # noqa: E402
    load_window_overrides,
    rebuild_event_catalog,
)
from xaj_routing.control_area import contributing_grid_areas  # noqa: E402
from xaj_routing.io import read_control_points_json, read_topology_json  # noqa: E402


def _read_observations(path: Path, sheet: str | None) -> pd.DataFrame:
    workbook = pd.ExcelFile(path)
    selected = sheet or workbook.sheet_names[-1]
    frame = pd.read_excel(path, sheet_name=selected, usecols=[0, 1, 2])
    frame.columns = ["time", "level", "flow"]
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["level"] = pd.to_numeric(frame["level"], errors="coerce")
    frame["flow"] = pd.to_numeric(frame["flow"], errors="coerce")
    return frame.dropna(subset=["time"]).drop_duplicates("time").set_index("time").sort_index()


def _read_grid_cells(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-catalog", required=True)
    parser.add_argument("--station-dir", required=True)
    parser.add_argument("--grid-dir", required=True)
    parser.add_argument("--observed-flow", required=True)
    parser.add_argument("--observed-sheet")
    parser.add_argument("--window-qc")
    parser.add_argument(
        "--grid-static",
        default=str(ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"),
    )
    parser.add_argument(
        "--grid-mapping",
        default=str(ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"),
    )
    parser.add_argument(
        "--topology",
        default=str(ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"),
    )
    parser.add_argument(
        "--control-points",
        default=str(
            ROOT
            / "data_interface"
            / "spatial"
            / "xufan_70115800_control_point_snapped_v1.json"
        ),
    )
    parser.add_argument(
        "--control-area-config",
        default=str(ROOT / "config" / "control_section_area_xufan.json"),
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--forcing-version", default="precipitation_qc_20260819")
    args = parser.parse_args()

    source_path = Path(args.source_catalog).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    overrides = load_window_overrides(args.window_qc)
    grid_cells = _read_grid_cells(Path(args.grid_static).resolve())
    topology = read_topology_json(Path(args.topology).resolve())
    control_points = read_control_points_json(Path(args.control_points).resolve())
    if len(control_points) != 1:
        raise ValueError("event catalog rebuild requires exactly one control point")
    control = control_points[0]
    control_area = json.loads(
        Path(args.control_area_config).resolve().read_text(encoding="utf-8-sig")
    )
    if control.control_id != str(control_area["control_id"]):
        raise ValueError("control point and control-area configuration do not match")
    if control.reach_id != str(control_area["control_reach_id"]):
        raise ValueError("control reach and control-area configuration do not match")
    effective_grid_areas = contributing_grid_areas(
        Path(args.grid_mapping).resolve(),
        topology,
        control.reach_id,
        [str(cell["unit_id"]) for cell in grid_cells],
    )
    model_area_km2 = float(effective_grid_areas.sum())
    observation_area_km2 = float(control_area["observation_area_km2"])
    area_relative_difference = abs(model_area_km2 - observation_area_km2) / observation_area_km2
    if area_relative_difference > float(
        control_area["maximum_model_to_observation_relative_difference"]
    ):
        raise ValueError("mapped control area exceeds configured observation-area tolerance")

    rebuilt, audit = rebuild_event_catalog(
        source,
        station_directory=Path(args.station_dir).resolve(),
        grid_directory=Path(args.grid_dir).resolve(),
        grid_cells=grid_cells,
        grid_areas_km2=effective_grid_areas,
        observations=_read_observations(
            Path(args.observed_flow).resolve(),
            args.observed_sheet,
        ),
        end_overrides=overrides,
        forcing_version=args.forcing_version,
    )
    rebuilt["controlSection"] = {
        "configId": control_area["config_id"],
        "controlId": control.control_id,
        "controlReachId": control.reach_id,
        "observationAreaKm2": observation_area_km2,
        "modelContributingAreaKm2": model_area_km2,
        "areaPolicy": control_area["policy"],
    }
    for item in rebuilt["packages"]:
        item["spatialRainInputs"]["modelContributingAreaKm2"] = model_area_km2
    catalog_path = output / "nananxi_flood_event_catalog_qc_20260819.json"
    audit_path = output / "event_catalog_rebuild_audit.csv"
    report_path = output / "event_catalog_rebuild_report.json"
    catalog_path.write_text(
        json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    audit.to_csv(audit_path, index=False, encoding="utf-8-sig")
    report = {
        "module_id": "DQ-EVENT-CATALOG-03",
        "status": "SUCCEEDED",
        "source_catalog": str(source_path),
        "forcing_version": args.forcing_version,
        "event_count": int(len(audit)),
        "window_override_count": int(audit["window_changed"].sum()),
        "minimum_station_count": int(audit["minimumReportingStationCount"].min()),
        "maximum_station_count": int(audit["maximumReportingStationCount"].max()),
        "incomplete_flow_event_count": int((audit["riverCompleteness"] < 0.999).sum()),
        "control_area": {
            "config_id": control_area["config_id"],
            "control_id": control.control_id,
            "control_reach_id": control.reach_id,
            "observation_area_km2": observation_area_km2,
            "model_contributing_area_km2": model_area_km2,
            "relative_difference": area_relative_difference,
        },
        "outputs": {
            "catalog": str(catalog_path),
            "audit": str(audit_path),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
