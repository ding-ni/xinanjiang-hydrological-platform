"""Isolated UI API fixtures. Never registers sources or products in real runtime.

Run against a unified-mode frontend. The sole real API read is GET /catalog;
all subsequent /api/model calls are fulfilled in the disposable browser context.
Synthetic UI-only records do not establish hydrological/scientific acceptance.
"""
import copy
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:18883")
OUTPUT = Path(os.environ.get("UI_OUTPUT_DIR", str(Path(__file__).resolve().parents[3] / "runtime" / "input-products-ui-fixtures")))
BASELINE = "NANANXI_QC_20260819_CONTROL_V2"
NEW_VERSION = "INPUT-UI-ONLY-QC"
COVERAGE = {"start": "2017-06-18T00:00", "end": "2017-06-18T01:00"}
STORE = "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1100})
    context.add_init_script("localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');")
    response = context.request.get(f"{BASE_URL}/api/model/catalog")
    assert response.ok, "Requires a running unified frontend/model service for read-only catalog bootstrap"
    backend_catalog = response.json()
    case = backend_catalog["cases"]["nananxi-xufan:1km"]
    versions = [{"bundleVersionId": BASELINE, "manifestSHA256": "a" * 64, "name": "UI-only baseline fixture", "sourceLabel": "UI-only fixture, not observations", "coverage": {"years": [2017]}, "productionEligible": False, "summary": {"gridCount": 110, "eventCount": 0}}]
    new_product = {"bundleVersionId": NEW_VERSION, "manifestSHA256": "b" * 64, "name": "UI-only successful window", "sourceLabel": "UI-only fixture, not observations", "coverage": COVERAGE, "productionEligible": False, "summary": {"gridCount": 110, "eventCount": 0}, "qualityNotes": ["用户声明来源，未独立验收（UI-only fixture）"]}
    sources = []
    processing = []
    posts = []

    def fulfill(route, payload, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def api_fixture(route):
        parsed = urlparse(route.request.url)
        path = parsed.path.removeprefix("/api/model/")
        method = route.request.method
        body = route.request.post_data_json if method == "POST" else None
        if method == "POST":
            posts.append({"path": path, "body": copy.deepcopy(body)})
        if path == "catalog":
            fulfill(route, backend_catalog)
        elif path == "input-products":
            fulfill(route, {"schemaVersion": "1.0", "defaultBundleVersionId": BASELINE, "versions": versions})
        elif path == "inputs/catalog":
            bundle = parse_qs(parsed.query).get("bundleVersionId", [BASELINE])[0]
            fulfill(route, {"schemaVersion": "2.0", "sourceType": "model-qc", "caseKey": "nananxi-xufan:1km", "bundleVersionId": bundle, "bundleManifestSHA256": ("a" if bundle == BASELINE else "b") * 64, "forcingVersion": case["forcingVersion"] if bundle == BASELINE else "UI-ONLY-FORCING", "observationVersion": case["observationVersion"], "gridCount": 110, "coverage": {"precipitation": COVERAGE, "observedFlow": COVERAGE, "common": COVERAGE}, "latestPeriod": COVERAGE, "events": [], "qualityNotes": ["UI-only fixture, not field observations"]})
        elif path == "input-sources" and method == "POST":
            source = {"sourceId": "SRC-UI-ONLY", "name": body["name"], "stationCount": len(body["stations"]), "coverage": {"start": body["times"][0], "end": body["times"][-1]}, "sourceLabel": "用户声明站点资料，未独立验收", "productionEligible": False}
            sources.append(source)
            fulfill(route, source, 201)
        elif path == "input-sources":
            fulfill(route, {"sources": sources})
        elif path == "input-tasks" and method == "POST":
            completed = body["name"] == "UI-only successful window"
            task = {"taskId": f"DATA-UI-ONLY-{len(processing) + 1}", "runConfig": body, "status": "completed" if completed else "queued", "progress": 100 if completed else 0, "stageLabel": "UI-only fixture", "createdAt": f"2026-10-10T12:00:0{len(processing)}", "productionEligible": False}
            processing.append(task)
            fulfill(route, task, 202)
        elif path == "input-tasks":
            fulfill(route, {"tasks": processing})
        elif path.startswith("input-tasks/"):
            pieces = path.split("/")
            task = next(row for row in processing if row["taskId"] == pieces[1])
            operation = pieces[2] if len(pieces) > 2 else ""
            if operation in {"cancel", "register"}:
                assert body == {}, "Mutation must carry bounded JSON body"
            if operation == "cancel":
                task["status"] = "cancelled"
                fulfill(route, task)
            elif operation == "register":
                assert task["status"] == "completed"
                task["registeredBundleVersionId"] = NEW_VERSION
                versions.append(copy.deepcopy(new_product))
                fulfill(route, new_product)
            elif operation == "log":
                fulfill(route, {"tail": "UI-ONLY fixture log; no real processing performed"})
            elif operation == "result":
                fulfill(route, {"productVersionId": NEW_VERSION, "resultVersionId": "RESULT-UI-ONLY", "product": new_product, "productionEligible": False})
            else:
                fulfill(route, task)
        elif path in {"tasks", "states", "parameter-versions"}:
            fulfill(route, {"tasks": [], "states": [], "versions": []})
        else:
            fulfill(route, {"error": f"UI-only fixture does not implement {path}"}, 404)

    context.route("**/api/model/**", api_fixture)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda failure: errors.append(str(failure)))
    page.goto(f"{BASE_URL}/#/config", wait_until="networkidle")
    page.wait_for_function(f"() => {STORE}.get('app')?.selectedInputCatalog?.bundleVersionId === '{BASELINE}'")
    page.get_by_role("button", name="水雨情资料", exact=True).click()
    panel = page.get_by_role("region", name="输入资料产品管理")
    panel.get_by_role("heading", name="资料加工与输入版本", exact=True).wait_for()
    page.wait_for_load_state("networkidle")
    panel.locator(".source-registration summary").click()
    panel.get_by_role("button", name="填入非实测格式模板").click()
    template = json.loads(panel.locator("#source-json-editor").input_value())
    assert all(value is None for station in template["stations"] for value in station["values"])
    assert "非实测" in template["name"]
    panel.get_by_role("button", name="校验并登记来源", exact=True).click()
    panel.get_by_role("alert").filter(has_text="模板不能直接登记").wait_for()
    assert not any(row["path"] == "input-sources" for row in posts)

    panel.locator("#input-product-name").fill("UI-only cancelled window")
    panel.locator("#input-window-start").fill(COVERAGE["start"])
    panel.locator("#input-window-end").fill(COVERAGE["end"])
    panel.get_by_role("button", name="生成降雨整编产品", exact=True).click()
    task_table = panel.get_by_role("table", name="资料加工任务")
    cancelled_row = task_table.get_by_role("row").filter(has_text="DATA-UI-ONLY-1")
    cancelled_row.get_by_role("button", name="取消", exact=True).click()
    cancelled_row.get_by_text("已取消", exact=False).wait_for()
    assert cancelled_row.get_by_role("button", name="校验并登记结果").count() == 0
    assert processing[0]["runConfig"]["parentBundleVersionId"] == BASELINE

    panel.locator("#input-product-name").fill("UI-only successful window")
    panel.get_by_role("button", name="生成降雨整编产品", exact=True).click()
    completed_row = task_table.get_by_role("row").filter(has_text="DATA-UI-ONLY-2")
    completed_row.get_by_role("button", name="校验并登记结果").wait_for()
    assert len(versions) == 1
    completed_row.get_by_role("button", name="详情 / 日志").click()
    panel.locator(".product-details").filter(has_text="UI-ONLY fixture log").wait_for()
    completed_row.get_by_role("button", name="校验并登记结果").click()
    version_table = panel.get_by_role("table", name="已登记输入版本")
    new_row = version_table.get_by_role("row").filter(has_text=NEW_VERSION)
    new_row.get_by_role("button", name="选用此版本").wait_for()
    assert page.evaluate(f"{STORE}.get('app').selectedInputBundleVersionId") == BASELINE

    frozen = page.evaluate(f"""() => {{
      const app = {STORE}.get('app'), cal = {STORE}.get('calibration'), monitor = {STORE}.get('monitor'), tasks = {STORE}.get('tasks');
      const request = {{context: {{contextKey: app.contextKey, projectId: app.basinId, resolutionId: app.modelScale, modelConfigId: app.modelConfigId}}, inputs: {{bundleVersionId: '{BASELINE}'}}, lineage: {{inputBundleVersionId: '{BASELINE}', inputBundleManifestSHA256: 'old-sha'}}}};
      tasks.tasksById['JOB-UI-OLD'] = {{taskId: 'JOB-UI-OLD', resultSource: 'model-backend', request, runConfig: request, contextKey: app.contextKey, status: 'completed', createdAt: '2026-10-10T11:00:00'}};
      cal.runResult = {{taskId: 'JOB-UI-OLD', resultSource: 'model-backend', contextKey: app.contextKey, inputBundleVersionId: '{BASELINE}', frozenRequest: request, lineage: request.lineage, isCurrent: true}};
      monitor.historicalPreview = {{sourceType: 'model-qc', times: ['2017-06-18T00:00'], bundleVersionId: '{BASELINE}'}};
      monitor.modelInputProduct = {{sourceType: 'model-qc', bundleVersionId: '{BASELINE}'}};
      app.updateWorkContext({{inputBundleVersionId: '{BASELINE}', sourceTaskId: 'JOB-UI-OLD'}});
      return JSON.stringify(request);
    }}""")
    new_row.get_by_role("button", name="选用此版本").click()
    page.wait_for_function(f"() => {STORE}.get('app').selectedInputBundleVersionId === '{NEW_VERSION}'")
    after = page.evaluate(f"""() => {{const app = {STORE}.get('app'), cal = {STORE}.get('calibration'), monitor = {STORE}.get('monitor'); return {{choice: app.selectedInputBundleVersionId, contextKey: app.contextKey, lineage: app.workContext.inputBundleVersionId, resultInput: cal.runResult.inputBundleVersionId, current: cal.runResult.isCurrent, preview: monitor.historicalPreview, grid: monitor.modelInputProduct, request: JSON.stringify({STORE}.get('tasks').tasksById['JOB-UI-OLD'].request), stored: JSON.parse(localStorage.getItem('hydro_work_contexts_v2') || '{{}}')}};}}""")
    assert after["choice"] == NEW_VERSION
    assert after["lineage"] == after["resultInput"] == BASELINE
    assert after["current"] is False and after["preview"] is None and after["grid"] is None
    assert after["request"] == frozen
    assert after["stored"][after["contextKey"]]["selectedInputBundleVersionId"] == NEW_VERSION

    template["name"] = "UI-only synthetic source (not field observations)"
    template["provenanceNote"] = "UI-only API fixture. This test never posts to a real backend."
    template["stations"] = [{"id": "UI-ONLY-STATION", "lon": 119.1, "lat": 29.8, "values": [None, None]}]
    panel.locator("#source-json-editor").fill(json.dumps(template))
    panel.get_by_role("button", name="校验并登记来源", exact=True).click()
    page.wait_for_function("() => document.querySelector('#input-source')?.value === 'SRC-UI-ONLY'", timeout=2000) if panel.locator("#input-source").count() else None
    panel.get_by_role("status").filter(has_text="来源已登记").wait_for()
    panel.locator("#input-operation").select_option("stationIdw")
    panel.locator("#input-source").select_option("SRC-UI-ONLY")
    panel.locator("#input-product-name").fill("UI-only IDW cancellation")
    panel.get_by_role("button", name="生成降雨整编产品", exact=True).click()
    idw_row = task_table.get_by_role("row").filter(has_text="DATA-UI-ONLY-3")
    idw_row.get_by_role("button", name="取消", exact=True).click()
    idw_row.get_by_text("已取消", exact=False).wait_for()
    idw = processing[2]["runConfig"]
    assert idw["operation"] == "stationIdw" and idw["sourceId"] == "SRC-UI-ONLY"
    assert idw["zeroOnlyPolicy"] == "KEEP" and idw["parentBundleVersionId"] == NEW_VERSION

    blocked = page.evaluate(f"""async () => {{
      const app = {STORE}.get('app'), state = {STORE}.get('state');
      const row = {{id: 'STATE-JOB-UI-OLD', time: '2017-06-18 00:00', domainId: app.basinId, modelScale: app.modelScale, modelConfigId: app.modelConfigId, contextKey: app.contextKey, resultSource: 'model-backend', inputBundleVersionId: '{BASELINE}', checkpoint: {{inputBundleVersionId: '{BASELINE}'}}, means: {{...state.means}}, parameters: {{}}}};
      state.initialStates.unshift(row); state.loadInitialState(row.id); state.dirty = false;
      return await state.runBackendStateTask('continuation', '2017-06-18T01:00');
    }}""")
    assert blocked["ok"] is False and "重新初始化" in blocked["error"]
    assert not any(row["path"] in {"calibration-tasks", "simulation-tasks", "state-tasks"} for row in posts)

    race = page.evaluate(f"""async () => {{const app = {STORE}.get('app'); const pending = app.selectInputBundleVersion('{BASELINE}').then(() => 'selected').catch(error => error.message); app.setModelScale('0p1deg'); return {{error: await pending, selection: app.selectedInputBundleVersionId, scale: app.modelScale}};}}""")
    assert race["scale"] == "0p1deg" and race["selection"] is None and race["error"] != "selected"
    page.evaluate(f"{STORE}.get('app').setModelScale('1km')")
    page.wait_for_function(f"() => {STORE}.get('app').selectedInputCatalog?.bundleVersionId === '{NEW_VERSION}'")
    page.wait_for_load_state("networkidle")
    assert page.evaluate(f"{STORE}.get('app').selectedInputBundleVersionId") == NEW_VERSION
    assert not errors, errors
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / "input-products-ui-fixture.png"), full_page=True)
    report = {"status": "passed", "scope": "Isolated API fixtures only; not real processing or scientific validation", "checks": ["null non-observed template cannot register", "QC processing frozen request", "cancellation cannot register", "completion requires explicit registration", "registration does not activate", "selection clears live cache", "historical request/result lineage unchanged", "station IDW routes to backend with KEEP", "cross-bundle continuation blocked", "pending selection cannot overwrite another scale", "selection restored per workspace"], "posts": posts, "pageErrors": errors}
    (OUTPUT / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "checks": len(report["checks"]), "report": str(OUTPUT / "result.json")}, ensure_ascii=False))
    context.close()
    browser.close()
