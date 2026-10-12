"""Run multi-year continuous Nan'an-Xufan water-balance diagnostics.

The script reuses the vectorized platform XAJ core and the exact fast routing
implementation used by formal multi-event calibration. It evaluates a frozen
parameter set; parameter search remains in the calibration workflow.
"""

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

from runoff_platform.config_loader import load_parameter_set  # noqa: E402
from runoff_platform.data.pet_forcing import provider_from_container  # noqa: E402
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids  # noqa: E402
from state_initialization.core import build_uniform_land_state  # noqa: E402
from xaj_routing.control_area import (  # noqa: E402
    contributing_grid_areas,
    contributing_topology_area,
)
from xaj_routing.io import (  # noqa: E402
    read_control_points_json,
    read_parameters_json,
    read_topology_json,
)
from scripts.run_formal_multievent_calibration import (  # noqa: E402
    FastRoutingRunner,
    build_mapping,
    find_observed_flow,
    load_grids,
    metrics,
)


def _state_storage_depth(state: dict[str, np.ndarray], weights: np.ndarray) -> float:
    storage = (
        state["swe"]
        + state["snow_liquid"]
        + state["WU"]
        + state["WL"]
        + state["WD"]
        + state["S"] * state["FR"]
    )
    return float(np.sum(storage * weights))


def _load_precipitation_year(
    directory: Path,
    year: int,
    array_positions: list[tuple[int, int]],
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    path = directory / f"nananxi_precip_hourly_1km_{year}.nc"
    if not path.is_file():
        raise FileNotFoundError(path)
    with xr.open_dataset(path, engine="h5netcdf") as dataset:
        times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
        source = np.asarray(dataset["precipitation"].values, dtype=np.float32)
        precipitation = np.stack(
            [source[:, row, column] for row, column in array_positions],
            axis=1,
        )
    expected = pd.date_range(f"{year}-01-01 00:00:00", f"{year}-12-31 23:00:00", freq="h")
    if not times.equals(expected):
        raise ValueError(f"precipitation year {year} is not a complete hourly series")
    if not np.isfinite(precipitation).all() or np.any(precipitation < 0.0):
        raise ValueError(f"precipitation year {year} contains invalid values")
    return times, precipitation


def _group_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"year_count": 0}
    return {
        "year_count": int(len(frame)),
        "mean_NSE": float(frame["NSE"].mean()),
        "mean_KGE": float(frame["KGE"].mean()),
        "mean_volume_error_percent": float(frame["volume_error_percent"].mean()),
        "mean_abs_volume_error_percent": float(frame["volume_error_percent"].abs().mean()),
        "mean_observed_coverage": float(frame["observed_coverage"].mean()),
        "maximum_abs_climate_balance_residual_mm": float(
            frame["climate_balance_residual_using_observed_runoff_mm"].abs().max()
        ),
    }


# [CAL-CONTINUOUS-BALANCE-09] Continuous land/routing state and annual diagnostics.
def run(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    groups = {
        str(group).upper(): [int(year) for year in years]
        for group, years in config["year_groups"].items()
    }
    year_to_group = {year: group for group, years in groups.items() for year in years}
    evaluation_years = sorted(year_to_group)
    simulation_start_year = int(config["simulation_start_year"])
    simulation_end_year = max(evaluation_years)
    if simulation_start_year >= min(evaluation_years):
        raise ValueError("simulation_start_year must precede evaluation years for warm-up")

    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    precipitation_directory = Path(args.precip_dir).resolve()
    data_root = Path(args.data_root).resolve()
    params = load_parameter_set(Path(args.parameter_set).resolve())
    initial_template = json.loads(Path(args.initial_land_state).read_text(encoding="utf-8-sig"))
    grids = load_grids(Path(args.grid_static).resolve())
    grid_areas = np.asarray([float(grid["area_km2"]) for grid in grids], dtype=float)
    total_project_grid_area_km2 = float(np.sum(grid_areas))
    array_positions = [(int(grid["array_row"]), int(grid["array_col"])) for grid in grids]

    topology_path = Path(args.topology).resolve()
    control_points_path = Path(args.control_points).resolve()
    topology = read_topology_json(topology_path)
    control_points = read_control_points_json(control_points_path)
    if len(control_points) != 1:
        raise ValueError("continuous balance requires exactly one control point")
    control_point = control_points[0]
    control_area_path = (config_path.parent / config["control_area_config"]).resolve()
    control_area_config = json.loads(control_area_path.read_text(encoding="utf-8-sig"))
    if control_point.control_id != str(control_area_config["control_id"]):
        raise ValueError("control point and control-area configuration do not match")
    if control_point.reach_id != str(control_area_config["control_reach_id"]):
        raise ValueError("control reach and control-area configuration do not match")
    observation_area_km2 = float(control_area_config["observation_area_km2"])
    if observation_area_km2 <= 0.0:
        raise ValueError("observation control-section area must be positive")

    units, mapping = build_mapping(
        Path(args.grid_mapping).resolve(),
        topology_path,
        grids,
    )
    mapping_error = float(np.max(np.abs(mapping.sum(axis=0) - 1.0)))
    control_grid_areas = contributing_grid_areas(
        Path(args.grid_mapping).resolve(),
        topology,
        control_point.reach_id,
        [str(grid["unit_id"]) for grid in grids],
    )
    model_contributing_area_km2 = float(np.sum(control_grid_areas))
    control_grid_weights = control_grid_areas / model_contributing_area_km2
    topology_contributing_area_km2 = contributing_topology_area(
        topology,
        control_point.reach_id,
    )
    area_relative_difference = abs(
        model_contributing_area_km2 - observation_area_km2
    ) / observation_area_km2
    maximum_area_difference = float(
        control_area_config["maximum_model_to_observation_relative_difference"]
    )
    if area_relative_difference > maximum_area_difference:
        raise ValueError(
            "mapped control area differs from the registered observation area: "
            f"{area_relative_difference:.6f} > {maximum_area_difference:.6f}"
        )
    router = FastRoutingRunner(
        topology_path,
        units,
        control_points_path,
    )
    routing = read_parameters_json(Path(args.routing_parameters).resolve())
    observed = find_observed_flow(data_root)
    # [CAL-CONTINUOUS-BALANCE-09A] Same PET provider as formal event calibration.
    pet_provider = provider_from_container(config, config_path)

    land_state = build_uniform_land_state(initial_template, len(grids), params)
    routing_state = None
    annual_rows = []
    all_years = list(range(simulation_start_year, simulation_end_year + 1))
    for number, year in enumerate(all_years, start=1):
        times, precipitation = _load_precipitation_year(
            precipitation_directory,
            year,
            array_positions,
        )
        pet = pet_provider.values(times)
        start_storage = _state_storage_depth(land_state, control_grid_weights)
        rs, ri, rg, land_state, diagnostics = simulate_xaj_liquid_grids(
            precipitation,
            params,
            pet,
            land_state,
            return_diagnostics=True,
        )
        end_storage = _state_storage_depth(land_state, control_grid_weights)
        unit_rs = rs @ mapping
        unit_ri = ri @ mapping
        unit_rg = rg @ mapping
        simulated_flow, routing_state = router.run(
            times,
            unit_rs,
            unit_ri,
            unit_rg,
            routing,
            routing_state,
        )
        progress = 100.0 * number / len(all_years)
        print(
            f"continuous_year={year} progress={progress:.2f}",
            flush=True,
        )
        if year not in year_to_group:
            continue

        precipitation_depth = float(np.sum(precipitation @ control_grid_weights))
        et_depth = float(np.sum(diagnostics["et_actual"] @ control_grid_weights))
        generated_runoff = rs + ri + rg
        generated_runoff_depth = float(np.sum(generated_runoff @ control_grid_weights))
        storage_change = end_storage - start_storage
        weighted_balance = diagnostics["water_balance_error"] @ control_grid_weights
        cumulative_internal_error = float(np.sum(weighted_balance))
        maximum_hourly_internal_error = float(np.max(np.abs(weighted_balance)))

        observed_year = observed.reindex(times)
        observed_coverage = float(observed_year.notna().mean())
        paired_metrics = metrics(observed_year.dropna(), simulated_flow)
        observed_runoff_depth = float(
            np.nansum(observed_year.to_numpy(float)) * 3.6 / observation_area_km2
        )
        simulated_runoff_depth = float(
            np.sum(simulated_flow.to_numpy(float)) * 3.6 / observation_area_km2
        )
        climate_balance_residual_volume = (
            (precipitation_depth - et_depth - storage_change)
            * model_contributing_area_km2
            - observed_runoff_depth * observation_area_km2
        )
        climate_balance_residual = (
            climate_balance_residual_volume / observation_area_km2
        )
        annual_rows.append(
            {
                "year": year,
                "group": year_to_group[year],
                "observation_area_km2": observation_area_km2,
                "model_contributing_area_km2": model_contributing_area_km2,
                "hour_count": len(times),
                "observed_coverage": observed_coverage,
                "precipitation_mm": precipitation_depth,
                "actual_et_mm": et_depth,
                "generated_runoff_mm": generated_runoff_depth,
                "simulated_control_runoff_depth_mm": simulated_runoff_depth,
                "observed_control_runoff_depth_mm": observed_runoff_depth,
                "land_storage_change_mm": storage_change,
                "internal_balance_error_mm": cumulative_internal_error,
                "maximum_abs_hourly_internal_balance_error_mm": maximum_hourly_internal_error,
                "climate_balance_residual_volume_mm_km2": climate_balance_residual_volume,
                "climate_balance_residual_using_observed_runoff_mm": climate_balance_residual,
                **paired_metrics,
            }
        )

    annual = pd.DataFrame(annual_rows)
    annual.to_csv(output / "annual_water_balance_metrics.csv", index=False, encoding="utf-8-sig")
    group_summary = {
        group: _group_summary(annual[annual["group"] == group])
        for group in ("CALIBRATION", "VALIDATION", "INDEPENDENT_TEST")
    }
    report = {
        "module_id": "CAL-CONTINUOUS-BALANCE-09",
        "status": "SUCCEEDED",
        "parameter_set": str(Path(args.parameter_set).resolve()),
        "simulation_years": [simulation_start_year, simulation_end_year],
        "evaluation_years": evaluation_years,
        "year_groups": groups,
        "state_carry_over": {"land": True, "routing": True},
        "pet": pet_provider.report(),
        "basin_area_km2": observation_area_km2,
        "area": {
            "config_id": control_area_config["config_id"],
            "control_id": control_point.control_id,
            "control_reach_id": control_point.reach_id,
            "observation_area_km2": observation_area_km2,
            "model_contributing_area_km2": model_contributing_area_km2,
            "topology_contributing_area_km2": topology_contributing_area_km2,
            "total_project_grid_area_km2": total_project_grid_area_km2,
            "model_to_observation_relative_difference": area_relative_difference,
            "policy": control_area_config["policy"],
        },
        "routing_mapping_max_relative_error": mapping_error,
        "minimum_observed_coverage": float(annual["observed_coverage"].min()),
        "maximum_abs_internal_balance_error_mm": float(
            annual["internal_balance_error_mm"].abs().max()
        ),
        "maximum_abs_hourly_internal_balance_error_mm": float(
            annual["maximum_abs_hourly_internal_balance_error_mm"].max()
        ),
        "groups": group_summary,
        "output_files": {"annual_metrics": "annual_water_balance_metrics.csv"},
        "warnings": (
            ["PET is a diagnostic assumption and is not production eligible"]
            if not pet_provider.production_eligible
            else []
        ),
    }
    (output / "continuous_water_balance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config" / "continuous_water_balance_config.json"))
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--precip-dir", required=True)
    parser.add_argument("--parameter-set", required=True)
    parser.add_argument("--initial-land-state", default=str(ROOT / "config" / "initial_state_land.json"))
    parser.add_argument("--grid-static", default=str(ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"))
    parser.add_argument("--grid-mapping", default=str(ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"))
    parser.add_argument("--topology", default=str(ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"))
    parser.add_argument("--control-points", default=str(ROOT / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"))
    parser.add_argument("--routing-parameters", default=str(ROOT / "validation" / "calibration_routing_2017" / "best_routing_parameters.json"))
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
