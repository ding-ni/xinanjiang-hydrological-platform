# -*- coding: utf-8 -*-
"""Export hydrographs, depth metrics and a local dashboard for a joint run."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--precip-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    model_root = Path(args.model_root).absolute()
    sys.path.insert(0, str(model_root / "code"))
    sys.path.insert(0, str(model_root / "scripts"))
    import run_formal_multievent_calibration as cal

    output = Path(args.output_dir).absolute()
    hydro_dir = output / "hydrographs"
    hydro_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(args.run_dir).absolute()
    best = json.loads((run_dir / "common_parameter_set.json").read_text(encoding="utf-8-sig"))
    candidate = best["search_parameters"]
    base_path = model_root / "config" / "parameter_set_land.json"
    params = cal.candidate_params(cal.load_parameter_set(base_path), candidate)
    routing_payload = json.loads((model_root / "validation" / "calibration_routing_2017" / "best_routing_parameters.json").read_text(encoding="utf-8-sig"))
    routing = cal.routing_from_candidate(routing_payload, candidate)
    grids = cal.load_grids(model_root / "data_interface" / "runoff_2017" / "grid_static.csv")
    events_meta, _ = cal.eligible_events(Path(args.data_root), 168, Path(args.catalog))
    observed = cal.find_observed_flow(Path(args.data_root))
    events = cal.load_event_data(Path(args.data_root), events_meta, grids, observed, 168, continuous=False, precip_dir=Path(args.precip_dir))
    pet_provider = cal.PetProvider.from_file(model_root / "config" / "pet_forcing_assumed_monthly.json")
    for event in events:
        event.pet_mm_h = pet_provider.values(event.times)
    topology_path = model_root / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    mapping_path = model_root / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    controls_path = model_root / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    units, mapping = cal.build_mapping(mapping_path, topology_path, grids)
    router = cal.FastRoutingRunner(topology_path, units, controls_path)
    template = json.loads((model_root / "config" / "initial_state_land.json").read_text(encoding="utf-8-sig"))
    area = np.asarray([float(row["area_km2"]) for row in grids], dtype=float)
    control_area_km2 = 64.1
    rows = []
    for event in events:
        state = cal.build_uniform_land_state(template, len(grids), params)
        sim, _, _ = cal.route_event_fast(event, params, routing, router, mapping, state)
        sim_event = sim.loc[event.start:event.end]
        obs_event = event.observed.loc[event.start:event.end]
        paired = pd.concat([obs_event.rename("observed"), sim_event.rename("simulated")], axis=1).dropna()
        times = pd.date_range(event.start, event.end, freq="h")
        rain = event.precip[np.isin(event.times, times)]
        rain_total = float(np.sum(rain * area[None, :]) / np.sum(area)) if rain.size else float("nan")
        obs_depth = float(obs_event.sum() * 3600.0 / (control_area_km2 * 1e6) * 1000.0)
        sim_depth = float(sim_event.sum() * 3600.0 / (control_area_km2 * 1e6) * 1000.0)
        hydro = pd.DataFrame({"time": times, "observed_flow_m3_s": obs_event.reindex(times).to_numpy(), "simulated_flow_m3_s": sim_event.reindex(times).to_numpy()})
        hydro.to_csv(hydro_dir / f"{event.event_id}.csv", index=False, encoding="utf-8-sig")
        metric = cal.metrics(obs_event, sim_event, json.loads((model_root / "config" / "qualification_config_event.json").read_text(encoding="utf-8-sig")))
        rows.append({"event_id": event.event_id, "flood_code": event.event_id.removeprefix("xf-"), "start": event.start.isoformat(), "end": event.end.isoformat(), "rainfall_mm": rain_total, "observed_runoff_depth_mm": obs_depth, "simulated_runoff_depth_mm": sim_depth, **metric})
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "joint_calibration_event_metrics_enriched.csv", index=False, encoding="utf-8-sig")
    dashboard = output / "selected_30_joint_calibration_dashboard.html"
    data = {"metrics": frame.replace({np.nan: None}).to_dict(orient="records"), "hydrograph_dir": "hydrographs"}
    dashboard.write_text(build_dashboard(data), encoding="utf-8")
    print(f"exported {len(frame)} events to {output}")
    return 0


def build_dashboard(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>30场洪水产汇流联合率定</title><style>
:root{--ink:#17232d;--muted:#65737c;--line:#d8e0e4;--paper:#f6f8f8;--white:#fff;--blue:#126782;--orange:#d76a32;--green:#27845f;--red:#b84d4d}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 Arial,"Microsoft YaHei",sans-serif}.wrap{max-width:1400px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;gap:20px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:18px}.eyebrow{color:var(--blue);font-size:12px;font-weight:bold;letter-spacing:1px}.top h1{margin:5px 0 0;font-size:27px}.note{color:var(--muted);max-width:520px;text-align:right}.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:18px 0}.stat{background:var(--white);border:1px solid var(--line);padding:14px 16px}.stat b{display:block;font-size:24px}.stat span{color:var(--muted);font-size:12px}.layout{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(360px,1fr);gap:16px}.panel{background:var(--white);border:1px solid var(--line);padding:16px}.panel h2{font-size:16px;margin:0 0 12px}.table-wrap{max-height:570px;overflow:auto}.table{width:100%;border-collapse:collapse;font-size:12px}.table th,.table td{padding:8px 7px;border-bottom:1px solid #edf0f1;text-align:right;white-space:nowrap}.table th{position:sticky;top:0;background:#eef4f5;color:#40525b}.table th:first-child,.table td:first-child{text-align:left}.table tr{cursor:pointer}.table tr.active{background:#eaf3f4}.ok{color:var(--green);font-weight:bold}.bad{color:var(--red);font-weight:bold}.controls{display:flex;gap:10px;align-items:center;margin-bottom:12px}.controls select{border:1px solid var(--line);padding:8px;background:white;min-width:150px}.chart{width:100%;height:260px;border:1px solid #edf0f1;background:#fbfcfc}.legend{display:flex;gap:16px;color:var(--muted);font-size:12px;margin-top:8px}.dot{display:inline-block;width:9px;height:9px;margin-right:4px}.observed{background:var(--blue)}.simulated{background:var(--orange)}.small{color:var(--muted);font-size:12px}.bars{height:220px;display:flex;align-items:end;gap:2px;border-bottom:1px solid var(--line);padding:8px 3px}.bar{flex:1;min-width:2px;background:var(--blue);position:relative}.bar.badbar{background:var(--red)}.bar:hover{opacity:.7}.bar i{display:none;position:absolute;bottom:100%;background:var(--ink);color:#fff;padding:3px 5px;font-style:normal;white-space:nowrap;font-size:11px;z-index:2}.bar:hover i{display:block}.foot{color:var(--muted);font-size:12px;margin-top:18px}@media(max-width:900px){.layout{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}.top{display:block}.note{text-align:left;margin-top:8px}.wrap{padding:16px}}
</style></head><body><main class="wrap"><header class="top"><div><div class="eyebrow">NAN'ANXI · XAJ RUNOFF + ROUTING</div><h1>30场洪水联合率定结果</h1></div><div class="note">1 km产流网格 → 河段局地入流 → 坡面线性水库 → 河段Muskingum → 徐畈控制断面</div></header><section class="stats" id="stats"></section><section class="layout"><div class="panel"><h2>逐场指标</h2><div class="table-wrap"><table class="table"><thead><tr><th>洪号</th><th>降雨<br>mm</th><th>实测径流深<br>mm</th><th>计算径流深<br>mm</th><th>实测峰<br>m³/s</th><th>计算峰<br>m³/s</th><th>峰时差<br>h</th><th>NSE</th><th>KGE</th><th>合格</th></tr></thead><tbody id="tbody"></tbody></table></div></div><div><div class="panel"><div class="controls"><h2 style="margin:0">洪水过程线</h2><select id="eventSelect"></select></div><svg id="hydro" class="chart" viewBox="0 0 760 260" role="img" aria-label="实测与模拟流量过程线"></svg><div class="legend"><span><i class="dot observed"></i>实测流量</span><span><i class="dot simulated"></i>模拟流量</span></div><div class="small" id="detail"></div></div><div class="panel" style="margin-top:16px"><h2>NSE逐场分布</h2><div class="bars" id="bars"></div><div class="small">横轴按洪号排列，红色为当前判据不合格场次。</div></div></div></section><div class="foot">本次率定为 30 场全部参与的联合率定，不包含独立验证集。径流深按控制面积 64.1 km²、小时步长由流量积分计算；正式生产使用前需完成控制断面和观测质量复核。</div></main><script>const DATA=__PAYLOAD__;const metrics=DATA.metrics;const $=id=>document.getElementById(id);const fmt=(v,n=1)=>v==null?'—':Number(v).toFixed(n);function stats(){const ok=metrics.filter(x=>x.qualified).length;const avg=k=>metrics.reduce((s,x)=>s+(Number(x[k])||0),0)/metrics.length;$('stats').innerHTML=[['洪水场次',metrics.length],['合格场次',ok+'/'+metrics.length],['平均NSE',fmt(avg('NSE'),3)],['平均KGE',fmt(avg('KGE'),3)],['平均峰时差',fmt(avg('peak_time_error_hours'),1)+' h']].map(x=>`<div class="stat"><b>${x[1]}</b><span>${x[0]}</span></div>`).join('')}function renderTable(){ $('tbody').innerHTML=metrics.map((x,i)=>`<tr data-i="${i}"><td>${x.flood_code}</td><td>${fmt(x.rainfall_mm,1)}</td><td>${fmt(x.observed_runoff_depth_mm,1)}</td><td>${fmt(x.simulated_runoff_depth_mm,1)}</td><td>${fmt(x.observed_peak_m3_s,1)}</td><td>${fmt(x.simulated_peak_m3_s,1)}</td><td>${fmt(x.peak_time_error_hours,1)}</td><td>${fmt(x.NSE,3)}</td><td>${fmt(x.KGE,3)}</td><td class="${x.qualified?'ok':'bad'}">${x.qualified?'√':'×'}</td></tr>`).join('');document.querySelectorAll('#tbody tr').forEach(tr=>tr.onclick=()=>select(+tr.dataset.i))}function select(i){$('eventSelect').value=metrics[i].event_id;document.querySelectorAll('#tbody tr').forEach((tr,j)=>tr.classList.toggle('active',i===j));drawHydro(metrics[i])}async function drawHydro(x){const svg=$('hydro');const res=await fetch(`${DATA.hydrograph_dir}/${x.event_id}.csv`);const text=await res.text();const lines=text.trim().split(/\r?\n/).slice(1).map(r=>r.split(','));const o=lines.map(r=>+r[1]||0),s=lines.map(r=>+r[2]||0),all=o.concat(s),max=Math.max(...all,1),W=760,H=260,p={l:42,r:12,t:14,b:28};const px=i=>p.l+i/(o.length-1||1)*(W-p.l-p.r),py=v=>H-p.b-v/max*(H-p.t-p.b);const path=a=>a.map((v,i)=>(i?'L':'M')+px(i).toFixed(1)+','+py(v).toFixed(1)).join(' ');svg.innerHTML=`<line x1="${p.l}" y1="${H-p.b}" x2="${W-p.r}" y2="${H-p.b}" stroke="#aab8bd"/><line x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${H-p.b}" stroke="#aab8bd"/><path d="${path(o)}" fill="none" stroke="#126782" stroke-width="2"/><path d="${path(s)}" fill="none" stroke="#d76a32" stroke-width="2"/><text x="8" y="18" fill="#65737c" font-size="11">${max.toFixed(0)} m³/s</text><text x="${p.l}" y="${H-7}" fill="#65737c" font-size="11">${x.start.slice(0,10)}</text><text x="${W-100}" y="${H-7}" fill="#65737c" font-size="11">${x.end.slice(0,10)}</text>`;$('detail').textContent=`洪号 ${x.flood_code} · NSE ${fmt(x.NSE,3)} · KGE ${fmt(x.KGE,3)} · 洪峰误差 ${fmt(x.peak_error_percent,1)}% · 实测/计算径流深 ${fmt(x.observed_runoff_depth_mm,1)} / ${fmt(x.simulated_runoff_depth_mm,1)} mm`}function bars(){const max=Math.max(...metrics.map(x=>Number(x.NSE)||0),1);$('bars').innerHTML=metrics.map(x=>`<div class="bar ${x.qualified?'':'badbar'}" style="height:${Math.max(2,(Number(x.NSE)||0)/max*100)}%"><i>${x.flood_code}: ${fmt(x.NSE,3)}</i></div>`).join('')}stats();renderTable();bars();$('eventSelect').innerHTML=metrics.map(x=>`<option value="${x.event_id}">${x.flood_code}</option>`).join('');$('eventSelect').onchange=e=>select(metrics.findIndex(x=>x.event_id===e.target.value));select(0);</script></body></html>'''.replace('__PAYLOAD__', payload)


if __name__ == "__main__":
    raise SystemExit(main())
