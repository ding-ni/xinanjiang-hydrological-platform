"""核验南安溪率定、起算状态和预报页面的水文业务表达。"""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    errors = []

    for viewport in ({"width": 1600, "height": 1000}, {"width": 1920, "height": 1080}):
        context = browser.new_context(viewport=viewport)
        context.add_init_script(
            "localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.goto(f"{BASE_URL}/#/calibration", wait_until="networkidle")
        page.get_by_text("洪水场次台账", exact=True).wait_for()
        assert page.get_by_text("率定与模拟", exact=True).count() >= 1
        assert "按 0 mm/h" in page.locator(".setting-panel").inner_text()
        page.get_by_role("button", name="随机按 7∶2∶1 划分", exact=True).click()
        counts = page.locator(".event-counts").inner_text()
        assert "158 场" in counts and "45 场" in counts and "23 场" in counts
        assert page.locator(".event-batch-actions input").input_value() == "42"
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        page.screenshot(path=f"测试结果/南安溪水文率定_{viewport['width']}x{viewport['height']}.png", full_page=True)

        page.locator('.sidebar a[href="#/state"]').click()
        page.wait_for_url("**/#/state")
        page.get_by_text("状态计算", exact=True).wait_for()
        state_body = page.locator("body").inner_text()
        assert "场次预热按 0 mm/h" in state_body, state_body
        assert "起算状态计算服务尚未接入" in state_body, state_body

        page.locator('.sidebar a[href="#/forecast"]').click()
        page.wait_for_url("**/#/forecast")
        page.get_by_text("预报任务输入清单", exact=True).wait_for()
        body = page.locator("body").inner_text()
        assert "产汇流计算" in body
        assert "小时尺度场次洪水处理" in body
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        page.screenshot(path=f"测试结果/南安溪水文预报_{viewport['width']}x{viewport['height']}.png", full_page=True)

        page.locator('.sidebar a[href="#/config"]').click()
        page.wait_for_url("**/#/config")
        page.get_by_role("button", name="资料与对象", exact=True).click()
        assert page.get_by_label("预热期（天）").input_value() == "90"
        assert "资料共同覆盖" in page.locator("body").inner_text()
        page.get_by_role("button", name="水雨情资料", exact=True).click()
        body = page.locator("body").inner_text()
        assert "1 km 实测降雨栅格" in body
        assert "场次蒸散发" in body
        assert "更新资料目录" in body
        assert "后端完成降雨空间化" not in body
        page.get_by_role("button", name="洪水场次", exact=True).click()
        peak_header = page.locator(".flood-ledger-table .table-sort").filter(has_text="Qmax")
        peak_header.click()
        assert peak_header.get_attribute("aria-sort") == "descending"
        assert "408 m³/s" in page.locator(".flood-ledger-table tbody tr").first.inner_text()
        peak_header.click()
        assert peak_header.get_attribute("aria-sort") == "ascending"
        context.close()

    assert not errors, errors
    browser.close()

print("Nananxi hydrology workflow smoke passed")
