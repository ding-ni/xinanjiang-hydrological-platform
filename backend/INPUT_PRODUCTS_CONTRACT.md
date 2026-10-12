# 输入产品阶段契约（2026-10-10）

本轮仅本机诊断，所有产物 productionEligible=false；原交付只读，vendor 数值升级逐项登记，旧资料和版本不覆盖、不删除。

## HTTP 与浏览选择

- GET /api/model/input-products → {schemaVersion,defaultBundleVersionId,versions:[公开登记版本]}。
- GET/POST /api/model/input-sources → {sources:[]} / 新来源公开记录。
- GET/POST /api/model/input-tasks → {tasks:[]} / 202 处理任务；GET /{id}, /{id}/result, /{id}/log；POST /{id}/cancel、/{id}/register。
- 输入 catalog/window/events 原路由增加可选 bundleVersionId；缺省固定旧版本，不允许全局 current/latest。
- 模型 POST request.inputs.bundleVersionId 显式选版本；服务端填写 bundleManifestSHA256，并冻结 lineage.inputBundleVersionId/inputBundleManifestSHA256/forcingVersion/observationVersion。客户端传入摘要不一致必须拒绝。
- GET catalog 的默认 case 不随选择变化，另以输入 catalog 提供该版本的 events、覆盖和来源。无关页面与色林错 demo 不受影响。

## 源资料 JSON

浏览器只上传有界 JSON，不接受本地路径、MDB 自动转换或任意 NC。

```json
{
  "schemaVersion": "1.0",
  "projectId": "nananxi-xufan", "resolutionId": "1km",
  "name": "真实站点资料名称", "provenanceNote": "资料单位与时刻已人工核对",
  "unit": "mm", "crs": "EPSG:4326", "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
  "times": ["2017-06-18T00:00", "2017-06-18T01:00"],
  "stations": [{"id": "真实站号", "lon": 119.1, "lat": 29.8, "values": [null, null]}]
}
```

示例 null 是格式说明，不是交付的实测资料。仅本地整点、连续时间轴，1–32 站，最多8784小时，最多200000个值，HTTP JSON体最多2MB；非数值非null拒绝。缺测null；负数可保留原值且处理时原内核判为缺测，需报告计数。年范围1960–2024（固定出口流量限制）。短窗口全零不是年度失效：默认保留零；只有覆盖完整自然年时才允许年度零站重新判缺政策。源资料登记不授予科学真实性或生产资格。

## 输入处理请求

```json
{
  "requestId": "DATA-客户端唯一号", "operation": "qcWindow",
  "projectId": "nananxi-xufan", "resolutionId": "1km", "name": "独立版本名",
  "start": "2017-06-12T11:00", "end": "2017-06-29T11:00",
  "parentBundleVersionId": "NANANXI_QC_20260819_CONTROL_V2"
}
```

operation=stationIdw 时传 sourceId、idwPower(1–4)、minimumStations(1–站数)、zeroOnlyPolicy=KEEP（默认）/ANNUAL_RECLASSIFY，不可传任何路径。处理窗口1–8784小时；新产品含同源网格NC、固定出口流量引用、新场次目录、QC报告、来源和每个文件SHA256。场次只保留完全落在新产品覆盖内的登记窗口，统计重新计算，不沿用不匹配的旧雨量；按原物理QC审计。不能把任务完成自动视为登记：POST register 在成功状态、摘要和文件再次校验后显式登记。

## 内部文件与API

input_products.py 管理来源、基线和登记产品；InputProducts(model_root=None,runtime_root=None) 提供 list_versions(), list_sources(), register_source(payload), normalize_processing_request(payload), resolve(bundle_version_id=None,verify=True), register_task(task_id)。resolve 返回私有描述，包含 bundleVersionId,manifestSHA256,forcingVersion,observationVersion,modelInputVersion,precipitationDir(Path),paths（grids/geometry/topology/mapping/flow/events/quality），以及公开信息。普通公开列表不包含绝对本地路径。

input_product_worker.py 执行独立资料加工，CLI --task 文件夹；只读request/status，写progress/task.log/result，result含 resultVersionId,lineage,productVersionId,公开产品摘要。目录为 runtime/input-tasks/DATA-*/、runtime/inputs/sources/SRC-*/、runtime/inputs/products/INPUT-*/；失败/取消产物不得登记。

server.py 负责同源 HTTP 和 DATA/JOB 两类任务编排；InputService、模型 worker 和 contracts 消费已验证版本。Vue 资料面板、app 独立输入选择和 modelInputService 查询使用同一版本身份；各提交入口冻结版本，切版清缓存并隔离并发响应。

固定默认保持基线 Forcing/Observation 常量以及模型输入名称。InputService 私有bundle可选；直接构造的 model_root 测试夹具不强制接 registry。实际 HTTP 输入查询（含省略 bundleVersionId）每次先 resolve 校验完整性，再按版本与摘要复用 reader；缓存不能跳过摘要核验或混读变动后的资料。生成产品仍sourceType=model-qc（模型QC数据通道）；但公开 metadata/qualityNotes/sourceLabel 必须明确用户来源、未独立验收，不能将用户声明伪称原始实测认证。

续算必须与父状态的bundleVersionId（旧无字段视为固定默认）、forcingVersion、observationVersion、PET和冻结汇流版本一致；跨资料版本需重新初始化，禁止无提示继承。已有离线历史结果保留其原版本，不被当前选择覆盖。
# 20261012 MDB独立输入补充

MDB在CLI中显式登记源快照，Access只连接副本；每次SELECT有单次超时、完成标记、记录数和文件摘要。查询范围为本轮所需连续区间及完整fl_code，范围外雨量/流量不声称已审计。雨量重复相同值只保留一次，冲突拒绝；非法/负值/缺记录保留缺测，只有效站参与IDW。产品仍沿现有不可变InputProducts注册，包含独立NC、观测流量、场次、质量配置和来源闭包。

`manifest.forcingPolicy=STRICT_MISSING` 使用公共strict_forcing加载器；浏览和worker读取同一产品。先裁剪所需终点，再检查实际需要的历史/预热缺测。新MDB的observation_coverage包含覆盖率、最大连续缺测、涨峰退水段覆盖和部分洪量范围，不套用旧默认接受政策。覆盖门槛为暂用诊断配置，不能冒称生产标准。
