"""验证南安溪历史实测过程已进入监视页，但不会启用洪水起报。"""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(
        "localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{BASE_URL}/#/realtime", wait_until="networkidle")

    page.get_by_text("实测资料已接入", exact=True).first.wait_for()
    page.get_by_text("逐小时实测", exact=False).first.wait_for()
    body = page.locator("body").inner_text()
    assert "2024-07-03" in body
    assert "雨水情资料回放" in body
    assert page.get_by_role("button", name="进入洪水预报", exact=True).is_enabled()

    replay_mode = page.locator(".replay-mode select")
    replay_mode.select_option("event")
    event_select = page.locator(".replay-event select")
    event_select.select_option("xf-31240619")
    page.wait_for_timeout(1200)
    assert "31240619" in page.locator(".replay-window").inner_text()
    assert "2024-06-19" in page.locator(".replay-window").inner_text()
    page.get_by_text("当前资料窗口降雨栅格已生成", exact=True).wait_for(timeout=15000)
    assert "110 个" in page.locator(".project-forcing").inner_text()

    station_point = page.evaluate(
        """() => {
          const canvas = document.querySelector('.map-canvas')
          const map = canvas.__hydroMap
          const feature = map.getSource('stations')._data.features.find(item => item.properties.id === '70115800')
          const point = map.project(feature.geometry.coordinates)
          const box = canvas.getBoundingClientRect()
          return {x: box.left + point.x, y: box.top + point.y}
        }"""
    )
    page.mouse.click(station_point["x"], station_point["y"])
    detail = page.locator(".right-detail")
    detail.get_by_text("水文过程", exact=True).wait_for()
    assert "xufan.mdb 实测资料" in detail.inner_text()
    assert "m³/s" in detail.inner_text()

    page.locator(".range").fill("35")
    page.wait_for_timeout(200)
    assert "m³/s" in page.locator(".metric-strip").inner_text()

    page.locator('.sidebar a[href="#/forecast"]').click()
    page.wait_for_url("**/#/forecast")
    page.get_by_text("预报任务输入清单", exact=True).wait_for()
    assert page.get_by_role("button", name="确认并提交预报", exact=True).first.is_disabled()

    page.locator('.sidebar a[href="#/calibration"]').click()
    page.wait_for_url("**/#/calibration")
    page.get_by_text("洪水场次台账", exact=True).wait_for()
    assert page.locator("#cal-event option").count() == 226
    assert "徐畈（二）" in page.locator(".setting-panel").inner_text()
    assert not errors, errors

    context.close()
    browser.close()

print("Nananxi historical UI smoke passed: observed playback works and forecast remains blocked")
