"""Browser regression for frontend-fixture interactions and responsive layout."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
ROUTES = ("realtime", "forecast", "state", "calibration", "archive", "config")
FORBIDDEN_TERMS = (
    "\u5951\u7ea6",
    "\u8349\u6848",
    "\u60c5\u666f",
    "\u6210\u5458",
    "\u53c2\u6570\u5f71\u54cd",
    "CalibrationExperiment",
)


def assert_no_horizontal_overflow(page, label):
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"{label}: horizontal overflow {overflow}px"


def assert_topbar_context_fits(page, label):
    layout = page.evaluate(
        """() => {
          const meta = document.querySelector('.topbar .meta')
          if (!meta || getComputedStyle(meta).display === 'none') return null
          const bounds = meta.getBoundingClientRect()
          return [...meta.querySelectorAll('.chip')]
            .filter((chip) => getComputedStyle(chip).display !== 'none')
            .map((chip) => {
              const rect = chip.getBoundingClientRect()
              return {
                top: rect.top,
                bottom: rect.bottom,
                left: rect.left,
                right: rect.right,
                parentTop: bounds.top,
                parentBottom: bounds.bottom,
                parentLeft: bounds.left,
                parentRight: bounds.right,
              }
            })
        }"""
    )
    if not layout:
        return
    for chip in layout:
        assert chip["top"] >= chip["parentTop"] - 1, f"{label}: top-bar control wraps upward"
        assert chip["bottom"] <= chip["parentBottom"] + 1, f"{label}: top-bar control wraps downward"
        assert chip["left"] >= chip["parentLeft"] - 1, f"{label}: top-bar control is clipped on the left"
        assert chip["right"] <= chip["parentRight"] + 1, f"{label}: top-bar control is clipped on the right"


def open_route(page, route, label):
    page.goto(f"{BASE_URL}/#/{route}")
    page.wait_for_load_state("networkidle")
    page.locator("main#main-workspace").wait_for()
    page.wait_for_timeout(250)
    assert_no_horizontal_overflow(page, label)
    assert_topbar_context_fits(page, label)
    assert page.locator(
        ".fade-enter-active, .fade-leave-active, .fade-enter-from, .fade-leave-to"
    ).count() == 0, f"{label}: route transition frame remains"
    content = page.locator("main#main-workspace").inner_text()
    assert len(content.strip()) > 120, f"{label}: workspace is unexpectedly sparse"
    for term in FORBIDDEN_TERMS:
        assert term not in content, f"{label}: visible forbidden term {term!r}"


def prepare_storage(page):
    page.add_init_script(
        """
        localStorage.setItem('hydro_basin_id', 'selinco');
        localStorage.setItem('hydro_model_scale', '1km');
        """
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    errors = []
    backend_requests = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    prepare_storage(page)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: backend_requests.append(request.url)
            if request.method == "POST" and any(endpoint in request.url for endpoint in
                ("forecast-tasks", "calibration-tasks", "simulation-tasks")) else None)
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error" and "AbortError" not in message.text
        else None,
    )

    for route in ROUTES:
        open_route(page, route, f"{route}-1440")

    open_route(page, "forecast", "forecast-action-1440")
    lead_select = page.locator(".settings-panel .field").filter(
        has=page.locator("label", has_text="预见期")
    ).locator("select.input")
    lead_options = lead_select.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert lead_options == ["6", "12", "24", "48", "72"], lead_options
    assert "120" not in lead_options
    assert lead_select.input_value() == "24", "Selinco duty forecast must default to 24 hours"
    page.get_by_role("button", name="\u5f00\u59cb\u9884\u62a5", exact=True).first.click()
    page.wait_for_function(
        "() => document.body.innerText.includes('\\u9884\\u62a5\\u8ba1\\u7b97\\u5b8c\\u6210')"
    )
    peak_text = page.locator(".forecast-metrics .metric.emphasis b").inner_text()
    assert "\u5f85\u9884\u62a5" not in peak_text
    assert "m" in peak_text
    assert page.locator(".task-status.completed .task-kicker").inner_text() == "试算任务"

    open_route(page, "calibration", "calibration-action-1440")
    page.get_by_role("button", name="\u5f00\u59cb\u7387\u5b9a", exact=True).click()
    page.wait_for_function(
        "() => document.body.innerText.includes('\\u7387\\u5b9a\\u5b8c\\u6210')"
    )
    assert page.locator(".task-status.completed .task-kicker").inner_text() == "试算任务"

    open_route(page, "archive", "archive-terms-1440")
    archive_text = page.locator("main#main-workspace").inner_text()
    assert "li.admin" not in archive_text
    assert "hydro.ops" not in archive_text
    assert "\u5f85\u590d\u6838" in archive_text or "\u5df2\u53d1\u5e03" in archive_text

    for route in ("forecast", "state", "calibration"):
        mobile_context = browser.new_context(viewport={"width": 390, "height": 844})
        mobile = mobile_context.new_page()
        prepare_storage(mobile)
        open_route(mobile, route, f"{route}-390")
        mobile_context.close()

    assert not errors, f"browser errors: {errors}"
    assert not backend_requests, f"unexpected model requests: {backend_requests}"
    context.close()
    browser.close()
    print("UI smoke passed: frontend fixtures, no model requests, routes, and responsive layout")
