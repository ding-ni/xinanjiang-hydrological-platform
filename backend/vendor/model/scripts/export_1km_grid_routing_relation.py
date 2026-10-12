# -*- coding: utf-8 -*-
"""Export the 1 km runoff-grid routing relation used by the integrated model."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path

import rasterio


NEIGHBOURS = (
    (-1, -1, math.sqrt(2)), (-1, 0, 1.0), (-1, 1, math.sqrt(2)),
    (0, -1, 1.0), (0, 1, 1.0),
    (1, -1, math.sqrt(2)), (1, 0, 1.0), (1, 1, math.sqrt(2)),
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(dem_path: Path, grid_static_path: Path, grid_geojson_path: Path, mapping_path: Path, topology_path: Path):
    with rasterio.open(dem_path) as dataset:
        dem = dataset.read(1)
        nodata = dataset.nodata if dataset.nodata is not None else -9999

    grid_static = {row["unit_id"]: row for row in csv.DictReader(grid_static_path.open(encoding="utf-8-sig"))}
    features = read_json(grid_geojson_path)["features"]
    centers = {}
    polygons = {}
    for feature in features:
        props = feature["properties"]
        grid_id = next(grid_id for grid_id, row in grid_static.items() if int(row["source_row"]) == int(props["row"]) and int(row["source_col"]) == int(props["col"]))
        ring = feature["geometry"]["coordinates"][0]
        centers[grid_id] = [sum(point[0] for point in ring[:-1]) / 4, sum(point[1] for point in ring[:-1]) / 4]
        polygons[grid_id] = ring

    rows = {int(row["source_row"]): row for row in grid_static.values()}
    by_cell = {(int(row["source_row"]), int(row["source_col"])): grid_id for grid_id, row in grid_static.items()}
    d8 = {}
    for grid_id, row in grid_static.items():
        r, c = int(row["source_row"]), int(row["source_col"])
        z = float(row["elevation_m"])
        best = None
        for dr, dc, distance in NEIGHBOURS:
            neighbour_id = by_cell.get((r + dr, c + dc))
            if neighbour_id is None:
                continue
            neighbour_z = float(grid_static[neighbour_id]["elevation_m"])
            slope = (z - neighbour_z) / distance
            if slope > 0 and (best is None or slope > best[0]):
                best = (slope, neighbour_id)
        d8[grid_id] = best[1] if best else None

    topology = read_json(topology_path)
    reach_downstream = {reach["reach_id"]: reach.get("downstream_reach_id") for reach in topology["reaches"]}
    mapping = {}
    for row in csv.DictReader(mapping_path.open(encoding="utf-8-sig")):
        item = mapping.setdefault(row["grid_id"], {"links": [], "total_fraction": 0.0})
        fraction = float(row["source_fraction"])
        item["links"].append({"reach_id": row["reach_id"], "unit_id": row["unit_id"], "area_km2": float(row["adjusted_overlap_area_km2"]), "fraction": fraction, "method": row["mapping_method"]})
        item["total_fraction"] += fraction

    grid_rows = []
    for grid_id, row in sorted(grid_static.items()):
        grid_rows.append({"id": grid_id, "row": int(row["source_row"]), "col": int(row["source_col"]), "elevation_m": float(row["elevation_m"]), "area_km2": float(row["area_km2"]), "center": centers[grid_id], "polygon": polygons[grid_id], "downstream_grid_id": d8[grid_id], "mapping": mapping.get(grid_id, {"links": [], "total_fraction": 0.0})})

    grid_ids = {row["id"] for row in grid_rows}
    network_rows = [{"grid_id": row["id"], "downstream_grid_id": row["downstream_grid_id"], "status": "VALID" if row["downstream_grid_id"] in grid_ids else "LOCAL_OUTLET"} for row in grid_rows]
    return {"summary": {"grid_count": len(grid_rows), "mapping_rows": sum(len(row["mapping"]["links"]) for row in grid_rows), "reach_count": len(topology["reaches"]), "d8_grid_edges": sum(row["downstream_grid_id"] is not None for row in grid_rows), "fallback_grid_count": sum(any(link["method"] == "NEAREST_FALLBACK" for link in row["mapping"]["links"]) for row in grid_rows), "note": "D8 关系由 1 km DEM 高程派生；网格到河段关系沿用 D8_FLOW_PATH_AREA_OVERLAP 面积权重。"}, "grids": grid_rows, "network_rows": network_rows, "reaches": [{"id": reach["reach_id"], "downstream": reach.get("downstream_reach_id"), "length_km": reach.get("length_km", 0), "is_control": reach["reach_id"] == "R0020", "is_terminal": reach.get("downstream_reach_id") is None} for reach in topology["reaches"]], "reach_downstream": reach_downstream}


def build_html(payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    reach_step = 60 / max(len(payload["reaches"]) - 1, 1)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>南安溪 1 km 网格汇流关系</title><style>
:root{{--bg:#f3f6f5;--panel:#fff;--ink:#172b33;--muted:#687b80;--line:#d5e0dd;--grid:#4d9a94;--river:#216c88;--control:#d36542;--outlet:#745f91;--arrow:#345f6e}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 "Microsoft YaHei",Arial,sans-serif}}main{{max-width:1500px;margin:auto;padding:22px}}h1{{margin:0 0 4px;font-size:23px}}.sub{{margin:0 0 15px;color:var(--muted)}}.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin-bottom:12px}}.stat,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:7px}}.stat{{padding:11px 13px}}.stat span{{display:block;color:var(--muted);font-size:11px}}.stat strong{{display:block;margin-top:4px;font-size:18px}}.layout{{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(330px,.7fr);gap:12px}}.panel{{overflow:hidden}}.toolbar{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:11px 13px;border-bottom:1px solid var(--line);color:var(--muted);font-size:12px}}.toolbar label{{margin-left:10px}}svg{{display:block;width:100%;height:auto;background:#f8fbfa}}.grid-cell{{fill:#79b8b1;fill-opacity:.55;stroke:#387f7a;stroke-width:.7;cursor:pointer}}.grid-cell.selected{{fill:#e1a64d;fill-opacity:.9;stroke:#9b641f;stroke-width:2}}.grid-cell.fallback{{fill:#d36542;fill-opacity:.8}}.d8{{stroke:var(--arrow);stroke-width:1.4;opacity:.55;fill:none;marker-end:url(#d8arrow)}}.map-reach{{stroke:var(--river);stroke-width:2;opacity:.28;fill:none}}.map-link{{stroke:var(--river);stroke-width:1;opacity:.16;fill:none}}.map-link.control{{stroke:var(--control);opacity:.65}}.map-link.fallback{{stroke:var(--control);stroke-dasharray:3 2;opacity:.9}}.reach-node{{fill:var(--river);stroke:#fff;stroke-width:1;cursor:pointer}}.reach-node.control{{fill:var(--control)}}.reach-node.outlet{{fill:var(--outlet)}}.label{{font-size:10px;fill:var(--ink);paint-order:stroke;stroke:#fff;stroke-width:3px;pointer-events:none}}.side{{padding:14px}}.side h2{{margin:0 0 8px;font-size:17px}}.side p{{color:var(--muted);font-size:12px}}.select{{width:100%;padding:8px;border:1px solid var(--line);border-radius:5px;background:white;color:var(--ink)}}.kv{{display:grid;grid-template-columns:125px 1fr;gap:7px 9px;margin-top:13px;font-size:12px}}.kv b{{font-weight:600;overflow-wrap:anywhere}}.list{{display:grid;gap:6px;margin:14px 0 0;padding:0;list-style:none}}.list li{{padding:8px;border:1px solid var(--line);border-radius:5px;background:#f8fbfa}}.legend{{display:flex;gap:12px;flex-wrap:wrap;padding:9px 13px;color:var(--muted);font-size:11px;border-top:1px solid var(--line)}}.legend i{{display:inline-block;width:20px;height:3px;margin-right:5px;vertical-align:middle;background:var(--river)}}.legend .box{{width:11px;height:11px;background:var(--grid);border:1px solid #387f7a}}.legend .dash{{height:2px;background:var(--control);border-top:1px dashed var(--control)}}@media(max-width:900px){{.layout{{grid-template-columns:1fr}}.stats{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:520px){{main{{padding:12px}}.stats{{grid-template-columns:1fr 1fr}}h1{{font-size:19px}}}}
</style></head><body><main><h1>南安溪 1 km 产流网格汇流关系</h1><p class="sub">网格之间：1 km DEM 派生 D8 下游关系；网格到河段：D8 流向路径面积权重映射。当前汇流输入的业务单元应使用 NX-xxx。</p><section class="stats" id="stats"></section><section class="layout"><div class="panel"><div class="toolbar"><span>左侧是 1 km 网格空间关系；右侧河段节点表示网格最终挂接的 30 m 河段</span><span><label><input id="showD8" type="checkbox" checked> 网格 D8</label><label><input id="showLinks" type="checkbox" checked> 网格→河段</label></span></div><svg id="map" viewBox="0 0 1000 720" role="img" aria-label="1 km网格和河段汇流关系"></svg><div class="legend"><span><i class="box"></i>1 km 汇流网格 NX-xxx</span><span><i></i>网格→河段面积权重</span><span><i class="dash"></i>最近邻兜底</span><span>橙色河段：徐畈 R0020</span><span>紫色河段：总出口 R0015</span></div></div><aside class="panel side"><h2>网格汇流详情</h2><p>点击网格查看其 D8 下游网格和挂接河段；点击河段节点查看其上游网格。</p><select class="select" id="gridSelect" aria-label="选择1公里网格"></select><div id="detail"></div></aside></section></main><script>const payload={data};const svg=document.getElementById('map');const W=1000,H=720;const bounds={{minX:Math.min(...payload.grids.map(g=>g.center[0])),maxX:Math.max(...payload.grids.map(g=>g.center[0])),minY:Math.min(...payload.grids.map(g=>g.center[1])),maxY:Math.max(...payload.grids.map(g=>g.center[1]))}};const reachY=new Map(payload.reaches.map((r,i)=>[r.id,70+i*{reach_step}]));let selected=payload.grids[0]?.id;function x(lon){{return 30+(lon-bounds.minX)/Math.max(bounds.maxX-bounds.minX,.0001)*650}}function y(lat){{return 650-(lat-bounds.minY)/Math.max(bounds.maxY-bounds.minY,.0001)*590}}function el(n,a,p){{const z=document.createElementNS('http://www.w3.org/2000/svg',n);Object.entries(a||{{}}).forEach(([k,v])=>z.setAttribute(k,v));if(p)p.appendChild(z);return z}}function fmt(v,d=3){{return Number.isFinite(Number(v))?Number(v).toFixed(d):'-'}}function render(){{svg.innerHTML='';const defs=el('defs',{{}},svg);const marker=el('marker',{{id:'d8arrow',markerWidth:6,markerHeight:6,refX:5,refY:3,orient:'auto'}},defs);el('path',{{d:'M0,0 L6,3 L0,6 z',fill:'var(--arrow)'}},marker);el('text',{{x:715,y:30,class:'label'}},svg).textContent='30 m 河段挂接节点';payload.reaches.forEach(r=>{{const yy=reachY.get(r.id);const color=r.is_control?'var(--control)':r.is_terminal?'var(--outlet)':'var(--river)';el('circle',{{cx:750,cy:yy,r:r.is_control||r.is_terminal?6:4,class:'reach-node '+(r.is_control?'control ':'')+(r.is_terminal?'outlet':'')}},svg).addEventListener('click',()=>showReach(r.id));const t=el('text',{{x:760,y:yy+3,class:'label'}},svg);t.textContent=r.id+(r.is_control?' · 徐畈':r.is_terminal?' · 总出口':'')}});const showD8=document.getElementById('showD8').checked;const showLinks=document.getElementById('showLinks').checked;payload.grids.forEach(g=>{{const points=g.polygon.map(p=>[x(p[0]),y(p[1])]);const poly=el('polygon',{{points:points.map(p=>p.join(',')).join(' '),class:'grid-cell '+(g.id===selected?'selected ':'')+(g.mapping.links.some(l=>l.method==='NEAREST_FALLBACK')?'fallback':'')}},svg);poly.addEventListener('click',()=>{{selected=g.id;document.getElementById('gridSelect').value=g.id;showGrid(g.id)}});if(showD8&&g.downstream_grid_id){{const d=payload.grids.find(q=>q.id===g.downstream_grid_id);if(d)el('line',{{x1:x(g.center[0]),y1:y(g.center[1]),x2:x(d.center[0]),y2:y(d.center[1]),class:'d8'}},svg)}}if(showLinks)g.mapping.links.forEach(l=>{{const yy=reachY.get(l.reach_id);if(yy===undefined)return;el('line',{{x1:x(g.center[0])+10,y1:y(g.center[1]),x2:750,y2:yy,class:'map-link '+(l.reach_id==='R0020'?'control ':'')+(l.method==='NEAREST_FALLBACK'?'fallback':'')}},svg)}});const lab=el('text',{{x:x(g.center[0])+2,y:y(g.center[1])+3,class:'label'}},svg);lab.textContent=g.id.replace('NX-','')}})}}function showGrid(id){{const g=payload.grids.find(q=>q.id===id);if(!g)return;selected=id;document.getElementById('gridSelect').value=id;const links=g.mapping.links;document.getElementById('detail').innerHTML='<div class="kv"><span>网格编号</span><b>'+g.id+'</b><span>面积</span><b>'+fmt(g.area_km2,4)+' km²</b><span>高程</span><b>'+fmt(g.elevation_m,1)+' m</b><span>D8 下游网格</span><b>'+(g.downstream_grid_id||'无，下游出口或局部汇点')+'</b><span>挂接关系</span><b>'+links.length+' 个河段</b><span>权重闭合</span><b>'+fmt(g.mapping.total_fraction,6)+'</b></div><ul class="list">'+(links.length?links.map(l=>'<li><b>'+l.reach_id+'</b> / '+l.unit_id+'<br>面积 '+fmt(l.area_km2,4)+' km² · 权重 '+fmt(l.fraction,4)+' · '+l.method+'</li>').join(''):'<li>没有河段映射记录</li>')+'</ul>';render()}}function showReach(id){{const rows=payload.grids.filter(g=>g.mapping.links.some(l=>l.reach_id===id));document.getElementById('detail').innerHTML='<div class="kv"><span>河段</span><b>'+id+'</b><span>上游网格数</span><b>'+rows.length+'</b><span>挂接面积</span><b>'+fmt(rows.reduce((s,g)=>s+g.mapping.links.filter(l=>l.reach_id===id).reduce((a,l)=>a+l.area_km2,0),0),4)+' km²</b></div><ul class="list">'+rows.map(g=>'<li>'+g.id+' · '+g.mapping.links.filter(l=>l.reach_id===id).map(l=>'权重 '+fmt(l.fraction,4)).join(', ')+'</li>').join('')+'</ul>'}}payload.grids.forEach(g=>document.getElementById('gridSelect').add(new Option(g.id,g.id)));document.getElementById('gridSelect').addEventListener('change',e=>showGrid(e.target.value));document.getElementById('showD8').addEventListener('change',render);document.getElementById('showLinks').addEventListener('change',render);document.getElementById('stats').innerHTML=[['1 km网格',payload.summary.grid_count],['网格D8边',payload.summary.d8_grid_edges],['网格→河段映射行',payload.summary.mapping_rows],['30 m河段',payload.summary.reach_count],['最近邻兜底网格',payload.summary.fallback_grid_count]].map(x=>'<div class="stat"><span>'+x[0]+'</span><strong>'+x[1]+'</strong></div>').join('');render();showGrid(selected);</script></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root)
    output = root / "data_products/visualizations/nan_an/1km_grid_routing_relation/nan_an_1km_grid_routing_relation.html"
    payload = build_payload(root / "data_interface/spatial/南安溪1公里DEM.tif", root / "data_interface/runoff_2017/grid_static.csv", root / "data_interface/spatial/南安溪1公里有效格点.geojson", root / "validation/integrated_2017/grid_to_unit_weight.csv", root / "data_interface/spatial/nan_an_topology_clipped_v1.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(payload), encoding="utf-8")
    (output.parent / "nan_an_1km_grid_routing_relation_summary.json").write_text(json.dumps(payload["summary"], ensure_ascii=False, indent=2), encoding="utf-8")
    with (output.parent / "nan_an_1km_grid_network.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["grid_id", "downstream_grid_id", "status"])
        writer.writeheader()
        writer.writerows(payload["network_rows"])
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
