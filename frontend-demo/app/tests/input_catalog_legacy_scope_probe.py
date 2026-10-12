"""API-read-only evidence probe for previously persisted unknown event scope.

It uses a disposable browser context and emulates one browser-local record
written by the older normalizer. No backend mutation is permitted.
"""
import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:18883")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("UI_OUTPUT_DIR", str(Path(__file__).resolve().parents[3] / "runtime" / "input-catalog-legacy-scope-probe"))))
parser.add_argument("--expect-preservation", action="store_true")
arguments = parser.parse_args()
OUTPUT = arguments.output_dir
BASELINE = "NANANXI_QC_20260819_CONTROL_V2"
TARGET = "xf-31170619"
STORE = "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1050})
    context.add_init_script("if (!localStorage.getItem('hydro_basin_id')) localStorage.setItem('hydro_basin_id', 'nananxi-xufan');")
    page = context.new_page()
    errors, mutations = [], []
    page.on("pageerror", lambda failure: errors.append(str(failure)))
    page.on("request", lambda request: mutations.append(request.url) if request.method not in {"GET", "HEAD", "OPTIONS"} and "/api/model/" in request.url else None)
    page.goto(f"{BASE_URL}/#/config", wait_until="networkidle")
    ready = f"() => {STORE}.get('app')?.selectedInputCatalog?.bundleVersionId === '{BASELINE}' && !{STORE}.get('app').inputCatalogError"
    page.wait_for_function(ready)
    before = page.evaluate(f"""() => {{
      const cal = {STORE}.get('calibration');
      const event = cal.events.find(row => row.id === '{TARGET}' && row.runtimeRegistered === true);
      if (!event) throw new Error('Actual registered target event missing');
      event.note = 'UI-only legacy preservation probe, not data correction';
      event.purpose = '独立检验'; event.revision = 4;
      event.history = [{{revision: 3, note: 'UI-only legacy original history', customReview: {{accepted: false}}}}];
      event.localDraft = {{id: event.id, basinProjectId: 'nananxi-xufan', note: event.note, revision: 3, history: JSON.parse(JSON.stringify(event.history)), customOriginal: 'preserve unchanged'}};
      event.basinProjectId = 'unknown-project';
      cal.syncEventCatalog();
      return {{event: JSON.parse(JSON.stringify(event)), contextKey: cal.contextKey, saved: JSON.parse(localStorage.getItem('hydro_calibration_workspaces_v1')).workspaces[cal.contextKey].events.find(row => row.id === '{TARGET}')}};
    }}""")
    assert before["saved"]["basinProjectId"] == "unknown-project"
    page.reload(wait_until="networkidle")
    page.wait_for_function(ready)
    after = page.evaluate(f"""() => {{
      const cal = {STORE}.get('calibration');
      const event = cal.events.find(row => row.id === '{TARGET}');
      return {{event: event ? JSON.parse(JSON.stringify(event)) : null, contextKey: cal.contextKey, saved: JSON.parse(localStorage.getItem('hydro_calibration_workspaces_v1')).workspaces[cal.contextKey].events.find(row => row.id === '{TARGET}')}};
    }}""")
    preserved = after["event"] is not None and all(after["event"].get(key) == before["event"].get(key) for key in ["note", "purpose", "history", "localDraft"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {"status": "preserved" if preserved else "legacy-local-record-loss-confirmed", "scope": "Actual catalogue and disposable browser-local record only; no backend writes", "targetEventId": TARGET, "before": before, "after": after, "pageErrors": errors, "apiMutationCalls": mutations}
    (OUTPUT / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not mutations, mutations
    assert not errors, errors
    print(json.dumps({"status": report["status"], "report": str(OUTPUT / "result.json")}, ensure_ascii=False))
    context.close()
    browser.close()
    if arguments.expect_preservation:
        assert preserved, "Known legacy event lost user assignment, notes, history or original draft"
