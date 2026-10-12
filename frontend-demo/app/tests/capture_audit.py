from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
out = ROOT / "测试结果" / "页面审计"
out.mkdir(parents=True, exist_ok=True)

ROUTE_NAMES = {
    "realtime": "雨水情监视",
    "calibration": "模型计算",
    "forecast": "洪水预报",
    "state": "状态续算",
    "archive": "运行档案",
    "config": "业务配置",
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    errors = []
    for route in ["realtime", "calibration", "forecast", "state", "archive", "config"]:
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and "AbortError" not in msg.text else None)
        page.goto(f"http://127.0.0.1:5173/#/{route}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1800)
        page.screenshot(path=str(out / f"{ROUTE_NAMES[route]}-1440.png"), full_page=True)
        metrics = page.evaluate("""() => ({
          viewport: [innerWidth, innerHeight],
          document: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
          workspace: (() => { const el = document.querySelector('#main-workspace'); return el ? [el.clientWidth, el.clientHeight, el.scrollWidth, el.scrollHeight] : null })()
        })""")
        print(route, metrics)
        page.close()
    assert not errors, errors
    browser.close()
