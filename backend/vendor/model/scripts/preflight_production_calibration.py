"""[CAL-PREFLIGHT-10P] Validate forcing and configuration before long calibration."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))

from runoff_platform.data.pet_forcing import provider_from_container  # noqa: E402
from runoff_platform.data.precipitation_quality import read_station_count  # noqa: E402
from scripts.run_formal_multievent_calibration import find_observed_flow  # noqa: E402
from xaj_routing.control_area import (  # noqa: E402
    contributing_grid_areas,
    contributing_topology_area,
)
from xaj_routing.io import read_control_points_json, read_topology_json  # noqa: E402


def _check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any) -> None:
    checks.append({"check_id": check_id, "passed": bool(passed), "detail": detail})


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run(request: dict[str, Any], output_path: str | Path) -> dict[str, Any]:
    """Run hard-failure checks and persist an auditable preflight report."""
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    required_files = [
        "parameter_space",
        "objective_config",
        "event_quality_config",
        "state_config",
        "pet_config",
        "initial_land_state",
        "continuous_balance_config",
        "event_catalog",
        "data_review_config",
    ]
    for name in required_files:
        path = Path(request[name])
        _check(checks, f"FILE_{name.upper()}", path.is_file(), str(path))
    for name in ("data_root", "precipitation_directory"):
        path = Path(request[name])
        _check(checks, f"DIRECTORY_{name.upper()}", path.is_dir(), str(path))

    event_catalog = json.loads(Path(request["event_catalog"]).read_text(encoding="utf-8-sig"))
    catalog_packages = event_catalog.get("packages", [])
    catalog_ids = [str(item.get("eventId", "")) for item in catalog_packages]
    catalog_windows_valid = True
    for item in catalog_packages:
        try:
            start = pd.Timestamp(item["start"])
            end = pd.Timestamp(item["end"])
            duration = int((end - start).total_seconds() / 3600.0) + 1
            catalog_windows_valid &= end >= start and duration == int(item["durationHours"])
        except (KeyError, TypeError, ValueError):
            catalog_windows_valid = False
    qc_revision = event_catalog.get("qcRevision", {})
    forcing_version = str(qc_revision.get("forcingVersion", ""))
    catalog_valid = bool(
        catalog_packages
        and int(event_catalog.get("count", -1)) == len(catalog_packages)
        and len(catalog_ids) == len(set(catalog_ids))
        and all(catalog_ids)
        and catalog_windows_valid
        and forcing_version
    )
    incomplete_catalog_events = sum(
        float(item.get("observedInputs", {}).get("riverCompleteness", 0.0)) < 0.999
        for item in catalog_packages
    )
    _check(
        checks,
        "EVENT_CATALOG_QC_INTEGRITY",
        catalog_valid,
        {
            "schema_version": event_catalog.get("schemaVersion"),
            "forcing_version": forcing_version,
            "event_count": len(catalog_packages),
            "unique_event_count": len(set(catalog_ids)),
            "windows_valid": catalog_windows_valid,
            "incomplete_flow_event_count": incomplete_catalog_events,
        },
    )
    if incomplete_catalog_events:
        warnings.append("EVENT_CATALOG_HAS_INCOMPLETE_FLOW_EVENTS_EXCLUDED_BY_FORMAL_SELECTION")

    data_review = json.loads(
        Path(request["data_review_config"]).read_text(encoding="utf-8-sig")
    )
    production_clearance = bool(data_review.get("production_clearance", False))
    _check(
        checks,
        "CONTROL_SECTION_DATA_PRODUCTION_CLEARANCE",
        production_clearance,
        {
            "review_id": data_review.get("review_id"),
            "status": data_review.get("status"),
            "affected_years": data_review.get("affected_years", []),
            "unresolved_items": data_review.get("unresolved_items", []),
        },
    )
    if not production_clearance:
        warnings.append("CONTROL_SECTION_DATA_REVIEW_REQUIRED")

    continuous_path = Path(request["continuous_balance_config"])
    continuous = json.loads(continuous_path.read_text(encoding="utf-8-sig"))
    control_area_path = (
        continuous_path.parent / continuous["control_area_config"]
    ).resolve()
    _check(checks, "FILE_CONTROL_AREA_CONFIG", control_area_path.is_file(), str(control_area_path))
    control_area = json.loads(control_area_path.read_text(encoding="utf-8-sig"))
    evaluation_years = sorted(
        {int(year) for years in continuous["year_groups"].values() for year in years}
    )
    years = list(range(int(continuous["simulation_start_year"]), max(evaluation_years) + 1))
    pet = provider_from_container(continuous, continuous_path)

    grid_path = ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"
    grids = _load_rows(grid_path)
    _check(checks, "GRID_STATIC_NONEMPTY", len(grids) > 0, {"grid_count": len(grids)})
    positions = [(int(row["array_row"]), int(row["array_col"])) for row in grids]

    precipitation_dir = Path(request["precipitation_directory"])
    yearly: list[dict[str, Any]] = []
    all_precipitation_valid = True
    all_pet_valid = True
    for year in years:
        expected = pd.date_range(
            f"{year}-01-01 00:00:00", f"{year}-12-31 23:00:00", freq="h"
        )
        path = precipitation_dir / f"nananxi_precip_hourly_1km_{year}.nc"
        record: dict[str, Any] = {"year": year, "path": str(path), "expected_hours": len(expected)}
        if not path.is_file():
            record["status"] = "MISSING"
            all_precipitation_valid = False
            yearly.append(record)
            continue
        try:
            with xr.open_dataset(path, engine="h5netcdf") as dataset:
                times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
                precipitation = dataset["precipitation"]
                shape = tuple(int(value) for value in precipitation.shape)
                row_count, column_count = shape[-2], shape[-1]
                positions_valid = all(
                    0 <= row < row_count and 0 <= column < column_count
                    for row, column in positions
                )
                minimum = float(precipitation.min().load())
                maximum = float(precipitation.max().load())
                station_count, station_count_field = read_station_count(
                    dataset,
                    required=True,
                )
                valid = bool(
                    times.equals(expected)
                    and positions_valid
                    and np.isfinite(minimum)
                    and np.isfinite(maximum)
                    and minimum >= 0.0
                    and station_count.shape == (len(expected),)
                    and np.all(station_count >= 1)
                )
                record.update(
                    {
                        "status": "PASSED" if valid else "FAILED",
                        "hours": len(times),
                        "shape": shape,
                        "minimum_mm_h": minimum,
                        "maximum_mm_h": maximum,
                        "grid_positions_valid": positions_valid,
                        "station_count_field": station_count_field,
                        "minimum_reporting_station_count": int(station_count.min()),
                        "maximum_reporting_station_count": int(station_count.max()),
                    }
                )
                all_precipitation_valid &= valid
        except Exception as exc:
            record.update({"status": "FAILED", "error": str(exc)})
            all_precipitation_valid = False
        try:
            values = pet.values(expected)
            all_pet_valid &= bool(
                values.shape == (len(expected),)
                and np.isfinite(values).all()
                and np.all(values >= 0.0)
            )
        except Exception as exc:
            record["pet_error"] = str(exc)
            all_pet_valid = False
        yearly.append(record)
    _check(checks, "PRECIPITATION_2014_2021", all_precipitation_valid, yearly)
    _check(checks, "PET_HOURLY_COVERAGE", all_pet_valid, pet.report())

    mapping_path = ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    mapping = _load_rows(mapping_path)
    mapped_area: dict[str, float] = {}
    source_fraction: dict[str, float] = {}
    for row in mapping:
        grid_id = row["grid_id"]
        mapped_area[grid_id] = mapped_area.get(grid_id, 0.0) + float(
            row["adjusted_overlap_area_km2"]
        )
        source_fraction[grid_id] = source_fraction.get(grid_id, 0.0) + float(
            row["source_fraction"]
        )
    grid_area = {row["unit_id"]: float(row["area_km2"]) for row in grids}
    missing_grids = sorted(set(grid_area) - set(mapped_area))
    maximum_area_error = max(
        (abs(mapped_area.get(grid_id, 0.0) - area) for grid_id, area in grid_area.items()),
        default=float("inf"),
    )
    maximum_fraction_error = max(
        (abs(source_fraction.get(grid_id, 0.0) - 1.0) for grid_id in grid_area),
        default=float("inf"),
    )
    mapping_valid = not missing_grids and maximum_area_error <= 1.0e-8 and maximum_fraction_error <= 1.0e-8
    _check(
        checks,
        "GRID_MAPPING_VOLUME_CONSERVATION",
        mapping_valid,
        {
            "source_grid_count": len(grid_area),
            "mapping_row_count": len(mapping),
            "missing_grids": missing_grids,
            "maximum_area_error_km2": maximum_area_error,
            "maximum_source_fraction_error": maximum_fraction_error,
        },
    )

    topology_path = ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    controls_path = ROOT / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    topology = read_topology_json(topology_path)
    controls = read_control_points_json(controls_path)
    control = controls[0] if len(controls) == 1 else None
    if control is None:
        control_area_valid = False
        control_area_detail: dict[str, Any] = {"error": "exactly one control point is required"}
    else:
        mapped_control_areas = contributing_grid_areas(
            mapping_path,
            topology,
            control.reach_id,
            grid_area,
        )
        model_control_area = float(np.sum(mapped_control_areas))
        observation_area = float(control_area["observation_area_km2"])
        relative_difference = abs(model_control_area - observation_area) / observation_area
        event_quality = json.loads(
            Path(request["event_quality_config"]).read_text(encoding="utf-8-sig")
        )
        tolerance = float(
            control_area["maximum_model_to_observation_relative_difference"]
        )
        control_area_valid = bool(
            control.control_id == str(control_area["control_id"])
            and control.reach_id == str(control_area["control_reach_id"])
            and abs(float(event_quality["basin_area_km2"]) - observation_area) <= 1.0e-9
            and relative_difference <= tolerance
            and str(event_catalog.get("controlSection", {}).get("controlId", ""))
            == control.control_id
            and abs(
                float(
                    event_catalog.get("controlSection", {}).get(
                        "observationAreaKm2", float("nan")
                    )
                )
                - observation_area
            )
            <= 1.0e-9
            and abs(
                float(
                    event_catalog.get("controlSection", {}).get(
                        "modelContributingAreaKm2", float("nan")
                    )
                )
                - model_control_area
            )
            <= 1.0e-9
        )
        control_area_detail = {
            "control_id": control.control_id,
            "control_reach_id": control.reach_id,
            "observation_area_km2": observation_area,
            "mapped_control_area_km2": model_control_area,
            "topology_control_area_km2": contributing_topology_area(
                topology,
                control.reach_id,
            ),
            "total_project_grid_area_km2": float(sum(grid_area.values())),
            "relative_difference": relative_difference,
            "maximum_relative_difference": tolerance,
            "event_quality_area_km2": float(event_quality["basin_area_km2"]),
            "event_catalog_control_section": event_catalog.get("controlSection"),
        }
    _check(checks, "CONTROL_SECTION_AREA_CONSISTENCY", control_area_valid, control_area_detail)

    parameter_space = json.loads(Path(request["parameter_space"]).read_text(encoding="utf-8-sig"))
    bounds_valid = all(
        np.isfinite(float(item["minimum"]))
        and np.isfinite(float(item["maximum"]))
        and float(item["minimum"]) < float(item["maximum"])
        for item in parameter_space.get("parameters", [])
    )
    _check(checks, "PARAMETER_BOUNDS", bounds_valid, parameter_space.get("status"))
    wlm_min = next(
        float(item["minimum"])
        for item in parameter_space["parameters"]
        if item["name"] == "runoff.WLM"
    )
    wlm_bounds = [float(value) for value in request["wlm_upper_bounds_mm"]]
    _check(
        checks,
        "WLM_BOUNDARY_EXPERIMENT",
        wlm_bounds == sorted(set(wlm_bounds)) and all(value > wlm_min for value in wlm_bounds),
        {"minimum_mm": wlm_min, "upper_bounds_mm": wlm_bounds},
    )
    seeds = [int(value) for value in request["search_seeds"]]
    _check(checks, "MULTI_SEED_COUNT", len(set(seeds)) >= 3, seeds)
    ratios = [float(request["calibration_ratio"]), float(request["validation_ratio"])]
    _check(
        checks,
        "EVENT_SPLIT_GROUPS",
        ratios[0] > 0.0 and ratios[1] > 0.0 and sum(ratios) < 1.0,
        {"calibration": ratios[0], "validation": ratios[1], "independent": 1.0 - sum(ratios)},
    )

    observed = find_observed_flow(Path(request["data_root"]))
    coverage = {
        str(year): float(observed.reindex(pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")).notna().mean())
        for year in evaluation_years
    }
    _check(checks, "OBSERVED_FLOW_COVERAGE", min(coverage.values()) >= 0.99, coverage)

    if not pet.production_eligible:
        warnings.append("PET_ASSUMED_DIAGNOSTIC_ONLY")
    failed = [item["check_id"] for item in checks if not item["passed"]]
    report = {
        "module_id": "CAL-PREFLIGHT-10P",
        "status": "FAILED" if failed else ("PASSED_WITH_WARNINGS" if warnings else "PASSED"),
        "ready_to_run": not failed,
        "diagnostic_only": not pet.production_eligible,
        "failed_check_ids": failed,
        "warnings": warnings,
        "checks": checks,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="resolved production workflow request JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    report = run(request, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_to_run"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
