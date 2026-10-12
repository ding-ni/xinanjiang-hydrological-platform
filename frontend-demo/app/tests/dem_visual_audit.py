from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "测试结果" / "DEM接入审计"
OUTPUT.mkdir(parents=True, exist_ok=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for basin_id in ("nananxi-xufan", "llpq"):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.add_init_script(
            "localStorage.clear(); localStorage.setItem('hydro_basin_id', '%s');" % basin_id
        )
        page.goto("http://127.0.0.1:5173/#/realtime", wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('.map-canvas')?.__hydroMap?.isStyleLoaded()"
        )
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUTPUT / f"{basin_id}-1km.png"), full_page=True)

        if basin_id == "nananxi-xufan":
            point = page.evaluate(
                """() => {
                  const canvas = document.querySelector('.map-canvas')
                  const map = canvas.__hydroMap
                  const feature = map.getSource('stations')._data.features[0]
                  const p = map.project(feature.geometry.coordinates)
                  const box = canvas.getBoundingClientRect()
                  return {x: box.left + p.x, y: box.top + p.y}
                }"""
            )
            page.mouse.click(point["x"], point["y"])
            page.locator(".right-detail").wait_for()
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUTPUT / "nananxi-xufan-站点详情.png"), full_page=True)
            page.locator(".right-detail button", has_text="×").click()
            page.locator(".right-detail").wait_for(state="detached")

        config_id = f"{basin_id}-0p1deg"
        page.locator(".scale-chip .meta-select").select_option(config_id)
        page.locator(".terrain-badge").wait_for()
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUTPUT / f"{basin_id}-0p1deg.png"), full_page=True)
        context.close()

    browser.close()
    print(f"DEM visual audit written to {OUTPUT}")
