"""Read-only actual API/browser regression for an empty registered product.

No model/source/processing/register endpoints are mutated. Version selection
and an audit-only local draft live in a disposable browser context only.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:18883")
EMPTY_VERSION = os.environ.get("UI_EMPTY_INPUT_VERSION", "INPUT-d040bef049e34dd181ca606964bf35fa")
BASELINE = "NANANXI_QC_20260819_CONTROL_V2"
OUTPUT = Path(os.environ.get("UI_OUTPUT_DIR", str(Path(__file__).resolve().parents[3] / "runtime" / "input-catalog-empty-browser")))
STORE = "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1050})
    # This is already a fresh disposable context. Do not clear storage on every
    # navigation: the regression explicitly verifies version/workspace reloads.
    context.add_init_script("if (!localStorage.getItem('hydro_basin_id')) localStorage.setItem('hydro_basin_id', 'nananxi-xufan');")
    def get_catalog(bundle):
        response = context.request.get(f"{BASE_URL}/api/model/inputs/catalog?{urlencode({'bundleVersionId': bundle})}")
        assert response.ok
        return response.json()
    baseline = get_catalog(BASELINE)
    empty = get_catalog(EMPTY_VERSION)
    assert baseline["events"], "Baseline must contain actual registered events"
    assert empty["events"] == [], "Pass an actual registered product with no completely covered events"
    assert empty["bundleVersionId"] == EMPTY_VERSION
    page = context.new_page()
    errors = []
    mutation_calls = []
    page.on("pageerror", lambda failure: errors.append(str(failure)))
    page.on("request", lambda request: mutation_calls.append(request.url) if request.method not in {"GET", "HEAD", "OPTIONS"} and "/api/model/" in request.url else None)
    page.goto(f"{BASE_URL}/#/config", wait_until="networkidle")
    page.wait_for_function(f"() => {STORE}.get('app')?.selectedInputCatalog?.bundleVersionId === '{BASELINE}' && !{STORE}.get('app').inputCatalogError")
    before = page.evaluate(f"""() => {{
      const app = {STORE}.get('app'), cal = {STORE}.get('calibration');
      const runtime = cal.events.filter(event => event.runtimeRegistered === true);
      const event = runtime[0];
      event.note = 'UI-only preservation audit, not a data correction'; event.revision = 2;
      event.history.push({{revision: 1, note: 'UI-only preservation audit'}});
      cal.saveCurrentWorkspace();
      return {{id: event.id, note: event.note, history: JSON.parse(JSON.stringify(event.history)), count: runtime.length, scoped: runtime.every(row => row.basinProjectId === app.basinId), forcingVersion: app.selectedInputProduct.forcingVersion}};
    }}""")
    assert before["count"] == len(baseline["events"]) and before["scoped"]
    page.evaluate(f"{STORE}.get('app').selectInputBundleVersion('{EMPTY_VERSION}')")
    empty_state = page.evaluate(f"""() => {{
      const app = {STORE}.get('app'), cal = {STORE}.get('calibration');
      const local = cal.events.find(event => event.id === '{before['id']}');
      // Old saved workspaces may lack optional fields. This must be safe and
      // store missing data as null without inventing any event or timestamp.
      cal.eventId = undefined; cal.eventStart = undefined; cal.eventEnd = undefined; cal.saveCurrentWorkspace();
      cal.configureBackend(app.backendCase);
      return {{catalog: app.selectedInputCatalog?.bundleVersionId, error: app.inputCatalogError, selected: {{eventId: cal.eventId, eventStart: cal.eventStart, eventEnd: cal.eventEnd}}, runtimeCount: cal.events.filter(event => event.runtimeRegistered).length, local: {{note: local.note, history: local.history, registered: local.runtimeRegistered, status: local.status}}}};
    }}""")
    assert empty_state["catalog"] == EMPTY_VERSION and empty_state["error"] == ""
    assert empty_state["selected"] == {"eventId": None, "eventStart": None, "eventEnd": None}
    assert empty_state["runtimeCount"] == 0
    assert empty_state["local"]["note"] == before["note"]
    assert empty_state["local"]["history"] == before["history"]
    assert empty_state["local"]["registered"] is False and empty_state["local"]["status"] == "draft"
    metadata = page.evaluate(f"({STORE}.get('app').selectedInputProduct)")
    header = page.locator('.data-contract-card[aria-label="真实模型输入版本"]').inner_text()
    assert metadata["forcingVersion"] != before["forcingVersion"]
    assert f"降雨：{metadata['forcingVersion']}；流量：{metadata['observationVersion']}。" in header
    assert EMPTY_VERSION in header
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / "config-selected-source.png"), full_page=True)
    page.goto(f"{BASE_URL}/#/realtime", wait_until="networkidle")
    page.wait_for_function(f"() => {STORE}.get('monitor')?.modelInputProduct?.cells?.length === 110 && {STORE}.get('monitor').modelInputProduct.bundleVersionId === '{EMPTY_VERSION}'")
    page.locator(".replay-mode select").select_option("event")
    no_event = page.locator(".replay-event select")
    assert no_event.is_disabled()
    assert no_event.inner_text().strip() == "本输入版本无覆盖内登记场次"
    assert page.evaluate(f"{STORE}.get('app').inputCatalogError") == ""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / "empty-version-explicit-status.png"), full_page=True)
    page.reload(wait_until="networkidle")
    page.wait_for_function(f"() => {STORE}.get('app')?.selectedInputCatalog?.bundleVersionId === '{EMPTY_VERSION}' && !{STORE}.get('app').inputCatalogError")
    assert page.evaluate(f"{STORE}.get('calibration').eventId") is None

    page.goto(f"{BASE_URL}/#/config", wait_until="networkidle")
    page.evaluate(f"{STORE}.get('app').selectInputBundleVersion('{BASELINE}')")
    restored = page.evaluate(f"""() => {{
      const app = {STORE}.get('app'), cal = {STORE}.get('calibration');
      const events = cal.events.filter(event => event.runtimeRegistered === true);
      const preserved = events.find(event => event.id === '{before['id']}');
      return {{catalog: app.selectedInputCatalog?.bundleVersionId, error: app.inputCatalogError, count: events.length, scoped: events.every(event => event.basinProjectId === app.basinId), selected: cal.eventId, note: preserved.note, history: preserved.history, draft: preserved.localDraft}};
    }}""")
    assert restored["catalog"] == BASELINE and restored["error"] == ""
    assert restored["count"] == len(baseline["events"]) and restored["scoped"]
    assert restored["selected"] in {event["id"] for event in baseline["events"]}
    assert restored["note"] == before["note"] and restored["history"] == before["history"]
    assert restored["draft"]["note"] == before["note"]
    page.reload(wait_until="networkidle")
    page.wait_for_function(f"() => {STORE}.get('app')?.selectedInputCatalog?.bundleVersionId === '{BASELINE}' && !{STORE}.get('app').inputCatalogError")
    assert page.evaluate(f"{STORE}.get('calibration').events.filter(event => event.runtimeRegistered === true && event.basinProjectId === 'nananxi-xufan').length") == len(baseline["events"])
    # A long immutable version ID must not paint over the adjacent warmup time.
    page.evaluate(f"{STORE}.get('app').selectInputBundleVersion('{EMPTY_VERSION}')")
    page.goto(f"{BASE_URL}/#/state", wait_until="networkidle")
    page.wait_for_function(f"() => document.querySelector('.state-input-status div:nth-child(2) b')?.textContent.trim() === '{EMPTY_VERSION}'")
    state_layout_checks = []
    for width in [1440, 1280]:
        page.set_viewport_size({"width": width, "height": 1050})
        layout = page.locator(".state-input-status").evaluate("""panel => [...panel.children].map(cell => {
          const label = cell.querySelector('span').textContent.trim();
          const value = cell.querySelector('b');
          const bounds = cell.getBoundingClientRect();
          const range = document.createRange(); range.selectNodeContents(value);
          const lines = [...range.getClientRects()];
          return { label, value: value.textContent.trim(), lineCount: lines.length,
            fitsCell: lines.every(line => line.left >= bounds.left - 1 && line.right <= bounds.right + 1) };
        })""")
        assert all(cell["fitsCell"] for cell in layout), {"width": width, "cells": layout}
        state_layout_checks.append({"viewportWidth": width, "cells": layout})
    page.screenshot(path=str(OUTPUT / "state-long-input-version.png"), full_page=True)
    assert not errors, errors
    assert mutation_calls == [], mutation_calls
    report = {"status": "passed", "scope": "Actual registered inputs and browser UI; API read-only, disposable local draft only", "emptyBundleVersionId": EMPTY_VERSION, "baselineEventCount": len(baseline["events"]), "checks": ["actual empty catalogue remains loaded without undefined JSON failure", "empty selection is null and persisted safely", "old optional undefined workspace fields do not crash", "local drafts and history survive empty version", "real 110-grid window remains readable", "Timeline distinguishes empty from loading", "empty product and null selection survive reload", "baseline event scope survives merge, persistence and reload", "QC baseline event count and localDraft remain intact", "Config source header matches selected product instead of fixed baseline", "long input version stays within State condition cells at 1440px and 1280px"], "selectedProductMetadata": metadata, "stateLayoutChecks": state_layout_checks, "pageErrors": errors, "apiMutationCalls": mutation_calls}
    (OUTPUT / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "checks": len(report["checks"]), "report": str(OUTPUT / "result.json")}, ensure_ascii=False))
    context.close()
    browser.close()
