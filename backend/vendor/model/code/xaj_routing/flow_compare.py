"""Flow hydrograph comparison utilities."""

from __future__ import annotations

import csv
import html
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .io import write_json
from .models import parse_time


TIME_COLUMN_CANDIDATES = (
    "time",
    "datetime",
    "date_time",
    "timestamp",
    "date",
    "tm",
    "时间",
)

FLOW_COLUMN_CANDIDATES = (
    "outflow_cms",
    "flow_cms",
    "discharge_cms",
    "q_cms",
    "sim_flow_cms",
    "obs_flow_cms",
    "flow",
    "discharge",
    "q",
    "Q",
    "流量",
    "流量（立方米每秒）",
)


@dataclass(frozen=True)
class FlowPoint:
    time: datetime
    flow_cms: float


def parse_flow_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    if not text:
        raise ValueError("blank time value")

    normalized = text.replace("/", "-")
    try:
        return parse_time(normalized).replace(tzinfo=None)
    except ValueError:
        pass

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d%H",
        "%Y%m%d%H%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid time value: {value!r}")


def read_flow_series(
    path: str | Path,
    *,
    time_col: str | None = None,
    flow_col: str | None = None,
    sheet_name: str | None = None,
    sheet_index: int | None = None,
    header_row: int = 1,
    filters: dict[str, str] | None = None,
    temp_dir: str | Path = Path(tempfile.gettempdir()) / "XAJ_xlsx_runtime_tmp",
    source_label: str = "flow",
) -> list[FlowPoint]:
    input_path = Path(path)
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        points = read_flow_csv(
            input_path,
            time_col=time_col,
            flow_col=flow_col,
            filters=filters,
            source_label=source_label,
        )
    elif suffix in {".xlsx", ".xlsm"}:
        points = read_flow_excel(
            input_path,
            time_col=time_col,
            flow_col=flow_col,
            sheet_name=sheet_name,
            sheet_index=sheet_index,
            header_row=header_row,
            temp_dir=temp_dir,
            source_label=source_label,
        )
    else:
        raise ValueError(f"unsupported flow file type: {input_path.suffix}")

    return _sort_and_validate_unique(points, source_label)


def read_flow_csv(
    path: str | Path,
    *,
    time_col: str | None = None,
    flow_col: str | None = None,
    filters: dict[str, str] | None = None,
    source_label: str = "CSV flow",
) -> list[FlowPoint]:
    points: list[FlowPoint] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        selected_time_col = _select_column(
            fieldnames,
            time_col,
            TIME_COLUMN_CANDIDATES,
            "time",
            source_label,
        )
        selected_flow_col = _select_column(
            fieldnames,
            flow_col,
            FLOW_COLUMN_CANDIDATES,
            "flow",
            source_label,
        )

        for row_number, row in enumerate(reader, start=2):
            if filters and not _matches_filters(row, filters):
                continue
            if _is_blank(row.get(selected_time_col)) or _is_blank(row.get(selected_flow_col)):
                continue
            try:
                points.append(
                    FlowPoint(
                        time=parse_flow_time(row[selected_time_col]),
                        flow_cms=_parse_float(row[selected_flow_col]),
                    )
                )
            except ValueError as exc:
                raise ValueError(f"{source_label} row {row_number}: {exc}") from exc
    return points


def read_flow_excel(
    path: str | Path,
    *,
    time_col: str | None = None,
    flow_col: str | None = None,
    sheet_name: str | None = None,
    sheet_index: int | None = None,
    header_row: int = 1,
    temp_dir: str | Path = Path(tempfile.gettempdir()) / "XAJ_xlsx_runtime_tmp",
    source_label: str = "Excel flow",
) -> list[FlowPoint]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("reading .xlsx flow files requires openpyxl") from exc

    workbook = _load_workbook_with_ascii_fallback(
        load_workbook,
        Path(path),
        Path(temp_dir),
    )
    try:
        worksheet = _select_worksheet(
            workbook,
            sheet_name=sheet_name,
            sheet_index=sheet_index,
            header_row=header_row,
            time_col=time_col,
            flow_col=flow_col,
            source_label=source_label,
        )
        header_values = _read_excel_row(worksheet, header_row)
        selected_time_col = _select_column(
            header_values,
            time_col,
            TIME_COLUMN_CANDIDATES,
            "time",
            source_label,
        )
        selected_flow_col = _select_column(
            header_values,
            flow_col,
            FLOW_COLUMN_CANDIDATES,
            "flow",
            source_label,
        )
        time_index = header_values.index(selected_time_col)
        flow_index = header_values.index(selected_flow_col)

        points: list[FlowPoint] = []
        for row_number, row in enumerate(
            worksheet.iter_rows(min_row=header_row + 1, values_only=True),
            start=header_row + 1,
        ):
            time_value = row[time_index] if time_index < len(row) else None
            flow_value = row[flow_index] if flow_index < len(row) else None
            if _is_blank(time_value) or _is_blank(flow_value):
                continue
            try:
                points.append(
                    FlowPoint(
                        time=parse_flow_time(time_value),
                        flow_cms=_parse_float(flow_value),
                    )
                )
            except ValueError as exc:
                raise ValueError(f"{source_label} row {row_number}: {exc}") from exc
        return points
    finally:
        workbook.close()


def compare_flow_series(
    simulated: list[FlowPoint],
    observed: list[FlowPoint],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sim_by_time = {point.time: point.flow_cms for point in simulated}
    obs_by_time = {point.time: point.flow_cms for point in observed}
    overlap_times = sorted(set(sim_by_time).intersection(obs_by_time))
    aligned_rows = [
        {
            "time": time,
            "sim_flow_cms": sim_by_time[time],
            "obs_flow_cms": obs_by_time[time],
            "difference_cms": sim_by_time[time] - obs_by_time[time],
        }
        for time in overlap_times
    ]

    metrics = {
        "simulated_count": len(simulated),
        "observed_count": len(observed),
        "aligned_count": len(aligned_rows),
        "simulated_start": _iso_or_none(simulated[0].time if simulated else None),
        "simulated_end": _iso_or_none(simulated[-1].time if simulated else None),
        "observed_start": _iso_or_none(observed[0].time if observed else None),
        "observed_end": _iso_or_none(observed[-1].time if observed else None),
        "aligned_start": _iso_or_none(aligned_rows[0]["time"] if aligned_rows else None),
        "aligned_end": _iso_or_none(aligned_rows[-1]["time"] if aligned_rows else None),
    }
    metrics.update(_series_peak_metrics(simulated, observed, aligned_rows))
    metrics.update(_error_metrics(aligned_rows))
    return aligned_rows, metrics


def write_aligned_flow_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["time", "sim_flow_cms", "obs_flow_cms", "difference_cms"]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "time": row["time"].isoformat(sep=" "),
                    "sim_flow_cms": f"{row['sim_flow_cms']:.6f}",
                    "obs_flow_cms": f"{row['obs_flow_cms']:.6f}",
                    "difference_cms": f"{row['difference_cms']:.6f}",
                }
            )


def write_flow_compare_html(
    path: str | Path,
    *,
    simulated: list[FlowPoint],
    observed: list[FlowPoint],
    metrics: dict[str, Any],
    title: str = "Flow comparison",
    plot_domain: str = "sim-window",
    plot_start: datetime | None = None,
    plot_end: datetime | None = None,
    max_points: int = 2000,
) -> dict[str, Any]:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start, end = _resolve_plot_window(
        simulated,
        observed,
        metrics,
        plot_domain=plot_domain,
        plot_start=plot_start,
        plot_end=plot_end,
    )
    sim_plot = _filter_window(simulated, start, end)
    obs_plot = _filter_window(observed, start, end)
    payload = {
        "title": title,
        "window": {
            "start": _iso_or_none(start),
            "end": _iso_or_none(end),
        },
        "metrics": metrics,
        "simulated": _points_to_payload(_downsample(sim_plot, max_points)),
        "observed": _points_to_payload(_downsample(obs_plot, max_points)),
        "plotCounts": {
            "simulated": len(sim_plot),
            "observed": len(obs_plot),
        },
    }
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(_build_html(payload))
    return payload


def write_flow_comparison_outputs(
    *,
    sim_flow: str | Path,
    obs_flow: str | Path,
    out_html: str | Path,
    out_csv: str | Path | None = None,
    metrics_out: str | Path | None = None,
    sim_time_col: str | None = None,
    sim_flow_col: str | None = None,
    obs_time_col: str | None = None,
    obs_flow_col: str | None = None,
    obs_sheet: str | None = None,
    obs_sheet_index: int | None = None,
    obs_header_row: int = 1,
    sim_filters: dict[str, str] | None = None,
    plot_domain: str = "sim-window",
    plot_start: datetime | None = None,
    plot_end: datetime | None = None,
    max_points: int = 2000,
    excel_temp_dir: str | Path = Path(tempfile.gettempdir()) / "XAJ_xlsx_runtime_tmp",
) -> dict[str, Any]:
    simulated = read_flow_series(
        sim_flow,
        time_col=sim_time_col,
        flow_col=sim_flow_col,
        filters=sim_filters,
        source_label="simulated flow",
    )
    observed = read_flow_series(
        obs_flow,
        time_col=obs_time_col,
        flow_col=obs_flow_col,
        sheet_name=obs_sheet,
        sheet_index=obs_sheet_index,
        header_row=obs_header_row,
        temp_dir=excel_temp_dir,
        source_label="observed flow",
    )
    aligned_rows, metrics = compare_flow_series(simulated, observed)
    if out_csv:
        write_aligned_flow_csv(out_csv, aligned_rows)
    if metrics_out:
        write_json(metrics_out, metrics)
    write_flow_compare_html(
        out_html,
        simulated=simulated,
        observed=observed,
        metrics=metrics,
        plot_domain=plot_domain,
        plot_start=plot_start,
        plot_end=plot_end,
        max_points=max_points,
    )
    return metrics


def _select_column(
    headers: list[Any],
    explicit: str | None,
    candidates: tuple[str, ...],
    role: str,
    source_label: str,
) -> str:
    clean_headers = [str(header).strip() for header in headers if not _is_blank(header)]
    if explicit:
        explicit_norm = _normalize_header(explicit)
        for header in clean_headers:
            if header == explicit or _normalize_header(header) == explicit_norm:
                return header
        raise ValueError(f"{source_label}: {role} column not found: {explicit}")

    normalized_candidates = {_normalize_header(candidate) for candidate in candidates}
    for header in clean_headers:
        if _normalize_header(header) in normalized_candidates:
            return header

    fuzzy_matches = []
    for header in clean_headers:
        normalized = _normalize_header(header)
        if role == "time" and ("time" in normalized or "时间" in header):
            fuzzy_matches.append(header)
        if role == "flow" and (
            "flow" in normalized
            or "discharge" in normalized
            or normalized in {"q", "qcms"}
            or "流量" in header
        ):
            fuzzy_matches.append(header)
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]

    raise ValueError(
        f"{source_label}: cannot infer {role} column from headers {clean_headers}"
    )


def _select_worksheet(
    workbook: Any,
    *,
    sheet_name: str | None,
    sheet_index: int | None,
    header_row: int,
    time_col: str | None,
    flow_col: str | None,
    source_label: str,
) -> Any:
    if sheet_name:
        return workbook[sheet_name]
    if sheet_index is not None:
        if sheet_index <= 0:
            raise ValueError("--obs-sheet-index is 1-based and must be positive")
        try:
            return workbook.worksheets[sheet_index - 1]
        except IndexError as exc:
            raise ValueError(f"{source_label}: sheet index out of range: {sheet_index}") from exc

    for worksheet in workbook.worksheets:
        headers = _read_excel_row(worksheet, header_row)
        try:
            _select_column(headers, time_col, TIME_COLUMN_CANDIDATES, "time", source_label)
            _select_column(headers, flow_col, FLOW_COLUMN_CANDIDATES, "flow", source_label)
            return worksheet
        except ValueError:
            continue
    raise ValueError(f"{source_label}: no worksheet contains time and flow columns")


def _read_excel_row(worksheet: Any, row_number: int) -> list[str]:
    rows = worksheet.iter_rows(
        min_row=row_number,
        max_row=row_number,
        values_only=True,
    )
    row = next(rows, ())
    return ["" if _is_blank(value) else str(value).strip() for value in row]


def _load_workbook_with_ascii_fallback(load_workbook: Any, path: Path, temp_dir: Path) -> Any:
    try:
        return load_workbook(path, read_only=True, data_only=True)
    except OSError:
        temp_path = _copy_to_ascii_excel_path(path, temp_dir)
        return load_workbook(temp_path, read_only=True, data_only=True)


def _copy_to_ascii_excel_path(path: Path, temp_dir: Path) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"flow_workbook{path.suffix.lower() or '.xlsx'}"
    shutil.copy2(path, temp_path)
    return temp_path


def _sort_and_validate_unique(points: list[FlowPoint], source_label: str) -> list[FlowPoint]:
    sorted_points = sorted(points, key=lambda point: point.time)
    seen: set[datetime] = set()
    duplicates: list[datetime] = []
    for point in sorted_points:
        if point.time in seen:
            duplicates.append(point.time)
        seen.add(point.time)
    if duplicates:
        example = duplicates[0].isoformat(sep=" ")
        raise ValueError(
            f"{source_label}: duplicate timestamp {example}. "
            "Use a single-section file or filter the CSV first."
        )
    return sorted_points


def _matches_filters(row: dict[str, Any], filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        if str(row.get(key, "")).strip() != expected:
            return False
    return True


def _parse_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "NA", "N/A", "nan", "NaN"}:
        raise ValueError("blank numeric value")
    return float(text)


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _normalize_header(value: Any) -> str:
    return "".join(str(value).strip().lower().split()).replace("_", "")


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(sep=" ")


def _series_peak_metrics(
    simulated: list[FlowPoint],
    observed: list[FlowPoint],
    aligned_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    sim_peak = max(simulated, key=lambda point: point.flow_cms, default=None)
    obs_peak = max(observed, key=lambda point: point.flow_cms, default=None)
    aligned_sim_peak = max(
        aligned_rows,
        key=lambda row: row["sim_flow_cms"],
        default=None,
    )
    aligned_obs_peak = max(
        aligned_rows,
        key=lambda row: row["obs_flow_cms"],
        default=None,
    )
    peak_time_error_hours = None
    peak_error_cms = None
    if aligned_sim_peak and aligned_obs_peak:
        peak_time_error_hours = (
            aligned_sim_peak["time"] - aligned_obs_peak["time"]
        ).total_seconds() / 3600.0
        peak_error_cms = aligned_sim_peak["sim_flow_cms"] - aligned_obs_peak["obs_flow_cms"]

    return {
        "simulated_peak_cms": sim_peak.flow_cms if sim_peak else None,
        "simulated_peak_time": _iso_or_none(sim_peak.time if sim_peak else None),
        "observed_peak_cms": obs_peak.flow_cms if obs_peak else None,
        "observed_peak_time": _iso_or_none(obs_peak.time if obs_peak else None),
        "aligned_simulated_peak_cms": aligned_sim_peak["sim_flow_cms"]
        if aligned_sim_peak
        else None,
        "aligned_simulated_peak_time": _iso_or_none(
            aligned_sim_peak["time"] if aligned_sim_peak else None
        ),
        "aligned_observed_peak_cms": aligned_obs_peak["obs_flow_cms"]
        if aligned_obs_peak
        else None,
        "aligned_observed_peak_time": _iso_or_none(
            aligned_obs_peak["time"] if aligned_obs_peak else None
        ),
        "aligned_peak_error_cms": peak_error_cms,
        "aligned_peak_time_error_hours": peak_time_error_hours,
    }


def _error_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "mae_cms": None,
            "rmse_cms": None,
            "bias_cms": None,
            "nse": None,
        }

    errors = [row["difference_cms"] for row in rows]
    mae = sum(abs(error) for error in errors) / len(errors)
    bias = sum(errors) / len(errors)
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    obs_values = [row["obs_flow_cms"] for row in rows]
    obs_mean = sum(obs_values) / len(obs_values)
    denominator = sum((value - obs_mean) ** 2 for value in obs_values)
    nse = None
    if len(rows) > 1 and denominator > 0:
        nse = 1.0 - sum(error * error for error in errors) / denominator
    return {
        "mae_cms": mae,
        "rmse_cms": rmse,
        "bias_cms": bias,
        "nse": nse,
    }


def _resolve_plot_window(
    simulated: list[FlowPoint],
    observed: list[FlowPoint],
    metrics: dict[str, Any],
    *,
    plot_domain: str,
    plot_start: datetime | None,
    plot_end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    if plot_start or plot_end:
        return plot_start, plot_end
    if plot_domain == "union":
        all_points = simulated + observed
        if not all_points:
            return None, None
        return min(point.time for point in all_points), max(point.time for point in all_points)
    if plot_domain == "overlap" and metrics.get("aligned_start") and metrics.get("aligned_end"):
        return parse_flow_time(metrics["aligned_start"]), parse_flow_time(metrics["aligned_end"])
    if simulated:
        return simulated[0].time, simulated[-1].time
    if observed:
        return observed[0].time, observed[-1].time
    return None, None


def _filter_window(
    points: list[FlowPoint],
    start: datetime | None,
    end: datetime | None,
) -> list[FlowPoint]:
    if start is None and end is None:
        return list(points)
    return [
        point
        for point in points
        if (start is None or point.time >= start) and (end is None or point.time <= end)
    ]


def _downsample(points: list[FlowPoint], max_points: int) -> list[FlowPoint]:
    if max_points <= 0 or len(points) <= max_points:
        return points
    step = math.ceil(len(points) / max_points)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _points_to_payload(points: list[FlowPoint]) -> list[dict[str, Any]]:
    return [
        {
            "time": point.time.isoformat(sep=" "),
            "flow": round(point.flow_cms, 6),
        }
        for point in points
    ]


def _build_html(payload: dict[str, Any]) -> str:
    title = html.escape(str(payload["title"]))
    json_payload = json.dumps(payload, ensure_ascii=False)
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --grid: #d9dee8;
      --sim: #2563eb;
      --obs: #d97706;
      --border: #d8dee9;
    }
    body {
      margin: 0;
      padding: 24px;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
    }
    h1 {
      margin: 0 0 14px;
      font-size: 24px;
      font-weight: 600;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }
    .metric-value {
      font-size: 18px;
      font-weight: 600;
      white-space: nowrap;
    }
    .chart-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .legend {
      display: flex;
      gap: 18px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .swatch {
      width: 22px;
      height: 3px;
      border-radius: 99px;
      display: inline-block;
    }
    .note {
      margin: 0 0 12px;
      color: #92400e;
      background: #fffbeb;
      border: 1px solid #fde68a;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 13px;
    }
    svg {
      display: block;
      width: 100%;
      height: auto;
      overflow: visible;
    }
    .axis text {
      fill: var(--muted);
      font-size: 12px;
    }
    .axis path,
    .axis line,
    .grid-line {
      stroke: var(--grid);
      stroke-width: 1;
      shape-rendering: crispEdges;
    }
    .series {
      fill: none;
      stroke-width: 2.4;
      stroke-linejoin: round;
      stroke-linecap: round;
    }
    .axis-title {
      fill: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 640px) {
      body {
        padding: 14px;
      }
      h1 {
        font-size: 20px;
      }
      .chart-card {
        padding: 10px;
      }
    }
  </style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <section class="summary" id="summary"></section>
  <section class="chart-card">
    <div class="legend">
      <span class="legend-item"><span class="swatch" style="background: var(--sim)"></span>模拟流量</span>
      <span class="legend-item"><span class="swatch" style="background: var(--obs)"></span>实测流量</span>
    </div>
    <p class="note" id="note" hidden></p>
    <svg id="chart" viewBox="0 0 1000 520" role="img" aria-label="模拟流量与实测流量对比曲线"></svg>
  </section>
</main>
<script>
const payload = __PAYLOAD__;

function fmtNumber(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Number(value).toFixed(digits);
}

function fmtMetric(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Math.abs(value) >= 100 ? Number(value).toFixed(1) : Number(value).toFixed(3);
}

function parseTime(point) {
  return new Date(point.time.replace(" ", "T")).getTime();
}

function fmtTime(ms) {
  const d = new Date(ms);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:00`;
}

function linePath(points, xScale, yScale) {
  if (!points.length) return "";
  return points.map((point, index) => {
    const command = index === 0 ? "M" : "L";
    return `${command}${xScale(parseTime(point)).toFixed(2)},${yScale(point.flow).toFixed(2)}`;
  }).join(" ");
}

function addText(svg, text, x, y, className, anchor = "middle") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "text");
  node.textContent = text;
  node.setAttribute("x", x);
  node.setAttribute("y", y);
  node.setAttribute("class", className);
  node.setAttribute("text-anchor", anchor);
  svg.appendChild(node);
  return node;
}

function addLine(svg, x1, y1, x2, y2, className) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "line");
  node.setAttribute("x1", x1);
  node.setAttribute("y1", y1);
  node.setAttribute("x2", x2);
  node.setAttribute("y2", y2);
  node.setAttribute("class", className);
  svg.appendChild(node);
  return node;
}

function renderSummary() {
  const m = payload.metrics;
  const items = [
    ["重叠时段数", String(m.aligned_count ?? 0)],
    ["MAE (m3/s)", fmtMetric(m.mae_cms)],
    ["RMSE (m3/s)", fmtMetric(m.rmse_cms)],
    ["NSE", fmtMetric(m.nse)],
    ["模拟洪峰 (m3/s)", fmtMetric(m.simulated_peak_cms)],
    ["实测洪峰 (m3/s)", fmtMetric(m.observed_peak_cms)]
  ];
  document.getElementById("summary").innerHTML = items.map(([label, value]) => `
    <article class="metric">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${value}</div>
    </article>
  `).join("");
}

function renderNote() {
  const note = document.getElementById("note");
  const m = payload.metrics;
  if ((m.aligned_count ?? 0) === 0) {
    note.hidden = false;
    note.textContent = `当前模拟时段 ${m.simulated_start || "-"} 至 ${m.simulated_end || "-"}，实测资料时段 ${m.observed_start || "-"} 至 ${m.observed_end || "-"}，没有相同时间戳，暂不能计算误差指标。`;
  } else if ((payload.plotCounts.observed ?? 0) === 0) {
    note.hidden = false;
    note.textContent = "绘图窗口内没有实测流量点，请检查模拟结果时段或指定 plot window。";
  }
}

function renderChart() {
  const svg = document.getElementById("chart");
  const sim = payload.simulated;
  const obs = payload.observed;
  const all = sim.concat(obs);
  const width = 1000;
  const height = 520;
  const margin = { top: 22, right: 24, bottom: 70, left: 76 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  svg.innerHTML = "";

  if (!all.length) {
    addText(svg, "没有可绘制的流量数据", width / 2, height / 2, "axis-title");
    return;
  }

  let minT = Math.min(...all.map(parseTime));
  let maxT = Math.max(...all.map(parseTime));
  if (minT === maxT) {
    minT -= 3600000;
    maxT += 3600000;
  }
  const maxQ = Math.max(1, ...all.map(d => Number(d.flow) || 0));
  const yMax = maxQ * 1.12;
  const xScale = value => margin.left + ((value - minT) / (maxT - minT)) * plotW;
  const yScale = value => margin.top + plotH - (Number(value) / yMax) * plotH;

  for (let i = 0; i <= 5; i += 1) {
    const yValue = (yMax / 5) * i;
    const y = yScale(yValue);
    addLine(svg, margin.left, y, width - margin.right, y, "grid-line");
    addText(svg, fmtNumber(yValue, 1), margin.left - 12, y + 4, "axis text", "end");
  }

  addLine(svg, margin.left, margin.top, margin.left, height - margin.bottom, "axis line");
  addLine(svg, margin.left, height - margin.bottom, width - margin.right, height - margin.bottom, "axis line");

  for (let i = 0; i <= 5; i += 1) {
    const xValue = minT + ((maxT - minT) / 5) * i;
    const x = xScale(xValue);
    addLine(svg, x, height - margin.bottom, x, height - margin.bottom + 6, "axis line");
    const label = addText(svg, fmtTime(xValue), x, height - margin.bottom + 24, "axis text");
    if (i === 0) label.setAttribute("text-anchor", "start");
    if (i === 5) label.setAttribute("text-anchor", "end");
  }

  const obsPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  obsPath.setAttribute("d", linePath(obs, xScale, yScale));
  obsPath.setAttribute("class", "series");
  obsPath.setAttribute("stroke", "var(--obs)");
  svg.appendChild(obsPath);

  const simPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  simPath.setAttribute("d", linePath(sim, xScale, yScale));
  simPath.setAttribute("class", "series");
  simPath.setAttribute("stroke", "var(--sim)");
  svg.appendChild(simPath);

  addText(svg, "时间", margin.left + plotW / 2, height - 18, "axis-title");
  const yTitle = addText(svg, "流量 (m3/s)", 18, margin.top + plotH / 2, "axis-title");
  yTitle.setAttribute("transform", `rotate(-90 18 ${margin.top + plotH / 2})`);
}

renderSummary();
renderNote();
renderChart();
</script>
</body>
</html>
""".replace("__TITLE__", title).replace("__PAYLOAD__", json_payload)
