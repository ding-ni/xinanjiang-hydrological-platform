"""Regression test: a missing rainfall catalog must not crash forecast routing."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for basin in ("nananxi-xufan", "llpq"):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(
            f"""
            localStorage.clear();
            localStorage.setItem('hydro_basin_id', '{basin}');
            localStorage.setItem('hydro_model_scale', '1km');
            """
        )
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        page.goto(f"{BASE_URL}/#/forecast")
        page.wait_for_load_state("networkidle")
        workspace = page.locator("main#main-workspace")
        workspace.get_by_text("预报降雨未接入", exact=True).first.wait_for()
        assert workspace.get_by_text("起报资料准备", exact=True).count() == 1
        assert workspace.get_by_text("无可用预报降雨产品", exact=True).count() == 1
        assert workspace.get_by_text("预报摘要", exact=True).count() == 0
        assert page.get_by_role("button", name="确认并提交预报", exact=True).is_disabled()
        assert not page_errors, f"{basin}: forecast page errors: {page_errors}"

        page.locator('.sidebar a[href="#/state"]').click()
        page.get_by_text("可用起报状态", exact=True).wait_for()
        assert not page_errors, f"{basin}: navigation after forecast failed: {page_errors}"
        context.close()
    browser.close()

print("Forecast empty-catalog smoke passed: non-Selinco 1 km routes stay usable")
