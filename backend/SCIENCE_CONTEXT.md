# 科学配置与状态兼容

科学配置登记在工作区 `science-versions/SCIENCE-<内容摘要>/`。每个版本冻结状态配置、初态模板、PET 配置；逐小时 PET 的 CSV 本体一并保存。记录不修改活动选择，诊断配置及候选始终 `productionEligible=false`。

`ScienceVersions(runtime_root, model_root).register(...)` 接受配置文件或 JSON 配置，逐小时 PET 必须从有依赖的文件登记。`resolve(version)` 重核每个文件的大小和 SHA256、PET 数据及版本身份。`list()` 返回公开配置和当前模型实现摘要，移除运行文件绝对路径。`apply_to_request(request, version)` 明确选择本次请求，返回冻结请求及描述，不写全局“当前”设置。

`freeze_science_selection(request, runtime_root=None, model_root=None)` 返回 `(request, descriptor)`。请求新增 `science={versionId,configurationSHA256,petConfigurationSHA256}`；来源新增 `scienceVersionId`、`modelImplementationSHA256`、`scienceConfigurationSHA256`、`petConfigurationSHA256` 和 `petMode`。描述的 `paths` 含冻结的 `state_config/initial_land_state/pet_config` 文件，`configs` 为其内容。

未选择版本时，既有场次零 PET 与模拟/连续率定月 PET 仍按输入选择登记。显式版本的零/月 PET 标签与真实配置不符时拒绝；`science-version` 必须携带登记版本。率定状态策略与版本不符时拒绝。状态策略为 `independent-warmup` 或 `continuous-origin`；默认预热小时来自登记的状态配置，具体任务时段、预热覆盖和显式连续起点仍留在冻结请求中，不随下一小时续算改变科学版本身份。

状态续算比较三个内容身份，并继续检查输入、汇流、上下文、网格、参数和下一小时。旧状态可查看、命名和导出；缺少实现/PET/科学身份时不得续算，提示重新初始化。新状态的身份同时与成功结果来源核对。

模型实现身份覆盖原数值内核、实际强迫读取、产汇流适配、科学条件冻结及原方案搜索编排。实现变更后旧状态仍保留，续算要求重新初始化；图件样式不并入计算身份。

`ScienceVersions.export(version)` 返回闭包 JSON，内含清单和文件原字节的 Base64。检查点保存 `scienceSnapshot`；`state_import` 用 `import_snapshot` 验证并登记后，再与目标机器当前模型实现比较。月 PET 单独检查点可移机；较大的逐小时 PET 仍受既有请求大小限制，使用完整工作区迁移。完整备份须包含 `science-versions`。

`relocated_task_science(task_folder, expected_digests)` 只读取同一任务目录的三个 `*_snapshot.json`，不追随旧电脑的 E: 绝对路径。提供原来源摘要时必须相同，缺失任何快照明确失败。通用调用未提供原摘要时只建立当前交付文件的摘要证据，不能声称核验了从未提供的原摘要。

`scripts/import_20261012_candidates.py` 是历史候选登记工具，不属于平台运行路径或便携发布包。它校验三份科学快照的来源 SHA256，登记冻结率定 KC=0.5411732734801327 与人工修改 KC=0.540001，保留各自路由和科学版本引用；人工修改方案不复制冻结最优指标。运行时必须显式提供已核验的 `--source` 和 `--runtime`，不会默认选择本机目录或活动方案。若目标模型实现不同，会产生新的登记任务编号，不覆盖旧记录。

导入候选只匹配来源 MDB 内容摘要、原空间计算定义和原QC配置对应的已登记输入，通过完整输入清单核验 `SRC-MDB-<SHA256>` 或 `databaseSHA256`，并要求 `targetCoordinatePolicy=LEGACY_CLOSED_RING_VERTEX_MEAN`、`forcingValueDtype=float64`、`sourceQcPolicy=SOURCE_ORIGINAL_20261011`。该来源算法把闭合多边形重复首点计入顶点均值，目标偏离真实网格中心；为原方案计算保留。原QC文件按字节登记，新增观测覆盖诊断不改变来源接受名单。网格中心产品另行保留，原指标不归给不同资料条件。匹配后记录必需输入版本、清单摘要、驱动/观测版本、原连续起点及预热小时；多个精确匹配版本明确拒绝自动选择。缺少精确产品时仍标记 `requiresInputRegistration=true`。原交付保存指标标明来源，平台固定重放另外保存当前实现的评价。

接入顺序：统一服务入队前冻结科学选择；模型 worker 执行时重新核验同一冻结选择；worker 使用描述的初态/PET文件；保存状态时写上述来源字段和 `scienceSnapshot`；结果来源继承冻结请求的身份。诊断子任务的基础请求和点查询来源也须保留原科学选择。

`inherit_state_science(request, checkpoint, runtime_root=None, model_root=None)` 只在父检查点已通过成功来源核验后调用。它核对当前实现和完整科学身份，必要时从检查点快照登记依赖，继承父版本；显式不同的科学配置拒绝。续算、起报补算及预报不会被旧客户端的默认月PET标签替换。连续初始化使用 `initialization.strategy=CONTINUOUS`，须明确选择连续状态策略科学版本和原点；独立预热或流量锚定另选对应初态条件。

来源候选内的 `originalCalibrationPlan` 保存原10份配置、原16场用途、7个物理组、16变量范围、目标函数和搜索种子等，不另建参数目录。恢复设置与固定重放分别构造请求，详情见API的1.11.0补充。
