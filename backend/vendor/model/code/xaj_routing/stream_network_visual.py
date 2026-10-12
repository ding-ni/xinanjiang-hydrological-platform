"""DEM-derived stream network visualization export."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import numpy as np

from .io import read_json, write_json
from .raster_formats import read_esri_ascii_grid
from .spatial import build_grid_topology, valid_mask


def write_dem_stream_network_visualization(
    *,
    ascii_path: str | Path,
    out_html: str | Path,
    out_png: str | Path | None = None,
    summary_out: str | Path | None = None,
    control_point_json: str | Path | None = None,
    cell_size_m: float = 30.0,
    stream_threshold_km2: float = 1.0,
    basin_id: str = "nan_an",
    spatial_version: str = "stream_network_v1",
    png_scale: int = 3,
) -> dict[str, Any]:
    grid = read_esri_ascii_grid(ascii_path)
    result = build_grid_topology(
        dem=grid.elevation,
        cell_size_m=cell_size_m,
        stream_threshold_km2=stream_threshold_km2,
        basin_id=basin_id,
        spatial_version=spatial_version,
        nodata=grid.nodata,
    )
    control = read_json(control_point_json) if control_point_json else None
    png_path = Path(out_png) if out_png else Path(out_html).with_suffix(".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    summary = _build_summary(
        ascii_path=ascii_path,
        png_path=png_path,
        html_path=out_html,
        grid=grid,
        result=result,
        control=control,
        stream_threshold_km2=stream_threshold_km2,
    )
    _write_png(
        path=png_path,
        dem=grid.elevation,
        nodata=grid.nodata,
        filled_dem=result.filled_dem,
        stream_raster=result.stream_raster,
        reaches=result.reaches,
        accumulation_area_km2=result.accumulation_area_km2,
        control=control,
        scale=png_scale,
    )
    _write_html(
        path=out_html,
        png_path=png_path,
        summary=summary,
    )
    if summary_out:
        write_json(summary_out, summary)
    return summary


def _build_summary(
    *,
    ascii_path: str | Path,
    png_path: str | Path,
    html_path: str | Path,
    grid: Any,
    result: Any,
    control: dict[str, Any] | None,
    stream_threshold_km2: float,
) -> dict[str, Any]:
    mask = valid_mask(grid.elevation, grid.nodata)
    valid_values = np.asarray(grid.elevation, dtype=float)[mask]
    terminal_reaches = [reach.reach_id for reach in result.reaches if reach.downstream_reach_id is None]
    stream_cell_count = int(np.asarray(result.stream_raster, dtype=bool).sum())
    cell_area_km2 = cell_size_area_km2(result.cell_size_m)
    stream_length_km = sum(float(reach.length_km) for reach in result.reaches)
    max_acc = float(np.nanmax(result.accumulation_area_km2))

    control_summary = None
    if control:
        snapped = control.get("snapped", {})
        control_summary = {
            "control_id": control.get("control_id"),
            "name": control.get("name"),
            "reach_id": snapped.get("reach_id"),
            "cell": snapped.get("cell"),
            "distance_m": _float_or_none(snapped.get("distance_m")),
            "accumulation_area_km2": _float_or_none(snapped.get("accumulation_area_km2")),
            "elevation_m": _float_or_none(snapped.get("elevation_m")),
        }

    return {
        "source_ascii": str(ascii_path).replace("\\", "/"),
        "out_html": str(html_path).replace("\\", "/"),
        "out_png": str(png_path).replace("\\", "/"),
        "basin_id": result.basin_id,
        "spatial_version": result.spatial_version,
        "nrows": int(grid.nrows),
        "ncols": int(grid.ncols),
        "cell_size_m": float(result.cell_size_m),
        "valid_cell_count": int(mask.sum()),
        "valid_area_km2": round(float(mask.sum()) * cell_area_km2, 6),
        "dem_min_m": float(np.nanmin(valid_values)) if valid_values.size else None,
        "dem_max_m": float(np.nanmax(valid_values)) if valid_values.size else None,
        "stream_threshold_km2": float(stream_threshold_km2),
        "stream_cell_count": stream_cell_count,
        "stream_cell_area_km2": round(stream_cell_count * cell_area_km2, 6),
        "reach_count": len(result.reaches),
        "unit_count": len(result.units),
        "extracted_stream_length_km": round(stream_length_km, 6),
        "terminal_reaches": terminal_reaches,
        "max_accumulation_area_km2": round(max_acc, 6),
        "control_point": control_summary,
    }


def cell_size_area_km2(cell_size_m: float) -> float:
    return cell_size_m * cell_size_m / 1_000_000.0


def _write_png(
    *,
    path: str | Path,
    dem: np.ndarray,
    nodata: float | None,
    filled_dem: np.ndarray,
    stream_raster: np.ndarray,
    reaches: list[Any],
    accumulation_area_km2: np.ndarray,
    control: dict[str, Any] | None,
    scale: int,
) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is required to export DEM stream-network PNG") from exc

    scale = max(int(scale), 1)
    mask = valid_mask(dem, nodata)
    base = _dem_rgb(filled_dem, mask)
    image = Image.fromarray(base, mode="RGB")
    if scale != 1:
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.BILINEAR)

    draw = ImageDraw.Draw(image, "RGBA")
    max_acc = float(np.nanmax(accumulation_area_km2))
    min_width = max(1, scale)
    for reach in reaches:
        cells = list(reach.cells)
        if len(cells) < 2:
            cells = [reach.start_cell, reach.end_cell]
        acc = accumulation_area_km2[reach.end_cell[0], reach.end_cell[1]]
        width = min_width + int(4 * scale * np.sqrt(float(acc) / max(max_acc, 1e-9)))
        color = (0, 86, 180, 230)
        if reach.downstream_reach_id is None:
            color = (96, 38, 180, 245)
            width += scale
        points = [(_cell_x(col, scale), _cell_y(row, scale)) for row, col in cells]
        if len(points) >= 2:
            draw.line(points, fill=color, width=width, joint="curve")

    # Add stream cells as a fine cyan trace so the raster extraction is visible.
    stream_rows, stream_cols = np.where(stream_raster)
    dot = max(1, scale // 2)
    for row, col in zip(stream_rows, stream_cols):
        x = _cell_x(int(col), scale)
        y = _cell_y(int(row), scale)
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(21, 160, 220, 150))

    for reach in reaches:
        if reach.downstream_reach_id is None:
            _draw_marker(draw, reach.end_cell, scale, fill=(114, 46, 209, 255), radius=5 * scale)

    if control:
        cell = control.get("snapped", {}).get("cell")
        if cell and len(cell) >= 2:
            _draw_marker(draw, (int(cell[0]), int(cell[1])), scale, fill=(220, 68, 5, 255), radius=6 * scale)

    image.save(path)


def _dem_rgb(filled_dem: np.ndarray, mask: np.ndarray) -> np.ndarray:
    arr = np.asarray(filled_dem, dtype=float)
    valid = arr[mask]
    p2, p98 = np.nanpercentile(valid, [2, 98]) if valid.size else (0.0, 1.0)
    if p98 <= p2:
        p98 = p2 + 1.0
    norm = np.clip((arr - p2) / (p98 - p2), 0.0, 1.0)

    gy, gx = np.gradient(arr)
    slope = np.hypot(gx, gy)
    aspect = np.arctan2(-gx, gy)
    azimuth = np.deg2rad(315.0)
    altitude = np.deg2rad(45.0)
    shaded = (
        np.sin(altitude) / np.sqrt(1.0 + slope * slope)
        + np.cos(altitude) * slope / np.sqrt(1.0 + slope * slope) * np.cos(azimuth - aspect)
    )
    shaded = np.clip((shaded + 1.0) / 2.0, 0.18, 1.0)

    low = np.array([232, 239, 220], dtype=float)
    mid = np.array([169, 205, 154], dtype=float)
    high = np.array([124, 105, 86], dtype=float)
    snow = np.array([242, 244, 241], dtype=float)
    rgb = np.zeros((*arr.shape, 3), dtype=float)
    lower = norm <= 0.55
    upper = (norm > 0.55) & (norm <= 0.88)
    top = norm > 0.88
    rgb[lower] = low + (mid - low) * (norm[lower] / 0.55)[:, None]
    rgb[upper] = mid + (high - mid) * ((norm[upper] - 0.55) / 0.33)[:, None]
    rgb[top] = high + (snow - high) * ((norm[top] - 0.88) / 0.12)[:, None]
    rgb = rgb * (0.62 + 0.45 * shaded[:, :, None])
    rgb[~mask] = np.array([246, 248, 251], dtype=float)
    return np.clip(np.nan_to_num(rgb, nan=246.0), 0, 255).astype(np.uint8)


def _cell_x(col: int, scale: int) -> int:
    return int(round((col + 0.5) * scale))


def _cell_y(row: int, scale: int) -> int:
    return int(round((row + 0.5) * scale))


def _draw_marker(draw: Any, cell: tuple[int, int], scale: int, fill: tuple[int, int, int, int], radius: int) -> None:
    row, col = cell
    x = _cell_x(col, scale)
    y = _cell_y(row, scale)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=(255, 255, 255, 245), width=max(2, scale))


def _write_html(
    *,
    path: str | Path,
    png_path: str | Path,
    summary: dict[str, Any],
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_src = Path(png_path).name
    json_summary = json.dumps(summary, ensure_ascii=False)
    title = "南安溪 DEM 提取河网结果"
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d5dce8;
      --river: #0056b4;
      --control: #dc4405;
      --terminal: #722ed1;
      --shadow: rgba(23, 32, 51, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }
    main {
      width: min(1420px, calc(100% - 32px));
      margin: 0 auto;
      padding: 22px 0 30px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 24px;
      font-weight: 600;
    }
    .subtitle {
      margin: 0 0 16px;
      color: var(--muted);
      font-size: 14px;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 350px;
      gap: 14px;
      align-items: start;
    }
    .map, .panel, .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 24px var(--shadow);
    }
    .map {
      padding: 12px;
    }
    .map img {
      display: block;
      width: 100%;
      height: auto;
      border-radius: 6px;
      border: 1px solid var(--line);
    }
    .legend {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .line {
      width: 26px;
      height: 4px;
      border-radius: 99px;
      background: var(--river);
    }
    .dot {
      width: 12px;
      height: 12px;
      border-radius: 99px;
      background: var(--control);
    }
    .dot.terminal {
      background: var(--terminal);
    }
    .panel {
      padding: 14px;
    }
    .stats {
      display: grid;
      gap: 9px;
      margin-bottom: 14px;
    }
    .stat {
      padding: 10px 12px;
    }
    .stat span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }
    .stat strong {
      font-size: 18px;
      font-weight: 600;
    }
    .kv {
      display: grid;
      grid-template-columns: 138px 1fr;
      gap: 7px 10px;
      font-size: 13px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .kv div:nth-child(odd) {
      color: var(--muted);
    }
    .note {
      margin: 12px 0 0;
      padding: 10px 12px;
      border-radius: 8px;
      background: #fff7ed;
      color: #8a3a0a;
      border: 1px solid #fed7aa;
      font-size: 13px;
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .panel { order: -1; }
    }
    @media (max-width: 560px) {
      main { width: min(100% - 20px, 1420px); }
      h1 { font-size: 20px; }
      .kv { grid-template-columns: 1fr; }
      .kv div:nth-child(even) { margin-bottom: 3px; }
    }
  </style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <p class="subtitle">基于裁剪 DEM 重新执行填洼、D8 流向、汇流累积，并按面积阈值提取河网。</p>
  <section class="grid">
    <div class="map">
      <div class="legend">
        <span><i class="line"></i>提取河网</span>
        <span><i class="dot terminal"></i>自动出口</span>
        <span><i class="dot"></i>徐畈挂接点</span>
      </div>
      <img src="__IMAGE_SRC__" alt="DEM 高程底图叠加提取河网">
    </div>
    <aside class="panel">
      <div class="stats" id="stats"></div>
      <div class="kv" id="details"></div>
      <p class="note">蓝色细线为 stream raster 单元，较粗蓝/紫线为按河源、汇流点和出口分段后的 reach。</p>
    </aside>
  </section>
</main>
<script>
const summary = __SUMMARY__;
function fmt(value, digits = 3) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return value.toFixed(digits);
  return String(value);
}
document.getElementById("stats").innerHTML = [
  ["河网阈值", `${fmt(summary.stream_threshold_km2, 2)} km²`],
  ["河段数量", summary.reach_count],
  ["河网长度", `${fmt(summary.extracted_stream_length_km, 2)} km`],
  ["河网栅格", summary.stream_cell_count],
  ["最大汇流面积", `${fmt(summary.max_accumulation_area_km2, 2)} km²`]
].map(([label, value]) => `<article class="stat"><span>${label}</span><strong>${value}</strong></article>`).join("");
const control = summary.control_point || {};
document.getElementById("details").innerHTML = [
  ["DEM 行列", `${summary.nrows} × ${summary.ncols}`],
  ["像元大小", `${fmt(summary.cell_size_m, 1)} m`],
  ["有效面积", `${fmt(summary.valid_area_km2, 2)} km²`],
  ["高程范围", `${fmt(summary.dem_min_m, 1)} - ${fmt(summary.dem_max_m, 1)} m`],
  ["出口河段", summary.terminal_reaches.join(", ")],
  ["徐畈挂接", `${control.control_id || "-"} -> ${control.reach_id || "-"}`],
  ["挂接距离", `${fmt(control.distance_m, 3)} m`],
  ["挂接点汇流面积", `${fmt(control.accumulation_area_km2, 4)} km²`]
].map(([label, value]) => `<div>${label}</div><div>${value}</div>`).join("");
</script>
</body>
</html>
""".replace("__TITLE__", html.escape(title))
            .replace("__IMAGE_SRC__", html.escape(image_src))
            .replace("__SUMMARY__", json_summary)
        )


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
