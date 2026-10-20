"""Continuous simulation must save its end state and open state continuation."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("localStorage.clear(); localStorage.setItem('hydro_basin_id', 'selinco');")
    page.goto(f"{BASE_URL}/#/calibration", wait_until="networkidle")

    page.locator(".operation-tabs button", has_text="连续模拟").click()
    page.get_by_role("button", name="开始连续模拟", exact=True).click()
    save_button = page.get_by_role("button", name="保存末状态并进入状态续算", exact=True)
    save_button.wait_for()
    end_state = page.locator(".simulation-result-card b").inner_text()
    save_button.click()

    page.wait_for_url("**/#/state")
    page.locator(".view-state").wait_for()
    assert page.locator(".node-item", has_text="连续模拟末时刻状态").count() == 1
    assert "连续模拟" in page.locator(".state-source-note").inner_text()
    assert end_state in page.locator(".metric-strip").inner_text()

    browser.close()
    print("Simulation-state handoff smoke passed: end state is saved with lineage and opened")
