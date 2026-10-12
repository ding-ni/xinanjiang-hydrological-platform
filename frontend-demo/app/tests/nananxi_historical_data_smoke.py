"""校验南安溪历史实测资料目录、洪水场次及前端角色边界。"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "public" / "data" / "projects" / "nananxi-xufan" / "observations"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


catalog = read_json(OBS / "catalog.json")
preview = read_json(OBS / "previews" / "latest-72h.json")
events = read_json(ROOT / "src" / "fixtures" / "catalogs" / "flood-events.json")["events"]
datasets = read_json(ROOT / "public" / "data" / "projects" / "nananxi-xufan" / "1km" / "datasets.json")
event_packages = read_json(OBS / "event-data-packages.json")
preparation = read_json(ROOT / "public" / "data" / "projects" / "nananxi-xufan" / "input-preparation.json")

rain_files = sorted((OBS / "rain").glob("*.json"))
river_files = sorted((OBS / "river").glob("*.json"))
assert len(rain_files) == 67, len(rain_files)
assert len(river_files) == 61, len(river_files)
assert catalog["rain"]["totalRecords"] == 2_944_497
assert catalog["river"]["totalRecords"] == 515_422
assert sum(item["records"] for item in catalog["rain"]["years"]) == 2_944_497
assert sum(item["records"] for item in catalog["river"]["years"]) == 515_422

nananxi_events = [item for item in events if item.get("basinProjectId") == "nananxi-xufan"]
assert len(nananxi_events) == 226, len(nananxi_events)
assert all(item.get("purpose") == "未使用" for item in nananxi_events)
assert event_packages["count"] == 226
assert len(event_packages["packages"]) == 226
assert all(item["status"] == "ready" for item in event_packages["packages"])
assert all(item["observedInputs"]["rainRecords"] > 0 for item in event_packages["packages"])
assert all(item["observedInputs"]["riverRecords"] > 0 for item in event_packages["packages"])
assert preparation["observationInputs"]["hourlyRain"]["status"] == "ready"
assert preparation["rainfallSpatialization"]["owner"] == (
    "offline-preprocessor-for-observed-data-and-model-backend-for-operational-updates"
)
assert preparation["forecastInputs"]["modelService"]["status"] == "not-connected"

assert len(preview["times"]) == 72
assert len(preview["river"]["flows"]) == 72
assert max(preview["river"]["flows"]) == 166
assert max(value for values in preview["rain"].values() for value in values if value is not None) > 0

roles = {item["id"]: item.get("role") for item in datasets["datasets"]}
assert roles["XF-OBS-RAIN-HOURLY"] == "observation"
assert roles["XF-OBS-RIVER-HOURLY"] == "observation"
assert roles["XF-FLOOD-EVENTS"] == "catalog"
assert roles["XF-MODEL-INPUT-PREPARATION"] == "input-catalog"
assert not any(item.get("role") == "forecast" for item in datasets["datasets"])

forecast_view = (ROOT / "src" / "views" / "ForecastView.vue").read_text(encoding="utf-8")
assert ".filter((item) => item.role === 'forecast')" in forecast_view

print("Nananxi historical data smoke passed: observations are complete and remain separate from forecasts")
