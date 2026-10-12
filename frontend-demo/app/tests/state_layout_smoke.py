"""Regression check: available initial states must not clip or reserve empty metric columns."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
VIEWPORTS = (
    {"width": 2048, "height": 1110},
    {"width": 1440, "height": 900},
    {"width": 1024, "height": 768},
    {"width": 390, "height": 844},
)
BASIN_CASES = (
    ("nananxi-xufan", 0, 2),
    ("selinco", 2, 4),
)


def state_layout(page):
    return page.evaluate(
        """() => {
          const details = (selector) => {
            const el = document.querySelector(selector)
            const box = el.getBoundingClientRect()
            return {
              width: box.width,
              scrollWidth: el.scrollWidth,
              clientWidth: el.clientWidth,
              rows: [...new Set([...el.children].map((child) => Math.round(child.getBoundingClientRect().top)))],
              childWidths: [...el.children].map((child) => child.getBoundingClientRect().width),
            }
          }
          return {
            list: details('.node-list'),
            means: details('.node-means'),
            pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          }
        }"""
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    errors = []

    for basin_id, expected_nodes, expected_metrics in BASIN_CASES:
        for viewport in VIEWPORTS:
            context = browser.new_context(viewport=viewport)
            page = context.new_page()
            page.add_init_script(
                "localStorage.clear(); localStorage.setItem('hydro_basin_id', '%s');" % basin_id
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{BASE_URL}/#/state", wait_until="networkidle")
            page.locator(".view-state").wait_for()
            page.wait_for_timeout(350)

            layout = state_layout(page)
            label = f"{basin_id}/{viewport}"
            assert layout["list"]["scrollWidth"] <= layout["list"]["clientWidth"] + 1, (
                f"{label}: available initial states require horizontal scrolling"
            )
            assert len(layout["list"]["childWidths"]) == expected_nodes, (
                f"{label}: unexpected number of available initial states"
            )
            assert layout["means"]["scrollWidth"] <= layout["means"]["clientWidth"] + 1, (
                f"{label}: initial-state metrics overflow"
            )
            assert len(layout["means"]["childWidths"]) == expected_metrics, (
                f"{label}: unexpected number of initial-state metrics"
            )
            min_share = 0.45 if expected_metrics == 2 else 0.20
            assert min(layout["means"]["childWidths"]) >= layout["means"]["width"] * min_share, (
                f"{label}: state metrics leave unused columns"
            )
            assert layout["pageOverflow"] <= 1, f"{label}: horizontal page overflow"
            context.close()

    assert not errors, f"browser page errors: {errors}"
    browser.close()
    print("State layout smoke passed: initial states fit without clipping or empty metric columns")
