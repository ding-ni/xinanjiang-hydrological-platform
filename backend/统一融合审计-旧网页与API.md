# 统一融合审计：原始网页/API 与当前 Vue 入口

初始审计：2026-10-09；最新增量：2026-10-10。前文初审保留，**当前状态见文末增量节**。

初始盘点发现“观测输入查询、洪号面雨量检查、完整逐时网格回放尚未迁移”；本轮已补上这些能力的新统一接口。该初审结论仅是改造前的历史快照，不能继续作为当前状态。旧 `/api/v1` 未直接挂载，与旧功能已有新契约替代，是两个不同判断。完整当前矩阵见 [原后端功能融合清单](F:/新安江水文模型/原后端功能融合清单.md)。

## 结论

当前统一入口融合的是：Vue 构建产物 `frontend-demo/app/dist-unified`、`backend/server.py` 的 `/api/model` 任务服务、`backend/worker.py` 复用的共享模型内核，以及同源 QC 输入浏览、独立场次质控、已保存逐时网格状态诊断。

原始网页和 HTML 产品仍保留在原始交付包 `后端文件/.../model/frontend`，未复制到 vendor 的前端目录（`backend/vendor/model/frontend` 不存在）。原 `/api/v1/*` 和 `/api/calibration/*` 服务代码有 vendor 副本，但旧路由和旧网页没有作为第二套运行时入口挂载。部分诊断能力已经迁移到新 `/api/model` 契约；数据源登记、任意点查询、汇流参数版本和生产工作流仍未迁移。

## 当前统一入口实际暴露的 API

证据：[server.py](F:/新安江水文模型/backend/server.py) 的 `Handler.route`。

- `/api/model/health`
- `/api/model/catalog`
- `/api/model/inputs/catalog`：实际 QC 降雨/出口流量覆盖、登记洪号、最新可用窗口和输入血缘
- `/api/model/inputs/window`：真实 NC 网格、出口 CSV 流量、控制区面积加权雨量及缺测
- `/api/model/inputs/events/{eventId}`：按登记洪号独立查询实际资料及物理质控，预热参与审计但不加入事件展示时段
- `/api/model/tasks`（GET/POST）
- `/api/model/calibration-tasks`、`/api/model/simulation-tasks`、`/api/model/forecast-tasks`
- `/api/model/tasks/{id}`、`result`、`log`、`cancel`、`archive`、`clone`
- `/api/model/tasks/{id}/grid-state/catalog`：成功任务保存的逐时网格目录
- `/api/model/tasks/{id}/grid-state?time=...`：精确整点网格帧
- `/api/model/tasks/{id}/grid-state?unitId=...`：单个登记网格的保存过程；不能与 time 同时查询
- `/api/model/tasks/{id}/event-quality`：已生成的作业场次质控记录，失败任务也可保留审计证据
- `/api/model/states`
- `/api/model/parameter-versions`

其他未登记 `/api/*` 直接返回“接口不存在”；静态入口默认是 `frontend-demo/app/dist-unified`，不会提供原 `model/frontend`。输入查询只支持登记的南安溪-徐畈 1 km，不能用一个案例的输入或结果替代未支持尺度。

## 当前 Vue 入口实际调用的 API

- `src/services/modelBackendService.js`：通过明确启用的 `VITE_MODEL_API_BASE` 调用统一模型 API；独立演示不隐式调用真实服务。
- `src/services/adapters/httpAdapter.js`、`src/stores/tasks.js`：任务提交、轮询、恢复、结果、日志、取消、归档和克隆。
- `src/stores/app.js`：刷新案例能力、状态和参数版本，配置原生参数并恢复真实任务。
- `src/services/modelInputService.js`：QC 目录、时段和登记洪号查询；检查案例、尺度、连续时次、输入版本、网格几何和出口序列对齐；逐站雨量/水位缺失不借用 MDB。
- `src/services/modelInputPresentation.js`：仅投影实际输入；计算前 `forecast=[]`、`metrics=null`，显示实测出口过程和控制区面积加权降雨，不生成替代模拟、温度或径流分量。
- `src/services/taskDiagnosticService.js`、`src/views/StateView.vue`：读取成功任务保存的网格时刻帧和单网格过程，不从均值合成曲线。
- `src/services/backendEventCatalogService.js`、`src/stores/calibration.js`：后端目录对窗口、统计和来源具有权威性；用户用途、备注、历史保留，人工修订完整隔离为 `localDraft`，未登记场次仍保留而不冒充 QC 输入。
- `src/services/calibrationSubmissionService.js`：候选集拒绝 `runtimeRegistered=false`，已登记场次须有权威 active 状态；null/空/非数值洪峰不被 Number(null) 当作零洪峰。
- `src/services/savedModelResultService.js`：只读成果投影要求真实结果有明确来源、`JOB-*` 和一致上下文；真正的保存结果允许离线浏览，南安溪旧演示/无来源/跨案例结果不展示。色林错两个完整登记尺度的显式 fixture 保留，历史资料不删除。
- `src/services/parameterLibraryService.js`：参数候选和自动库同样按完整上下文、来源和任务投影，旧示例、跨尺度及无来源版本只留原存储；服务端版本覆盖缓存指标与生产资格，发布底层要求明确生产资格，人工审核不能授予资格。Config 页同步真实候选，“载入试算”进入原生参数连续模拟。
- `src/views/CalibrationView.vue`、`src/views/ConfigView.vue`：显示真实/离线资料区别、固定输入版本、独立物理质控及已融合/未融合模块。统一模式断线或切尺度不静默回退为旧示意计算。
- `src/services/forecastSubmissionService.js`：保留 `/api/model/forecast-tasks` 入口，但当前后端契约仍拒绝 forecast（见下一节）。

## 预报入口的实际边界

- `backend/contracts.py` 的 `OPERATIONS` 只登记 `calibration`、`simulation`、`stateInitialization`、`stateContinuation`，没有 `forecast`。
- `backend/catalog.py` 的 operations 没有 forecast，并明确声明 `forecast` 未登记正式预报降雨与生产参数。
- 因而 server 虽注册了 `/forecast-tasks` 路径，`manager.submit()` 经 `normalize_request()` 仍会拒绝该 runType。该接口是入口占位，不是已接通的生产预报能力。
- 色林错的完整登记前端 fixture 演示保留；旧南安溪或不完整上下文的 mock 不恢复、克隆或当作真实结果使用，但资料不删除。

## 原始网页包含的能力

证据：原始 `model/frontend/index.html:17-29`。

原始页面包含 11 个业务页：实时监视、预报作业台、空间拓扑、状态诊断、洪水面雨量检查、产流状态回放、参数管理、运行档案、率定验证、数据源管理、系统配置。

原始客户端 `model/frontend/services/api-client.js:23-73` 调用：

- `/api/v1/overview`
- `/api/v1/data-source`（含上传）
- `/api/v1/monitor/stations`
- `/api/v1/topology`、`/api/v1/topology/dem`
- `/api/v1/events`、事件详情和事件过程线
- `/api/v1/parameters`（GET/PUT）
- `/api/v1/grid-geometry`
- 洪号网格雨量、面雨量查询
- `/api/v1/runs`、手工运行、运行档案、过程线、诊断、网格状态、SSE 运行事件
- `/api/calibration/catalog`、`/api/calibration/tasks`

原始页面对这些 API 的直接调用证据：

- `model/frontend/app.js:122,156,278,413,424,444-488,512-522`
- `model/frontend/state-explorer.js:78,116-125`
- `model/frontend/flood-rainfall-check.js:31,52,59-61`
- `model/frontend/parameter-editor.js:58,73,91-99`

## 原始 API 仍然存在，但没有挂到统一服务

原始 `model/code/calibration_platform/http_adapter.py` 是完整的另一套 HTTP 适配器：

- `http_adapter.py:55-68` 路由 `/api/v1/*` 和 calibration catalog/tasks。
- `http_adapter.py:231-348` 实现 overview、数据源、监测站、拓扑、DEM、点查询上下文、事件、参数、网格几何、网格/面雨量、事件与运行过程线、诊断、网格状态、SSE 等 GET 能力。
- `http_adapter.py:348-369` 实现手工运行、数据源上传、运行提交、点流量查询等 POST 能力。
- `http_adapter.py:369-374` 实现参数 PUT。
- `http_adapter.py:425-440` 的 `create_platform_server()` 可以创建原始平台服务器，但 `backend/server.py` 没有导入或调用它。

因此旧路由本身属于“保留的 vendor 能力”，不是当前 Vue 可直接调用的旧 API。不过其中网格状态、洪号资料和面雨量等功能，本轮已有上文列明的新统一实现；不能由旧路由未挂载推断这些功能仍全部缺失。

## Worker 实际使用范围

`backend/worker.py` 没有调用 `IntegratedResultsService` 或原始 HTTP API，而是直接导入共享内核并读取固定 QC 文件：

- 加载南安溪 110 个有效 1 km 网格、控制切分 v2 拓扑、权重、原生参数、出口流量 QC 和固定 1 km 降雨 QC；结果始终 `productionEligible=false`。
- `ModelJob.route_with_history` 逐小时调用原共享 `simulate_xaj_liquid_grids`，保存真实逐格状态、产流分量、输入和诊断；预热真实计算但不保存到分析时段的回放产品。坡面和河网汇流使用原 `FastRoutingRunner`。
- 连续模拟/状态初始化/状态续算生成完整 land/routing checkpoint；续算继承全部记忆，不重复预热。场次率定复用原搜索和内核，保存参数版本、各组评价和审计。
- [task_diagnostics.py](F:/新安江水文模型/backend/task_diagnostics.py) 验证成功结果与 history/checkpoint/request 的任务、上下文、输入哈希、时间轴、网格、字段和数组形状；检查 NPZ/JSON SHA256 和历史末 land 精确等于 checkpoint。缺历史或错配时明确失败，不插值、外推或倒推。
- [state_integrity.py](F:/新安江水文模型/backend/state_integrity.py) 由提交和执行路径调用，确认父任务 completed/archived、版本/来源任务和成功结果一致、两份 checkpoint 相同、输入哈希与时刻一致；失败、取消或运行中的残留状态拒绝续算。
- [input_service.py](F:/新安江水文模型/backend/input_service.py) 读取与 worker 相同的登记输入、控制区和权重，独立审计场次预热期资料；查询服务不执行另一套降雨空间化。

这说明“内核被统一任务服务复用”成立，但“原始网页/API 全部融合”不成立。

## 当前缺口分类

未由统一 Vue/API 等价接入的主要能力：

1. 30 m DEM、河网拓扑详细查询和网格—河段诊断。
2. 原始源站 QC 和独立降雨 IDW/场次目录重建任务；登记洪号的网格雨量、控制区面雨量及实测过程已通过统一只读接口迁移，不再列为全量缺口。
3. 任意点流量查询。
4. 旧运行未保存逐时状态时的完整回放无法追补；新任务逐时、逐网格保存及回放已迁移。旧原生网页本身未复制。
5. 坡面/河网汇流参数编辑和版本管理；原始直接参数 PUT 未兼容。11 个原生的产流参数已可随统一请求冻结，不再属于全部未接入。
6. MDB/ACCDB/CSV/XLSX 数据源上传和登记。
7. 原始运行档案、诊断、静态报告产品。
8. production calibration workflows（原始适配器可选挂载，但当前统一服务没有启用）。
9. 动态实时监测服务。

工程缺口与科学资格分开：上述页面、接口、任务编排缺失是可继续迁移的工程工作，不应以“尚未生产验收”替代说明；实测 PET、新 MDB 字段映射、未来预报降雨、代表性独立评价和参数生产发布则另需数据与科学验收。旧生产工作流可以保留并用于受限诊断，但当前没有统一编排，也不能据此发布生产参数。

## 本轮验证范围

- 已确认前端 89 项测试全部通过，含 QC 输入、缺测、时次/尺度隔离、场次合并、启停防绕过、保存结果来源白名单，以及参数库读写隔离/原记录保留/生产资格约束。
- 本轮真实 110 网格、48 小时数值核对报告：逐时保存与原批量内核、连续计算与分段续算的最大差均为 0。另有只读内存 120 小时等价核对，无模型公式修改。
- [QC 浏览器验收记录](F:/新安江水文模型/runtime/model-input-browser/result.json) 已保存 passed：110 格逐值一致、控制区面雨量、无源站替代、场次回放、出口过程、断网清空和不支持尺度隔离，`pageErrors=[]`。这是固定 QC 产品回放，不是动态实时或生产预报。
- 后端全量测试、最新构建和其余浏览器验收以本轮最终验收记录为准，这里不提前填写未确认通过数。
- 输入缺测拒绝、状态完整性校验及数值复现不等于生产科学验收，不能推出全部外围功能已经完成。

## 重要判断

不能把当前系统描述为“原始后端及其网页已经全部统一”。准确表述应为：

> 统一平台已接入南安溪 1 km 的真实模型内核、场次率定、连续模拟、状态初始化/续算、同源 QC 输入浏览、独立场次质控、保存状态回放及统一任务生命周期；旧网页和旧路由没有作为第二套入口挂载。此句为 2026-10-09 阶段结论；受限资料加工、汇流版本、点查询及四阶段诊断的最新状态见下节，生产资格仍需独立的数据与科学验收。

## 2026-10-10 增量：不再属于缺口的能力

- 输入加工/登记：`input_products.py`、`input_processing.py` 与 Vue 资料工作台提供受限站点 JSON、QC 窗口、原站点 QC/IDW、成功后显式登记和手动选用；不是全部 MDB/ACCDB/Excel/CSV 映射。
- 汇流版本：`routing_versions.py` 提供不可变全局坡面 K 和河段 K/X；提交与执行共同冻结并核验版本摘要，续算拒绝跨版。
- 空间拓扑：`diagnostic_jobs.py::spatial_payload` 和 `/spatial-products` 提供真实网格/河段/控制区，`DiagnosticView.vue` 地图消费，不是旧静态示意图。
- 任意点：`pointQuery` 复用原点分类、控制切分和汇流；控制点读成功源结果，河道内部重跑，坡面明确估算；来源和结果可追溯。
- 诊断实验：`diagnosticExperiment` 冻结完整率定请求，执行原 WLM/多种子判据；候选不自动生产发布。
- 完整诊断编排：`workflow_jobs.py` 执行 WLM、固定边界多种子、跨年连续水量平衡和 12 项门槛，逐小时 CSV/年度表/验收报告进入统一白名单成果包。不是完整连续/分阶段/profile 率定策略集。
- 统一任务生命周期与前端：新类型使用同一任务队列、日志、取消、归档、克隆和档案详情；Vue 不挂载旧 HTML，不运行第二个旧任务管理器。采用候选核对上下文、输入、汇流，并维持诊断身份。

剩余工程缺口：完整 DEM/PNG 产品、逐单元坡面汇流、实测流量锚定初始化、任意检查点恢复、完整连续/分阶段/profile 率定。新数据映射、动态观测、正式预报和冷区/湖泊实际物理接入按范围暂缓或未验收。所有真实结果仍为 `productionEligible=false`；工程融合、运行完成和科学验收是三个不同事实。

操作路径见项目根目录 `平台实操流程.md`，逐项状态见 `原后端功能融合清单.md` 第十一节，最终证据见 `统一系统验收记录.md` 最新节。

