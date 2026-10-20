"""检查洪水场次台账的密度、筛选和桌面布局。"""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
OUT = Path(__file__).resolve().parents[2] / "测试结果" / "页面审计" / "模型计算-洪水台账-1440.png"
OUT.parent.mkdir(parents=True, exist_ok=True)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(
        "localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{BASE_URL}/#/calibration", wait_until="networkidle")
    page.get_by_text("洪水场次台账", exact=True).wait_for()

    rows = page.locator(".event-table-row:not(.event-table-header)")
    assert rows.count() == 8, rows.count()
    assert "共 226 场" in page.locator(".event-panel-head").inner_text()
    assert "第 1 / 29 页" in page.locator(".event-pagination").inner_text()

    peak_header = page.locator(".event-sort").filter(has_text="洪峰")
    peak_header.click()
    assert peak_header.get_attribute("aria-sort") == "descending"
    assert "408 m³/s" in rows.first.inner_text()
    peak_header.click()
    assert peak_header.get_attribute("aria-sort") == "ascending"

    page.locator(".event-search input").fill("31240619")
    assert rows.count() == 1
    assert "31240619" in rows.first.inner_text()
    page.locator(".event-search input").fill("")

    chart = page.locator(".process-panel").bounding_box()
    catalog = page.locator(".events-panel").bounding_box()
    params = page.locator(".parameter-panel").bounding_box()
    assert chart and chart["height"] >= 390, chart
    assert catalog and catalog["height"] < 520, catalog
    assert params and params["x"] > chart["x"], (chart, params)
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1

    page.screenshot(path=str(OUT), full_page=True)
    assert not errors, errors
    context.close()
    browser.close()

print(f"Calibration event catalog smoke passed: {OUT}")
