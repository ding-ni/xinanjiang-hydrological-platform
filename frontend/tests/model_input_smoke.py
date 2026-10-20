"""Read-only browser acceptance for fixed model QC inputs (no model jobs)."""
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:8878")
OUTPUT = Path(os.environ.get("UI_OUTPUT_DIR", str(Path(__file__).resolve().parents[3] / "runtime" / "model-input-browser")))
OUTPUT.mkdir(parents=True, exist_ok=True)

READ_MONITOR = """() => {
  const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
  const monitor = pinia._s.get('monitor');
  return {preview: monitor.historicalPreview, product: monitor.modelInputProduct,
          summary: monitor.modelInputSummary, provenance: monitor.modelInputProvenance,
          timeIndex: monitor.timeIndex, timeLabel: monitor.currentTimeLabel,
          error: monitor.historicalError, rainSeries: monitor.selectedRainSeries};
}"""

READ_MAP = """() => {
  const map = document.querySelector('.map-canvas').__hydroMap;
  return map ? {rain: map.getSource('qpf-grid')?._data?.features || [],
                smoothVisible: map.getLayoutProperty('smooth-rain-fill', 'visibility'),
                stations: map.getSource('stations')?._data?.features || []} : null;
}"""

READ_STATE_PALETTE = """() => {
  const map = document.querySelector('.map-canvas').__hydroMap;
  const expression = map.getPaintProperty('grid-fill', 'fill-color');
  const pairs = [];
  for (let i = 3; i < expression.length; i += 2) {
    const swatch = document.createElement('span');
    swatch.style.backgroundColor = expression[i + 1];
    pairs.push({value: expression[i], color: swatch.style.backgroundColor});
  }
  return {field: expression[2][1], pairs,
    title: document.querySelector('.map-legend .legend-title').textContent,
    note: document.querySelector('.legend-scale-note')?.textContent,
    rows: Array.from(document.querySelectorAll('.map-legend .legend-row')).map(row => ({
      label: row.querySelector('span:last-child').textContent,
      color: row.querySelector('.swatch').style.backgroundColor
    }))};
}"""


def verify_state_palette(page, field, button_name, title_fragment):
    page.locator(".variable-tabs").get_by_role("button", name=button_name, exact=False).click()
    page.wait_for_function("""field => {
      const map = document.querySelector('.map-canvas')?.__hydroMap;
      return map?.getLayer('grid-fill') && map.getPaintProperty('grid-fill', 'fill-color')[2][1] === field;
    }""", arg=field)
    palette = page.evaluate(READ_STATE_PALETTE)
    assert palette["field"] == field
    assert title_fragment in palette["title"]
    assert "数值刻度" in palette["note"]
    assert len(palette["pairs"]) == len(palette["rows"]) == 5
    for index, (pair, row) in enumerate(zip(palette["pairs"], palette["rows"])):
        assert row["color"] == pair["color"], palette
        expected = ("≤ " if index == 0 else "≥ " if index == 4 else "") + str(pair["value"])
        assert row["label"] == expected, palette
    return {"field": field, "ticks": [pair["value"] for pair in palette["pairs"]]}


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{BASE_URL}/#/realtime", wait_until="networkidle")
    page.get_by_text("后端实际 QC 降雨栅格已载入", exact=True).wait_for(timeout=60000)
    page.wait_for_function("""() => document.querySelector('.map-canvas')?.__hydroMap?.getSource('qpf-grid')?._data?.features?.length === 110""")
    page.wait_for_function("""() => {
      const map = document.querySelector('.map-canvas')?.__hydroMap;
      return map && !map.isMoving() && map.getCenter().lng > 100;
    }""")
    assert page.locator(".chip.status b").inner_text() == "固定 QC 已载入"
    assert page.locator(".project-readiness .count").inner_text() == "固定 QC 已载入"
    assert "实测资料已接入" not in page.locator(".project-readiness").inner_text()
    page.screenshot(path=str(OUTPUT / "realtime-qc-initial.png"), full_page=True)
    initial = page.evaluate(READ_MONITOR)
    mapped = page.evaluate(READ_MAP)
    assert initial["preview"]["sourceType"] == "model-qc"
    assert initial["timeLabel"] == initial["product"]["times"][0].replace("T", " ")[:16]
    assert initial["preview"]["rain"] == {}
    assert initial["rainSeries"] == []
    assert all(value is None for value in initial["preview"]["river"]["levels"])
    assert mapped["smoothVisible"] == "none"
    rain_stations = [feature["properties"] for feature in mapped["stations"] if feature["properties"].get("type") == "rain"]
    assert rain_stations
    assert all(station.get("rain1h") is None for station in rain_stations)
    assert all("逐站 QC" in station.get("sourceLabel", "") for station in rain_stations)
    assert "当前小时面积加权 QC" in page.locator(".metric-strip").inner_text()
    assert "站点均值" not in page.locator(".metric-strip").inner_text()
    assert page.locator(".metric-strip").inner_text().count(" m") >= 1

    query = urlencode({"projectId": "nananxi-xufan", "resolutionId": "1km", "start": initial["preview"]["start"], "end": initial["preview"]["end"]})
    response = page.request.get(f"{BASE_URL}/api/model/inputs/window?{query}")
    assert response.ok, response.text()
    registered = response.json()
    assert initial["product"]["times"] == registered["product"]["times"]
    assert initial["product"]["cells"] == registered["product"]["cells"]
    assert initial["summary"]["arealRainfallMm"] == registered["summary"]["arealRainfallMm"]
    assert initial["preview"]["river"]["flows"] == registered["observationPreview"]["river"]["flows"]
    first_values = {cell["id"]: cell["values"][0] for cell in registered["product"]["cells"]}
    assert {feature["properties"]["id"]: feature["properties"].get("qpf") for feature in mapped["rain"]} == first_values

    page.locator(".replay-mode select").select_option("event")
    event_select = page.locator(".replay-event select")
    event_select.select_option("xf-31170619")
    page.wait_for_function("""() => {
      const monitor = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('monitor');
      return !monitor.historicalLoading && monitor.historicalReplayEventId === 'xf-31170619' && monitor.historicalPreview?.start?.startsWith('2017-06-19');
    }""", timeout=60000)
    page.get_by_text("后端实际 QC 降雨栅格已载入", exact=True).wait_for()
    event = page.evaluate(READ_MONITOR)
    assert event["preview"]["rain"] == {}
    assert event["preview"]["start"].startswith("2017-06-19")
    assert "后端 QC 场次资料" in page.locator(".replay-window").inner_text()
    slider = page.locator(".range")
    slider.focus()
    slider.press("Home")
    for _ in range(5):
        slider.press("ArrowRight")
    page.wait_for_function("""() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('monitor').timeIndex === 5""")
    expected_replay = {cell["id"]: cell["values"][5] for cell in event["product"]["cells"]}
    page.wait_for_function("""expected => {
      const features = document.querySelector('.map-canvas')?.__hydroMap?.getSource('qpf-grid')?._data?.features || [];
      return features.length === 110 && features.every(row => row.properties.qpf === expected[row.properties.id]);
    }""", arg=expected_replay)
    replayed = page.evaluate(READ_MONITOR)
    assert replayed["timeLabel"] == event["product"]["times"][5].replace("T", " ")[:16]
    outlet = page.evaluate("""() => {
      const canvas = document.querySelector('.map-canvas');
      const map = canvas.__hydroMap;
      const feature = map.getSource('stations')._data.features.find(row => row.properties.id === '70115800');
      const point = map.project(feature.geometry.coordinates);
      const box = canvas.getBoundingClientRect();
      return {x: box.left + point.x, y: box.top + point.y};
    }""")
    page.mouse.click(outlet["x"], outlet["y"])
    page.locator(".right-detail").get_by_text("水文过程", exact=True).wait_for()
    assert "模型 QC 流量" in page.locator(".right-detail").inner_text()
    assert "xufan.mdb" not in page.locator(".right-detail").inner_text()
    assert page.locator(".right-detail .chart-box").count() == 1
    page.screenshot(path=str(OUTPUT / "realtime-qc-event.png"), full_page=True)
    saved_event_clock = page.locator(".clock-chip b").inner_text()

    # The task/catalog polling discovers the connection loss. The monitoring
    # page then clears all model input values instead of switching to fixtures.
    page.route("**/api/model/**", lambda route: route.abort())
    page.wait_for_function("""() => {
      const monitor = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('monitor');
      return !monitor.modelInputProduct && !monitor.historicalPreview && Boolean(monitor.historicalError);
    }""", timeout=30000)
    page.wait_for_function("""() => (document.querySelector('.map-canvas')?.__hydroMap?.getSource('qpf-grid')?._data?.features || []).length === 0""")
    assert page.get_by_role("button", name="开始回放", exact=True).is_disabled()
    assert page.evaluate(READ_MONITOR)["rainSeries"] == []
    assert "当前小时面积加权 QC" not in page.locator(".metric-strip").inner_text()
    assert "未载入可用的 QC 输入时段" in page.locator(".replay-window").inner_text()
    assert "未载入可用的 QC 时次" in page.locator(".project-forcing").inner_text()
    assert page.locator(".ticks span").all_text_contents() == ["—", "—"]
    assert page.locator(".clock-chip b").inner_text() == saved_event_clock
    assert page.locator(".chip.status b").inner_text() == "固定 QC 服务离线"
    assert page.locator(".project-readiness .count").inner_text() == "固定 QC 服务离线"
    assert "实测资料已接入" not in page.locator(".project-readiness").inner_text()
    assert page.evaluate(READ_MAP)["smoothVisible"] == "none"
    page.screenshot(path=str(OUTPUT / "realtime-qc-offline.png"), full_page=True)
    page.unroute("**/api/model/**")
    page.get_by_text("后端实际 QC 降雨栅格已载入", exact=True).wait_for(timeout=60000)

    page.locator(".scale-chip select").select_option("nananxi-xufan-0p1deg")
    page.get_by_text("当前尺度未接入 QC 输入", exact=True).wait_for(timeout=30000)
    page.wait_for_load_state("networkidle")
    unsupported = page.evaluate(READ_MONITOR)
    assert unsupported["preview"] is None and unsupported["product"] is None
    assert unsupported["rainSeries"] == []
    assert "1 km" in unsupported["error"]
    assert "未载入可用的 QC 输入时段" in page.locator(".replay-window").inner_text()
    assert page.locator(".ticks span").all_text_contents() == ["—", "—"]
    assert page.locator(".chip.status b").inner_text() == "当前尺度 QC 未接入"
    assert page.locator(".project-readiness .count").inner_text() == "当前尺度 QC 未接入"
    assert "实测资料已接入" not in page.locator(".project-readiness").inner_text()
    page.wait_for_function("""() => (document.querySelector('.map-canvas')?.__hydroMap?.getSource('qpf-grid')?._data?.features || []).length === 0""")
    page.wait_for_function("""() => {
      const map = document.querySelector('.map-canvas')?.__hydroMap;
      const center = map?.getCenter();
      return map && !map.isMoving() && center.lng > 100 && center.lat > 20;
    }""", timeout=15000)
    page.screenshot(path=str(OUTPUT / "realtime-qc-unsupported-scale.png"), full_page=True)

    # Existing backend checkpoints and isolated Selinco fixtures are inspected
    # read-only. No state initialization/calibration/simulation is submitted.
    palettes = []
    page.locator(".scale-chip select").select_option("nananxi-xufan-1km")
    page.goto(f"{BASE_URL}/#/state", wait_until="networkidle")
    page.locator(".variable-tabs").wait_for(timeout=30000)
    for field, label, title in [("wu", "上层张力水", "WU (mm)"), ("wl", "下层张力水", "WL (mm)"),
                                ("wd", "深层张力水", "WD (mm)"), ("freeWater", "自由水蓄量", "S (mm)"),
                                ("soil", "张力水相对蓄量", "W/WM")]:
        palettes.append(verify_state_palette(page, field, label, title))
    page.screenshot(path=str(OUTPUT / "state-qc-soil-legend.png"), full_page=True)
    page.locator(".basin-chip select").select_option("selinco")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="水土状态", exact=True).click()
    page.locator(".variable-tabs").get_by_role("button", name="时段产流深", exact=False).wait_for()
    palettes.append(verify_state_palette(page, "runoff", "时段产流深", "R (mm)"))
    palettes.append(verify_state_palette(page, "soil", "张力水相对蓄量", "W/WM"))
    page.screenshot(path=str(OUTPUT / "state-selinco-soil-legend.png"), full_page=True)
    page.get_by_role("button", name="冰雪冻土", exact=True).click()
    for field, label, title in [("snowWater", "积雪水当量", "SWE (mm)"), ("snowDepth", "积雪深度", "(cm)"),
                                ("snowLiquid", "雪层液态水", "(mm)"), ("frozenIndex", "冻土冻结程度", "(%)"),
                                ("glacierMelt", "冰川消融量", "(mm/h)")]:
        palettes.append(verify_state_palette(page, field, label, title))
    assert not errors, errors
    (OUTPUT / "result.json").write_text(json.dumps({"status": "passed", "gridCount": 110, "forcingVersion": initial["provenance"]["forcingVersion"], "checks": ["same backend QC values", "area-weighted rainfall", "no station substitution", "event playback", "QC flow chart", "offline clears data", "unsupported scale isolation", "empty window captions and demo guard", "state map paint matches legend"], "palettes": palettes, "pageErrors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    context.close()
    browser.close()

print("Fixed model input browser acceptance passed")
