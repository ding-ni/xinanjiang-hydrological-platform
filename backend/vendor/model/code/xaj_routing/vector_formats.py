"""Lightweight vector file adapters for spatial preprocessing.

The project intentionally keeps GDAL/Fiona out of the core backend runtime.
This module implements the small subset of the ESRI Shapefile format needed for
basin boundary polygons, plus optional CRS transformation through pyproj when it
is available.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolygonShape:
    """A polygon shapefile record represented as one or more rings."""

    parts: tuple[tuple[tuple[float, float], ...], ...]
    bbox: tuple[float, float, float, float]
    attributes: dict[str, Any]


def read_shapefile_polygons(path: str | Path, read_attributes: bool = True) -> list[PolygonShape]:
    """Read Polygon records from a shapefile.

    Supported shape types are Polygon, PolygonZ, and PolygonM.  The function is
    deliberately narrow: it is for basin boundaries, not a general GIS library.
    """

    shp_path = Path(path)
    with shp_path.open("rb") as handle:
        header = handle.read(100)
        if len(header) != 100:
            raise ValueError("invalid shapefile header")
        file_code = struct.unpack(">i", header[:4])[0]
        if file_code != 9994:
            raise ValueError(f"invalid shapefile file code: {file_code}")
        shape_type = struct.unpack("<i", header[32:36])[0]
        if shape_type not in (5, 15, 25):
            raise ValueError(f"unsupported shapefile type {shape_type}; expected polygon")

        shapes: list[PolygonShape] = []
        while True:
            record_header = handle.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise ValueError("truncated shapefile record header")
            _, content_words = struct.unpack(">2i", record_header)
            content = handle.read(content_words * 2)
            if len(content) != content_words * 2:
                raise ValueError("truncated shapefile record content")
            item = _parse_polygon_record(content)
            if item is not None:
                shapes.append(item)

    if read_attributes:
        attributes = read_dbf_records(shp_path.with_suffix(".dbf"))
        if attributes and len(attributes) == len(shapes):
            shapes = [
                replace(shape, attributes=attributes[index])
                for index, shape in enumerate(shapes)
            ]
    return shapes


def _parse_polygon_record(content: bytes) -> PolygonShape | None:
    shape_type = struct.unpack("<i", content[:4])[0]
    if shape_type == 0:
        return None
    if shape_type not in (5, 15, 25):
        raise ValueError(f"unsupported record shape type {shape_type}")
    if len(content) < 44:
        raise ValueError("truncated polygon record")

    xmin, ymin, xmax, ymax = struct.unpack("<4d", content[4:36])
    num_parts, num_points = struct.unpack("<2i", content[36:44])
    parts_offset = 44
    points_offset = parts_offset + 4 * num_parts
    points_end = points_offset + 16 * num_points
    if len(content) < points_end:
        raise ValueError("truncated polygon points")

    part_starts = list(struct.unpack(f"<{num_parts}i", content[parts_offset:points_offset]))
    raw_points = struct.unpack(f"<{num_points * 2}d", content[points_offset:points_end])
    points = [
        (float(raw_points[index]), float(raw_points[index + 1]))
        for index in range(0, len(raw_points), 2)
    ]

    rings: list[tuple[tuple[float, float], ...]] = []
    for index, start in enumerate(part_starts):
        end = part_starts[index + 1] if index + 1 < len(part_starts) else len(points)
        ring = tuple(points[start:end])
        if len(ring) >= 4:
            rings.append(ring)

    return PolygonShape(parts=tuple(rings), bbox=(xmin, ymin, xmax, ymax), attributes={})


def read_dbf_records(path: str | Path) -> list[dict[str, Any]]:
    """Read records from a dBASE file next to a shapefile."""

    dbf_path = Path(path)
    if not dbf_path.exists():
        return []

    encoding = _read_cpg_encoding(dbf_path.with_suffix(".cpg"))
    with dbf_path.open("rb") as handle:
        header = handle.read(32)
        if len(header) != 32:
            raise ValueError("invalid DBF header")
        num_records = struct.unpack("<I", header[4:8])[0]
        header_length = struct.unpack("<H", header[8:10])[0]
        record_length = struct.unpack("<H", header[10:12])[0]

        field_bytes = handle.read(header_length - 32)
        fields = _parse_dbf_fields(field_bytes, encoding)

        records: list[dict[str, Any]] = []
        for _ in range(num_records):
            record = handle.read(record_length)
            if len(record) != record_length:
                raise ValueError("truncated DBF record")
            if record[:1] == b"*":
                continue
            values: dict[str, Any] = {}
            offset = 1
            for name, field_type, length, decimals in fields:
                raw = record[offset : offset + length]
                values[name] = _parse_dbf_value(raw, field_type, decimals, encoding)
                offset += length
            records.append(values)
    return records


def _read_cpg_encoding(path: Path) -> str:
    if not path.exists():
        return "gbk"
    value = path.read_text(encoding="ascii", errors="ignore").strip()
    if not value:
        return "gbk"
    if value.isdigit():
        return f"cp{value}"
    return value


def _parse_dbf_fields(
    field_bytes: bytes,
    encoding: str,
) -> list[tuple[str, str, int, int]]:
    fields: list[tuple[str, str, int, int]] = []
    for offset in range(0, len(field_bytes), 32):
        descriptor = field_bytes[offset : offset + 32]
        if not descriptor or descriptor[0] == 0x0D:
            break
        name = descriptor[:11].split(b"\x00", 1)[0].decode(encoding, errors="replace").strip()
        field_type = chr(descriptor[11])
        length = int(descriptor[16])
        decimals = int(descriptor[17])
        fields.append((name, field_type, length, decimals))
    return fields


def _parse_dbf_value(raw: bytes, field_type: str, decimals: int, encoding: str) -> Any:
    text = raw.decode(encoding, errors="replace").strip()
    if text == "":
        return None
    if field_type in ("N", "F"):
        try:
            return float(text) if decimals > 0 or "." in text else int(text)
        except ValueError:
            return text
    if field_type == "L":
        return text.upper() in ("Y", "T", "1")
    return text


def transform_polygon_shapes(
    shapes: list[PolygonShape],
    src_crs: str | None,
    dst_crs: str | None,
) -> list[PolygonShape]:
    """Transform polygon coordinates with pyproj when source and target CRS differ."""

    if not src_crs or not dst_crs or src_crs.upper() == dst_crs.upper():
        return shapes
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError(
            "pyproj is required when boundary CRS differs from grid CRS. "
            "Run this command with a Python environment that has pyproj installed."
        ) from exc

    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    transformed: list[PolygonShape] = []
    for shape in shapes:
        rings: list[tuple[tuple[float, float], ...]] = []
        xs: list[float] = []
        ys: list[float] = []
        for ring in shape.parts:
            new_ring = tuple(
                (float(x_out), float(y_out))
                for x_out, y_out in (transformer.transform(x, y) for x, y in ring)
            )
            rings.append(new_ring)
            xs.extend(x for x, _ in new_ring)
            ys.extend(y for _, y in new_ring)
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs and ys else shape.bbox
        transformed.append(
            PolygonShape(parts=tuple(rings), bbox=bbox, attributes=shape.attributes)
        )
    return transformed


def point_in_polygon_shapes(x: float, y: float, shapes: list[PolygonShape]) -> bool:
    """Return True when a point lies inside any polygon record.

    Rings are evaluated with an even-odd rule, which handles ordinary outer-ring
    and hole structures used by basin boundary shapefiles.
    """

    for shape in shapes:
        xmin, ymin, xmax, ymax = shape.bbox
        if x < xmin or x > xmax or y < ymin or y > ymax:
            continue
        inside_shape = False
        for ring in shape.parts:
            if _point_in_ring(x, y, ring):
                inside_shape = not inside_shape
        if inside_shape:
            return True
    return False


def _point_in_ring(x: float, y: float, ring: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    count = len(ring)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = (yi > y) != (yj > y)
        if intersects:
            x_intersection = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x <= x_intersection:
                inside = not inside
        j = i
    return inside
