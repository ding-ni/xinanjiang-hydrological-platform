"""Export an interactive view of 30 m DEM-derived reach catchments."""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from xaj_routing.raster_formats import read_esri_ascii_grid  # noqa: E402
from xaj_routing.spatial import (  # noqa: E402
    assign_cells_to_reaches,
    build_grid_topology,
    valid_mask,
)


def _terrain_png(dem: np.ndarray, mask: np.ndarray) -> str:
    values = dem[mask]
    lo, hi = np.percentile(values, [2, 98])
    normalized = np.clip((dem - lo) / max(hi - lo, 1.0), 0.0, 1.0)

    dz_drow, dz_dcol = np.gradient(np.where(mask, dem, np.nanmedian(values)))
    shade = np.clip(0.62 - 0.36 * dz_dcol + 0.28 * dz_drow, 0.25, 1.0)
    stops = np.asarray(
        [
            [38, 96, 82],
            [109, 145, 91],
            [189, 178, 121],
            [174, 151, 117],
            [235, 235, 226],
        ],
        dtype=float,
    )
    scaled = normalized * (len(stops) - 1)
    lower = np.floor(scaled).astype(int)
    upper = np.minimum(lower + 1, len(stops) - 1)
    fraction = (scaled - lower)[..., None]
    rgb = stops[lower] * (1.0 - fraction) + stops[upper] * fraction
    rgb *= shade[..., None]
    rgba = np.concatenate([rgb, np.where(mask, 255, 0)[..., None]], axis=2).astype("uint8")

    stream = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(stream, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _catchment_geometries(code_grid: np.ndarray, reach_ids: list[str]) -> dict[str, dict]:
    polygons: dict[int, list] = defaultdict(list)
    for geom, value in shapes(
        code_grid.astype("int16"),
        mask=code_grid > 0,
        transform=Affine.identity(),
        connectivity=8,
    ):
        polygons[int(value)].append(shape(geom))
    output = {}
    for code, parts in polygons.items():
        geometry = unary_union(parts).simplify(0.08, preserve_topology=True)
        output[reach_ids[code - 1]] = mapping(geometry)
    return output


def _upstream_areas(reaches, local_area_by_reach: dict[str, float]) -> dict[str, float]:
    upstream: dict[str, list[str]] = defaultdict(list)
    for reach in reaches:
        if reach.downstream_reach_id:
            upstream[reach.downstream_reach_id].append(reach.reach_id)

    memo: dict[str, float] = {}

    def total(reach_id: str) -> float:
        if reach_id not in memo:
            memo[reach_id] = local_area_by_reach[reach_id] + sum(
                total(item) for item in upstream.get(reach_id, [])
            )
        return memo[reach_id]

    return {reach.reach_id: total(reach.reach_id) for reach in reaches}


def build_payload(dem_path: Path, threshold_km2: float) -> dict:
    grid = read_esri_ascii_grid(dem_path)
    mask = valid_mask(grid.elevation, grid.nodata)
    topology = build_grid_topology(
        grid.elevation,
        grid.cellsize,
        threshold_km2,
        basin_id="nan_an",
        spatial_version="nan_an_30m_catchment_visual",
        nodata=grid.nodata,
    )
    assignments = assign_cells_to_reaches(
        topology.flow_dir,
        topology.stream_raster,
        topology.reaches,
        mask,
    )
    reach_ids = [reach.reach_id for reach in topology.reaches]
    code_by_reach = {reach_id: index + 1 for index, reach_id in enumerate(reach_ids)}
    code_grid = np.zeros(grid.elevation.shape, dtype="int16")
    unassigned = np.zeros(grid.elevation.shape, dtype=bool)
    counts = defaultdict(int)
    for (row, col), reach_id in assignments.items():
        if reach_id is None:
            unassigned[row, col] = True
        else:
            code_grid[row, col] = code_by_reach[reach_id]
            counts[reach_id] += 1

    cell_area_km2 = grid.cellsize * grid.cellsize / 1_000_000.0
    local_area = {reach_id: counts[reach_id] * cell_area_km2 for reach_id in reach_ids}
    cumulative_area = _upstream_areas(topology.reaches, local_area)
    geometries = _catchment_geometries(code_grid, reach_ids)
    unit_by_reach = {unit["reach_id"]: unit for unit in topology.units}

    catchments = []
    for reach in topology.reaches:
        unit = unit_by_reach[reach.reach_id]
        cells = [[col + 0.5, row + 0.5] for row, col in reach.cells]
        catchments.append(
            {
                "reach_id": reach.reach_id,
                "unit_id": unit["unit_id"],
                "downstream_reach_id": reach.downstream_reach_id,
                "local_area_km2": round(local_area[reach.reach_id], 4),
                "cumulative_area_km2": round(cumulative_area[reach.reach_id], 4),
                "cell_count": counts[reach.reach_id],
                "length_km": round(reach.length_km, 3),
                "mean_elevation_m": round(float(unit["elevation_mean"]), 1),
                "mean_slope_deg": round(float(unit["slope_mean"]), 2),
                "outlet": [reach.end_cell[1] + 0.5, reach.end_cell[0] + 0.5],
                "stream_cells": cells,
                "geometry": geometries[reach.reach_id],
                "is_control": reach.reach_id == "R0020",
                "is_outlet": reach.downstream_reach_id is None,
            }
        )

    areas = np.asarray(list(local_area.values()))
    return {
        "width": grid.ncols,
        "height": grid.nrows,
        "cell_size_m": grid.cellsize,
        "stream_threshold_km2": threshold_km2,
        "terrain_png": _terrain_png(grid.elevation, mask),
        "catchments": catchments,
        "unassigned_cells": [[col + 0.5, row + 0.5] for row, col in zip(*np.where(unassigned))],
        "summary": {
            "valid_cells": int(mask.sum()),
            "assigned_cells": int((code_grid > 0).sum()),
            "unassigned_cells": int(unassigned.sum()),
            "assigned_area_km2": round(float((code_grid > 0).sum() * cell_area_km2), 4),
            "unassigned_area_km2": round(float(unassigned.sum() * cell_area_km2), 4),
            "catchment_count": len(catchments),
            "minimum_local_area_km2": round(float(areas.min()), 4),
            "median_local_area_km2": round(float(np.median(areas)), 4),
            "maximum_local_area_km2": round(float(areas.max()), 4),
        },
    }


def write_html(payload: dict, output: Path) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>南安溪30米DEM河段集水区</title>
<style>
:root{color-scheme:light;background:#f3f5f4;color:#17231f;font-family:"Microsoft YaHei",sans-serif}*{box-sizing:border-box}body{margin:0}main{height:100vh;display:grid;grid-template-rows:auto 1fr}.top{padding:14px 18px;background:#fff;border-bottom:1px solid #ccd5d1;display:flex;align-items:end;justify-content:space-between;gap:16px}.top h1{font-size:20px;margin:0 0 4px}.top p{margin:0;color:#5a6862;font-size:13px}.metrics{display:flex;gap:18px;white-space:nowrap}.metric span{display:block;color:#65736d;font-size:11px}.metric b{font-size:16px}.layout{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 330px}.map-panel{min-width:0;position:relative;background:#dce4df;overflow:hidden}.map-panel svg{width:100%;height:100%;display:block}.side{padding:16px;background:#fff;border-left:1px solid #ccd5d1;overflow:auto}.side h2{font-size:16px;margin:0 0 12px}.toolbar{position:absolute;z-index:2;left:12px;top:12px;background:rgba(255,255,255,.94);border:1px solid #bdc9c3;padding:8px 10px;display:flex;gap:12px;font-size:12px}.toolbar label{display:flex;align-items:center;gap:4px}.catchment{stroke:#fafcfb;stroke-width:.55;vector-effect:non-scaling-stroke;fill-opacity:.62;cursor:pointer}.catchment:hover,.catchment.selected{stroke:#111b17;stroke-width:2;fill-opacity:.78}.stream{fill:none;stroke:#1976a3;stroke-width:1.15;vector-effect:non-scaling-stroke;pointer-events:none}.stream.control{stroke:#e36a20;stroke-width:2.3}.stream.outlet{stroke:#9b3fa8;stroke-width:2.3}.outlet-point{stroke:#fff;stroke-width:1;vector-effect:non-scaling-stroke}.unassigned{fill:#e43d30;stroke:none}.legend{position:absolute;left:12px;bottom:12px;background:rgba(255,255,255,.94);border:1px solid #bdc9c3;padding:8px 10px;font-size:11px;display:flex;gap:12px;flex-wrap:wrap}.swatch{display:inline-block;width:14px;height:3px;margin-right:4px;vertical-align:middle}.kv{display:grid;grid-template-columns:1fr auto;gap:9px 12px;font-size:13px}.kv span{color:#65736d}.kv b{text-align:right}.note{margin-top:16px;padding-top:14px;border-top:1px solid #d6ded9;color:#56645e;font-size:12px;line-height:1.65}.rank{margin-top:18px}.rank h3{font-size:13px;margin:0 0 8px}.rank button{width:100%;display:grid;grid-template-columns:1fr auto;border:0;border-top:1px solid #e1e6e3;background:none;padding:7px 2px;text-align:left;cursor:pointer;color:inherit}.rank button:hover{background:#f3f6f4}@media(max-width:760px){main{height:auto;min-height:100vh}.top{align-items:start;flex-direction:column}.metrics{white-space:normal;flex-wrap:wrap}.layout{grid-template-columns:1fr}.map-panel{height:62vh}.side{border-left:0;border-top:1px solid #ccd5d1}.toolbar{max-width:calc(100% - 24px);flex-wrap:wrap}}
</style></head><body><main><header class="top"><div><h1>南安溪 30 m DEM 河段局地集水区</h1><p>颜色表示 U_Rxxxx 局地侧向集水区，蓝线表示对应河段</p></div><div class="metrics" id="metrics"></div></header><div class="layout"><section class="map-panel"><div class="toolbar"><label><input id="demToggle" type="checkbox" checked>DEM</label><label><input id="catchToggle" type="checkbox" checked>集水区</label><label><input id="streamToggle" type="checkbox" checked>河段</label><label><input id="unassignedToggle" type="checkbox" checked>未归属像元</label></div><svg id="map" role="img" aria-label="30米DEM河段局地集水区地图"></svg><div class="legend"><span><i class="swatch" style="background:#1976a3"></i>河段</span><span><i class="swatch" style="background:#e36a20"></i>徐畈 R0020</span><span><i class="swatch" style="background:#9b3fa8"></i>总出口 R0015</span><span><i class="swatch" style="background:#e43d30"></i>未归属30 m像元</span></div></section><aside class="side"><h2 id="title">集水区详情</h2><div id="detail"></div><div class="rank"><h3>局地面积排序</h3><div id="rank"></div></div></aside></div></main><script>
const P=__PAYLOAD__;const svg=document.getElementById('map');const NS='http://www.w3.org/2000/svg';let selected=P.catchments[0].reach_id;const colors=['#5e8f72','#87a45f','#d2aa52','#cf795a','#7396a6','#ae7fa0','#8a9c73'];
function node(name,attrs,parent=svg){const e=document.createElementNS(NS,name);Object.entries(attrs||{}).forEach(([k,v])=>e.setAttribute(k,v));parent.appendChild(e);return e}function pathGeom(g){if(g.type==='Polygon')return g.coordinates.map(r=>'M'+r.map(p=>p.join(',')).join('L')+'Z').join('');return g.coordinates.map(poly=>poly.map(r=>'M'+r.map(p=>p.join(',')).join('L')+'Z').join('')).join('')}
function render(){svg.innerHTML='';svg.setAttribute('viewBox',`0 0 ${P.width} ${P.height}`);svg.setAttribute('preserveAspectRatio','xMidYMid meet');if(document.getElementById('demToggle').checked)node('image',{href:P.terrain_png,x:0,y:0,width:P.width,height:P.height,preserveAspectRatio:'none'});const cg=node('g',{id:'catchments'});P.catchments.forEach((c,i)=>{const p=node('path',{d:pathGeom(c.geometry),fill:colors[i%colors.length],class:'catchment '+(selected===c.reach_id?'selected':'')},cg);p.onclick=()=>select(c.reach_id)});if(!document.getElementById('catchToggle').checked)cg.style.display='none';const sg=node('g',{});P.catchments.forEach(c=>{node('polyline',{points:c.stream_cells.map(p=>p.join(',')).join(' '),class:'stream '+(c.is_control?'control ':'')+(c.is_outlet?'outlet':'')},sg);node('circle',{cx:c.outlet[0],cy:c.outlet[1],r:c.is_control||c.is_outlet?2.4:1.1,fill:c.is_control?'#e36a20':c.is_outlet?'#9b3fa8':'#1976a3',class:'outlet-point'},sg)});if(!document.getElementById('streamToggle').checked)sg.style.display='none';const ug=node('g',{});P.unassigned_cells.forEach(p=>node('rect',{x:p[0]-.6,y:p[1]-.6,width:1.2,height:1.2,class:'unassigned'},ug));if(!document.getElementById('unassignedToggle').checked)ug.style.display='none'}
function select(id){selected=id;const c=P.catchments.find(x=>x.reach_id===id);document.getElementById('title').textContent=`${c.unit_id} / ${c.reach_id}`;document.getElementById('detail').innerHTML=`<div class="kv"><span>局地侧向集水面积</span><b>${c.local_area_km2.toFixed(4)} km²</b><span>上游累计控制面积</span><b>${c.cumulative_area_km2.toFixed(4)} km²</b><span>30 m 像元数</span><b>${c.cell_count}</b><span>对应河段长度</span><b>${c.length_km.toFixed(3)} km</b><span>平均高程</span><b>${c.mean_elevation_m.toFixed(1)} m</b><span>平均坡度</span><b>${c.mean_slope_deg.toFixed(2)}°</b><span>下游河段</span><b>${c.downstream_reach_id||'流域总出口'}</b></div><div class="note">局地面积只包括直接进入该河段的坡面像元；累计面积还包括所有上游河段及其局地集水区。空间边界由30 m像元构成，理论栅格精度为30 m，实际水文位置精度还受原始DEM、填洼、D8和1 km²河网阈值影响。</div>`;render()}
const s=P.summary;document.getElementById('metrics').innerHTML=[['集水区',s.catchment_count],['局地面积范围',`${s.minimum_local_area_km2}–${s.maximum_local_area_km2} km²`],['已归属面积',`${s.assigned_area_km2} km²`],['未归属',`${s.unassigned_area_km2} km²`]].map(x=>`<div class="metric"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');document.getElementById('rank').innerHTML=[...P.catchments].sort((a,b)=>b.local_area_km2-a.local_area_km2).map(c=>`<button type="button" data-id="${c.reach_id}"><span>${c.reach_id}</span><b>${c.local_area_km2.toFixed(4)} km²</b></button>`).join('');document.getElementById('rank').onclick=e=>{const b=e.target.closest('button');if(b)select(b.dataset.id)};['demToggle','catchToggle','streamToggle','unassignedToggle'].forEach(id=>document.getElementById(id).onchange=render);select(selected);
</script></body></html>'''.replace("__PAYLOAD__", data)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dem",
        type=Path,
        default=ROOT / "data_interface/spatial/nan_an_dem_utm50_30m_clipped.asc",
    )
    parser.add_argument("--threshold-km2", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "data_products/visualizations/nan_an/reach_catchments/nan_an_reach_catchments.html",
    )
    parser.add_argument("--mirror", type=Path, default=None)
    args = parser.parse_args()
    payload = build_payload(args.dem.resolve(), args.threshold_km2)
    write_html(payload, args.output.resolve())
    if args.mirror:
        args.mirror.resolve().parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output.resolve(), args.mirror.resolve())
    print(json.dumps({"output": str(args.output.resolve()), **payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
