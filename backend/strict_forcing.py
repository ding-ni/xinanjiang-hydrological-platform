"""Strict frozen hourly NC loader for new input products.

Only the actually requested interval is inspected. Full-year files may preserve
missing hours outside that interval; historical forcing needed for warm-up is
never silently filled with zero.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path


def _hour(value):
    value = datetime.fromisoformat(str(value)) if not isinstance(value, datetime) else value
    if value.tzinfo is not None or value.minute or value.second or value.microsecond:
        raise ValueError("强迫时间须为无时区整点")
    return value


def load_hourly_forcing(bundle, grids, start, requested_end, require_complete=True):
    import numpy as np
    import pandas as pd
    import xarray as xr

    start, end = _hour(start), _hour(requested_end)
    if end < start:
        raise ValueError("强迫结束时刻早于起点")
    times = pd.date_range(start, end, freq="h")
    directory = Path(bundle["precipitationDir"])
    values = np.full((len(times), len(grids)), np.nan, dtype=float)
    counts = np.full(len(times), -1, dtype=np.int16)
    present = np.zeros(len(times), dtype=bool)
    negative_by_hour = np.zeros(len(times), dtype=np.int32)
    nonfinite_by_hour = np.zeros(len(times), dtype=np.int32)
    negative = invalid = 0
    fields = set()
    for year in range(start.year, end.year + 1):
        path = directory / f"nananxi_precip_hourly_1km_{year}.nc"
        if not path.is_file():
            continue
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            if dataset["precipitation"].dims != ("time", "lat", "lon") or dataset["precipitation"].attrs.get("units") != "mm":
                raise ValueError("降雨须为time/lat/lon逐小时量mm")
            # Clip before checking gaps or invalid values: unused future/end-of-year
            # entries must not invalidate an otherwise complete requested prefix.
            subset = dataset.sel(time=slice(start, end))
            actual = pd.DatetimeIndex(pd.to_datetime(subset.time.values))
            if actual.has_duplicates or actual.tz is not None:
                raise ValueError("降雨时间轴重复或含时区")
            indexes = times.get_indexer(actual)
            if np.any(indexes < 0):
                raise ValueError("降雨时间轴含非整点")
            # Preserve the registered file's precision. Historical float32
            # values widen exactly; explicit source-replay float64 stays intact.
            rain = np.asarray(subset.precipitation.values, dtype=float)
            extracted = np.stack([rain[:, int(row["array_row"]), int(row["array_col"])] for row in grids], axis=1)
            negative += int((extracted < 0).sum())
            invalid += int((~np.isfinite(extracted)).sum())
            negative_by_hour[indexes] = (extracted < 0).sum(axis=1)
            nonfinite_by_hour[indexes] = (~np.isfinite(extracted)).sum(axis=1)
            extracted = extracted.astype(float)
            extracted[(~np.isfinite(extracted)) | (extracted < 0)] = np.nan
            values[indexes] = extracted
            present[indexes] = True
            field = next((name for name in ("source_station_count", "reporting_station_count") if name in subset), None)
            if field:
                raw = np.asarray(subset[field].values, dtype=float)
                if raw.shape != (len(actual),) or np.any((raw[np.isfinite(raw)] < 0) | (raw[np.isfinite(raw)] != np.floor(raw[np.isfinite(raw)]))):
                    raise ValueError("有效站数须为一维非负整数")
                counts[indexes] = np.where(np.isfinite(raw), raw, -1).astype(np.int16)
                fields.add(field)
    ready = present & np.isfinite(values).all(axis=1) & (counts > 0)
    audit = {"start": start.isoformat(), "end": end.isoformat(), "requested_hours": len(times),
             "missing_time_hours": int((~present).sum()), "incomplete_forcing_hours": int((~ready).sum()),
             "negative_values_reclassified_missing": negative, "nonfinite_values": invalid,
             "station_count_fields": sorted(fields), "missing_value_policy": "NO_ZERO_FILL_NEGATIVE_INVALID_AS_MISSING"}
    audit["present_hour_mask"] = present.tolist()
    audit["negative_counts_by_hour"] = negative_by_hour.tolist()
    audit["nonfinite_counts_by_hour"] = nonfinite_by_hour.tolist()
    if require_complete and not bool(ready.all()):
        first = times[np.flatnonzero(~ready)[0]].isoformat()
        raise ValueError(f"实际所需强迫时段缺测，拒绝模拟：{int((~ready).sum())}小时，首个{first}")
    return times, values, counts, audit


__all__ = ["load_hourly_forcing"]
