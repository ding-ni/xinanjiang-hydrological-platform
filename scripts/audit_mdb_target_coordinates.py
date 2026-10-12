"""Quantify MDB forcing differences caused by target coordinates and precision."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from common import MODEL, read_json, write_json
from input_products import InputProducts, file_sha256
from mdb_import import XUFAN_COORDINATES, normalize_records, reuse_verified_records
from strict_forcing import load_hourly_forcing


def audit(runtime, identifier, output, source_model):
    import h5py
    import h5netcdf
    import numpy as np
    import pandas as pd
    import xarray as xr
    from pyproj import Transformer
    code = str(MODEL / "code")
    if code not in sys.path:
        sys.path.insert(0, code)
    from runoff_platform.data.precipitation_idw import idw_interpolate_hourly

    products = InputProducts(MODEL, runtime)
    bundle = products.resolve(identifier)
    manifest = bundle["manifest"]
    source = products.source(manifest["sourceId"])
    snapshot = products._path(read_json(source["path"])["databaseFile"])
    previous = read_json(bundle["manifestPath"].parent / "import-audit.json")["reusedQueryTask"]
    records = reuse_verified_records(products, previous, snapshot)
    rain, _ = normalize_records(records["rain"], station_field="HS_Code", time_field="YMDHM", value_field="DTRN", allowed_stations=XUFAN_COORDINATES)
    scope = records["_queryScope"]
    times = pd.date_range(scope["start"], scope["end"], freq="h")
    station_ids = sorted(XUFAN_COORDINATES)
    station_values = np.array([[rain.get((station, time.to_pydatetime()), np.nan) for station in station_ids] for time in times], dtype=float)
    with bundle["paths"]["grids"].open(encoding="utf-8-sig", newline="") as handle:
        grids = list(csv.DictReader(handle))
    rows, cols = np.array([int(row["array_row"]) for row in grids]), np.array([int(row["array_col"]) for row in grids])
    with xr.open_dataset(next(bundle["precipitationDir"].glob("*.nc")), engine="h5netcdf") as template:
        nc_lon, nc_lat = template.lon.values[cols], template.lat.values[rows]
    geometry = read_json(bundle["paths"]["geometry"])
    rings = {(int(feature["properties"]["row"]), int(feature["properties"]["col"])): feature["geometry"]["coordinates"][0] for feature in geometry["features"]}
    source_lon, source_lat, unique_lon, unique_lat = [], [], [], []
    for row in grids:
        ring = np.asarray(rings[(int(row["source_row"]), int(row["source_col"]))], dtype=float)
        source_lon.append(float(sum(point[0] for point in ring) / len(ring)))
        source_lat.append(float(sum(point[1] for point in ring) / len(ring)))
        unique_lon.append(float(np.mean(ring[:-1, 0])))
        unique_lat.append(float(np.mean(ring[:-1, 1])))
    projection = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    sx, sy = projection.transform([XUFAN_COORDINATES[station][0] for station in station_ids], [XUFAN_COORDINATES[station][1] for station in station_ids])
    nc_x, nc_y = projection.transform(nc_lon, nc_lat)
    source_x, source_y = projection.transform(source_lon, source_lat)
    nc_rain, nc_counts = idw_interpolate_hourly(station_values, np.asarray(sx), np.asarray(sy), np.asarray(nc_x), np.asarray(nc_y))
    source_rain, source_counts = idw_interpolate_hourly(station_values, np.asarray(sx), np.asarray(sy), np.asarray(source_x), np.asarray(source_y))
    actual_times, actual, actual_counts, _ = load_hourly_forcing(bundle, grids, scope["start"], scope["end"])
    precision_diff = np.abs(nc_rain - nc_rain.astype(np.float32).astype(float))
    coordinate_diff = np.abs(source_rain - nc_rain)
    actual_diff = np.abs(source_rain - actual)
    maximum = np.unravel_index(np.argmax(actual_diff), actual_diff.shape)
    events = []
    for event in records["events"]:
        start, end = pd.Timestamp(event["s_dt"]), pd.Timestamp(event["e_dt"])
        mask = (times >= start) & (times <= end)
        events.append({"eventId": "xf-" + event["flcd"], "maximumSourceVsRegisteredRainMm": float(actual_diff[mask].max()),
                       "maximumCoordinateOnlyRainMm": float(coordinate_diff[mask].max()), "maximumFloat32LossMm": float(precision_diff[mask].max())})
    source_geometry = next((Path(source_model) / "data_interface/spatial").glob("*有效格点.geojson"))
    report = {"bundleVersionId": identifier, "sourceGeometrySHA256": file_sha256(source_geometry), "bundleGeometrySHA256": file_sha256(bundle["paths"]["geometry"]),
              "sourceGeometryBytesEqual": file_sha256(source_geometry) == file_sha256(bundle["paths"]["geometry"]),
              "sourceTargetDefinition": "run_mdb_flood._grid_cells: arithmetic mean of the complete closed ring including duplicated final vertex",
              "registeredTargetDefinition": manifest.get("targetCoordinatePolicy", "NC_GRID_AXIS_CENTRE"),
              "registeredForcingDtype": manifest.get("forcingValueDtype", "float32"),
              "maximumUniqueVertexMeanVsNCCentreLongitudeDegrees": float(np.abs(np.asarray(unique_lon) - nc_lon).max()),
              "maximumUniqueVertexMeanVsNCCentreLatitudeDegrees": float(np.abs(np.asarray(unique_lat) - nc_lat).max()),
              "maximumTargetShiftMetres": float(np.hypot(np.asarray(source_x) - np.asarray(nc_x), np.asarray(source_y) - np.asarray(nc_y)).max()),
              "minimumTargetShiftMetres": float(np.hypot(np.asarray(source_x) - np.asarray(nc_x), np.asarray(source_y) - np.asarray(nc_y)).min()),
              "maximumLongitudeDifferenceDegrees": float(np.abs(np.asarray(source_lon) - nc_lon).max()),
              "maximumLatitudeDifferenceDegrees": float(np.abs(np.asarray(source_lat) - nc_lat).max()),
              "maximumCoordinateOnlyRainMm": float(coordinate_diff.max()), "maximumFloat32LossMm": float(precision_diff.max()),
              "maximumSourceVsRegisteredRainMm": float(actual_diff.max()),
              "maximumRegisteredVsExpectedNCCentreFloat32Mm": float(np.abs(actual - nc_rain.astype(np.float32).astype(float)).max()),
              "sourceStationCountsEqual": bool(np.array_equal(source_counts, actual_counts)),
              "maximumDifferenceAt": {"time": times[maximum[0]].isoformat(), "gridId": grids[maximum[1]]["unit_id"]},
              "queryScope": scope, "events": events}
    write_json(Path(output) / "MDB目标坐标差异定位.json", report)
    with (Path(output) / "MDB目标坐标逐格.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gridId", "ncLongitude", "ncLatitude", "sourceClosedRingMeanLongitude", "sourceClosedRingMeanLatitude", "shiftMetres"])
        writer.writeheader()
        writer.writerows({"gridId": row["unit_id"], "ncLongitude": float(nc_lon[index]), "ncLatitude": float(nc_lat[index]),
                          "sourceClosedRingMeanLongitude": source_lon[index], "sourceClosedRingMeanLatitude": source_lat[index],
                          "shiftMetres": float(np.hypot(source_x[index] - nc_x[index], source_y[index] - nc_y[index]))} for index, row in enumerate(grids))
    print(json.dumps({key: value for key, value in report.items() if key not in {"events", "queryScope"}}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-model", required=True)
    args = parser.parse_args()
    audit(args.runtime, args.bundle, args.output, args.source_model)
