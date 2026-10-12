"""Headless, read-only browser diagnosis with bounded requests and UI snapshots."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def inspect(url, output):
    report = {'url': url, 'console': [], 'errors': [], 'requests': [], 'responses': [], 'failures': [], 'steps': []}
    output.mkdir(parents=True, exist_ok=True)

    def record(kind, value):
        report[kind].append(value)
        print(kind, json.dumps(value, ensure_ascii=True), flush=True)

    async def bounded(name, operation, seconds=12):
        try:
            result = await asyncio.wait_for(operation(), timeout=seconds)
            report['steps'].append({'step': name, 'status': 'ok'})
            print('STEP', name, 'ok', flush=True)
            return result
        except Exception as exc:
            report['steps'].append({'step': name, 'status': 'error', 'error': str(exc)})
            print('STEP', name, str(exc), flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = await context.new_page()
        page.set_default_timeout(5000)
        page.on('console', lambda message: record('console', {'type': message.type, 'text': message.text}))
        page.on('pageerror', lambda error: record('errors', str(error)))
        page.on('crash', lambda: record('errors', 'renderer crashed'))
        page.on('request', lambda request: record('requests', {'url': request.url, 'method': request.method}))
        page.on('response', lambda response: record('responses', {'url': response.url, 'status': response.status}))
        page.on('requestfailed', lambda request: record('failures', {'url': request.url, 'failure': request.failure}))

        async def read_only_route(route):
            if route.request.method in {'GET', 'HEAD', 'OPTIONS'}:
                await route.continue_()
            else:
                record('failures', {'url': route.request.url, 'failure': 'diagnostic prevented external write'})
                await route.abort()

        await context.route('**/api/model/**', read_only_route)
        try:
            await bounded('goto-domcontentloaded', lambda: page.goto(url + '/#/calibration', wait_until='domcontentloaded', timeout=10000))
            await bounded('networkidle', lambda: page.wait_for_load_state('networkidle', timeout=10000))
            dom = await bounded('body-text', lambda: page.locator('body').inner_text(timeout=5000), seconds=7)
            if dom is not None:
                (output / 'body.txt').write_text(dom, encoding='utf-8')
            state = await bounded('vue-store-read', lambda: page.evaluate('''() => {
                const app = document.querySelector('#app').__vue_app__;
                const provides = app && app._context.provides;
                const pinia = provides && Reflect.ownKeys(provides).map(key => provides[key]).find(value => value && value._s instanceof Map);
                return { ready: document.readyState, appMounted: Boolean(app), stores: pinia ? Object.fromEntries([...pinia._s].map(([key, value]) => [key, {
                    contextKey: value.contextKey, scienceVersionId: value.scienceVersionId,
                    warmupDays: value.warmupDays, inputSelectionKey: value.inputSelectionKey,
                    eventCount: value.events?.length, backendLoading: value.backendLoading,
                    backendError: value.backendError, inputCatalogError: value.inputCatalogError,
                    runStatus: value.runStatus
                }])) : null };
            }'''), seconds=7)
            report['state'] = state
            await bounded('screenshot', lambda: page.screenshot(path=str(output / 'page.png'), timeout=5000), seconds=7)
        finally:
            (output / 'diagnostic.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            await bounded('browser-close', browser.close, seconds=5)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    asyncio.run(inspect(args.url.rstrip('/'), Path(args.output).resolve()))
