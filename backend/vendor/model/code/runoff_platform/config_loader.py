"""[CONFIG-01] Load parameter values from external JSON files."""
from __future__ import annotations

import json
from pathlib import Path

from .core.parameters import apply_parameter_overrides
from .core.types import GlacierParams, LandParams


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_parameter_set(path, zone_type="NON_GLACIER"):
    payload = load_json(path)
    values = payload.get("parameters", payload)
    params = GlacierParams() if zone_type.upper() == "GLACIER" else LandParams()
    return apply_parameter_overrides(params, values)
