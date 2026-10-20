"""Browser check for basin and model-configuration workspace isolation."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


def wait_for_workspace(page):
    page.locator("main#main-workspace").wait_for()
    page.wait_for_timeout(300)
    text = page.locator("main#main-workspace").inner_text().strip()
    assert len(text) > 120


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        """
        localStorage.clear();
        localStorage.setItem('hydro_basin_id', 'selinco');
        """
    )

    page.goto(f"{BASE_URL}/#/forecast")
    page.wait_for_load_state("networkidle")
    wait_for_workspace(page)

    basin_select = page.locator(".basin-chip .meta-select")
    config_select = page.locator(".scale-chip .meta-select")
    assert basin_select.input_value() == "selinco"
    assert config_select.input_value() == "selinco-1km"

    initial_state_select = page.locator(".settings-panel select.input").nth(1)
    state_values = initial_state_select.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert state_values and all(value.startswith("STATE-SEL") for value in state_values), state_values

    config_select.select_option("selinco-0p1deg")
    page.wait_for_function(
        "() => document.querySelector('.scale-chip .meta-select')?.value === 'selinco-0p1deg'"
    )
    wait_for_workspace(page)
    state_values = initial_state_select.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert state_values and all("0P1" in value for value in state_values), state_values

    basin_select.select_option("nananxi-xufan")
    page.wait_for_function(
        "() => document.querySelector('.scale-chip .meta-select')?.value === 'nananxi-xufan-1km'"
    )
    wait_for_workspace(page)
    state_values = initial_state_select.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert state_values == [], state_values
    assert "无可用状态" in page.locator(".settings-panel").inner_text()

    basin_select.select_option("selinco")
    page.wait_for_function(
        "() => document.querySelector('.scale-chip .meta-select')?.value === 'selinco-0p1deg'"
    )
    wait_for_workspace(page)

    page.goto(f"{BASE_URL}/#/state")
    page.wait_for_load_state("networkidle")
    wait_for_workspace(page)
    assert page.locator(".node-item").count() >= 1
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1

    assert not errors, f"browser page errors: {errors}"
    context.close()
    browser.close()
    print("Context isolation smoke passed: basin and configuration workspaces remain independent")
