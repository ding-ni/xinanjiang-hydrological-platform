"""Validate persisted state handoff against its successfully completed task."""
from __future__ import annotations

import re
from common import DEFAULT_INPUT_BUNDLE, ROUTING_VERSION, read_json, safe_task


def assert_state_input_compatibility(checkpoint, request):
    """Changing the forcing product requires explicit reinitialization."""
    lineage = request.get("lineage", {})
    selection = request.get("inputs", {})
    version = selection.get("bundleVersionId", DEFAULT_INPUT_BUNDLE)
    parent_version = checkpoint.get("inputBundleVersionId", DEFAULT_INPUT_BUNDLE)
    if version != parent_version:
        raise ValueError("父状态与当前输入版本不一致；切换资料必须重新初始化")
    digest = selection.get("bundleManifestSHA256")
    parent_digest = checkpoint.get("inputBundleManifestSHA256")
    if parent_digest is not None and parent_digest != digest:
        raise ValueError("父状态的输入产品摘要与登记版本不一致")
    if parent_digest is None and parent_version != DEFAULT_INPUT_BUNDLE:
        raise ValueError("父状态缺少输入产品摘要，不能继承未确认的资料版本")
    defaults = {"forcingVersion": "precipitation_qc_20260819",
                "observationVersion": "xufan_outlet_hourly_qc_20260819"}
    for key, default in defaults.items():
        if checkpoint.get(key, default) != lineage.get(key, default):
            raise ValueError("父状态与当前驱动或观测来源不一致；请重新初始化")
    if checkpoint.get("petMode", "MONTHLY_CLIMATOLOGY_ASSUMPTION") != "MONTHLY_CLIMATOLOGY_ASSUMPTION":
        raise ValueError("父状态 PET 方案不兼容，不能自动转换")
    if checkpoint.get("routingVersionId", ROUTING_VERSION) != lineage.get("routingVersionId", ROUTING_VERSION):
        raise ValueError("父状态汇流版本不兼容，不能自动转换")
    parent_routing_digest = checkpoint.get("routingManifestSHA256")
    # Legacy compatibility applies only when the entire added bundle identity
    # is absent. An explicit default ID (or a present-but-null digest) is still
    # new-format metadata and cannot be downgraded by deleting routing evidence.
    has_bundle_identity = any(key in checkpoint for key in ("inputBundleVersionId", "inputBundleManifestSHA256"))
    if has_bundle_identity and parent_routing_digest is None:
        raise ValueError("父状态缺少汇流文件摘要；新格式输入版本必须重新初始化")
    if parent_routing_digest is not None and parent_routing_digest != lineage.get("routingManifestSHA256"):
        raise ValueError("父状态的冻结汇流文件摘要不一致，必须重新初始化")


def validated_parent_state(state_path, tasks_root, state_version_id):
    if not re.fullmatch(r"STATE-JOB-[A-Za-z0-9_-]+", str(state_version_id)):
        raise ValueError("只能使用后端生成的真实状态版本")
    checkpoint = read_json(state_path)
    source_id = checkpoint.get("sourceTaskId")
    if source_id != state_version_id.removeprefix("STATE-") or checkpoint.get("stateVersionId") != state_version_id:
        raise ValueError("状态版本与来源任务编号不一致")
    folder = safe_task(tasks_root, source_id)
    status = read_json(folder / "status.json")
    if status.get("taskId") != source_id or status.get("status") not in {"completed", "archived"}:
        raise ValueError("父状态来源任务尚未成功完成；失败、取消或运行中状态不能续算")
    result = read_json(folder / "result.json")
    stored = read_json(folder / "checkpoint.json")
    if checkpoint != stored:
        raise ValueError("状态版本与来源任务保存的检查点不一致")
    if status.get('runType') == 'stateImport':
        from state_import import fingerprint
        provenance = checkpoint.get('importProvenance', {})
        source = read_json(folder / 'source-checkpoint.json')
        if (provenance.get('sourceSHA256') != fingerprint(source)
                or checkpoint.get('inputManifestSHA256') != fingerprint(provenance)
                or result.get('importProvenance') != provenance):
            raise ValueError('导入状态与原始文件摘要不一致')
        # Imported values must remain exactly those in the preserved source file.
        for key in ('time', 'context', 'parameters', 'land', 'routing', 'gridIds', 'gridGeometry'):
            if source.get(key) != checkpoint.get(key):
                raise ValueError('导入状态数值与原始文件不一致')
    digest = checkpoint.get("inputManifestSHA256")
    # Older successful jobs recorded contextKey but retain the full frozen
    # request. Verify that authoritative request; do not invent missing context.
    result_context = result.get("context")
    if result_context is None:
        result_context = read_json(folder / "request.json").get("context")
        if not isinstance(result_context, dict) or result.get("contextKey") != result_context.get("contextKey"):
            raise ValueError("旧任务结果与冻结请求上下文不一致")
    if (result.get("taskId") != source_id or result.get("stateVersionId") != state_version_id
            or result.get("resultSource") != "model-backend"
            or result_context != checkpoint.get("context")
            or not digest or result.get("lineage", {}).get("inputManifestSHA256") != digest
            or result.get("stateTime") != checkpoint.get("time")):
        raise ValueError("父状态与成功结果、上下文或冻结输入哈希不一致")
    if checkpoint.get("inputBundleVersionId") is not None:
        for key in ("inputBundleVersionId", "inputBundleManifestSHA256", "forcingVersion", "observationVersion", "routingVersionId", "routingManifestSHA256"):
            if result.get("lineage", {}).get(key) != checkpoint.get(key):
                raise ValueError("父状态的输入产品或汇流版本与成功结果血缘不一致")
    return checkpoint
