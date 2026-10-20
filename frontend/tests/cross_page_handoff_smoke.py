from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


app_store = read("src/stores/app.js")
realtime = read("src/views/RealtimeView.vue")
state_store = read("src/stores/state.js")
state_view = read("src/views/StateView.vue")
forecast_store = read("src/stores/forecast.js")
forecast_view = read("src/views/ForecastView.vue")
archive_view = read("src/views/ArchiveView.vue")
calibration_store = read("src/stores/calibration.js")
calibration_view = read("src/views/CalibrationView.vue")

assert "handoff: saved.handoff || null" in app_store
assert "prepareHandoff(targetPage" in app_store

assert "准备起算状态" in realtime
assert "进入洪水预报" in realtime
assert "function bringToState()" in realtime
assert "function bringToForecast()" in realtime
assert "有效计算栅格" in realtime
assert "<span>流域面积</span>" not in realtime

for field in (
    "parentStateVersionId",
    "inputBundleVersionId",
    "parameterVersionId",
    "sourceTaskId",
    "resultSource",
):
    assert field in state_store

assert "prepareFromWorkContext" in state_store
assert "条件来自：{{ state.pendingHandoff.sourceLabel }}" in state_view
assert "父状态" in state_view and "来源任务" in state_view

assert "prepareNextRun(hours = 6)" in forecast_store
assert "复制为下一次作业" in forecast_view
assert "function copyAsNextOperation()" in forecast_view
assert "forecastHandoff" in forecast_view

assert "按原条件打开" in archive_view
assert "function openTask(task)" in archive_view
assert "loadFromTaskConfig(runType" in calibration_store
assert "calculationHandoff" in calibration_view

print("Cross-page handoff smoke passed: monitor, calculation, state, forecast and archive are connected")
