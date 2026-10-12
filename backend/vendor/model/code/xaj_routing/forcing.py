"""Rainfall forcing adapters for historical model inputs."""

from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime
    source: str = "manual"
    event_id: str | None = None
    event_name: str | None = None
    observed_peak_cms: float | None = None


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=None)


def resolve_time_window(
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    event_json: str | Path | None = None,
    event_id: str | None = None,
) -> TimeWindow:
    """Resolve a time window from manual bounds or a flood-event catalog."""

    if event_json:
        if not event_id:
            raise ValueError("event_id is required when event_json is provided")
        event = find_flood_event(event_json, event_id)
        return TimeWindow(
            start=parse_datetime(event["start"]),
            end=parse_datetime(event["end"]),
            source=str(event_json).replace("\\", "/"),
            event_id=str(event.get("id") or event_id),
            event_name=event.get("name"),
            observed_peak_cms=_optional_float(event.get("observedPeak")),
        )
    if start is None or end is None:
        raise ValueError("start and end are required when event_json is not provided")
    window = TimeWindow(start=parse_datetime(start), end=parse_datetime(end))
    if window.end < window.start:
        raise ValueError("end must be greater than or equal to start")
    return window


def find_flood_event(event_json: str | Path, event_id: str) -> dict[str, Any]:
    data = json.loads(Path(event_json).read_text(encoding="utf-8"))
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("event catalog JSON must contain an events list")
    needle = str(event_id).strip()
    normalized = needle if needle.startswith("xf-") else f"xf-{needle}"
    for event in events:
        candidates = {
            str(event.get("id", "")),
            str(event.get("name", "")),
            str(event.get("floodCode", "")),
        }
        if needle in candidates or normalized in candidates:
            return event
        if str(event.get("id", "")).endswith(needle):
            return event
    raise ValueError(f"flood event {event_id!r} was not found")


def export_rainfall_forcing_csv(
    nc_dir: str | Path,
    out_csv: str | Path,
    window: TimeWindow,
    dataset_kind: str = "auto",
    valid_grid_geojson: str | Path | None = None,
    temp_dir: str | Path = Path(tempfile.gettempdir()) / "XAJ_nc_runtime_tmp",
) -> dict[str, Any]:
    """Export rainfall forcing rows from yearly NetCDF files."""

    if window.end < window.start:
        raise ValueError("window end must be greater than or equal to start")
    nc_paths = select_netcdf_files(nc_dir, window.start, window.end)
    if not nc_paths:
        raise ValueError("no NetCDF files found for the requested time window")

    output_path = Path(out_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    total_missing = 0
    total_precip = 0.0
    max_precip = None
    first_time = None
    last_time = None
    dataset_types: set[str] = set()
    files_used: list[str] = []

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = None
        for nc_path in nc_paths:
            with open_netcdf_dataset(nc_path, temp_dir=temp_dir) as ds:
                kind = infer_dataset_kind(ds) if dataset_kind == "auto" else dataset_kind
                dataset_types.add(kind)
                files_used.append(str(nc_path).replace("\\", "/"))
                time_values, time_mask = selected_time_values(ds, window)
                if not time_values:
                    continue
                if kind == "station":
                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=STATION_FIELDS)
                        writer.writeheader()
                    rows, stats = _iter_station_rows(ds, time_values, time_mask, nc_path.name)
                elif kind in ("grid", "grid1km", "grid0p1deg"):
                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=GRID_FIELDS)
                        writer.writeheader()
                    valid_cells = load_valid_grid_cells(valid_grid_geojson, ds) if valid_grid_geojson else None
                    rows, stats = _iter_grid_rows(ds, time_values, time_mask, nc_path.name, valid_cells)
                else:
                    raise ValueError(f"unsupported dataset_kind: {dataset_kind}")
                for row in rows:
                    writer.writerow(row)
                total_rows += stats["row_count"]
                total_missing += stats["missing_count"]
                total_precip += stats["total_precip_mm"]
                if stats["max_precip_mm"] is not None:
                    max_precip = stats["max_precip_mm"] if max_precip is None else max(max_precip, stats["max_precip_mm"])
                if stats["first_time"] and first_time is None:
                    first_time = stats["first_time"]
                if stats["last_time"]:
                    last_time = stats["last_time"]
    if total_rows == 0:
        raise ValueError("requested time window did not overlap available NetCDF times")

    return {
        "source_nc_dir": str(nc_dir).replace("\\", "/"),
        "out_csv": str(output_path).replace("\\", "/"),
        "dataset_kind": ",".join(sorted(dataset_types)),
        "window_start": window.start.isoformat(sep=" "),
        "window_end": window.end.isoformat(sep=" "),
        "event_id": window.event_id,
        "event_name": window.event_name,
        "observed_peak_cms": window.observed_peak_cms,
        "files_used": files_used,
        "row_count": total_rows,
        "missing_count": total_missing,
        "total_precip_mm": total_precip,
        "max_precip_mm": max_precip,
        "first_time": first_time,
        "last_time": last_time,
    }


STATION_FIELDS = [
    "source_file",
    "time",
    "station_id",
    "legacy_code",
    "station_name",
    "longitude",
    "latitude",
    "precip_mm",
]

GRID_FIELDS = [
    "source_file",
    "time",
    "grid_id",
    "grid_row",
    "grid_col",
    "nc_row",
    "nc_col",
    "lat",
    "lon",
    "precip_mm",
]


def select_netcdf_files(nc_dir: str | Path, start: datetime, end: datetime) -> list[Path]:
    directory = Path(nc_dir)
    if not directory.exists():
        raise ValueError(f"NetCDF directory does not exist: {directory}")
    years = set(range(start.year, end.year + 1))
    paths: list[Path] = []
    for path in directory.glob("*.nc"):
        year = _year_from_name(path.name)
        if year in years:
            paths.append(path)
    return sorted(paths)


def infer_dataset_kind(ds: Any) -> str:
    precipitation = ds.variables.get("precipitation")
    if precipitation is None:
        raise ValueError("NetCDF file has no precipitation variable")
    dims = tuple(precipitation.dimensions)
    if dims == ("time", "station"):
        return "station"
    if dims == ("time", "lat", "lon"):
        return "grid"
    raise ValueError(f"unsupported precipitation dimensions: {dims}")


@contextmanager
def open_netcdf_dataset(path: str | Path, temp_dir: str | Path) -> Iterator[Any]:
    """Open NetCDF paths, copying to an ASCII temp path when the backend needs it."""

    try:
        import netCDF4
    except ImportError as exc:
        raise RuntimeError(
            "netCDF4 is required to read rainfall NetCDF. "
            "Run this command with a Python environment that has netCDF4 installed."
        ) from exc

    src = Path(path)
    copied = None
    try:
        ds = netCDF4.Dataset(str(src))
    except (FileNotFoundError, OSError):
        temp_root = Path(temp_dir)
        temp_root.mkdir(parents=True, exist_ok=True)
        copied = temp_root / f"xaj_nc_{src.stem}_{abs(hash(str(src)))}{src.suffix}"
        shutil.copy2(src, copied)
        ds = netCDF4.Dataset(str(copied))
    try:
        yield ds
    finally:
        ds.close()
        if copied is not None:
            copied.unlink(missing_ok=True)


def selected_time_values(ds: Any, window: TimeWindow) -> tuple[list[datetime], list[int]]:
    import netCDF4

    variable = ds.variables["time"]
    values = variable[:]
    dates = netCDF4.num2date(
        values,
        units=getattr(variable, "units"),
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    selected_dates: list[datetime] = []
    selected_indices: list[int] = []
    for index, date_value in enumerate(dates):
        current = parse_datetime(date_value)
        if window.start <= current <= window.end:
            selected_dates.append(current)
            selected_indices.append(index)
    return selected_dates, selected_indices


def load_valid_grid_cells(path: str | Path | None, ds: Any) -> list[dict[str, Any]] | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lat_values = [float(value) for value in ds.variables["lat"][:]]
    lon_values = [float(value) for value in ds.variables["lon"][:]]
    cells: list[dict[str, Any]] = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        lon, lat = _feature_centroid(feature)
        nc_row = _nearest_index(lat_values, lat)
        nc_col = _nearest_index(lon_values, lon)
        grid_row = int(props.get("row", nc_row + 1))
        grid_col = int(props.get("col", nc_col + 1))
        cells.append(
            {
                "grid_row": grid_row,
                "grid_col": grid_col,
                "grid_id": f"G{grid_row:04d}_{grid_col:04d}",
                "nc_row": nc_row,
                "nc_col": nc_col,
                "lat": lat_values[nc_row],
                "lon": lon_values[nc_col],
            }
        )
    return cells


def _iter_station_rows(ds: Any, times: list[datetime], indices: list[int], source_file: str):
    precipitation = ds.variables["precipitation"]
    station_ids = _string_values(ds.variables["station_id"][:])
    legacy_codes = _string_values(ds.variables["legacy_code"][:])
    station_names = _string_values(ds.variables["station_name"][:])
    longitudes = [float(value) for value in ds.variables["longitude"][:]]
    latitudes = [float(value) for value in ds.variables["latitude"][:]]
    rows = []
    stats = _new_stats()
    for time_value, time_index in zip(times, indices):
        values = precipitation[time_index, :]
        for station_index, value in enumerate(values):
            precip = _precip_value(value)
            rows.append(
                {
                    "source_file": source_file,
                    "time": time_value.isoformat(sep=" "),
                    "station_id": station_ids[station_index],
                    "legacy_code": legacy_codes[station_index],
                    "station_name": station_names[station_index],
                    "longitude": f"{longitudes[station_index]:.12f}",
                    "latitude": f"{latitudes[station_index]:.12f}",
                    "precip_mm": "" if precip is None else f"{precip:.6f}",
                }
            )
            _update_stats(stats, time_value, precip)
    return rows, stats


def _iter_grid_rows(
    ds: Any,
    times: list[datetime],
    indices: list[int],
    source_file: str,
    valid_cells: list[dict[str, Any]] | None,
):
    precipitation = ds.variables["precipitation"]
    lat_values = [float(value) for value in ds.variables["lat"][:]]
    lon_values = [float(value) for value in ds.variables["lon"][:]]
    if valid_cells is None:
        valid_cells = [
            {
                "grid_id": f"NC_R{row + 1:04d}_C{col + 1:04d}",
                "grid_row": row + 1,
                "grid_col": col + 1,
                "nc_row": row,
                "nc_col": col,
                "lat": lat_values[row],
                "lon": lon_values[col],
            }
            for row in range(len(lat_values))
            for col in range(len(lon_values))
        ]

    rows = []
    stats = _new_stats()
    for time_value, time_index in zip(times, indices):
        values = precipitation[time_index, :, :]
        for cell in valid_cells:
            precip = _precip_value(values[cell["nc_row"], cell["nc_col"]])
            rows.append(
                {
                    "source_file": source_file,
                    "time": time_value.isoformat(sep=" "),
                    "grid_id": cell["grid_id"],
                    "grid_row": cell["grid_row"],
                    "grid_col": cell["grid_col"],
                    "nc_row": cell["nc_row"],
                    "nc_col": cell["nc_col"],
                    "lat": f"{cell['lat']:.12f}",
                    "lon": f"{cell['lon']:.12f}",
                    "precip_mm": "" if precip is None else f"{precip:.6f}",
                }
            )
            _update_stats(stats, time_value, precip)
    return rows, stats


def _new_stats() -> dict[str, Any]:
    return {
        "row_count": 0,
        "missing_count": 0,
        "total_precip_mm": 0.0,
        "max_precip_mm": None,
        "first_time": None,
        "last_time": None,
    }


def _update_stats(stats: dict[str, Any], time_value: datetime, precip: float | None) -> None:
    stats["row_count"] += 1
    text_time = time_value.isoformat(sep=" ")
    if stats["first_time"] is None:
        stats["first_time"] = text_time
    stats["last_time"] = text_time
    if precip is None:
        stats["missing_count"] += 1
        return
    stats["total_precip_mm"] += precip
    stats["max_precip_mm"] = precip if stats["max_precip_mm"] is None else max(stats["max_precip_mm"], precip)


def _precip_value(value: Any) -> float | None:
    try:
        import numpy as np

        if np.ma.is_masked(value):
            return None
        number = float(value)
        if not np.isfinite(number) or number <= -9990.0:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _string_values(values: Any) -> list[str]:
    return [str(value) for value in values]


def _feature_centroid(feature: dict[str, Any]) -> tuple[float, float]:
    coords: list[tuple[float, float]] = []

    def walk(item: Any) -> None:
        if isinstance(item, list):
            if len(item) >= 2 and all(isinstance(value, (int, float)) for value in item[:2]):
                coords.append((float(item[0]), float(item[1])))
            else:
                for sub in item:
                    walk(sub)

    walk(feature.get("geometry", {}).get("coordinates"))
    if not coords:
        raise ValueError("valid-grid feature has no coordinates")
    return (
        (min(x for x, _ in coords) + max(x for x, _ in coords)) / 2.0,
        (min(y for _, y in coords) + max(y for _, y in coords)) / 2.0,
    )


def _nearest_index(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def _year_from_name(name: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", name)
    return int(match.group(0)) if match else None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
