"""Copy a verified input bundle closure between independent local runtimes."""
from __future__ import annotations

import shutil
from pathlib import Path

from common import MODEL, read_json
from input_products import InputProducts, DEFAULT_BUNDLE_VERSION_ID, DATA_TASK_ID, file_sha256


def promote_input_bundle(bundle_id, source_runtime, target_runtime, *, model_root=MODEL, dry_run=False):
    source = InputProducts(model_root, source_runtime)
    target = InputProducts(model_root, target_runtime)
    if source.runtime_root == target.runtime_root:
        raise ValueError("输入接入须复制到另一个独立runtime")
    descriptors, active, finished = [], set(), set()
    def visit(identifier):
        if identifier == DEFAULT_BUNDLE_VERSION_ID:
            return
        if identifier in active:
            raise ValueError("输入版本来源链形成循环")
        if identifier in finished:
            return
        active.add(identifier)
        descriptor = source.resolve(identifier)
        visit(descriptor["manifest"].get("parentBundleVersionId", DEFAULT_BUNDLE_VERSION_ID))
        descriptors.append(descriptor)
        active.remove(identifier)
        finished.add(identifier)
    visit(bundle_id)
    if not descriptors:
        raise ValueError("固定基线由目标自行登记，不能复制覆盖")
    baseline = source.resolve()
    # Resolving verifies an existing target baseline, or pins the same model's
    # unchanged baseline. The source baseline registration file is never copied.
    if target.resolve()["manifestSHA256"] != baseline["manifestSHA256"]:
        raise ValueError("目标固定基线与来源模型摘要不同，拒绝接入")
    records, registry_paths = {}, set()
    def add(path):
        path = Path(path).resolve()
        reference = source.reference(path)
        if reference["root"] != "runtime":
            return
        relative = reference["path"]
        if relative == "inputs/fixed-baseline-manifest.json":
            raise ValueError("不得覆盖目标固定基线清单")
        destination = target._path(reference)
        if not path.is_file() or path.is_symlink():
            raise ValueError("来源依赖不是可复制的普通文件")
        digest, size = file_sha256(path), path.stat().st_size
        if destination.exists() and (not destination.is_file() or file_sha256(destination) != digest or destination.stat().st_size != size):
            raise ValueError("目标已有同路径不同内容，拒绝覆盖：" + relative)
        records[relative] = {"path": relative, "sha256": digest, "bytes": size,
                             "source": path, "destination": destination, "existingIdentical": destination.is_file()}
    for descriptor in descriptors:
        manifest = descriptor["manifest"]
        for entry in manifest["files"]:
            add(source._path({"root": entry["root"], "path": entry["path"]}))
        add(descriptor["manifestPath"])
        task_id = manifest.get("sourceTaskId")
        if not isinstance(task_id, str) or not DATA_TASK_ID.fullmatch(task_id):
            raise ValueError("来源版本没有明确的成功登记任务")
        folder = source.tasks_root / task_id
        status = read_json(folder / "status.json")
        if status.get("taskId") != task_id or status.get("status") not in {"completed", "archived"}:
            raise ValueError("不能接入未成功的来源任务")
        for name in ("request.json", "result.json", "status.json"):
            add(folder / name)
        if manifest.get("sourceId"):
            source_descriptor = source.source(manifest["sourceId"])
            add(source_descriptor["path"])
            add(source_descriptor["manifestPath"])
            for entry in source_descriptor["manifest"].get("immutableSourceFiles", []):
                add(source._path({"root": entry["root"], "path": entry["path"]}))
        registration_path = source.registry_root / (descriptor["bundleVersionId"] + ".json")
        add(registration_path)
        registry_paths.add(source.reference(registration_path)["path"])
    plan = {"bundleVersionId": bundle_id, "sourceRuntime": str(source.runtime_root), "targetRuntime": str(target.runtime_root),
            "manifestSHA256": descriptors[-1]["manifestSHA256"], "fileCount": len(records),
            "newFileCount": sum(not row["existingIdentical"] for row in records.values()),
            "totalBytes": sum(row["bytes"] for row in records.values()),
            "defaultUnchanged": True, "files": [{key: row[key] for key in ("path", "sha256", "bytes", "existingIdentical")} for row in records.values()]}
    if dry_run:
        return {"status": "VALIDATED_DRY_RUN", **plan}
    # All collisions were checked before any copy. Registry entries are last,
    # so interruption leaves unregistered residue instead of a partial product.
    for relative, row in sorted(records.items(), key=lambda item: (item[0] in registry_paths, item[0])):
        if not row["existingIdentical"]:
            row["destination"].parent.mkdir(parents=True, exist_ok=True)
            with row["source"].open("rb") as incoming, row["destination"].open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        if file_sha256(row["source"]) != row["sha256"] or file_sha256(row["destination"]) != row["sha256"]:
            raise ValueError("复制期间来源或目标依赖摘要改变")
    for descriptor in descriptors:
        accepted = target.resolve(descriptor["bundleVersionId"])
        if accepted["manifestSHA256"] != descriptor["manifestSHA256"]:
            raise ValueError("接入后版本摘要不一致")
        target.register_task(descriptor["manifest"]["sourceTaskId"])
    return {"status": "PROMOTED_AND_REVERIFIED", **plan, "registered": target.resolve(bundle_id)["public"]["registered"],
            "defaultBundleVersionId": target.resolve()["bundleVersionId"]}


__all__ = ["promote_input_bundle"]
