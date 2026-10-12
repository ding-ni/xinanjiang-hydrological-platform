"""Raster file adapters for static topology workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .vector_formats import PolygonShape, point_in_polygon_shapes


@dataclass(frozen=True)
class AsciiGrid:
    elevation: np.ndarray
    ncols: int
    nrows: int
    xllcorner: float
    yllcorner: float
    cellsize: float
    nodata: float | None

    @property
    def extent(self) -> dict[str, float]:
        return {
            "xmin": self.xllcorner,
            "ymin": self.yllcorner,
            "xmax": self.xllcorner + self.ncols * self.cellsize,
            "ymax": self.yllcorner + self.nrows * self.cellsize,
        }


def read_esri_ascii_grid(path: str | Path) -> AsciiGrid:
    """Read an ESRI ASCII grid into memory."""
    input_path = Path(path)
    header: dict[str, float] = {}

    with input_path.open("r", encoding="utf-8", errors="replace") as handle:
        for index in range(6):
            line = handle.readline()
            if not line:
                raise ValueError("ASCII grid header is incomplete")
            parts = line.strip().split()
            if len(parts) != 2:
                raise ValueError(f"invalid ASCII grid header line {index + 1}: {line!r}")
            header[parts[0].lower()] = float(parts[1])

    required = {"ncols", "nrows", "cellsize"}
    missing = sorted(required - set(header))
    if missing:
        raise ValueError(f"ASCII grid header missing required keys: {', '.join(missing)}")

    if "xllcorner" in header:
        xllcorner = header["xllcorner"]
    elif "xllcenter" in header:
        xllcorner = header["xllcenter"] - header["cellsize"] / 2.0
    else:
        raise ValueError("ASCII grid header missing xllcorner/xllcenter")

    if "yllcorner" in header:
        yllcorner = header["yllcorner"]
    elif "yllcenter" in header:
        yllcorner = header["yllcenter"] - header["cellsize"] / 2.0
    else:
        raise ValueError("ASCII grid header missing yllcorner/yllcenter")

    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    cellsize = float(header["cellsize"])
    nodata = header.get("nodata_value")

    elevation = np.loadtxt(input_path, dtype=float, skiprows=6)

    if elevation.shape != (nrows, ncols):
        raise ValueError(
            f"ASCII grid data shape {elevation.shape} does not match header {(nrows, ncols)}"
        )

    return AsciiGrid(
        elevation=elevation,
        ncols=ncols,
        nrows=nrows,
        xllcorner=xllcorner,
        yllcorner=yllcorner,
        cellsize=cellsize,
        nodata=nodata,
    )


def write_esri_ascii_grid(path: str | Path, grid: AsciiGrid) -> None:
    """Write an ESRI ASCII grid."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nodata = -9999.0 if grid.nodata is None else float(grid.nodata)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"ncols {grid.ncols}\n")
        handle.write(f"nrows {grid.nrows}\n")
        handle.write(f"xllcorner {grid.xllcorner:.12f}\n")
        handle.write(f"yllcorner {grid.yllcorner:.12f}\n")
        handle.write(f"cellsize {grid.cellsize:.12f}\n")
        handle.write(f"NODATA_value {nodata:g}\n")
        np.savetxt(handle, grid.elevation, fmt="%.6f")


def cell_center_xy(grid: AsciiGrid, row: int, col: int) -> tuple[float, float]:
    """Return the projected/geographic center coordinate of an ASCII grid cell."""

    x = grid.xllcorner + (col + 0.5) * grid.cellsize
    y = grid.yllcorner + (grid.nrows - row - 0.5) * grid.cellsize
    return float(x), float(y)


def mask_ascii_grid_by_polygons(grid: AsciiGrid, shapes: list[PolygonShape]) -> np.ndarray:
    """Rasterize polygon membership by testing ASCII cell centers."""

    mask = np.zeros((grid.nrows, grid.ncols), dtype=bool)
    for row in range(grid.nrows):
        y = grid.yllcorner + (grid.nrows - row - 0.5) * grid.cellsize
        for col in range(grid.ncols):
            x = grid.xllcorner + (col + 0.5) * grid.cellsize
            mask[row, col] = point_in_polygon_shapes(x, y, shapes)
    return mask


def clip_ascii_grid_by_polygons(
    grid: AsciiGrid,
    shapes: list[PolygonShape],
    output_nodata: float = -9999.0,
    crop: bool = True,
) -> tuple[AsciiGrid, dict[str, Any]]:
    """Clip an ASCII grid with polygon records using cell-center inclusion."""

    polygon_mask = mask_ascii_grid_by_polygons(grid, shapes)
    data_mask = np.isfinite(grid.elevation)
    if grid.nodata is not None:
        data_mask &= grid.elevation != grid.nodata
    keep_mask = polygon_mask & data_mask
    if not np.any(keep_mask):
        raise ValueError("boundary polygon does not overlap any valid grid cells")

    clipped_data = np.where(keep_mask, grid.elevation, float(output_nodata))
    row_min = 0
    row_max = grid.nrows - 1
    col_min = 0
    col_max = grid.ncols - 1
    if crop:
        rows, cols = np.where(keep_mask)
        row_min = int(rows.min())
        row_max = int(rows.max())
        col_min = int(cols.min())
        col_max = int(cols.max())
        clipped_data = clipped_data[row_min : row_max + 1, col_min : col_max + 1]

    nrows, ncols = clipped_data.shape
    xllcorner = grid.xllcorner + col_min * grid.cellsize
    yllcorner = grid.yllcorner + (grid.nrows - row_max - 1) * grid.cellsize
    clipped = AsciiGrid(
        elevation=np.asarray(clipped_data, dtype=float),
        ncols=int(ncols),
        nrows=int(nrows),
        xllcorner=float(xllcorner),
        yllcorner=float(yllcorner),
        cellsize=float(grid.cellsize),
        nodata=float(output_nodata),
    )
    cell_area_km2 = grid.cellsize * grid.cellsize / 1_000_000.0
    summary = {
        "source_ncols": grid.ncols,
        "source_nrows": grid.nrows,
        "output_ncols": clipped.ncols,
        "output_nrows": clipped.nrows,
        "source_extent": grid.extent,
        "output_extent": clipped.extent,
        "inside_cell_count": int(keep_mask.sum()),
        "inside_area_km2": float(keep_mask.sum() * cell_area_km2),
        "crop_window": {
            "row_min": row_min,
            "row_max": row_max,
            "col_min": col_min,
            "col_max": col_max,
        },
        "output_nodata": float(output_nodata),
    }
    return clipped, summary


def write_geotiff_from_ascii_grid(
    path: str | Path,
    grid: AsciiGrid,
    crs: str | None = None,
) -> dict[str, Any]:
    """Write a GeoTIFF from an ASCII grid when rasterio is available."""

    try:
        import rasterio
        from rasterio.transform import from_origin
    except ImportError as exc:
        raise RuntimeError(
            "rasterio is required to write GeoTIFF output. "
            "Run this command with a Python environment that has rasterio installed."
        ) from exc

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nodata = -9999.0 if grid.nodata is None else float(grid.nodata)
    transform = from_origin(
        grid.xllcorner,
        grid.yllcorner + grid.nrows * grid.cellsize,
        grid.cellsize,
        grid.cellsize,
    )
    profile = {
        "driver": "GTiff",
        "height": grid.nrows,
        "width": grid.ncols,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "lzw",
    }
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(np.asarray(grid.elevation, dtype=np.float32), 1)
    return {
        "geotiff_out": _json_path(output_path),
        "crs": crs,
        "ncols": grid.ncols,
        "nrows": grid.nrows,
        "nodata": nodata,
        "extent": grid.extent,
    }


def convert_arcgrid_with_rasterio(
    source: str | Path,
    geotiff_out: str | Path,
    ascii_out: str | Path | None = None,
    dst_crs: str | None = None,
    resolution: float | None = None,
) -> dict[str, Any]:
    """Convert an Arc/Info Grid with rasterio when that optional dependency exists."""
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.transform import array_bounds
        from rasterio.warp import calculate_default_transform, reproject
    except ImportError as exc:
        raise RuntimeError(
            "rasterio is required for Arc/Info Grid conversion. "
            "Run this command with a Python environment that has rasterio installed."
        ) from exc

    source_path = Path(source)
    geotiff_path = Path(geotiff_out)
    ascii_path = Path(ascii_out) if ascii_out else None
    geotiff_path.parent.mkdir(parents=True, exist_ok=True)
    if ascii_path:
        ascii_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(source_path) as src:
        data = src.read(1)
        profile = src.profile.copy()
        source_bounds = src.bounds
        source_crs = src.crs

        if dst_crs:
            dst_transform, dst_width, dst_height = calculate_default_transform(
                src.crs,
                dst_crs,
                src.width,
                src.height,
                *src.bounds,
                resolution=resolution,
            )
            dst_data = np.full(
                (dst_height, dst_width),
                src.nodata if src.nodata is not None else -9999,
                dtype=data.dtype,
            )
            reproject(
                source=rasterio.band(src, 1),
                destination=dst_data,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=src.nodata if src.nodata is not None else -9999,
                resampling=Resampling.bilinear,
            )
            profile.update(
                {
                    "driver": "GTiff",
                    "height": dst_height,
                    "width": dst_width,
                    "transform": dst_transform,
                    "crs": dst_crs,
                    "compress": "lzw",
                }
            )
            output_data = dst_data
            output_transform = dst_transform
            output_crs = dst_crs
        else:
            profile.update({"driver": "GTiff", "compress": "lzw"})
            output_data = data
            output_transform = src.transform
            output_crs = src.crs.to_string() if src.crs else None

        with rasterio.open(geotiff_path, "w", **profile) as dst:
            dst.write(output_data, 1)

        if ascii_path:
            ascii_profile = profile.copy()
            ascii_profile.update({"driver": "AAIGrid", "compress": None})
            ascii_profile.pop("tiled", None)
            ascii_profile.pop("blockxsize", None)
            ascii_profile.pop("blockysize", None)
            ascii_profile.pop("interleave", None)
            with rasterio.open(ascii_path, "w", **ascii_profile) as dst:
                dst.write(output_data, 1)

        height, width = output_data.shape
        bounds = array_bounds(height, width, output_transform)
        nodata = profile.get("nodata")
        valid = output_data if nodata is None else output_data[output_data != nodata]
        return {
            "source": _json_path(source_path),
            "source_driver": src.driver,
            "source_crs": source_crs.to_string() if source_crs else None,
            "source_bounds": list(source_bounds),
            "geotiff_out": _json_path(geotiff_path),
            "ascii_out": _json_path(ascii_path) if ascii_path else None,
            "output_crs": output_crs,
            "output_width": width,
            "output_height": height,
            "output_bounds": list(bounds),
            "output_nodata": nodata,
            "output_min": float(np.nanmin(valid)) if valid.size else None,
            "output_max": float(np.nanmax(valid)) if valid.size else None,
        }


def _json_path(path: str | Path) -> str:
    return str(path).replace("\\", "/")
