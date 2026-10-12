import os
import atexit
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = os.environ.get('UI_BASE_URL')
OUTPUT = Path(os.environ.get('UI_OUTPUT_DIR', str(ROOT / 'runtime/grid-routing-browser')))
OUTPUT.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'errors': [], 'failedRequests': []}
atexit.register(lambda: (OUTPUT / 'acceptance.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'))


def check(name):
    report['checks'].append(name)
    print('PASS', name, flush=True)


@contextmanager
def server_address():
    if FRONTEND:
        yield FRONTEND.rstrip('/')
        return
    python = os.environ.get('XAJ_TEST_PYTHON', str(ROOT / 'backend/.venv/Scripts/python.exe'))
    with (OUTPUT / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(
            [python, '-B', str(ROOT / 'backend/tests/grid_routing_smoke_server.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
        )
        try:
            address = process.stdout.readline().strip()
            if not address.startswith('http://127.0.0.1:'):
                raise RuntimeError(f'测试后端启动失败，查看 {OUTPUT / "server.log"}')
            yield address
        finally:
            try:
                process.communicate(input='\n', timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            if process.returncode:
                raise RuntimeError(f'测试后端退出码 {process.returncode}，查看 {OUTPUT / "server.log"}')


with server_address() as address, sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = report['errors']
    network = report['failedRequests']
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on('requestfailed', lambda request: network.append({'url': request.url, 'failure': request.failure}))
    page.goto(address + '/#/diagnostics', wait_until="domcontentloaded", timeout=120000)
    page.wait_for_load_state("networkidle", timeout=120000)

    grid_view = page.get_by_role("button", name="1 km 网格去向", exact=True)
    try:
        grid_view.wait_for(state="visible", timeout=30000)
    except Exception:
        print("诊断页面：", page.url, page.locator("body").inner_text()[:1200], "浏览器错误：", errors, flush=True)
        raise
    try:
        expect(grid_view).to_be_enabled(timeout=45000)
    except Exception:
        page.screenshot(path=str(OUTPUT / 'load-failure.png'), full_page=True)
        print(json.dumps({'page': page.locator('body').inner_text(), 'errors': errors, 'network': network}, ensure_ascii=True), flush=True)
        raise
    grid_view.click()
    page.locator("svg.grid-map").wait_for(state="visible")
    page.screenshot(path=str(OUTPUT / 'grid-routing-desktop.png'), full_page=True)
    assert page.locator(".grid-feature").count() == 110
    assert "110" in page.locator(".relation-stats").inner_text()

    first_grid = page.locator("svg.grid-map .grid-feature").first
    selected_label = first_grid.get_attribute("aria-label")
    first_grid.click()
    expect(first_grid).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('.selected-marker text')).to_have_text(selected_label.split('，', 1)[0])
    check('110格绘制与鼠标选择')

    second_grid = page.locator('svg.grid-map .grid-feature').nth(1)
    second_grid.focus()
    second_grid.press('Enter')
    expect(second_grid).to_have_attribute('aria-pressed', 'true')
    first_grid.focus()
    first_grid.press('Space')
    expect(first_grid).to_have_attribute('aria-pressed', 'true')
    check('Enter与空格选择格点')

    next_grid = page.locator('.grid-facts button')
    target = next_grid.text_content().split()[0]
    next_grid.click()
    expect(page.get_by_label('选择产流网格', exact=True)).to_have_value(target)
    check('D8下游跳转')
    page.get_by_label("D8 下游方向").uncheck()
    assert page.locator(".flow-arrows line").count() == 0
    page.get_by_label("D8 下游方向").check()
    assert page.locator(".flow-arrows line").count() > 0
    check('D8箭头显示切换')

    reach = page.locator('.mapping-list .reach-link').first
    unit = page.locator('.mapping-row div span').first.text_content()
    reach.click()
    expect(page.get_by_label('选择汇流单元', exact=True)).to_have_value(unit)
    expect(page.locator('.unit-editor .unit-id')).to_have_text(unit)
    expect(page.get_by_role('tab', name='汇流单元', exact=True)).to_have_attribute('aria-selected', 'true')
    check('挂接河段定位单元参数')

    toggle = page.get_by_label('地表径流独立 K', exact=True)
    toggle.check()
    field = page.get_by_label(f'{unit} RS K', exact=True)
    field.fill('6')
    field.blur()
    expect(page.locator('.workbench-status')).to_contain_text('参数已修改')
    with page.expect_download() as download:
        page.get_by_role('button', name='导出汇流参数 JSON', exact=True).click()
    download.value.save_as(str(OUTPUT / 'routing-draft.json'))
    draft = json.loads((OUTPUT / 'routing-draft.json').read_text(encoding='utf-8'))
    assert draft['parameters']['unit_component_slope_k_hours'][unit]['RS'] == 6
    page.get_by_role('button', name='恢复当前版本参数', exact=True).click()
    expect(toggle).not_to_be_checked()
    check('单元独立K编辑导出与恢复')

    with page.expect_download() as download:
        page.get_by_role('button', name='导出空间产品 JSON', exact=True).click()
    download.value.save_as(str(OUTPUT / 'spatial-products.json'))
    product = json.loads((OUTPUT / 'spatial-products.json').read_text(encoding='utf-8'))
    assert product['gridRouting']['summary']['gridCount'] == 110
    assert product['gridRouting']['summary']['mappingRowCount'] == 236
    check('真实网格与面积权重导出')

    original_versions = page.request.get(address + '/api/model/routing-versions').json()['versions']
    toggle.check()
    field.fill('6')
    field.blur()
    page.locator('.name-field input').fill('网格联动实操检验')
    page.get_by_role('button', name='登记新版本', exact=True).click()
    expect(page.get_by_role('status')).to_contain_text('新汇流版本已登记')
    registered = page.request.get(address + '/api/model/routing-versions').json()['versions']
    assert len(registered) == len(original_versions) + 1
    version = next(row for row in registered if row['name'] == '网格联动实操检验')
    assert version['parameters']['unit_component_slope_k_hours'][unit]['RS'] == 6
    assert next(row for row in registered if row['routingVersionId'] == 'control-split-v2')['parameters'] == next(
        row for row in original_versions if row['routingVersionId'] == 'control-split-v2')['parameters']
    page.get_by_role('button', name='选用此版本', exact=True).click()
    expect(page.locator('.identity').first).to_contain_text('网格联动实操检验')
    check('登记并选用独立汇流版本且原方案不变')

    page.get_by_role("button", name="河网与汇流单元", exact=True).click()
    page.locator(".topology-map").wait_for(state="visible")
    page.locator(".topology-map canvas").wait_for(state="visible", timeout=10000)
    for _ in range(2):
        grid_view.click()
        expect(page.locator('.grid-feature')).to_have_count(110)
        page.get_by_role('button', name='河网与汇流单元', exact=True).click()
        page.locator('.topology-map canvas').wait_for(state='visible')
    check('网格与河网地图反复切换')

    grid_view.click()
    page.get_by_role('button', name='刷新', exact=True).click()
    expect(page.get_by_role('button', name='登记新版本', exact=True)).to_be_enabled()
    expect(page.locator('.grid-feature')).to_have_count(110)
    expect(page.get_by_role('alert')).to_have_count(0)
    check('刷新保留网格视图')

    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(OUTPUT / 'grid-routing-mobile.png'), full_page=True)
    assert page.locator('.diagnostic-view').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    assert page.locator('.grid-routing').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
    check('390px窄屏无横向溢出')
    assert not errors, f"浏览器运行错误：{errors}"
    assert not network, network
    report['status'] = 'passed'
    (OUTPUT / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
