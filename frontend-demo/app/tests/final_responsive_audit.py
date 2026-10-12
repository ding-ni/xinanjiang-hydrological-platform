"""Final responsive and overlap audit for all six operational pages."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "测试结果" / "最终响应式验收"
OUTPUT.mkdir(parents=True, exist_ok=True)

ROUTES = ("realtime", "calibration", "state", "forecast", "archive", "config")
VIEWPORTS = {
    "desktop-1440": {"width": 1440, "height": 900},
    "laptop-1024": {"width": 1024, "height": 768},
    "tablet-768": {"width": 768, "height": 1024},
    "mobile-390": {"width": 390, "height": 844},
}


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser_errors = []

    for viewport_name, viewport in VIEWPORTS.items():
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        page.add_init_script("localStorage.clear(); localStorage.setItem('hydro_basin_id', 'selinco');")
        page.on("pageerror", lambda error: browser_errors.append(str(error)))

        for route in ROUTES:
            page.goto(f"{BASE_URL}/#/{route}", wait_until="networkidle")
            page.locator("main#main-workspace").wait_for()
            page.wait_for_timeout(200)

            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, f"{viewport_name}/{route}: horizontal overflow {overflow}px"

            clipped_controls = page.evaluate(
                """() => [...document.querySelectorAll('button, select, input')]
                  .filter(el => {
                    const style = getComputedStyle(el)
                    const rect = el.getBoundingClientRect()
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
                  })
                  .filter(el => el.scrollWidth > el.clientWidth + 3)
                  .map(el => (el.innerText || el.value || el.getAttribute('aria-label') || el.tagName).trim())"""
            )
            assert not clipped_controls, f"{viewport_name}/{route}: clipped controls {clipped_controls}"

            page.screenshot(path=str(OUTPUT / f"{route}-{viewport_name}.png"), full_page=True)

        context.close()

    assert not browser_errors, browser_errors
    browser.close()
    print("Final responsive audit passed: 6 routes x 4 viewports, no overflow or clipped controls")
