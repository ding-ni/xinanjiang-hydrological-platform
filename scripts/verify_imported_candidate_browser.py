"""Verify the real migrated candidate selects its complete registered inputs."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'errors': []}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    try:
        response = context.request.get(args.url + '/api/model/parameter-versions')
        assert response.ok, response.text()
        payload = response.json()
        rows = payload.get('versions', payload.get('parameters', []))
        candidate = next(row for row in rows if row.get('candidateKind') == 'frozen-calibration'
                         and row.get('requiredSpatialTargetPolicy') == 'LEGACY_CLOSED_RING_VERTEX_MEAN')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='参数率定', exact=False).click()
        page.locator('#cal-version').select_option(candidate['parameterVersionId'])
        expect(page.locator('#science-version')).to_have_value(candidate['science']['versionId'], timeout=30000)
        expect(page.get_by_label('预热期天数', exact=True)).to_have_value('7')
        expect(page.get_by_label('预热期天数', exact=True)).to_be_disabled()
        page.get_by_role('button', name='洪水场次', exact=True).click()
        page.wait_for_load_state('networkidle')
        catalog_response = context.request.get(args.url + '/api/model/inputs/catalog', params={
            'projectId': 'nananxi-xufan', 'resolutionId': '1km',
            'bundleVersionId': candidate['requiredInputBundleVersionId']})
        assert catalog_response.ok, catalog_response.text()
        selected_catalog = catalog_response.json()
        assert selected_catalog['bundleVersionId'] == candidate['requiredInputBundleVersionId']
        expected_ids = sorted(row.get('id') or row.get('eventId') for row in selected_catalog['events']
                              if row.get('status') != 'inactive')
        expect(page.locator('#cal-event option')).to_have_count(len(expected_ids), timeout=30000)
        body = page.locator('body').inner_text()
        (output / 'body.txt').write_text(body, encoding='utf-8')
        expect(page.locator('.submission-plan')).to_contain_text(candidate['continuousOrigin'])
        expect(page.locator('.submission-plan')).to_contain_text(candidate['requiredInputBundleVersionId'])
        # Query the currently selected input through the UI's public service.
        # No default/global input was changed merely by this explicit adoption.
        events = page.locator('#cal-event option').all_text_contents()
        option_ids = sorted(page.locator('#cal-event option').evaluate_all('options => options.map(option => option.value)'))
        report.update(expectedEventIds=expected_ids, eventOptionIds=option_ids)
        assert option_ids == expected_ids, {'actual': option_ids, 'expected': expected_ids}
        assert any('2021-08-11' in value for value in events), events[:10]
        assert not any('1964' in value for value in events), events[:10]
        assert '原' in body and ('复算' in body or '评价' in body)
        report.update(status='passed', parameterVersionId=candidate['parameterVersionId'],
                      requiredInputBundleVersionId=candidate['requiredInputBundleVersionId'],
                      scienceVersionId=candidate['science']['versionId'], continuousOrigin=candidate['continuousOrigin'],
                      warmupDays=7, visibleEventOptions=len(events), productionEligible=False)
        assert not report['errors'], report['errors']
        page.screenshot(path=str(output / 'imported-candidate.png'), full_page=True)
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
