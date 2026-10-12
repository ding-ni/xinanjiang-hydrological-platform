"""Portable paths and strict JSON for the unified platform (no model equations)."""
from __future__ import annotations

import json
import math
import os
import re
import uuid
import time
from platform_config import SETTINGS
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
MODEL = SETTINGS['model']
RUNTIME = SETTINGS['workspace']
DEFAULT_INPUT_BUNDLE = "NANANXI_QC_20260819_CONTROL_V2"
ROUTING_VERSION = "control-split-v2"
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,100}$")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path):
    for attempt in range(8):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.01 * (attempt + 1))


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "tolist"):
        return clean(value.tolist())
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    # Windows readers/antivirus can briefly deny ReplaceFile on an open target.
    for attempt in range(8):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.01 * (attempt + 1))


def safe_task(root, task_id):
    if not TASK_ID.fullmatch(str(task_id)):
        raise ValueError("无效任务编号")
    return Path(root) / task_id
