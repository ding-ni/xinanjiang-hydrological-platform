import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
view = (ROOT / "src/views/ConfigView.vue").read_text(encoding="utf-8")

tabs = ["流域与站网", "水雨情资料", "洪水场次", "模型方案", "参数与状态", "率定算法"]
for tab in tabs:
    assert tab in view

assert "用户与角色" not in view
assert "algorithmCatalog" in view
assert "洪水场次" in view and "Qmax" in view
assert "setEventStatus" in (ROOT / "src/stores/calibration.js").read_text(encoding="utf-8")

algorithms = json.loads((ROOT / "public/data/catalogs/algorithms.json").read_text(encoding="utf-8"))
assert {item["id"] for item in algorithms["algorithms"]} == {"sce-ua", "dds", "de", "pso"}

events = json.loads((ROOT / "src/fixtures/catalogs/flood-events.json").read_text(encoding="utf-8"))
states = json.loads((ROOT / "src/fixtures/catalogs/state-versions.json").read_text(encoding="utf-8"))
assert events["events"] and states["states"]

calibration_store = (ROOT / "src/stores/calibration.js").read_text(encoding="utf-8")
state_store = (ROOT / "src/stores/state.js").read_text(encoding="utf-8")
assert "floodEventCatalog.events" in calibration_store
assert "stateVersionCatalog.states" in state_store

print("Configuration catalog smoke passed: six tabs and external catalogs are active")
