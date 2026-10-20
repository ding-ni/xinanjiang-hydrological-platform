"""验证真实项目轻量目录已替代非色林错流域的旧示意空间数据。"""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


def map_snapshot(page):
    return page.evaluate(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          if (!map) return null
          const basinFeatures = map.getSource('basin')?._data?.features || []
          const stationFeatures = map.getSource('stations')?._data?.features || []
          const coordinates = basinFeatures?.[0]?.geometry?.coordinates?.flat(4) || []
          const lngs = coordinates.filter((_, index) => index % 2 === 0)
          const lats = coordinates.filter((_, index) => index % 2 === 1)
          return {
            basinFeatures: basinFeatures.length,
            stationFeatures: stationFeatures.length,
            minLng: Math.min(...lngs),
            maxLng: Math.max(...lngs),
            minLat: Math.min(...lats),
            maxLat: Math.max(...lats),
          }
        }"""
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    terrain_responses = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda response: terrain_responses.append((response.url, response.status))
        if "terrain-" in response.url and response.url.endswith(".png")
        else None,
    )
    page.add_init_script(
        "localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');"
    )

    page.goto(f"{BASE_URL}/#/realtime", wait_until="networkidle")
    page.wait_for_function(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          const data = map?.getSource('basin')?._data
          return data?.features?.[0]?.geometry?.coordinates?.flat(4)?.some(value => value > 119)
        }"""
    )
    nananxi = map_snapshot(page)
    assert nananxi["basinFeatures"] == 1, nananxi
    assert 119.4 < nananxi["minLng"] < 119.6, nananxi
    assert nananxi["stationFeatures"] == 12, nananxi
    assert "实测资料已接入" in page.locator(".chip.status").inner_text()
    page.wait_for_function(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          return map?.getLayoutProperty('terrain-grid-fill', 'visibility') === 'visible'
        }"""
    )
    raster_state = page.evaluate(
        """async () => {
          const map = document.querySelector('.map-canvas').__hydroMap
          const gridUrl = map.getStyle().sources['real-grid'].data
          const grid = await fetch(gridUrl).then(response => response.json())
          return {
            terrainVisibility: map.getLayoutProperty('terrain-grid-fill', 'visibility'),
            gridVisibility: map.getLayoutProperty('real-grid-line', 'visibility'),
            gridFeatures: grid.features.length,
          }
        }"""
    )
    assert raster_state["terrainVisibility"] == "visible", raster_state
    assert raster_state["gridVisibility"] == "visible", raster_state
    assert raster_state["gridFeatures"] == 110, raster_state
    body_text = page.locator("body").inner_text()
    assert "1,230 m³/s" not in body_text
    assert "新安江大桥" not in body_text
    assert "实测资料已接入" in body_text

    station_point = page.evaluate(
        """() => {
          const canvas = document.querySelector('.map-canvas')
          const map = canvas.__hydroMap
          const feature = map.getSource('stations')._data.features[0]
          const point = map.project(feature.geometry.coordinates)
          const box = canvas.getBoundingClientRect()
          return { x: box.left + point.x, y: box.top + point.y }
        }"""
    )
    page.mouse.click(station_point["x"], station_point["y"])
    page.locator(".right-detail").wait_for()
    map_box = page.locator(".map-card").bounding_box()
    detail_box = page.locator(".right-detail").bounding_box()
    assert map_box and detail_box and detail_box["x"] > map_box["x"], (map_box, detail_box)

    page.evaluate(
        """() => document.querySelector('.map-canvas').__hydroMap.jumpTo({center: [118, 29], zoom: 6})"""
    )
    page.get_by_role("button", name="复位", exact=True).click()
    page.wait_for_timeout(700)
    reset_state = page.evaluate(
        """() => {
          const map = document.querySelector('.map-canvas').__hydroMap
          const center = map.getCenter()
          const bounds = map.getBounds()
          return {center: [center.lng, center.lat], bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]}
        }"""
    )
    assert abs(reset_state["center"][0] - 119.497216) < 0.08, reset_state
    assert abs(reset_state["center"][1] - 30.108503) < 0.08, reset_state
    assert reset_state["bounds"][0] < 119.431675 < reset_state["bounds"][2], reset_state
    assert reset_state["bounds"][0] < 119.562757 < reset_state["bounds"][2], reset_state

    page.locator(".basin-chip .meta-select").select_option("llpq")
    page.wait_for_function(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          const data = map?.getSource('basin')?._data
          return data?.features?.[0]?.geometry?.coordinates?.flat(4)?.some(value => value > 93 && value < 95)
        }"""
    )
    llpq = map_snapshot(page)
    assert llpq["basinFeatures"] == 1, llpq
    assert 93.4 < llpq["minLng"] < 94.2, llpq
    assert llpq["stationFeatures"] == 1, llpq
    assert "仅空间资料" in page.locator(".chip.status").inner_text()
    llpq_text = page.locator("body").inner_text()
    assert "新安江大桥" not in llpq_text
    assert "后续仍需补充" in llpq_text
    page.wait_for_function(
        """() => document.querySelector('.map-canvas')?.__hydroMap?.getLayoutProperty('terrain-grid-fill', 'visibility') === 'visible'"""
    )

    with page.expect_response(lambda response: "llpq/spatial/terrain-grid-0p1deg.geojson" in response.url) as coarse_response:
        page.locator(".scale-chip .meta-select").select_option("llpq-0p1deg")
    assert coarse_response.value.status == 200
    page.wait_for_function(
        """() => document.querySelector('.map-canvas')?.__hydroMap?.getLayoutProperty('terrain-grid-fill', 'visibility') === 'visible'"""
    )

    page.locator('.sidebar a[href="#/forecast"]').click()
    page.wait_for_url("**/#/forecast")
    page.wait_for_load_state("networkidle")
    run_button = page.get_by_role("button", name="确认并提交预报", exact=True).first
    assert run_button.is_disabled(), "LLPQ forecast must remain disabled while required inputs are missing"
    assert not errors, errors

    context.close()
    browser.close()
    print("Project data smoke passed: external spatial catalogs and availability rules are active")
