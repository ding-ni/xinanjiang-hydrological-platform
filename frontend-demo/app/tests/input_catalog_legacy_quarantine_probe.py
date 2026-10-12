"""API-read-only regression for retained, non-eligible legacy event evidence.

Only disposable browser-local event snapshots are created. No server/source,
processing, registration or model task endpoint is mutated.
"""
import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:18883")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[3] / "runtime" / "input-catalog-legacy-quarantine-probe")
parser.add_argument("--expect-preservation", action="store_true")
arguments = parser.parse_args()
BASELINE = "NANANXI_QC_20260819_CONTROL_V2"
STORE = "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s"
UNMATCHED_ID = "ui-only-unknown-legacy-draft"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1050})
    context.add_init_script("if (!localStorage.getItem('hydro_basin_id')) localStorage.setItem('hydro_basin_id', 'nananxi-xufan');")
    page = context.new_page()
    errors, mutations = [], []
    page.on("pageerror", lambda failure: errors.append(str(failure)))
    page.on("request", lambda request: mutations.append(request.url) if request.method not in {"GET", "HEAD", "OPTIONS"} and "/api/model/" in request.url else None)
    ready = f"() => {STORE}.get('app')?.selectedInputCatalog?.bundleVersionId === '{BASELINE}' && !{STORE}.get('app').inputCatalogError"
    page.goto(f"{BASE_URL}/#/config", wait_until="networkidle")
    page.wait_for_function(ready)
    before = page.evaluate(f"""() => {{
      const cal = {STORE}.get('calibration');
      const foreign = cal.events.find(row => row.runtimeRegistered === true);
      if (!foreign) throw new Error('Actual native event missing');
      foreign.basinProjectId = 'selinco'; foreign.purpose = '独立检验'; foreign.revision = 4;
      foreign.note = 'UI-only explicit foreign legacy evidence';
      foreign.history = [{{revision: 3, note: 'Original foreign history', customReview: {{accepted: false}}}}];
      foreign.localDraft = {{id: foreign.id, basinProjectId: 'selinco', customOriginal: 'retain foreign untouched'}};
      const unknown = {{id: '{UNMATCHED_ID}', eventId: '{UNMATCHED_ID}', basinProjectId: 'unknown-project',
        runtimeRegistered: false, status: 'draft', purpose: '率定', note: 'UI-only unmatched legacy evidence',
        start: null, end: null, observedPeak: null, revision: 3, source: 'UI-only-preservation-probe',
        history: [{{revision: 2, note: 'Original unmatched history'}}], localDraft: {{customOriginal: {{zero: 0, accepted: false}}}}}};
      cal.events.push(unknown); cal.syncEventCatalog();
      const saved = JSON.parse(localStorage.getItem('hydro_calibration_workspaces_v1')).workspaces[cal.contextKey];
      return {{contextKey: cal.contextKey, originals: [JSON.parse(JSON.stringify(foreign)), unknown],
        persisted: saved.events.filter(row => row.id === foreign.id || row.id === '{UNMATCHED_ID}')}};
    }}""")
    assert len(before["persisted"]) == 2

    def state_snapshot():
        return page.evaluate(f"""() => {{
          const cal = {STORE}.get('calibration');
          const saved = JSON.parse(localStorage.getItem('hydro_calibration_workspaces_v1')).workspaces[cal.contextKey];
          return {{quarantinedEvents: cal.quarantinedEvents || [], persistedQuarantine: saved.quarantinedEvents || [],
            unmatchedInEvents: cal.events.some(row => row.id === '{UNMATCHED_ID}'),
            foreignInEvents: cal.events.some(row => row.basinProjectId === 'selinco'),
            runtimeEvents: cal.events.filter(row => row.runtimeRegistered === true).map(row => ({{id: row.id, scope: row.basinProjectId, purpose: row.purpose}}))}};
        }}""")

    page.reload(wait_until="networkidle")
    page.wait_for_function(ready)
    after = state_snapshot()
    page.reload(wait_until="networkidle")
    page.wait_for_function(ready)
    again = state_snapshot()
    preserved = all(original in after["quarantinedEvents"] and original in after["persistedQuarantine"] for original in before["originals"])
    stable = after["quarantinedEvents"] == again["quarantinedEvents"] and after["persistedQuarantine"] == again["persistedQuarantine"]
    ineligible = not after["unmatchedInEvents"] and not after["foreignInEvents"] and all(row["scope"] == "nananxi-xufan" for row in after["runtimeEvents"])
    report = {"status": "preserved-and-quarantined" if preserved and stable and ineligible else "legacy-excluded-record-loss-confirmed",
      "scope": "Actual catalogue plus disposable browser-local snapshots; no backend writes",
      "before": before, "after": after, "afterSecondReload": again, "preservedVerbatim": preserved,
      "stableWithoutSnapshotGrowth": stable, "excludedFromCandidateEvents": ineligible,
      "pageErrors": errors, "apiMutationCalls": mutations}
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    (arguments.output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not errors, errors
    assert not mutations, mutations
    print(json.dumps({"status": report["status"], "report": str(arguments.output_dir / "result.json")}, ensure_ascii=False))
    context.close()
    browser.close()
    if arguments.expect_preservation:
        assert preserved and stable and ineligible, "Excluded legacy records must remain verbatim, stable and non-eligible in quarantine"
