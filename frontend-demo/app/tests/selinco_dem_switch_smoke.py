"""Regression check: activating Selinco must place its DEM in Selinco bounds."""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")
EXPECTED_TOP_LEFT = [85.544583, 33.730416999999996]
NANANXI_TOP_LEFT = [119.3011344538468, 30.28182639557535]


def terrain_state(page):
    return page.evaluate(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          if (!map) return null
          const source = map.getStyle().sources['terrain-dem-1km']
          return {
            source,
            visible: map.getLayoutProperty('terrain-dem-1km', 'visibility'),
            sourceLoaded: map.isSourceLoaded('terrain-dem-1km')
          }
        }"""
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    dem_responses = []
    page.on(
        "response",
        lambda response: dem_responses.append((response.url, response.status))
        if "dem-1km.png" in response.url
        else None,
    )
    page.add_init_script(
        "localStorage.clear(); localStorage.setItem('hydro_basin_id', 'nananxi-xufan');"
    )

    page.goto(f"{BASE_URL}/#/forecast", wait_until="domcontentloaded")
    page.locator(".map-canvas").wait_for(state="attached")
    page.wait_for_function("() => document.querySelector('.map-canvas')?.__hydroMap")

    initial = terrain_state(page)
    assert initial["source"]["coordinates"][0] == NANANXI_TOP_LEFT, (
        "The initially active external basin must keep its own DEM coordinates"
    )

    page.locator(".basin-chip .meta-select").select_option("selinco")
    page.wait_for_function(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          const source = map?.getStyle().sources['terrain-dem-1km']
          return source?.coordinates?.[0]?.[0] === 85.544583
            && map.getLayoutProperty('terrain-dem-1km', 'visibility') === 'visible'
            && map.isSourceLoaded('terrain-dem-1km')
        }"""
    )

    active = terrain_state(page)
    assert active["visible"] == "visible", "Selinco 1 km DEM layer is hidden after basin switch"
    assert active["source"]["coordinates"][0] == EXPECTED_TOP_LEFT, (
        "Selinco DEM remains at the default world coordinates after basin switch"
    )
    assert any(status == 200 for _, status in dem_responses), "Selinco DEM image was not served"

    page.locator(".scale-chip .meta-select").select_option("selinco-0p1deg")
    page.wait_for_function(
        """() => {
          const map = document.querySelector('.map-canvas')?.__hydroMap
          const source = map?.getStyle().sources['terrain-dem-0p1deg']
          return source?.coordinates?.[0]?.[0] === 85.544583
            && map.getLayoutProperty('terrain-dem-1km', 'visibility') === 'none'
            && map.getLayoutProperty('terrain-dem-0p1deg', 'visibility') === 'visible'
            && map.isSourceLoaded('terrain-dem-0p1deg')
        }"""
    )
    coarse = page.evaluate(
        """() => {
          const map = document.querySelector('.map-canvas').__hydroMap
          return {
            source: map.getStyle().sources['terrain-dem-0p1deg'],
            visible: map.getLayoutProperty('terrain-dem-0p1deg', 'visibility')
          }
        }"""
    )
    assert coarse["visible"] == "visible", "Selinco 0.1 degree DEM layer is hidden"
    assert coarse["source"]["coordinates"][0] == EXPECTED_TOP_LEFT, (
        "Selinco 0.1 degree DEM does not use Selinco coordinates"
    )

    browser.close()
    print("Selinco DEM switch smoke passed")
