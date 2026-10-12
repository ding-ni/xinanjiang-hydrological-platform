"""Verify a registered MDB product against the shared reader and strict loader."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from common import MODEL, RUNTIME, read_json, write_json
from input_products import InputProducts, DEFAULT_BUNDLE_VERSION_ID, file_sha256
from input_service import InputService
from strict_forcing import load_hourly_forcing


def verify(runtime, bundle_id, output, model=MODEL, source_event_quality=None):
    import h5py
    import h5netcdf
    import numpy as np

    products = InputProducts(model, runtime)
    bundle = products.resolve(bundle_id)
    if bundle["manifest"].get("operation") != "mdbImport":
        raise ValueError("验证目标须为已登记MDB资料版本")
    service = InputService(model, bundle)
    catalog = service.catalog()
    events = []
    for event in catalog["events"]:
        identifier = event.get("eventId", event["id"])
        payload = service.event(identifier, warmup_hours=168)
        quality = payload["quality"]
        coverage = quality["observation_coverage"]
        events.append({"eventId": identifier, "start": payload["eventPeriod"]["start"], "end": payload["eventPeriod"]["end"],
                       "hours": coverage["window_hours"], "validObservedHours": coverage["valid_hours"],
                       "observedCoverage": coverage["coverage_fraction"], "maximumMissingGapHours": coverage["maximum_missing_gap_hours"],
                       "phaseCoverage": coverage["phase_coverage"], "accepted": quality["accepted"],
                       "modelInputReady": quality["model_input_ready"], "volumeScope": quality["observed_volume_scope"],
                       "coverageEnforced": coverage["policy"]["enforce"],
                       "exclusionReasons": quality["exclusion_reasons"], "warningReasons": quality["warning_reasons"]})
    original_qc_comparison = None
    if source_event_quality:
        with Path(source_event_quality).open(encoding="utf-8-sig", newline="") as handle:
            original = {row["event_id"]: row for row in csv.DictReader(handle)}
        if set(original) != {row["eventId"] for row in events}:
            raise ValueError("来源QC与当前目录的洪水窗口不一致")
        differences = []
        for row in events:
            source = original[row["eventId"]]
            expected_exclusions = source["exclusion_reasons"].split(";") if source["exclusion_reasons"] else []
            if (row["accepted"] != (source["accepted"] == "True") or row["hours"] != int(source["duration_hours"])
                    or row["validObservedHours"] != int(source["paired_flow_hours"]) or row["exclusionReasons"] != expected_exclusions):
                differences.append(row["eventId"])
        original_qc_comparison = {"sourceFileSHA256": file_sha256(source_event_quality), "eventCount": len(original),
                                  "acceptedEventCount": sum(row["accepted"] == "True" for row in original.values()),
                                  "acceptedAndExcludedExactlyMatched": not differences, "differentEventIds": differences}
        if differences:
            raise ValueError("来源方案QC接受集合或窗口发生改变：" + ",".join(differences))
    summary = bundle["manifest"]["summary"]
    times, values, counts, audit = load_hourly_forcing(bundle, service.grids, summary["continuousOrigin"], summary["requestedContinuousEnd"])
    checks = []
    for start, end in (("2021-08-11T10:00:00", "2021-08-12T10:00:00"),
                       ("2024-06-30T09:00:00", "2024-06-30T15:00:00")):
        payload = service.window(start, end)
        actual, expected, station_counts, _ = load_hourly_forcing(bundle, service.grids, start, end)
        displayed = np.array([cell["values"] for cell in payload["product"]["cells"]], dtype=float).T
        if not np.array_equal(displayed, expected, equal_nan=True):
            raise ValueError("输入展示与实际模拟loader降雨不一致")
        checks.append({"start": start, "end": end, "hours": len(actual), "maximumGridDifferenceMm": 0.0,
                       "stationCountsEqual": payload["summary"]["reportingStationCount"] == station_counts.tolist()})
    comparison = read_json(bundle["manifestPath"].parent / "import-audit.json")["semanticComparison"]
    result = {"status": "VERIFIED", "bundleVersionId": bundle_id, "manifestSHA256": bundle["manifestSHA256"],
              "registered": bundle["public"]["registered"], "productionEligible": False,
              "defaultBundleVersionId": products.resolve()["bundleVersionId"],
              "defaultUnchanged": products.resolve()["bundleVersionId"] == DEFAULT_BUNDLE_VERSION_ID,
              "eventCount": len(events), "acceptedEventCount": sum(event["accepted"] for event in events), "events": events,
              "continuousPrefix": {**{key: value for key, value in audit.items() if not key.endswith("_mask") and not key.endswith("_by_hour")},
                                   "allGridValuesFinite": bool(np.isfinite(values).all()), "minimumStationCount": int(counts.min())},
              "displayedForcingChecks": checks, "semanticComparison": comparison,
              "queryScope": bundle["manifest"]["recordQueryScope"]}
    result.update(sourceQcPolicy=bundle["manifest"].get("sourceQcPolicy"),
                  eventQualityConfigurationSHA256=file_sha256(bundle["paths"]["quality"]),
                  sourceQualityComparison=original_qc_comparison)
    output = Path(output)
    write_json(output / "MDB真实接入验证.json", result)
    with (output / "MDB17场观测覆盖.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["eventId", "start", "end", "hours", "validObservedHours", "observedCoverage", "maximumMissingGapHours",
                  "accepted", "modelInputReady", "volumeScope", "coverageEnforced", "exclusionReasons", "warningReasons"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: json.dumps(row[key], ensure_ascii=False) if isinstance(row[key], list) else row[key] for key in fields} for row in events)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=str(RUNTIME))
    parser.add_argument("--model", default=str(MODEL))
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-event-quality", help="Compare the complete original event_quality.csv acceptance and paired-hour policy")
    args = parser.parse_args()
    result = verify(args.runtime, args.bundle, args.output, args.model, args.source_event_quality)
    print(json.dumps({"status": result["status"], "bundleVersionId": result["bundleVersionId"],
                      "eventCount": result["eventCount"], "acceptedEventCount": result["acceptedEventCount"],
                      "continuousHours": result["continuousPrefix"]["requested_hours"],
                      "sameNormalizedRain": result["semanticComparison"]["sameNormalizedRain"],
                      "sameNormalizedFlow": result["semanticComparison"]["sameNormalizedFlow"],
                      "sameEventWindows": result["semanticComparison"]["sameEventWindows"],
                      "defaultUnchanged": result["defaultUnchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
