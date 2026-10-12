# -*- coding: utf-8 -*-
"""Render timing and control-section audit as a self-contained HTML page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timing", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    frame = pd.read_csv(args.timing, encoding="utf-8-sig")
    data = frame.to_dict("records")
    payload = json.dumps(data, ensure_ascii=False)
    html = '''<!doctype html><meta charset="utf-8"><title>峰现时序与控制断面诊断</title><style>body{font:14px Arial,'Microsoft YaHei';margin:24px;background:#f4f7f7;color:#1d3037}.panel{background:#fff;border:1px solid #d4e0e0;padding:16px;margin:12px 0}svg{width:100%;height:360px}table{border-collapse:collapse;width:100%}th,td{padding:7px;border-bottom:1px solid #e5eeee;text-align:right}th:first-child,td:first-child{text-align:left}.note{color:#5c6f75}</style><h1>峰现时序与控制断面诊断</h1><section class="panel"><p class="note">正值表示模拟峰晚于实测峰。雨峰为当前1 km网格面积加权雨峰，仅用于时序诊断。</p><svg id="chart" viewBox="0 0 1000 360"></svg></section><section class="panel"><table><thead><tr><th>洪号</th><th>雨峰到实测峰 h</th><th>雨峰到模拟峰 h</th><th>模拟峰-实测峰 h</th></tr></thead><tbody id="rows"></tbody></table></section><script>const d=__DATA__,s=document.querySelector('#chart'),w=Math.max(900,d.length*30),mid=180,scale=2.0; s.setAttribute('viewBox',`0 0 ${w} 360`); s.innerHTML=`<line x1="20" y1="${mid}" x2="${w-20}" y2="${mid}" stroke="#9aaeb1"/>`+d.map((x,i)=>{let xx=30+i*30,yo=mid-(+x.rain_to_observed_peak_hours)*scale,ys=mid-(+x.rain_to_simulated_peak_hours)*scale;return `<line x1="${xx}" y1="${mid}" x2="${xx}" y2="${yo}" stroke="#3d9b86" stroke-width="4"/><circle cx="${xx}" cy="${yo}" r="3" fill="#3d9b86"/><line x1="${xx+7}" y1="${mid}" x2="${xx+7}" y2="${ys}" stroke="#d77b42" stroke-width="4"/><circle cx="${xx+7}" cy="${ys}" r="3" fill="#d77b42"/><text x="${xx}" y="330" font-size="9" text-anchor="middle" transform="rotate(-65 ${xx} 330)">${x.flood_code}</text>`}).join('')+`<text x="30" y="18" fill="#3d9b86">绿色：雨峰到实测流量峰</text><text x="250" y="18" fill="#d77b42">橙色：雨峰到模拟流量峰</text>`;document.querySelector('#rows').innerHTML=d.map(x=>`<tr><td>${x.flood_code}</td><td>${(+x.rain_to_observed_peak_hours).toFixed(1)}</td><td>${(+x.rain_to_simulated_peak_hours).toFixed(1)}</td><td>${(+x.simulated_minus_observed_peak_hours).toFixed(1)}</td></tr>`).join('');</script>'''.replace('__DATA__', payload)
    args.output.write_text(html, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
