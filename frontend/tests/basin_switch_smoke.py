import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
ROUTES = ("realtime", "calibration", "forecast", "state", "archive", "config")


def assert_view_rendered(page, basin_id, route):
    page.goto(f"{BASE_URL}/#/{route}")
    page.wait_for_load_state("networkidle")
    main = page.locator("main#main-workspace")
    main.wait_for()
    page.wait_for_timeout(500)
    assert len(main.inner_text().strip()) > 120, f"{basin_id}/{route}: main workspace is blank"
    assert main.locator(":scope > *").count() > 0, f"{basin_id}/{route}: no rendered view"


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page_errors = []

    for basin_id in ("selinco", "nananxi-xufan", "llpq"):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.add_init_script(
            """
            localStorage.setItem('hydro_basin_id', '%s');
            """ % basin_id
        )
        page.on("pageerror", lambda exc: page_errors.append(f"{basin_id}: {exc}"))

        for route in ROUTES:
            assert_view_rendered(page, basin_id, route)

        assert_view_rendered(page, basin_id, "calibration")
        page.locator(".basin-chip .meta-select").select_option(
            "nananxi-xufan" if basin_id == "selinco" else "selinco"
        )
        page.wait_for_timeout(700)
        main = page.locator("main#main-workspace")
        assert len(main.inner_text().strip()) > 120, f"{basin_id}: calibration is blank after switching basin"
        context.close()

    assert not page_errors, f"browser page errors: {page_errors}"
    browser.close()
    print("Basin switch smoke passed: all routes render for all registered basins")
