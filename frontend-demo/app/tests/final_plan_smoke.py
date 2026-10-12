from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


engine = read("src/models/xajParamEngine.js")
archive_store = read("src/stores/archive.js")
calibration_store = read("src/stores/calibration.js")
state_store = read("src/stores/state.js")
calibration_view = read("src/views/CalibrationView.vue")
archive_view = read("src/views/ArchiveView.vue")

assert "PARAMETER_PRESETS" in engine
assert "XAJ-ZJ-v3.2':" not in engine
assert "FORECAST_PRODUCT_FIXTURES" in archive_store
assert "const seed =" not in archive_store
assert "legacy-compatibility" in archive_store
assert "floodEventCatalog.events" in calibration_store
assert "event.basinProjectId === domainId" in calibration_store
assert "legacy-xinanjiang-fixture" not in calibration_store
assert "stateVersion.domainId !== 'xinanjiang'" in state_store

assert "保存为起算状态" in calibration_view
assert "function saveSimulationState()" in calibration_view
assert "taskTypeFilter" in archive_view
assert "查看条件" in archive_view
assert "旧预报成果兼容区" in archive_view

for view in (
    "src/views/RealtimeView.vue",
    "src/views/CalibrationView.vue",
    "src/views/StateView.vue",
    "src/views/ForecastView.vue",
    "src/views/ArchiveView.vue",
    "src/views/ConfigView.vue",
):
    assert "ContextStatusMetric" in read(view), view

print("Final plan smoke passed: catalogs, lineage, unified archive and page states are active")
