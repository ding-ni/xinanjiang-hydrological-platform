"""Real UI input-source/product acceptance; use a dedicated local runtime.

The source is converted in memory from the existing 2017 station JSON, not
invented observations. Source/product registration and model submission occur
through rendered controls; API reads independently verify what the UI used.
Run with --recon first to inspect the rendered application without mutations.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend/tests"))
from test_input_products_real import build_existing_2017_source

BASE = os.environ.get("XAJ_TEST_URL", "http://127.0.0.1:18883")
OUTPUT = Path(os.environ.get("XAJ_TEST_OUTPUT", str(ROOT / "runtime/input-products-browser")))
DEFAULT = "NANANXI_QC_20260819_CONTROL_V2"
STORE = "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s"
READ_APP = f"""() => {{ const app = {STORE}.get('app'); return {{
  selected: app.selectedInputBundleVersionId, catalog: app.selectedInputCatalog,
  context: app.contextKey, work: app.workContext, error: app.inputCatalogError }}; }}"""
READ_MONITOR = f"""() => {{ const m = {STORE}.get('monitor'); return {{
  preview: m.historicalPreview, product: m.modelInputProduct, summary: m.modelInputSummary,
  provenance: m.modelInputProvenance, error: m.historicalError, index: m.timeIndex }}; }}"""
READ_MAP = """() => { const m = document.querySelector('.map-canvas')?.__hydroMap;
  return {rain: m?.getSource('qpf-grid')?._data?.features || [],
  smooth: m?.getLayer('smooth-rain-fill') ? m.getLayoutProperty('smooth-rain-fill','visibility') : null}; }"""


def get(page, path):
    response = page.request.get(BASE + "/api/model/" + path)
    assert response.ok, response.text()
    return response.json()


def config(page):
    page.goto(BASE + "/#/config", wait_until="networkidle")
    page.get_by_role("button", name="水雨情资料", exact=True).click()
    page.locator("[aria-label='输入资料产品管理']").wait_for(timeout=30000)
    page.locator("[aria-label='已登记输入版本'] tbody tr").first.wait_for()


def select(page, version):
    row = page.get_by_role("table", name="已登记输入版本").locator("tbody tr").filter(has_text=version)
    row.get_by_role("button", name="选用此版本", exact=True).click()
    page.wait_for_function(f"v => {STORE}.get('app').selectedInputBundleVersionId === v", arg=version)
    catalog = page.evaluate(READ_APP)["catalog"]
    assert catalog["bundleVersionId"] == version
    metadata = page.locator("[aria-label='真实模型输入版本']")
    metadata.filter(has_text=catalog["forcingVersion"]).wait_for()
    assert catalog["observationVersion"] in metadata.inner_text()


def submit_product(page, name, operation="qcWindow", source_id=None):
    page.locator("#input-operation").select_option(operation)
    page.locator("#input-product-name").fill(name)
    selected_before = page.evaluate(READ_APP)["selected"]
    if operation == "qcWindow":
        # The current backend QC catalog is authoritative. Its revised event
        # ends later than the retained historical/frontend display window.
        catalog = get(page, "inputs/catalog?" + urlencode({"bundleVersionId": selected_before}))
        event = next(row for row in catalog["events"] if row["id"] == "xf-31170619")
        start = (datetime.fromisoformat(event["start"]) - timedelta(days=7)).isoformat(timespec="minutes")
        end = datetime.fromisoformat(event["end"]).isoformat(timespec="minutes")
    else:
        start, end = "2017-01-01T00:00", "2017-12-31T23:00"
        page.locator("#input-source").select_option(source_id)
        page.locator("#input-idw-power").fill("2")
        page.locator("#input-minimum-stations").fill("1")
        page.locator("#input-zero-policy").select_option("ANNUAL_RECLASSIFY")
    page.locator("#input-window-start").fill(start)
    page.locator("#input-window-end").fill(end)
    with page.expect_response(lambda r: r.request.method == "POST" and r.url == BASE + "/api/model/input-tasks") as posted:
        page.get_by_role("button", name="生成降雨整编产品", exact=True).click()
    response = posted.value
    assert response.status == 202, response.text()
    task = response.json()
    task_id = task["taskId"]
    assert task["runConfig"]["parentBundleVersionId"] == selected_before
    assert task["productionEligible"] is False
    row = page.get_by_role("table", name="资料加工任务").locator("tbody tr").filter(has_text=task_id)
    row.wait_for()
    if task["status"] in {"queued", "running"}:
        assert row.get_by_role("button", name="校验并登记结果", exact=True).count() == 0
    row.get_by_role("button", name="校验并登记结果", exact=True).wait_for(timeout=120000)
    result = get(page, f"input-tasks/{task_id}/result")
    version = result["productVersionId"]
    assert version not in [v["bundleVersionId"] for v in get(page, "input-products")["versions"]]
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/input-tasks/{task_id}/register")) as registered:
        row.get_by_role("button", name="校验并登记结果", exact=True).click()
    assert registered.value.ok, registered.value.text()
    assert registered.value.json()["bundleVersionId"] == version
    page.get_by_role("table", name="已登记输入版本").locator("tbody tr").filter(has_text=version).wait_for()
    assert page.evaluate(READ_APP)["selected"] == selected_before, "Registration must not select the product"
    row.get_by_role("button", name="详情 / 日志", exact=True).click()
    page.locator(".product-details").filter(has_text="冻结文件摘要").wait_for()
    assert "等待显式登记" in get(page, f"input-tasks/{task_id}/log")["tail"]
    return task_id, version


def verify_realtime(page, version):
    page.goto(BASE + "/#/realtime", wait_until="networkidle")
    page.wait_for_function(f"v => {{ const m = {STORE}.get('monitor'); return !m.historicalLoading && m.modelInputProduct && m.modelInputProvenance?.bundleVersionId === v; }}", arg=version, timeout=60000)
    page.wait_for_function("() => document.querySelector('.map-canvas')?.__hydroMap?.getSource('qpf-grid')?._data?.features?.length === 110")
    page.get_by_text("已登记 QC 诊断输入", exact=True).wait_for()
    monitor = page.evaluate(READ_MONITOR)
    query = urlencode({"bundleVersionId": version, "start": monitor["preview"]["start"], "end": monitor["preview"]["end"]})
    actual = get(page, "inputs/window?" + query)
    assert monitor["product"]["cells"] == actual["product"]["cells"]
    assert monitor["product"]["times"] == actual["product"]["times"]
    assert monitor["summary"]["arealRainfallMm"] == actual["summary"]["arealRainfallMm"]
    assert monitor["preview"]["rain"] == {}
    mapped = page.evaluate(READ_MAP)
    assert mapped["smooth"] == "none"
    values = {cell["id"]: cell["values"][monitor["index"]] for cell in actual["product"]["cells"]}
    assert {f["properties"]["id"]: f["properties"].get("qpf") for f in mapped["rain"]} == values
    page.screenshot(path=str(OUTPUT / (version[:22] + "-realtime.png")), full_page=True)
    return actual


def model_submit(page, button):
    with page.expect_response(lambda r: r.request.method == "POST" and r.url == BASE + "/api/model/tasks") as posted:
        page.get_by_role("button", name=button, exact=True).click()
    assert posted.value.status == 202, posted.value.text()
    return posted.value.json()["taskId"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recon", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1100})
        context.add_init_script("if (!localStorage.getItem('input_product_smoke_started')) {localStorage.clear(); localStorage.setItem('hydro_basin_id','nananxi-xufan'); localStorage.setItem('input_product_smoke_started','1');}")
        page = context.new_page()
        errors, failures = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", lambda r: failures.append(f"{r.status} {r.url}") if r.url.startswith(BASE) and r.status >= 400 else None)
        try:
            config(page)
            print(json.dumps({"buttons": page.locator("button").all_text_contents(), "inputs": page.locator("input").evaluate_all("rows => rows.map(r => ({id:r.id,type:r.type,value:r.value}))"), "products": page.get_by_role("table", name="已登记输入版本").inner_text()}, ensure_ascii=False), flush=True)
            if args.recon:
                page.screenshot(path=str(OUTPUT / "input-products-recon.png"), full_page=True)
                return
            assert page.evaluate(READ_APP)["selected"] == DEFAULT
            sources_before = {s["sourceId"] for s in get(page, "input-sources")["sources"]}
            page.locator(".source-registration summary").click()
            page.get_by_role("button", name="填入非实测格式模板", exact=True).click()
            template = json.loads(page.locator("#source-json-editor").input_value())
            assert template["stations"][0]["values"] == [None, None]
            page.get_by_role("button", name="校验并登记来源", exact=True).click()
            page.locator("[role='alert']").filter(has_text="非实测格式模板不能直接登记").wait_for()
            assert {s["sourceId"] for s in get(page, "input-sources")["sources"]} == sources_before
            source = build_existing_2017_source(ROOT)
            # Native upload uses an in-memory file, without another on-disk source.
            page.get_by_label("选择站点来源 JSON").set_input_files({"name": "existing-2017-stations.json", "mimeType": "application/json", "buffer": json.dumps(source, ensure_ascii=False).encode("utf-8")})
            page.wait_for_function("() => document.querySelector('#source-json-editor').value.includes('Existing 2017 station')")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url == BASE + "/api/model/input-sources") as posted:
                page.get_by_role("button", name="校验并登记来源", exact=True).click()
            assert posted.value.status == 201, posted.value.text()
            source_meta = posted.value.json()
            assert source_meta["missingValueCount"] == 61320 and source_meta["productionEligible"] is False
            print("Real 2017 source registered; template rejected without artifacts", flush=True)
            stamp = datetime.now().strftime("%H%M%S")
            qc_task, qc_version = submit_product(page, "Browser actual QC window " + stamp)
            print("QC window processed and explicitly registered; not automatically selected", flush=True)
            select(page, qc_version)
            page.reload(wait_until="networkidle")
            assert page.evaluate(READ_APP)["selected"] == qc_version
            qc_window = verify_realtime(page, qc_version)
            # Event playback must ask for this product rather than an old cache.
            page.locator(".replay-mode select").select_option("event")
            page.locator(".replay-event select").select_option("xf-31170619")
            page.wait_for_function(f"v => {{ const m={STORE}.get('monitor'); return !m.historicalLoading && m.historicalReplayEventId==='xf-31170619' && m.modelInputProvenance?.bundleVersionId===v; }}", arg=qc_version, timeout=60000)
            page.goto(BASE + "/#/calibration", wait_until="networkidle")
            page.get_by_role("button", name="连续模拟", exact=False).first.click()
            page.locator(".field").filter(has=page.locator("label", has_text="开始时间")).locator("input").fill("2017-06-19T11:00")
            page.locator(".field").filter(has=page.locator("label", has_text="结束时间")).locator("input").fill("2017-06-20T11:00")
            model_task = model_submit(page, "确认模拟任务")
            config(page)
            select(page, DEFAULT)
            status_at_switch = get(page, "tasks/" + model_task)["status"]
            page.goto(BASE + "/#/calibration", wait_until="networkidle")
            page.get_by_role("button", name="连续模拟", exact=False).first.click()
            page.wait_for_function("id => document.querySelector('.simulation-result-card')?.innerText.includes('STATE-' + id)", arg=model_task, timeout=120000)
            model_result = get(page, f"tasks/{model_task}/result")
            assert model_result["lineage"]["inputBundleVersionId"] == qc_version
            assert len(model_result["hydrograph"]["forecast"]) == 25
            assert model_result["productionEligible"] is False
            card = page.locator(".simulation-result-card").inner_text()
            assert "不是当前方案" in card and qc_version in card
            page.get_by_role("button", name="查看模拟状态", exact=True).click()
            page.wait_for_url("**/#/state")
            page.get_by_role("button", name="续算状态", exact=True).click()
            page.locator(".state-block-note").filter(has_text="跨资料版本必须重新初始化").wait_for()
            assert page.get_by_role("button", name="开始续算", exact=True).is_disabled()
            page.screenshot(path=str(OUTPUT / "cross-version-state-blocked.png"), full_page=True)
            config(page)
            select(page, qc_version)
            page.goto(BASE + "/#/state", wait_until="networkidle")
            page.get_by_role("button", name="续算状态", exact=True).click()
            page.locator(".compact-field input").fill("2017-06-20T17:00")
            continuation_task = model_submit(page, "开始续算")
            page.wait_for_function("id => document.querySelector('.task-status .task-head b')?.textContent.includes(id) && document.querySelector('.task-status .task-state')?.textContent === '已完成'", arg=continuation_task, timeout=120000)
            continued = get(page, f"tasks/{continuation_task}/result")
            assert continued["parentStateVersionId"] == model_result["stateVersionId"]
            assert continued["lineage"]["inputBundleVersionId"] == qc_version
            print("Frozen model version and same-version continuation verified; cross-version handoff blocked", flush=True)
            config(page)
            select(page, DEFAULT)
            idw_task, idw_version = submit_product(page, "Browser actual station IDW 2017 " + stamp, "stationIdw", source_meta["sourceId"])
            print("Real annual station IDW processed and explicitly registered", flush=True)
            select(page, idw_version)
            idw_window = verify_realtime(page, idw_version)
            query = urlencode({"start": idw_window["product"]["times"][0], "end": idw_window["product"]["times"][-1]})
            baseline = get(page, "inputs/window?" + query)
            assert baseline["bundleVersionId"] == DEFAULT
            assert idw_window["product"]["cells"] == baseline["product"]["cells"]
            assert idw_window["summary"]["arealRainfallMm"] == baseline["summary"]["arealRainfallMm"]
            page.reload(wait_until="networkidle")
            assert page.evaluate(READ_APP)["selected"] == idw_version
            verify_realtime(page, idw_version)
            page.locator(".scale-chip select").select_option("nananxi-xufan-0p1deg")
            page.wait_for_function(f"() => !{STORE}.get('monitor').modelInputProduct")
            assert page.evaluate(READ_APP)["selected"] is None
            assert page.evaluate(READ_MAP)["rain"] == []
            page.locator(".scale-chip select").select_option("nananxi-xufan-1km")
            verify_realtime(page, idw_version)
            assert not errors, errors
            assert not failures, failures
            # An unavailable real API must clear input fields, not change identity.
            page.route("**/api/model/**", lambda route: route.abort())
            page.wait_for_function(f"() => !{STORE}.get('monitor').modelInputProduct && Boolean({STORE}.get('monitor').historicalError)", timeout=30000)
            assert page.evaluate(READ_APP)["selected"] == idw_version
            page.wait_for_function("() => (document.querySelector('.map-canvas')?.__hydroMap?.getSource('qpf-grid')?._data?.features || []).length === 0")
            page.screenshot(path=str(OUTPUT / "registered-input-offline.png"), full_page=True)
            page.unroute("**/api/model/**")
            verify_realtime(page, idw_version)
            report = {"status": "passed", "sourceId": source_meta["sourceId"], "qcTaskId": qc_task,
                      "qcVersionId": qc_version, "stationIdwTaskId": idw_task, "stationIdwVersionId": idw_version,
                      "modelTaskId": model_task, "continuationTaskId": continuation_task,
                      "modelStatusAtSelectionChange": status_at_switch, "defaultBundleVersionId": DEFAULT,
                      "checks": ["missing template rejected", "real 2017 source upload preserves 61320 nulls",
                                 "complete is not registration", "register is not selection", "selection persists",
                                 "selected metadata header matches version",
                                 "selected API equals 110 map grids", "browser smoothing disabled", "event playback version isolated",
                                 "frozen model version unaffected by selection", "old result labelled", "cross-version state blocked",
                                 "same-version continuation", "real annual station IDW equals baseline", "scale isolation",
                                 "offline clears values without fallback", "reconnection restores selected version"],
                      "pageErrors": errors, "localHttpFailures": failures, "productionEligible": False}
            (OUTPUT / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
        except BaseException:
            label = "failure-" + datetime.now().strftime("%H%M%S")
            page.screenshot(path=str(OUTPUT / (label + ".png")), full_page=True)
            (OUTPUT / (label + ".json")).write_text(json.dumps({
                "body": page.locator("body").inner_text(), "app": page.evaluate(READ_APP),
                "monitor": page.evaluate(READ_MONITOR), "pageErrors": errors, "localHttpFailures": failures
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
