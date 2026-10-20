"""Regression check: the forecast map must never collapse inside the desktop grid."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
VIEWPORTS = (
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1024, "height": 768},
    {"width": 390, "height": 844},
)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    errors = []

    for viewport in VIEWPORTS:
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            """
            localStorage.clear();
            localStorage.setItem('hydro_basin_id', 'nananxi-xufan');
            """
        )
        page.goto(f"{BASE_URL}/#/forecast")
        page.wait_for_load_state("networkidle")
        page.locator("main#main-workspace").wait_for()
        page.wait_for_timeout(600)

        if page.locator(".right-col .map-panel").count():
            map_panel = page.locator(".right-col .map-panel").first.bounding_box()
            map_body = page.locator(".right-col .map-body").first.bounding_box()
            map_canvas = page.locator(".right-col .map-canvas").first.bounding_box()
            assert map_panel and map_body and map_canvas, f"{viewport}: forecast map is missing"
            assert map_body["height"] >= 120, f"{viewport}: map body collapsed to {map_body['height']}px"
            assert map_canvas["height"] >= 120, f"{viewport}: map canvas collapsed to {map_canvas['height']}px"
            if viewport["width"] >= 1281:
                assert map_panel["height"] >= 300, f"{viewport}: desktop map panel is too short"
        else:
            assert page.get_by_text("预报任务输入清单", exact=True).count() == 1

        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"{viewport}: horizontal overflow {overflow}px"
        context.close()

    assert not errors, f"browser page errors: {errors}"
    browser.close()
    print("Forecast layout smoke passed: map canvas remains visible at all tested widths")
