"""Config-driven event qualification shared by calibration and evaluation."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "qualification_config_event.json"
)


class QualificationConfigError(ValueError):
    """Raised when the platform event-qualification contract is invalid."""


@lru_cache(maxsize=16)
def load_qualification_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    resolved = Path(path).resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    if str(payload.get("method", "")).upper() != "NSE_THRESHOLD":
        raise QualificationConfigError("only method=NSE_THRESHOLD is currently supported")
    if str(payload.get("metric", "")).upper() != "NSE":
        raise QualificationConfigError("NSE_THRESHOLD requires metric=NSE")
    try:
        minimum = float(payload["minimum"])
    except (KeyError, TypeError, ValueError) as exc:
        raise QualificationConfigError("qualification minimum must be numeric") from exc
    if not math.isfinite(minimum):
        raise QualificationConfigError("qualification minimum must be finite")
    if bool(payload.get("strict_combined_rate_enabled", False)):
        raise QualificationConfigError("strict combined qualification is disabled for this platform")
    return {**payload, "minimum": minimum, "config_path": str(resolved)}


def qualify_event(
    event_metrics: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> bool:
    """Return the single platform qualification flag; no strict flag is produced."""
    policy = config if config is not None else load_qualification_config()
    value = event_metrics.get("NSE")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= float(policy["minimum"])


def qualification_definition(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = config if config is not None else load_qualification_config()
    return {
        "module_id": policy.get("module_id", "EVENT-QUALIFICATION-14"),
        "method": "NSE_THRESHOLD",
        "metric": "NSE",
        "minimum": float(policy["minimum"]),
        "definition_zh": policy.get("definition_zh"),
        "strict_combined_rate_enabled": False,
        "note": policy.get("note"),
        "config_path": policy.get("config_path"),
    }


__all__ = [
    "QualificationConfigError",
    "load_qualification_config",
    "qualification_definition",
    "qualify_event",
]
