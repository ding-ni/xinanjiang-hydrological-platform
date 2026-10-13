# Unified model API / schema 2.0

## 人工降雨情景预报（1.8.0）

- `GET /forecast-rain` 返回 `products`。`POST /forecast-rain` 精确接收 `name/contextKey/issueTime/hourlyRainfallMm`；南安溪1km、本地整点、1–72项、0–1000mm。保存后返回不可变 `QPF-*`、`manifestSHA256`、逐时序列和累计量；相同内容重复登记返回同一版本。
- `POST /forecast-tasks` 使用schema2.0，`runType:"forecast"`。`issue` 包含 `mode:"manual"`、`sectionId:"70115800"`、`issueTime`、整数 `leadHours` 和 `forecastEndTime`。
- `period.startTime` 为起报下一小时，`endTime` 为起报+预见期，`initialStateVersionId` 是起报整点的成功实测状态；`warmupDays:1` 为契约兼容字段，预报不再预热。
- `inputs` 包含已登记实测 `bundleVersionId`、`forecastPrecipitation:{versionId,manifestSHA256}`、`evaporation:{mode:"monthly-climatology-assumption"}`。参数省略或空对象时沿用状态参数；显式参数须完整一致。汇流版本也须与状态一致。
- 状态没有独立参数版本时，服务冻结 `STATE-PARAMETERS-{stateVersionId}`；再次提交该编号仍可复算。请求编号按既有任务队列幂等处理。
- 降雨采用时段末标记，全域均匀分配；不读取起报之后的实测降雨或流量作为预报输入/评价。返回6至72小时UI所选过程（API允许1至72小时）、真实格点过程、洪峰和 `volume=sum(Q)*3600`；未来观测为 `obs:[]`。
- `forecast-rain.json`、`forecast-end-state.json` 留在任务内，后者不注册到实测状态库。报告ZIP包含这两份文件；完整工作区备份包含 `forecast-rain/` 目录。
- `mode:"automatic"` 仍需后端已审核生产参数；本版本不提供生产发布、定时调度或历史预报补发。

## Continuous calibration (platform 1.2)

Use the existing `POST /tasks` or `/calibration-tasks`, with `runType: "calibration"` and `period.mode: "continuous"`.
Supply `calibrationStart/calibrationEnd`, `validationStart/validationEnd`, optional `independentStart/independentEnd`, and `warmupDays` in `period`.
The server freezes canonical `period.windows` (IDs `CALIBRATION`, `VALIDATION`, `INDEPENDENT`) and removes event-only fields and event-set lineage.
Each scoring interval is disjoint, inclusive, local hourly time, at most 366 days. Each interval independently warms up from the parameter-dependent template; no parent checkpoint is accepted.
Continuous PET is explicitly `inputs.evaporation.mode: "monthly-climatology-assumption"`.

`calibration.optimizer.strategy` is `joint` (default) or `staged`; `seed` is an unsigned 32-bit integer (default 42).
The existing `maxEvaluations` is the total budget, split evenly across storage/runoff and source-separation stages; the second receives the remainder.
Each stage includes an incumbent evaluation and preserves the incoming solution when no candidate improves it. Vendor optimizer populations are not warm-started.
Staged differential evolution requires at least 10 evaluations; staged random/grid search requires at least 4.
Only CALIBRATION observations contribute to optimization. Validation and independent-test metrics are computed after selecting parameters.

Task status uses `runType: "continuousCalibration"`. Results retain the usual `bestParameters`, parameter version, group metrics, `eventMetrics` (compatibility name), and `hydrographs` keyed by period ID.
Additional fields: `calibrationMode`, `periods`, `periodQuality`, `statePolicy`, `optimizationGroups`, `optimizer.strategy/stages/history`.
The input manifest also hashes the calibration adapter implementation. Candidate parameters, frozen requests and results use the existing SQL/task/workspace lifecycle.
Event calibration requests remain compatible and also support the staged strategy.

Base `/api/model`, same origin as Vue. Loopback Host only. JSON bodies and mutation origins are checked; not a public authenticated service.

| Method | Resource | Meaning |
| --- | --- | --- |
| GET | `/health`, `/catalog` | Identity; server-owned readiness, native params, algorithms, QC events |
| GET / POST | `/tasks` | Durable list / submit frozen request |
| POST | `/calibration-tasks`, `/simulation-tasks` | Aliases requiring the corresponding runType, plus the same validation |
| GET | `/tasks/{id}` | Durable status + live progress |
| GET | `/tasks/{id}/result`, `/tasks/{id}/log` | Success-only result; log tail |
| POST | `/tasks/{id}/cancel` | Cancel queue or terminate only this worker |
| POST | `/tasks/{id}/archive` | Archive success without deleting |
| POST | `/tasks/{id}/clone` | New request/job from frozen conditions, with sourceTaskId |
| GET | `/states`, `/parameter-versions` | Checkpoints/candidates from successful jobs only |
| GET | `/inputs/catalog` | QC forcing/flow coverage, latest paired window, registered events, control areas and input hashes |
| GET | `/inputs/window?start=...&end=...` | Actual hourly rain for all 110 grids, outlet flow, control-weighted areal rainfall; omit both times for latest paired window |
| GET | `/inputs/events/{id}?warmupHours=168` | Registered event main window plus independent physical QC of warmup + event; not a recalibration or catalog rebuild |
| GET | `/input-products` | Fixed default + explicitly registered diagnostic input bundles; no global current/latest |
| GET / POST | `/input-sources` | Registered user-declared station JSON source list / bounded source registration |
| GET / POST | `/input-tasks` | Separate persistent processing queue / submit qcWindow or stationIdw |
| GET | `/input-tasks/{id}`, `/{id}/result`, `/{id}/log` | Processing status; success-only result; processing log tail |
| POST | `/input-tasks/{id}/cancel`, `/{id}/register` | Cancel only that processing task; explicitly register a verified successful product |
| GET | `/tasks/{id}/grid-state/catalog` | Saved successful grid-history metadata and exact computed times |
| GET | `/tasks/{id}/grid-state?time=...` | Exact saved hourly grid frame, no interpolation or recomputation |
| GET | `/tasks/{id}/grid-state?unitId=...` | Actual saved time series of one registered grid |
| GET | `/tasks/{id}/event-quality` | Saved execution-time QC audits, including rejected calibration tasks |
| GET / POST | `/routing-versions` | List immutable baseline/registered routing versions; register a new version |
| GET | `/spatial-products?bundleVersionId=...` | Selected bundle's real geometry, topology, rivers and control metadata |
| GET | `/tasks/{id}/report-package` | Success-only ZIP of allowlisted reports and frozen evidence |
| GET | `/database/summary`, `/database/stations` | SQLite record coverage, registered stations, source/event/task/scheme counts |
| GET | `/database/window`, `/database/export` | Station hourly observations / exact corresponding CSV |
| GET | `/database/events?search=...`, `/database/monitor?end=...` | Existing flood catalog / raw station observations for the preceding 24 hours |
| POST | `/database/rain-source` | Register a bounded station-rain source directly from existing SQL observations |
| GET / POST | `/database/schemes` | List reusable frozen schemes / save from a successful task |
| POST | `/database/schemes/{id}/run`, `/database/backup` | Submit a fresh normal task from a frozen scheme / consistent SQLite backup |

`/forecast-tasks` accepts registered manual rainfall scenarios (platform 1.8, below). Errors are `{"error":"..."}`: 400 invalid, 404 missing, 409 unavailable result, 500 unexpected failure. Submission returns 202 + task, not a numerical result.

Minimal simulation:

```json
{
  "schemaVersion": "2.0",
  "requestId": "unique-client-id",
  "runType": "simulation",
  "context": {
    "contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km",
    "projectId": "nananxi-xufan",
    "resolutionId": "1km",
    "modelConfigId": "nananxi-xufan-1km"
  },
  "inputs": {
    "bundleVersionId": "NANANXI_QC_20260819_CONTROL_V2",
    "evaporation": {"mode": "monthly-climatology-assumption"}
  },
  "period": {"startTime": "2017-06-19T11:00", "endTime": "2017-06-20T11:00", "warmupDays": 7, "initialStateVersionId": null},
  "parameters": {},
  "lineage": {"parameterVersionId": "NANANXI_XAJ_REFERENCE_V1"}
}
```

Omitted params use catalog reference; overrides accept only finite native names/bounds. `WM>WUM+WLM`, `KI+KG<0.98`, warmup 1–365 days, local whole-hour timestamps, 1960–2024 and ≤366-day windows are enforced. Actual hourly file and outlet observation coverage are still required. Browser URLs never select server filesystem inputs.

`stateInitialization` uses no parent; Vue requests the target hour after 7-day warmup. `stateContinuation` requires a successfully completed or archived parent, starts exactly one hour later, and matches context/grid/parameters. Both submission and worker verify the state version against the source task/result/checkpoint and input digest. Failed, cancelled, running or mismatched state artifacts are rejected. Older successful contextKey-only results are checked against their full frozen request. Checkpoints retain complete land and routing memories, not means alone.

Calibration requires `runType=calibration`, `period.mode=event`, registered events with starts/ends/status/purpose (`率定/验证/独立检验/未使用`), and eventSet.selectedEventIds exactly equal used rows. Start/end must exactly match the server QC catalog. At least one calibration and one validation event. No regrouping or silent QC exclusions. Frontend manual revisions remain localDraft snapshots, never overwrite the runtime QC input.

PET must be `{"mode":"constant-zero","valueMmPerHour":0}`. `calibration` includes native initialParameters, objectives metrics `nse/peak/time/volume`, positive enabled weights summing to 1, and optimizer algorithm `DIFFERENTIAL_EVOLUTION/RANDOM_SEARCH/GRID_SEARCH`, maxEvaluations integer 1–10000 (DE≥4), populationSize 4–1000. Search uses vendor stage2 daily-source bounds, KC/C and routing frozen. Requested peak/time tolerances remain diagnostic, not a new qualification formula.

Same requestId + same canonical JSON returns the existing job; changed conditions under the same ID are rejected. New experiment → new ID. States: queued → running → completed/failed; cancelling → cancelled; completed → archived. Restarted unfinished jobs fail/interrupted, not automatically rerun.

Results always include taskId, resultVersionId, contextKey, lineage, resultSource=model-backend, productionEligible=false and warnings. Hydrographs use timestamp/value arrays, missing observations serialize null, never NaN. Their forcing.precipitation contains the actual QC forcing, weighted by grid overlap area upstream of the Xufan control, aligned to the hydrograph timestamps; forcing.temperature is empty because no measured temperature was used. No static-preview rainfall or fabricated temperature replaces numerical inputs. Simulation returns endStateVersionId/stateTime/real means/gridCount/native parameters. Calibration returns bestParameters, group summaries, per-event metrics/hydrographs, actual searchSpace and qualificationPolicy. Small-budget smoke runs are not scientific calibration acceptance.

Checkpoints also record parentStateVersionId, forcingVersion, observationVersion, petMode and inputManifestSHA256. New simulation/state jobs save `grid-history.json` and compressed `grid-history.npz`: WU/WL/WD/S/FR, RS/RI/RG, actual precipitation/PET/ET and water-balance error. The state page replays saved hourly frames/series, not synthetic future-state curves. Grid-history access verifies task/context/input digest, file hashes, time axis, array shapes, finite values and exact final checkpoint. Old jobs without history explicitly report unavailable; no process is reconstructed from means. The simulation chart uses the frozen result period even when the current scheme has been edited while the job was running.

Task folder contains frozen request/status/progress/result/checkpoint/input-manifest/log and, for new simulation/state tasks, grid-history JSON/NPZ. Input manifest hashes actual forcing, QC observations, configs, model source and parent state; lineage carries its digest.

Input GET queries accept projectId/resolutionId and optional `bundleVersionId`, with explicit defaults `nananxi-xufan/1km` and the fixed baseline bundle; other cases/scales and repeated query fields are rejected. Selection never changes a global default. Windows require paired, local whole-hour start/end, maximum 744 hours. Events allow warmupHours integer 0–8760: audits cover the full warmup + event, but products serialize only the main event window; qualityPeriod/warmupPeriod record the audit bounds. Coverage dates are bounds, not a promise of complete observations. Unknown years, events, missing files or unsupported scales never fall back to frontend previews.

Grid rain reads array_row/array_col and joins polygon source_row/source_col exactly as the worker. Model v2 control has 96 contributing grids and 63.27190569363709 km² effective overlap area; registered observed runoff-depth reference is 64.1 km². Areal rain is weighted by that control area, not a whole-project average. Missing rain remains null without remaining-area renormalization. reporting_station_count is the QC support count, not a per-station rainfall series. Within the model `/inputs` contract, per-station rain is empty, levels are null and measured temperature is unavailable. This does not mean the delivered observations lack levels: `/database` serves existing station rain, level and flow from SQLite. Realtime station displays use SQL observations; map grid rainfall and model/event previews continue using the selected model input product. Raw observations never silently replace numerical forcing.

## Existing Hydrological Database — Added 2026-10-10

`hydrology_schema.sql` defines projects, stations, station_aliases, source_files, observations, observation_coverage, flood_events, task_index, schemes and state_metadata. SQL is authoritative for observations, reusable schemes, state names and favourites. Large arrays, state checkpoints and numerical results remain in durable task folders; task_index mirrors the lifecycle rather than becoming a second task runner. Existing null legacy submission times are indexed as blank, without fabricating dates or modifying the original payload.

On startup, known MDB-converted yearly JSON records are transactionally imported into `runtime/database/hydrology.sqlite3`: 128 files, 12 registered stations, 226 flood events, 2,944,497 rain records across 11 stations (1959–2025), and 515,422 records each for outlet level and flow (1964–2024). Coverage bounds do not promise every hour is observed. SHA256-pinned sources skip unchanged imports and reject changed files without overwriting old records. This is not an arbitrary new MDB parser.

`GET /database/window` and `/export` require `stationId`, `variable=rain|level|flow`, and paired `start`/`end` local whole-hour timestamps, with both endpoints included. Multi-year observation ranges are supported. `/window` returns full-range SQL statistics and `range:{start,end}`, while `series` contains at most 8784 consecutive display hours. Optional `offsetHours` is a nonnegative integer inside the query range; `display` describes its start/end, hours, offsetHours, totalHours, limitHours, hasPrevious and hasNext. `/export` streams every hour of the complete selected range, independent of the display offset. Values stay null when missing; `recorded` distinguishes missing recorded values from absent records. SQL queries are parameterized and duplicate-hour sources are rejected before export headers. Export retains time, station, variable, value, unit, recorded flag and source ID. Station-source extraction retains its independent 8784-hour and 200000-value processing bounds. All database routes belong to `nananxi-xufan`; an explicit foreign project is rejected.

`GET /database/monitor?end=2017-06-24T23:00` returns the 24-hour window ending at that hour, raw station rain/level/flow, actual series and station summaries. It does not interpolate levels, create flow values or substitute model outputs for measured observations.

`POST /database/rain-source` accepts a bounded selection from existing SQL records:

```json
{
  "name": "2017 June station rainfall",
  "stationIds": ["70149600", "70149720", "70149640", "70149680", "70115800"],
  "start": "2017-06-22T00:00",
  "end": "2017-06-28T23:00"
}
```

It returns 200 with the ordinary registered source contract and server-generated `sourceOrigin`: original file IDs/hashes, station IDs and exact window. Clients cannot supply or spoof sourceOrigin. Values and coordinates pass the existing input-source bounds; this route neither runs spatialization nor automatically selects a product. Continue through `/input-tasks` stationIdw, successful registration and explicit input selection. Product/task provenance retains the SQL origin, but `productionEligible` stays false.

`POST /database/schemes` accepts `{name, taskId}` from a completed/archived supported calculation and saves the frozen request with its digest. `POST /database/schemes/{id}/run` revalidates that saved request and submits a new task with a fresh request ID, preserving original period, parameters, input and routing identities; it does not use current browser edits. Both return 200. This follows the same worker, queue, logs, cancellation and result contract.

`POST /database/backup` accepts `{}` and returns 200 after SQLite's backup API and integrity check. Backups live in `runtime/database-backups`; they contain database tables, not numerical task artifacts or browser localStorage. Full snapshots additionally retain source, product, task, state, parameter and routing folders. Do not copy a live main SQLite file alone as a complete backup.

## Diagnostic input products — added 2026-10-10

The original delivery and `backend/vendor/model` remain read-only. This stage opens restricted input processing, not arbitrary local file access, dynamic MDB conversion, scientific authentication or production release. All sources, products and model results retain `productionEligible=false`.

`GET /input-products` returns `{schemaVersion, defaultBundleVersionId, versions}`. Only the pinned fixed baseline and explicitly registered `INPUT-*` products are selectable. A completed `DATA-*` processing task is not automatically a registered product. GET lists expose metadata and SHA256 identities, never absolute local paths. The baseline is pinned in `runtime/inputs/fixed-baseline-manifest.json`; changing a referenced file is an integrity error, not a silent replacement under the same version ID.

### Register a user-declared station source

`POST /input-sources` returns 201 with `sourceId`, coverage, station/value/missing/negative counts, source and manifest SHA256, and the authenticity marker `USER_DECLARED_NOT_INDEPENDENTLY_VERIFIED`. Example JSON below illustrates the schema, not supplied observations:

```json
{
  "schemaVersion": "1.0",
  "projectId": "nananxi-xufan",
  "resolutionId": "1km",
  "name": "Station JSON format example",
  "provenanceNote": "Replace example values with independently checked source records",
  "unit": "mm",
  "crs": "EPSG:4326",
  "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
  "times": ["2017-06-18T00:00", "2017-06-18T01:00"],
  "stations": [{"id": "EXAMPLE-01", "lon": 119.1, "lat": 29.8, "values": [null, null]}]
}
```

Only this bounded JSON contract is accepted: 1–32 stations, 1–8784 continuous unique whole-hour local timestamps, years 1960–2024, at most 200000 values and a 2 MB JSON body/source bound. Station IDs are unique labels; coordinates must be finite EPSG:4326 numbers. Values are finite numbers or null, not booleans, numeric strings, objects, NaN or infinity. Null is missing, never guessed as zero. Negative source values are retained and counted; the original processing QC marks them missing and discloses the count. No local path, database connection, URL or arbitrary NetCDF upload is accepted.

### Create and explicitly register a processing product

`POST /input-tasks` returns 202 + queued `DATA-*` task. Minimal QC-window request:

```json
{
  "requestId": "DATA-unique-client-id",
  "operation": "qcWindow",
  "projectId": "nananxi-xufan",
  "resolutionId": "1km",
  "name": "Independent QC window version",
  "start": "2017-06-12T11:00",
  "end": "2017-06-25T08:00",
  "parentBundleVersionId": "NANANXI_QC_20260819_CONTROL_V2"
}
```

`stationIdw` additionally requires a registered `sourceId`; optional `idwPower` is a finite number 1–4, `minimumStations` is an integer 1–registered station count, and `zeroOnlyPolicy` is `KEEP` by default. `ANNUAL_RECLASSIFY` is permitted only for a complete natural-year window. A short all-zero window stays zero; it is not evidence of annual station failure. Window bounds must fit the source and current fixed outlet observations; source and parent bundle SHA256 are frozen before enqueueing.

The backend reuses the original QC/IDW implementation and writes independent annual NetCDF files, event catalog, QC report, source metadata and per-file SHA256. Only previously registered event windows fully covered by the new product are retained. Their rainfall/flow statistics and physical QC are recalculated using the new product. This is not an API for registering arbitrary new event windows or turning localDraft edits into runtime observations.

After successful completion, `POST /input-tasks/{id}/register` with `{}` verifies task status, frozen request, result, manifest, referenced files and source/parent identities again. It then creates an immutable registry entry; an identical repeat is idempotent. Queued, running, cancelling, cancelled and failed residues are never registrable. Cancel stops only that DATA queue task, not a model JOB. Processing task logs/results use the separate queue, not `/tasks/{id}`. Same requestId + same conditions is one task; changed conditions require a new requestId.

Files live under `runtime/input-tasks/DATA-*`, `runtime/inputs/sources/SRC-*`, `runtime/inputs/products/INPUT-*` and `runtime/inputs/registry/INPUT-*.json`. Existing sources, the fixed baseline and old results are not overwritten or deleted. A source is user-declared, even after numeric QC: neither registration nor interpolation certifies measured-data authenticity.

### Freeze browser selection into real model consumption

`/inputs/catalog`, `/inputs/window` and `/inputs/events/{id}` accept the selected `bundleVersionId`. Every request revalidates the pinned bundle files before accessing the reader cache, including omitted/default selection; a warmed catalog or flow cache cannot hide changed numerical inputs. The reader cache is keyed by bundle ID + manifest SHA256, keeping concurrent/default/new-version reads separate. Omitted selection always means the fixed baseline, never the newest product. Invalid, changed or unregistered versions fail explicitly. Restoring the exact original bytes restores the pinned identity; it does not issue a new digest under the old ID.

Model requests choose `inputs.bundleVersionId`. Submission resolves the registered product and sets `inputs.bundleManifestSHA256`, `lineage.inputBundleVersionId`, `inputBundleManifestSHA256`, `forcingVersion` and `observationVersion`. Contradictory client digests or lineage are rejected; the worker revalidates the frozen selection before reading its actual numerical files. The model uses the selected event catalog rather than assuming the default catalog. A clone preserves its original frozen input bundle, not the browser's later selection.

Routing defaults to `control-split-v2`; the diagnostic workbench also registers immutable `ROUTING-*` parameter versions. Model requests select `routing.versionId`, frozen into `lineage.routingVersionId` and `routingManifestSHA256` over topology, grid-to-unit weights and routing parameter files. State handoff requires matching input bundle ID/digest, forcing, observations, PET policy and routing version/digest. Switching input or routing versions requires reinitialization. A checkpoint containing either added bundle-identity field (`inputBundleVersionId` or `inputBundleManifestSHA256`, including an explicit default ID or a null field) must include its matching routing digest. Deleting routing evidence cannot turn a new state into a legacy one. Only old successful fixed-default states completely lacking both added bundle-identity fields retain the missing-routing-digest compatibility boundary; failed/mismatched artifacts still fail the existing state integrity checks. The product manifest digest is distinct from task `inputManifestSHA256`: parent and child task hashes need not be equal because their periods and parent-state dependencies differ.

The real frontend's input selection is independent of its basin/model `contextKey`; switching it invalidates input previews/caches and does not re-label historical task outputs as new-version results. Existing local drafts, offline history and unrelated lake/cold-region demos stay isolated and preserved. Automated tests cover these boundaries using temporary source/product/runtime fixtures and mocked queues; final all-suite/browser verification counts are recorded only when available.

## Routing and spatial diagnostics - added 2026-10-10

`GET /routing-versions` returns `{versions:[...]}`. To register a variant, copy an existing row's complete `parameters`, modify it, and POST `{name,parentVersionId,parameters}`. The response is 201 with `routingVersionId`. Registration does not select it for future tasks. The baseline and registered files are not overwritten. Unknown parameter names, missing/extra reaches, non-finite values and non-hourly timestep are rejected. RS/RI/RG slope K and all 32 reach K/X values are editable within the validated contract; each Muskingum pair must satisfy `2*K*X <= 1 <= 2*K*(1-X)`. Invalid values are rejected, never clipped.

`parameters.unit_component_slope_k_hours` accepts partial per-unit overrides using authoritative `topology.units[].unit_id` from `/spatial-products`, for example `{"UNIT_ID_FROM_TOPOLOGY":{"RS":8,"RI":30}}`. Each nonempty unit object contains only RS/RI/RG finite numeric K in 0.05–720 hours. Omitted components inherit `default_slope_k_hours`; omit/delete the unit key to reset all its components. Unknown unit IDs, empty objects, booleans and invalid values are rejected. `routing_adapter.py` binds arrays to the unchanged original fast kernel for simulation, calibration, continuation and diagnostic workflows. Its SHA256 is frozen in implementation evidence. Point-query temporary splits inherit parameters only from a unique authoritative parent catchment; ambiguous merges or changed assigned-cell coverage fail instead of averaging K. Cells unassigned in both original and split DEM topology remain unassigned.

`GET /spatial-products` returns `geometry` (110 grid features), `topology` (32 reaches and downstream relationships), real rivers/basin/control products, and `gridRouting` for the 1 km runoff-grid relation. `gridRouting.schemaVersion` is `1.0`; `grids` contains each grid's elevation, area, polygon, center, `downstreamGridId` and registered grid-to-reach area-weight links. D8 direction is derived from the maximum positive downhill slope among adjacent valid cells using `grid_static.csv:elevation_m`; grids without a lower adjacent cell have a null downstream ID. The grid-to-reach links retain `grid_to_unit_weight_control_split_v2.csv` fractions, which close to 1 for each grid. `summary` reports grid, D8-edge, local-terminal, mapping-row, reach and nearest-neighbor-fallback counts. The optional registered input bundle is validated before access. The result is a JSON/map product, not the old DEM raster or full PNG export service.

All following jobs share the durable `/tasks` queue, status/log/cancel/archive/clone operations, and success-only results. They remain `productionEligible=false`. Requests freeze selected input/routing and implementation evidence; clone preserves the frozen conditions and rejects changed implementation evidence rather than silently substituting current code.

### Arbitrary point query

```json
{
  "runType":"pointQuery",
  "requestId":"unique-point-id",
  "sourceTaskId":"JOB-successful-standalone-simulation",
  "point":{"lon":119.45537819524547,"lat":30.143731102817043,"channelWidthM":20}
}
```

The source must be a successful independent simulation/initialization with complete saved runoff evidence, not a continuation. Existing control nodes read the saved formal split result (`FORMAL_SPLIT_RESULT`); river-interior queries split topology and rerun original routing (`RERUN_COMPLETED`); slope points use the original D8/reservoir diagnostic estimate (`ESTIMATED_POINT_FLOW`). Result `pointQuery` contains classification, quality, and `series.time/flow_cms`. None are invented point observations. The query consumes source-task routing, not the browser's current version.

### Boundary and multi-seed experiments

POST `{runType:"diagnosticExperiment",requestId,mode,values,baseRequest}`. `mode` is `multiSeed` (3-5 unique uint32 seeds) or `wlmBoundary` (2-5 unique bounds in 60-200 mm). `baseRequest` is a complete frozen calibration request with calibration, validation and independent-test groups. Maximum search budget per run is 500; optimizer minimums still apply. The original boundary/stability diagnostics operate on actual kernel searches. Results include `experiment.runs/evaluation`, `bestParameters` and a diagnostic candidate version. Nested trial candidates are not published independently. Adopting a candidate requires the same context/input/routing and marks the workspace for rerun; it never grants production eligibility.

### Four-stage diagnostic acceptance

```json
{
  "runType":"productionWorkflow",
  "requestId":"unique-workflow-id",
  "baseRequest":"REPLACE_WITH_COMPLETE_CALIBRATION_REQUEST_OBJECT",
  "wlmUpperBounds":[80,120,160],
  "seeds":[42,101,2026],
  "warmupYear":2014,
  "yearGroups":{"CALIBRATION":[2015,2016,2017,2018],"VALIDATION":[2019,2020],"INDEPENDENT_TEST":[2021]}
}
```

The string above is a documentation placeholder, not a valid `baseRequest`; submit the actual request object. Evaluation years must be unique across groups, in 1961-2024; warmup is 1960 or later and precedes all evaluation years; total span is at most ten years. All annual inputs, including intervening years, must exist. The worker runs WLM-boundary search, fixes the selected upper bound for multi-seed search, continuously carries land/routing state across annual boundaries, then invokes the delivered 12-check gate. `workflow` contains `boundary`, `stability`, `continuous` and `gate`. The legacy runType name does not authorize production: assumed PET is rejected by the gate and every result remains diagnostic. This is not the original full continuous/staged/profile calibration suite.

### Report package and service startup

`GET /tasks/{id}/report-package` emits an attachment ZIP only for successful/archived jobs. Allowlisted filenames are `request.json`, `result.json`, `status.json`, `task.log`, `input-manifest.json`, `event-quality.json`, `routing-evidence.json`, `formal-diagnostic-reports.json`, `reach_flow.csv`, `component_flow.csv`, `annual_water_balance_metrics.csv`, `continuous_hourly_flow.csv`, `continuous_water_balance_report.json`, `production-gate-report.json`. Nested directories are limited to `continuous-balance` and `JOB-*`; arbitrary files, symlink escapes and internal runtime registries are not exported. Files absent for that task type are not fabricated.

The Windows HTTP listener uses exclusive port ownership. Port reservation precedes task-manager recovery, so a duplicate startup fails without marking another running service's tasks interrupted. Use one service per runtime directory; separate test services must set a separate `XAJ_RUNTIME_DIR`. This remains a loopback, single-user diagnostic service, not multi-user deployment.

## State initialization and library

For `runType: "stateInitialization"`, set `period.startTime` and `period.endTime` to the same state hour, `period.warmupDays` to 1–365, and `initialization: {"strategy": "FIXED_WARMUP"}` or `{"strategy": "OBSERVED_FLOW_ANCHORED"}`. Omitted initialization uses fixed warmup for legacy requests. Supported strategies and labels are returned in the case catalog.

Observed-flow anchoring reads the exact state hour from the selected product's outlet-flow CSV. The server freezes time, flow in m³/s, station/control-reach IDs, observation version and SHA256 in `initialization.anchor`, then rechecks it when execution begins. Zero is valid; missing, negative or duplicate observations are rejected. Clients need not provide an anchor. The delivered routing initializer builds routing state; land storage remains the actual warmup state. Result and checkpoint carry `initialization` with both warmup and anchored control flow. The warmup hydrograph is unchanged. Continuation starts one hour after the checkpoint.

| Endpoint | Response / body |
| --- | --- |
| `GET /states` | Existing numerical checkpoint list, unchanged by names or favourites. |
| `GET /state-catalog` | `{ "metadata": { "STATE-JOB-*": { "name", "favorite", "revision", "updatedAt" } } }`. |
| `POST /states/{id}/metadata` | Exact body `{ "name": "June flood", "favorite": true, "revision": 0 }`; name ≤80 characters, blank clears the name. Response is the new metadata row. Revision starts at 0 and increments on each save; stale writes return 409. |
| `GET /states/{id}/checkpoint` | Complete verified numerical checkpoint JSON for a successful or archived task. |

Metadata is stored in the additive `state_metadata` SQL table. Saving a name or favourite does not rewrite the immutable numerical checkpoint. The frontend exports JSON from the verified checkpoint endpoint and CSV with `grid_id,time` plus every land-state field, preserving numerical precision.
# 人工试调补充（1.3.0）

沿用 `POST /api/model/tasks` 和 `runType: "calibration"`，在 `calibration` 对象中传入 `evaluationMode: "manual"`。`initialParameters` 为本次固定参数；场次分组、连续评价期、预热、输入与汇流版本沿用率定契约。后端将 optimizer 冻结为 `FIXED_PARAMETERS`，只评价给定参数一次，不调用搜索器。结果带 `evaluationMode: "manual"`、完整参数版本、各组过程线与指标；任务显示名为“人工参数试调”。案例目录的 `manualCalibration` 标志确认后端支持此模式。

起报状态补算沿用 `runType: "stateContinuation"`：父状态 `initialStateVersionId`、开始时刻为父状态下一小时、结束时刻为所选起报时刻，参数与输入、汇流版本继承父状态。此操作不创建预报单，也不是定时调度任务。

## 成果整理与状态导入（1.4.0）

- `GET /api/model/result-catalog`：`{metadata: {taskId: {name,collection,notes,favorite,revision,updated_at}}}`。
- `POST /api/model/result-metadata/{taskId}`：完整 body `{name,collection,notes,favorite,revision}`；名称≤80、分组≤40、备注≤2000 字符。仅成功/归档任务可整理；初始 revision=0，冲突返回409，保存后递增。SQLite 附加表 `result_metadata` 与数值成果分离。
- `GET /api/model/state-catalog` 额外返回 `checkpointImport:true`。
- `POST /api/model/states/import`：`{checkpoint: <完整平台2.0检查点>, name?: <≤80字符>}`，返回 `{state,task,reused}`。沿用2MB JSON请求上限，网页文件上限1.5MB。
- 导入需精确匹配登记输入及汇流摘要、上下文、网格和几何顺序。原生参数、所有陆面字段和汇流数组均检查有限数值、维度与物理范围。连续蒸散发模式保持一致。
- 导入建立 `stateImport` 来源任务、`source-checkpoint.json`、`input-manifest.json`、本地 checkpoint 和 result。来源文件规范化 SHA256 决定导入编号；成功标记最后写入，未完成中断可重新提交，相同成功文件直接复用。
- 导入操作没有伪造的流量或状态历史，不可克隆为模拟或保存成计算方案。可用返回 stateVersionId 走既有 `stateContinuation`。
- 检查点读取和续算检查导入来源摘要及原始数值；`report-package` 白名单增加 `checkpoint.json`、`source-checkpoint.json`，其余64MB上限和路径约束不变。

## 空间成果（1.5.0）

- `POST /api/model/tasks`：`{runType:"spatialExport", requestId:"唯一请求编号", inputs:{bundleVersionId:"已登记输入版本"}}`。返回统一排队任务，支持进度、取消、归档、克隆。请求不接受资料路径或自定拓扑。
- 后端冻结输入清单及 DEM、控制断面、登记拓扑、网格、面积权重和算法来源摘要。工作进程复用原 D8 与控制断面切分流程，核对全部河段起终点、下游连接、河长、栅格数及单元面积；生成后复查来源摘要。
- `GET /api/model/tasks/{id}/result` 的 `spatial` 含 summary、manifestSHA256、图件文件名及文件摘要清单。
- `GET /api/model/tasks/{id}/spatial-preview?name=terrain-network.png` 或 `routing-units.png`：PNG；仅接受这两个固定名称。
- `GET /api/model/tasks/{id}/spatial-package`：全部空间产品与 manifest.json 的 ZIP。仅成功/归档任务开放，下载前校验 SHA256、文件名及边界，未压缩内容上限64MB。
- 工作区位置：`tasks/{id}/spatial/`。原始 DEM 逐字节保留；5套 ASC/PRJ 分别为高程、汇流分区、流向、汇水面积和河网标记。栅格 EPSG:32650、30m、北侧首行、NoData=-9999；汇流分区0表示有效但未归属河网的栅格，1–32与 units.csv对应。
- 生成的河段/控制断面 GeoJSON 为 EPSG:4326。登记边界和网格保留原经纬度文件，不重投影；源文件没有 crs 时清单保留 null，不擅自补写来源坐标基准。面积权重表原样保留。PNG 是带图例的排版图，GIS 数值分析使用 ASC/PRJ。
- 成果元数据沿用 `result_metadata`，不新建第二套档案或资料库。`report-package` 仍提供任务记录；空间数值与图件使用 `spatial-package`。

## 坡面汇流率定（1.7.0）

沿用 `POST /api/model/tasks`、`runType:"calibration"`，在 `calibration` 中设置 `target:"routing"`。连续时段与场次分组契约不变，`initialParameters` 固定产流参数；`optimizer.strategy` 为 `joint`。

```json
{"target":"routing","routingSearchParameters":{"RS_k_hours":{"minimum":0.5,"maximum":4}}}
```

- 可搜索 `RS_k_hours`、`RI_k_hours`、`RG_k_hours`，范围单位为小时；省略或 null 使用目录默认三参数范围。自定范围须为非空对象、0.05–720小时且包含当前值。未选参数、单元独立覆盖和河道K/X不变。已被全部单元覆盖的全局参数不能搜索。
- 不同时提交产流 `searchParameters` 或 `wlmUpperBound`。人工评价使用产流目标；多随机种子与边界实验不接收坡面汇流来源。
- 结果包含 `calibrationTarget:"routing"`、`bestParameters`（固定产流参数）、`bestRoutingParameters`（完整汇流参数）、`routingCandidate`（新版本）和 `searchPlan.target:"routing"`。`lineage.routingVersionId` 保留起始方案身份；计算结果所用新方案身份在 `routingCandidate.routingVersionId`。
- 新 `ROUTING-*` 版本与产流候选关联登记。采用候选同时载入产流参数和新汇流版本；按原条件重开或克隆仍恢复原起始方案与搜索条件。原始及既有版本不覆盖，无新增库表。

## 自动率定搜索范围（1.6.0）

- 案例目录增加 `searchParameterDefinitions`，含 key、中文 label、symbol、unit、默认 minimum/maximum、允许 limitMin/limitMax 及 storage/sources 分组。
- `calibration.searchParameters` 可省略或为 null，保留原9维默认搜索。自定示例：`{"SM":{"minimum":10,"maximum":40}}`；未列出的搜索坐标固定于本次 initialParameters。KC、C与汇流方案固定。
- 允许 WM、WUM、WLM、B、SM、EX、K_total_day、groundwater_fraction、IM。每项仅含有限数值 minimum、maximum，下限严格小于上限且在登记允许范围内，本次初始坐标需包含在内。空对象、未知参数及错误数值返回400。
- `staged` 自定方案须同时包含 storage 和 sources。日自由水总出流比例及地下径流分配比例由原生 KI/KG 换算；只率定一项时，另一搜索坐标固定，但 KI、KG 可能同时改变。
- `evaluationMode:"manual"` 忽略搜索选择，仍仅评价给定参数一次；页面发送人工请求时不携带 searchParameters。默认旧请求及人工试算保持兼容。
- 返回 `searchPlan:{mode,rows,hourlySource,routing}`，每行含 key/label/symbol/unit、active、initial、value、minimum、maximum。固定项边界为 null，routing 为 fixed。计划保存在不可变结果及参数成果中，`searchSpace` 保留实际活动搜索坐标；前端SQL草稿另保存尚未提交的选择。
- 多随机种子检验保留自定搜索范围。WLM边界及综合工作流与自定范围互斥，提交前明确选择预设范围方案。

# 20261012 接入补充（1.10.0）

`calibration.target` 新增 `joint`，对应产流、三分量坡面和河道联合搜索。`jointProfile` 为 `reference-16`（含KC/C）或 `fixed-kc-c-14`（当前KC/C固定）；`searchParameters` 指产流范围，`routingSearchParameters` 指三坡面K、`muskingum_k_scale`、`muskingum_x`。河段K倍数相对于提交时选定的汇流版本。所有路径继续由后端登记，客户端不能传入文件路径。

`calibration.statePolicy` 支持 `independent-warmup` 和 `continuous-origin`；后者可携带本地整点 `continuousOrigin`。模拟请求的 `period.continuousOrigin` 用于重放已冻结原点；父状态续算不另指定原点。连续原点与首个请求时刻不能超过十年。场次PET可显式选 `constant-zero`、`monthly-climatology-assumption`，或用 `inputs.evaporation.mode=science-version` 和 `science.versionId` 选择不可变科学配置。

`GET /api/model/science-versions` 返回登记科学条件及当前实现摘要；`GET /api/model/science-versions/{id}` 导出经校验的完整依赖闭包。入队时冻结 `lineage.modelImplementationSHA256/scienceConfigurationSHA256/petConfigurationSHA256`。末状态携带同样身份和 `scienceSnapshot`。缺身份的历史状态继续可读，跨实现续算需要重新初始化。

场次提交后冻结 `period.overlapGroups`。闭区间重叠（包括共用一小时）的窗口必须同用途，否则400拒绝。结果主汇总按物理洪水组的唯一小时重新计算，`overlapGroupMetrics` 保存组指标，`eventMetrics` 和 `windowSummary` 保存窗口诊断，`evaluationPolicy=EQUAL_PHYSICAL_GROUP_UNIQUE_HOURS`。报告包增加 `overlap-group-metrics.csv`。

优化结果包含实际基线评价、有效/无效次数及终止原因。基线计入总预算；若后续候选更差，保留当前方案。联合候选保存 `bestParameters/bestRoutingParameters/routingCandidate/science`；采用和人工重放应携带完整方案、来源输入版本与原点。

MDB通过 `scripts/import_mdb_bundle.py` 显式从只读副本登记独立输入版本，不能作为浏览器任意本地路径执行。查询结果须有完成标记和行数核验；超时、截断或冲突记录拒绝登记。完整年NC保留未查询时段缺测，所需模拟范围先按请求终点裁剪，再严格检查。新资料的观测覆盖和连续缺口政策写在自己的质量配置中；水量误差缺测时只代表共同有效小时。

## 同事原方案与状态衔接（1.11.0）

`GET /api/model/parameter-versions/{id}/original-plan?operation=search|fixed-replay` 返回 `{plan,request}`，只读取，不入队。计划嵌在原候选中，冻结10份来源配置及其摘要、16窗口用途、7个物理组、16变量顺序、原产汇流基准、五指标权重和DE控制参数。

`search` 恢复原种群、种子20260820、分组种子42、960评价、48个体、19代、F=0.8、CR=0.7。原当前参数作为初始种群候选，无效参数组计入预算；原河道K基准只作为可信来源搜索的倍率参考，不允许普通固定模拟采用。`fixed-replay` 使用候选最佳参数，仅一次固定评价，补算17窗口。提交仍走既有 `POST /api/model/tasks`。

请求的 `sourcePlan={parameterVersionId,planId,planSHA256,operation}` 由后端重新核验。修改用途、目标权重、种子、范围或原汇流参考后，页面解除原方案标识，按当前方案提交。允许缩小试运行预算，但不超过原预算。普通未核验请求不能借原标识绕过汇流或资料检查。

来源资料使用 `sourceQcPolicy=SOURCE_ORIGINAL_20261011`，原QC接受16窗口。`31230701`因721h被原QC排除，只允许可信来源固定重放补算，保持 `accepted=false` 和补算说明；缺降雨或其他QC失败不能补算。原16窗口和7组评价分别保存于 `sourcePlanEvaluation.originalWindowMetrics/originalGroupMetrics`，17扩展组不能改称原7组。

联合预设增加 `all-physical-16`，宽范围仍在原搜索编辑器中。五指标目标支持NSE、KGE、PBIAS、洪峰误差及峰现时间误差；旧 `VOLUME_ERROR`默认保持兼容。

`stateInitialization`的 `initialization.strategy=CONTINUOUS` 使用显式科学版本及 `period.continuousOrigin`，从原点补算至目标时刻。`stateContinuation`和 `forecast`校验父检查点后继承父科学配置；显式不同科学条件返回400。案例目录提供当前 `modelImplementationSHA256`，前端据此提示旧状态重新初始化。科学条件依赖见《SCIENCE_CONTEXT》。

普通人工试调允许单场洪水，仍须有率定用途、有效观测和完整降雨。冻结的原搜索计划不进入更换种子的诊断实验；需要参数检验时先采用候选，登记普通自动方案后再选入诊断页。

