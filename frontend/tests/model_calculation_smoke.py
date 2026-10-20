"""验证模型计算页的连续模拟与参数率定是独立作业。"""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.add_init_script("localStorage.clear(); localStorage.setItem('hydro_basin_id', 'selinco');")
    page.goto(f"{BASE_URL}/#/calibration", wait_until="networkidle")

    page.locator(".operation-tabs button", has_text="连续模拟").click()
    settings = page.locator(".setting-panel")
    assert "开始连续模拟" in settings.inner_text()
    assert "搜索方法" not in settings.inner_text()
    assert "评价次数" not in settings.inner_text()
    assert page.locator(".parameter-panel").count() == 0

    page.get_by_role("button", name="开始连续模拟", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('.setting-panel .task-status')?.classList.contains('completed')"
    )
    page.locator(".simulation-result-card").wait_for()
    assert "末时刻状态版本" in settings.inner_text()
    persisted = page.evaluate("() => JSON.parse(localStorage.getItem('hydro_fixture_tasks_v1') || '{}').tasks || []")
    simulations = [task for task in persisted if task["runType"] == "continuousSimulation"]
    assert simulations and simulations[0]["status"] == "completed", simulations

    page.locator(".operation-tabs button", has_text="参数率定").click()
    assert "搜索方法" in settings.inner_text()
    assert "新增洪水场次" not in settings.inner_text()
    assert "先在下方洪水台账设置用途" in settings.inner_text()
    assert page.locator(".parameter-panel").count() == 1

    page.locator(".basin-chip .meta-select").select_option("llpq")
    page.locator(".operation-tabs button", has_text="连续模拟").click()
    page.wait_for_timeout(400)
    disabled = page.get_by_role("button", name="开始连续模拟", exact=True).is_disabled()
    assert disabled
    assert "缺少小时驱动" in settings.inner_text()

    browser.close()
    print("Model calculation smoke passed: continuous simulation and calibration are separated")
