"""Read-only smoke check of a freshly imported portable release."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'errors': [], 'failedResources': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('response', lambda response: report['failedResources'].append(response.url)
            if response.status >= 400 else None)
    try:
        for route, selector in [('system', '.system-view'), ('calibration', '.view-cal'), ('config', '.view-config'), ('realtime', 'canvas.maplibregl-canvas')]:
            page.goto(args.url + '/?release-check=' + route + '#/' + route)
            expect(page.locator(selector).first).to_be_visible(timeout=60000)
            page.wait_for_load_state('networkidle')
            page.screenshot(path=str(output / (route + '.png')), full_page=True)
            report['checks'].append(route)
        response = page.request.get(args.url + '/api/model/workspace')
        assert response.ok
        report['workspace'] = response.json()
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
        print('Portable release: 4 pages passed')
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
