"""Exercise the colleague's registered source plan through existing Vue pages."""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'checks': [], 'errors': [], 'failedResources': [], 'tasks': {}}

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.on('response', lambda response: report['failedResources'].append({'url': response.url, 'status': response.status}) if response.status >= 400 else None)
        submitted = []
        page.on('request', lambda request: submitted.append(request.url) if request.method == 'POST' and request.url.split('?')[0].endswith(('/tasks', '/forecast-tasks')) else None)

        def api(path):
            response = context.request.get(args.url.rstrip('/') + '/api/model/' + path, timeout=60000)
            assert response.ok, response.text()
            return response.json()

        def check(label):
            report['checks'].append(label)
            save('acceptance.json', report)
            print('PASS', label, flush=True)

        def fill(locator, value):
            locator.fill(value)
            locator.press('Tab')

        def submit(button, confirm=False):
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.split('?')[0].endswith(('/api/model/tasks', '/api/model/forecast-tasks')), timeout=60000) as response:
                expect(button).to_be_enabled(timeout=60000)
                button.click()
                if confirm:
                    page.locator('.el-message-box').get_by_role('button', name='确认提交', exact=True).click()
            assert response.value.status == 202, response.value.text()
            return response.value.json()

        def complete(label, task):
            report['tasks'][label] = task['taskId']
            save('acceptance.json', report)
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                status = api('tasks/' + task['taskId'])
                if status['status'] not in {'queued', 'running'}:
                    assert status['status'] == 'completed', status
                    result = api('tasks/' + task['taskId'] + '/result')
                    save(label + '.json', {'task': status, 'result': result})
                    return result
                page.wait_for_timeout(500)
            raise AssertionError(label + ' task timed out: ' + task['taskId'])

        def draft():
            raw = page.evaluate("localStorage.getItem('hydro_calibration_workspaces_v1')")
            value = json.loads(raw)
            return value['workspaces'][candidate['context']['contextKey']]

        def workspace_selection():
            return page.evaluate("""() => {
                const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
                const app = pinia._s.get('app'), state = pinia._s.get('state'), cal = pinia._s.get('calibration');
                return {contextKey: app.contextKey, inputSelectionKey: app.inputSelectionKey,
                    inputBundleVersionId: app.selectedInputBundleVersionId, routingVersionId: app.selectedRoutingVersionId,
                    parameterVersionId: cal.paramVersion, scienceVersionId: cal.scienceVersionId,
                    params: {...cal.params}, stateContextKey: state.contextKey,
                    selectedStateVersionId: state.adoptedVersion, stateTaskStatus: state.lastTaskStatus,
                    stateTaskRunning: state.taskRunning};
            }""")

        def adopt_candidate():
            page.locator('#cal-version').select_option(version)
            # The same science value may already be displayed before loadVersion
            # finishes verifying the candidate's input. Wait for its UI action.
            expect(page.locator('#cal-version')).to_be_enabled(timeout=60000)
            expect(page.locator('#science-version')).to_have_value(science, timeout=60000)
            expect(page.locator('#cal-event option')).to_have_count(17, timeout=60000)
            selected = workspace_selection()
            assert selected['inputBundleVersionId'] == candidate['requiredInputBundleVersionId'], selected
            assert selected['routingVersionId'] == candidate['routingCandidate']['routingVersionId'], selected
            assert selected['parameterVersionId'] == version, selected
            assert selected['params'] == candidate['bestParameters'], selected
            return selected

        def layout(label):
            for width in (1440, 390, 360):
                page.set_viewport_size({'width': width, 'height': 1000})
                page.wait_for_timeout(250)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (label, width)
                page.screenshot(path=str(output / (label + '-' + str(width) + '.png')), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})

        try:
            catalog = api('catalog')
            implementation = catalog['cases']['nananxi-xufan:1km']['modelImplementationSHA256']
            candidates = api('parameter-versions')['versions']
            candidate = next(row for row in candidates if row.get('candidateKind') == 'frozen-calibration'
                             and row.get('originalCalibrationPlan')
                             and row.get('lineage', {}).get('modelImplementationSHA256') == implementation)
            version = candidate['parameterVersionId']
            science = candidate['science']['versionId']
            original = api('parameter-versions/' + version + '/original-plan')
            plan, source_request = original['plan'], original['request']
            save('source-candidate.json', candidate)
            save('source-original-plan.json', original)
            report.update(parameterVersionId=version, scienceVersionId=science,
                          inputBundleVersionId=candidate['requiredInputBundleVersionId'], modelImplementationSHA256=implementation)

            page.goto(args.url.rstrip('/') + '/#/calibration')
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='参数率定', exact=False).click()
            page.get_by_role('button', name='洪水场次', exact=True).click()
            save('candidate-selection.json', adopt_candidate())
            group = page.locator('details').filter(has=page.locator('summary').filter(has_text='同事原方案'))
            group.locator('summary').click()
            before = len(submitted)
            group.get_by_role('button', name='恢复原率定设置', exact=True).click()
            expect(group.get_by_role('button', name='恢复原率定设置', exact=True)).to_be_enabled(timeout=60000)
            expect(page.locator('#max-evals')).to_have_value('960', timeout=60000)
            expect(page.locator('#population-size')).to_have_value('48')
            expect(page.locator('#search-seed')).to_have_value(str(plan['sourceSearchSeed']))
            expect(page.locator('#joint-profile')).to_have_value('reference-16')
            kge = page.locator('.objective-row').filter(has_text='KGE')
            expect(kge.locator('input[type=checkbox]')).to_be_checked()
            expect(kge.locator('input[type=number]')).to_have_value('0.2')
            page.wait_for_timeout(1000)
            restored = draft()
            assert restored['params'] == source_request['calibration']['initialParameters']
            assert restored['searchParameters'] == source_request['calibration']['searchParameters']
            assert restored['routingSearchParameters'] == source_request['calibration']['routingSearchParameters']
            assert restored['searchParameterOrder'] == source_request['calibration']['searchParameterOrder']
            assert restored['optimizerSettings'] == {key: value for key, value in source_request['calibration']['optimizer'].items()
                                                    if key in {'differentialWeight', 'crossoverProbability', 'generations', 'initializationPolicy', 'initialCandidates', 'effectiveInitialCandidates'}}
            assert restored['criteria']['weights'] == source_request['calibration']['objectives']['weights']
            assert restored['eventSplitSeed'] == plan['sourceSplitSeed']
            expected_roles = {row['id']: row['purpose'] for row in source_request['period']['events']}
            roles = {row['id']: row['purpose'] for row in restored['events'] if row.get('purpose') != '未使用' and row.get('runtimeRegistered')}
            assert roles == expected_roles
            expect(page.locator('.physical-group-note')).to_contain_text('7 个物理洪水组')
            assert len(submitted) == before, 'Restoring settings submitted a calibration search'
            save('restored-draft.json', restored)
            check('original-reference-settings-restored-exactly-without-starting-search')
            layout('source-settings')

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                stored = api('workspace/drafts').get('drafts', {}).get('hydro_calibration_workspaces_v1', {}).get('value', {})
                saved = stored.get('workspaces', {}).get(candidate['context']['contextKey'], {})
                if saved.get('sourcePlan') == restored['sourcePlan']:
                    break
                page.wait_for_timeout(300)
            else:
                raise AssertionError('Original settings did not reach the shared draft registry')
            check('original-settings-reach-shared-draft-registry')

            before = len(submitted)
            fill(page.locator('#search-seed'), str(plan['sourceSearchSeed'] + 1))
            page.wait_for_timeout(500)
            changed = draft()
            assert changed['sourcePlan'] is None
            assert changed['originalPlanChanged'] is True
            assert len(submitted) == before
            expect(page.locator('.source-candidate-note').filter(has_text='原方案已调整')).to_be_visible()
            page.get_by_role('button', name='恢复原率定设置', exact=True).click()
            expect(page.locator('#search-seed')).to_have_value(str(plan['sourceSearchSeed']), timeout=60000)
            expect(page.get_by_role('button', name='恢复原率定设置', exact=True)).to_be_enabled(timeout=60000)
            assert draft()['sourcePlan']['operation'] == 'search'
            check('editing-original-search-detaches-source-identity-without-submitting')

            replay = submit(page.get_by_role('button', name='原方案重放', exact=True))
            frozen = replay['runConfig']
            assert frozen['sourcePlan']['operation'] == 'fixed-replay'
            assert len(frozen['period']['events']) == 17
            assert frozen['calibration']['optimizer']['maxEvaluations'] == 1
            assert frozen['calibration']['initialParameters'] == candidate['bestParameters']
            assert frozen['science']['versionId'] == science
            replay_result = complete('source-fixed-17', replay)
            assert len(replay_result['hydrographs']) == 17
            assert replay_result['productionEligible'] is False
            check('source-17-fixed-parameter-replay-completes-through-existing-page')

            expect(page.locator('#cal-version')).to_be_enabled(timeout=60000)
            save('state-candidate-selection.json', adopt_candidate())
            page.goto(args.url.rstrip('/') + '/#/state')
            page.wait_for_load_state('networkidle')
            states = api('states')['states']
            incompatible = [state for state in states if state.get('modelImplementationSHA256') != implementation]
            if incompatible:
                old = incompatible[0]
                node = page.locator('.state-library .node-item[title="' + old['stateVersionId'] + '"]')
                node.click()
                expect(page.locator('.state-library .library-error')).to_contain_text('重新', timeout=60000)
                expect(page.locator('.state-library').get_by_role('button', name='用于续算', exact=True)).to_be_disabled()
                report['legacyStateChecked'] = old['stateVersionId']
                check('real-old-state-remains-readable-and-shows-reinitialization-requirement')
            else:
                report['legacyStateBrowserCoverage'] = 'No incompatible persisted state exists in this isolated workspace; unit tests cover the guard.'
            page.get_by_role('button', name='生成初始状态', exact=True).click()
            expect(page.locator('#initialization-strategy')).to_have_value('CONTINUOUS', timeout=60000)
            expect(page.locator('.state-task-panel')).to_contain_text(candidate['continuousOrigin'].replace('T', ' '))
            fill(page.locator('#state-target-time'), '2021-01-01T00:00')
            save('state-selection-before-initialization.json', workspace_selection())
            initial_task = submit(page.get_by_role('button', name='开始初始化', exact=True))
            assert initial_task['runConfig']['science']['versionId'] == science
            assert initial_task['runConfig']['period']['continuousOrigin'] == candidate['continuousOrigin']
            initial = complete('source-initial-state', initial_task)
            assert initial['lineage']['scienceVersionId'] == science
            expect(page.locator('.state-library .node-item.active')).to_have_attribute('title', initial['stateVersionId'], timeout=60000)
            save('state-selection-after-initialization.json', workspace_selection())
            check('continuous-source-initial-state-uses-original-origin-and-scientific-configuration')

            page.get_by_role('button', name='续算状态', exact=True).click()
            fill(page.locator('#state-target-time'), '2021-01-01T03:00')
            continuation_task = submit(page.get_by_role('button', name='开始续算', exact=True))
            assert continuation_task['runConfig']['science']['versionId'] == science
            assert continuation_task['runConfig']['period']['initialStateVersionId'] == initial['stateVersionId']
            continuation = complete('source-state-continuation', continuation_task)
            assert continuation['lineage']['scienceVersionId'] == science
            expect(page.locator('.state-library .node-item.active')).to_have_attribute('title', continuation['stateVersionId'], timeout=60000)
            layout('source-state')
            check('state-continuation-preserves-parent-scientific-configuration')

            page.goto(args.url.rstrip('/') + '/#/forecast')
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='人工预报', exact=False).click()
            fill(page.locator('#forecast-issue-time'), '2021-01-01T06:00')
            page.locator('#forecast-state').select_option(continuation['stateVersionId'])
            catchup_task = submit(page.get_by_role('button', name='补算至起报时刻', exact=True))
            assert catchup_task['runConfig']['science']['versionId'] == science
            catchup = complete('source-forecast-catchup', catchup_task)
            expect(page.locator('#forecast-state')).to_have_value(catchup['stateVersionId'], timeout=60000)
            page.locator('#forecast-lead').select_option('6')
            editor = page.locator('.rain-editor')
            if not editor.evaluate('element => element.open'):
                editor.locator('summary').click()
            fill(page.locator('#forecast-rain-name'), '原方案接入验收 · 六小时雨量')
            fill(page.locator('#forecast-hourly-rainfall'), '0 1 5 10 2 0')
            page.get_by_role('button', name='保存并采用情景', exact=True).click()
            expect(page.locator('#forecast-rain-product option')).not_to_have_count(0, timeout=60000)
            forecast_task = submit(page.locator('.run-button'), confirm=True)
            assert forecast_task['runConfig']['science']['versionId'] == science
            assert forecast_task['runConfig']['period']['initialStateVersionId'] == catchup['stateVersionId']
            forecast = complete('source-manual-forecast', forecast_task)
            assert forecast['lineage']['scienceVersionId'] == science
            assert len(forecast['hydrograph']['forecast']) == 6
            assert forecast['stateVersionId'] is None
            assert forecast['parentStateVersionId'] == catchup['stateVersionId']
            layout('source-forecast')
            check('catchup-and-manual-forecast-preserve-parent-science-and-complete-six-hours')

            # Finish all operations in the first browser before checking a new
            # browser. Concurrent draft writers correctly cause revision 409s.
            page.goto(args.url.rstrip('/') + '/#/calibration')
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='参数率定', exact=False).click()
            group = page.locator('details').filter(has=page.locator('summary').filter(has_text='同事原方案'))
            if not group.evaluate('element => element.open'):
                group.locator('summary').click()
            before = len(submitted)
            group.get_by_role('button', name='恢复原率定设置', exact=True).click()
            expect(group.get_by_role('button', name='恢复原率定设置', exact=True)).to_be_enabled(timeout=60000)
            assert len(submitted) == before
            final_draft = draft()
            assert final_draft['sourcePlan'] == restored['sourcePlan']
            page.wait_for_function("""() => Object.keys(localStorage)
                .filter(key => key.startsWith('xaj_pending_drafts:'))
                .every(key => Object.keys(JSON.parse(localStorage.getItem(key) || '{}')).length === 0)""", timeout=60000)
            saved = api('workspace/drafts')['drafts']['hydro_calibration_workspaces_v1']['value']['workspaces'][candidate['context']['contextKey']]
            assert saved['sourcePlan'] == final_draft['sourcePlan']
            assert saved['optimizerSettings'] == final_draft['optimizerSettings']
            save('final-shared-draft.json', saved)
            page.screenshot(path=str(output / 'before-fresh-browser.png'), full_page=True)
            context.close()
            fresh = browser.new_context(viewport={'width': 1440, 'height': 1000})
            fresh_page = fresh.new_page()
            page = fresh_page
            fresh_page.on('pageerror', lambda error: report['errors'].append(str(error)))
            fresh_page.on('response', lambda response: report['failedResources'].append({'url': response.url, 'status': response.status}) if response.status >= 400 else None)
            fresh_page.goto(args.url.rstrip('/') + '/#/calibration')
            fresh_page.wait_for_load_state('networkidle')
            fresh_page.get_by_role('button', name='参数率定', exact=False).click()
            expect(fresh_page.locator('#science-version')).to_have_value(science, timeout=60000)
            expect(fresh_page.locator('#max-evals')).to_have_value('960')
            expect(fresh_page.locator('.objective-row').filter(has_text='KGE').locator('input[type=checkbox]')).to_be_checked()
            fresh_raw = json.loads(fresh_page.evaluate("localStorage.getItem('hydro_calibration_workspaces_v1')"))
            fresh_draft = fresh_raw['workspaces'][candidate['context']['contextKey']]
            assert fresh_draft['sourcePlan'] == final_draft['sourcePlan']
            assert fresh_draft['optimizerSettings'] == final_draft['optimizerSettings']
            save('fresh-browser-draft.json', fresh_draft)
            check('original-settings-survive-shared-draft-and-fresh-browser')
            assert not report['errors'], report['errors']
            assert not report['failedResources'], report['failedResources']
            report['status'] = 'passed'
        except Exception as error:
            report['failure'] = str(error)
            raise
        finally:
            save('acceptance.json', report)
            try:
                save('last-workspace-selection.json', workspace_selection())
                page.screenshot(path=str(output / 'last-ui.png'), full_page=True, timeout=5000)
                (output / 'last-body.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
            except Exception:
                pass
            browser.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
