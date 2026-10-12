"""Browser regression for catalog-driven cases and empty-case isolation.

The extra case is served from in-memory routes; no case files are created or
moved. Run against Vite or the production site under a URL subdirectory.
"""

from __future__ import annotations

import argparse
import json
import os

from playwright.sync_api import sync_playwright


TEST_CASE_ID = "empty-portable-case"
ROUTES = ("realtime", "forecast", "state", "calibration", "archive", "config")
FOREIGN_LABELS = ("新安江大桥", "兰江口", "寿昌控制断面", "徐畈（二）", "扎加藏布入湖口")


def case_documents():
    resolutions = {
        scale: {
            "label": label,
            "status": "draft",
            "statusLabel": "资料待登记",
            "inputStatus": "missing",
            "missingInputs": ["小时降水", "出口流量"],
            "spatialManifest": f"./{scale}/spatial.json",
            "datasetCatalog": f"./{scale}/datasets.json",
            "fixtureCatalog": f"./{scale}/fixtures.json",
        }
        for scale, label in (("1km", "1 km"), ("0p1deg", "0.1°"))
    }
    documents = {
        "manifest.json": {
            "schemaVersion": "1.0",
            "id": TEST_CASE_ID,
            "name": "空资料测试流域",
            "modelFrameworkId": "distributed-xinanjiang",
            "capabilities": {
                capability: True
                for capability in (
                    "forecast", "stateContinuation", "eventCalibration",
                    "continuousCalibration", "continuousSimulation",
                )
            },
            "resolutions": resolutions,
        }
    }
    for scale in resolutions:
        documents[f"{scale}/spatial.json"] = {}
        documents[f"{scale}/datasets.json"] = {"datasets": [], "missingInputs": ["小时降水", "出口流量"]}
        documents[f"{scale}/fixtures.json"] = {"resultSource": "unavailable", "runs": []}
    return documents


def install_test_case(context):
    documents = case_documents()

    def catalog_route(route):
        response = route.fetch()
        catalog = response.json()
        catalog["projects"].append({
            "id": TEST_CASE_ID, "name": "空资料测试流域",
            "manifest": f"./{TEST_CASE_ID}/manifest.json",
        })
        route.fulfill(response=response, json=catalog)

    def case_route(route):
        relative = route.request.url.split(f"/data/projects/{TEST_CASE_ID}/", 1)[1].split("?", 1)[0]
        payload = documents.get(relative)
        if payload is None:
            route.fulfill(status=404, body="unexpected test resource")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    context.route("**/data/projects/catalog.json", catalog_route)
    context.route(f"**/data/projects/{TEST_CASE_ID}/**", case_route)


def wait_ready(page):
    page.wait_for_load_state("networkidle")
    page.locator("main#main-workspace").wait_for()
    page.wait_for_function("!document.querySelector('.chip.status b')?.textContent?.includes('加载')")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173"))
    parser.add_argument("--inspect", action="store_true", help="Inspect rendered selectors without running case-switch assertions.")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        install_test_case(context)
        page = context.new_page()
        page_errors = []
        resource_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("response", lambda response: resource_errors.append(f"{response.status} {response.url}")
                if response.status >= 400 and "/data/" in response.url else None)
        # Fresh browser context only; no existing user storage is altered.
        page.add_init_script(f"localStorage.setItem('hydro_basin_id', '{TEST_CASE_ID}');")
        try:
            page.goto(f"{base_url}/#/forecast")
            wait_ready(page)
            print(json.dumps({
                "inspection": {
                    "selects": page.locator(".topbar select").evaluate_all(
                        "rows => rows.map(row => ({className: row.className, value: row.value, options: [...row.options].map(option => ({value: option.value, label: option.text}))}))"
                    ),
                    "main": page.locator("main#main-workspace").inner_text()[:500],
                }
            }, ensure_ascii=False))
            if args.inspect:
                assert not page_errors, page_errors
                return

            basin_select = page.locator(".basin-chip .meta-select")
            scale_select = page.locator(".scale-chip .meta-select")
            assert basin_select.input_value() == TEST_CASE_ID, "persisted catalog-only case was not restored"
            actual_cases = basin_select.locator("option").evaluate_all("rows => rows.map(row => row.value)")
            assert set(actual_cases) == {"nananxi-xufan", "llpq", "selinco", TEST_CASE_ID}, actual_cases

            checked = []
            for case_id in ("selinco", "nananxi-xufan", "llpq", TEST_CASE_ID):
                basin_select.select_option(case_id)
                wait_ready(page)
                for scale in ("1km", "0p1deg"):
                    scale_select.select_option(f"{case_id}-{scale}")
                    wait_ready(page)
                    for route_name in ROUTES:
                        page.goto(f"{base_url}/#/{route_name}")
                        wait_ready(page)
                        assert basin_select.input_value() == case_id
                        assert scale_select.input_value() == f"{case_id}-{scale}"
                        main_text = page.locator("main#main-workspace").inner_text()
                        assert len(main_text.strip()) > 40, f"blank view: {case_id}/{scale}/{route_name}"
                        if case_id == TEST_CASE_ID:
                            for foreign in FOREIGN_LABELS:
                                assert foreign not in main_text, f"foreign label {foreign}: {route_name}"
                            assert "STATE-SEL" not in main_text
                            assert "XF-OBS-" not in main_text
                            if route_name == "state":
                                assert page.locator(".node-item").count() == 0
                                page.get_by_role("button", name="续算状态", exact=True).click()
                                assert page.get_by_role("button", name="开始续算", exact=True).is_disabled()
                                page.get_by_role("button", name="生成初始状态", exact=True).click()
                                assert page.get_by_role("button", name="开始初始化", exact=True).is_disabled()
                                assert "状态空间场待生成" in main_text
                            if route_name == "calibration":
                                assert page.get_by_role("button", name="开始率定", exact=True).is_disabled()
                            if route_name == "realtime":
                                assert "当前案例空间资料未接入" in main_text
                        checked.append(f"{case_id}/{scale}/{route_name}")

            assert not page_errors, page_errors
            assert not resource_errors, resource_errors
            print(json.dumps({"passed": True, "baseUrl": base_url, "viewsChecked": len(checked)}, ensure_ascii=False))
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
