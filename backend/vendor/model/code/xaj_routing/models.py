"""Data contracts for the XAJ routing prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


RUNOFF_FIELDS = (
    "RS",
    "RI",
    "RG",
    "glacier_fast",
    "glacier_slow",
    "glacier_base",
)

COMPONENT_TYPES = (
    "RS",
    "RI",
    "RG",
    "GLACIER_FAST",
    "GLACIER_SLOW",
    "GLACIER_BASE",
)

FIELD_TO_COMPONENT = {
    "RS": "RS",
    "RI": "RI",
    "RG": "RG",
    "glacier_fast": "GLACIER_FAST",
    "glacier_slow": "GLACIER_SLOW",
    "glacier_base": "GLACIER_BASE",
}

DEFAULT_SLOPE_K_HOURS = {
    "RS": 2.0,
    "RI": 8.0,
    "RG": 24.0,
    "GLACIER_FAST": 2.0,
    "GLACIER_SLOW": 12.0,
    "GLACIER_BASE": 48.0,
}


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid ISO time value: {value!r}") from exc


def _float_value(data: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = data.get(key, default)
    if value is None or value == "":
        return default
    return float(value)


@dataclass(frozen=True)
class RunoffRecord:
    unit_id: str
    time: datetime
    RS: float = 0.0
    RI: float = 0.0
    RG: float = 0.0
    glacier_fast: float = 0.0
    glacier_slow: float = 0.0
    glacier_base: float = 0.0

    def __post_init__(self) -> None:
        if not self.unit_id:
            raise ValueError("unit_id is required")
        for field_name in RUNOFF_FIELDS:
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RunoffRecord":
        return cls(
            unit_id=str(data["unit_id"]).strip(),
            time=parse_time(data["time"]),
            RS=_float_value(data, "RS"),
            RI=_float_value(data, "RI"),
            RG=_float_value(data, "RG"),
            glacier_fast=_float_value(data, "glacier_fast"),
            glacier_slow=_float_value(data, "glacier_slow"),
            glacier_base=_float_value(data, "glacier_base"),
        )

    def component_depths(self) -> list[tuple[str, str, float]]:
        return [
            (field_name, FIELD_TO_COMPONENT[field_name], getattr(self, field_name))
            for field_name in RUNOFF_FIELDS
        ]


@dataclass(frozen=True)
class UnitInfo:
    unit_id: str
    area_km2: float
    zone_type: str = "non_glacier"
    reach_id: str | None = None
    downstream_unit_id: str | None = None

    def __post_init__(self) -> None:
        if not self.unit_id:
            raise ValueError("unit_id is required")
        if self.area_km2 < 0:
            raise ValueError("area_km2 must be non-negative")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "UnitInfo":
        reach_id = data.get("reach_id")
        downstream_unit_id = data.get("downstream_unit_id")
        return cls(
            unit_id=str(data["unit_id"]).strip(),
            area_km2=float(data["area_km2"]),
            zone_type=str(data.get("zone_type", "non_glacier")).strip(),
            reach_id=None if reach_id in (None, "") else str(reach_id).strip(),
            downstream_unit_id=None
            if downstream_unit_id in (None, "")
            else str(downstream_unit_id).strip(),
        )


@dataclass(frozen=True)
class ReachInfo:
    reach_id: str
    downstream_reach_id: str | None = None
    length_km: float = 0.0

    def __post_init__(self) -> None:
        if not self.reach_id:
            raise ValueError("reach_id is required")
        if self.length_km < 0:
            raise ValueError("length_km must be non-negative")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ReachInfo":
        downstream = data.get("downstream_reach_id")
        return cls(
            reach_id=str(data["reach_id"]).strip(),
            downstream_reach_id=None if downstream in (None, "") else str(downstream).strip(),
            length_km=float(data.get("length_km", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class MuskingumParameters:
    k_hours: float
    x: float

    def __post_init__(self) -> None:
        if self.k_hours <= 0:
            raise ValueError("Muskingum k_hours must be positive")
        if not 0.0 <= self.x <= 0.5:
            raise ValueError("Muskingum x must be between 0 and 0.5")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "MuskingumParameters":
        return cls(k_hours=float(data["k_hours"]), x=float(data.get("x", 0.2)))


@dataclass(frozen=True)
class RoutingParameters:
    time_step_hours: float
    default_slope_k_hours: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SLOPE_K_HOURS)
    )
    unit_component_slope_k_hours: dict[str, dict[str, float]] = field(default_factory=dict)
    reach_muskingum: dict[str, MuskingumParameters] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.time_step_hours <= 0:
            raise ValueError("time_step_hours must be positive")
        for component, k_hours in self.default_slope_k_hours.items():
            if k_hours <= 0:
                raise ValueError(f"default slope k for {component} must be positive")
        for unit_id, values in self.unit_component_slope_k_hours.items():
            for component, k_hours in values.items():
                if k_hours <= 0:
                    raise ValueError(
                        f"slope k for unit {unit_id} component {component} must be positive"
                    )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RoutingParameters":
        default_slope = dict(DEFAULT_SLOPE_K_HOURS)
        default_slope.update(
            {
                str(key).upper(): float(value)
                for key, value in data.get("default_slope_k_hours", {}).items()
            }
        )

        unit_component = {}
        for unit_id, values in data.get("unit_component_slope_k_hours", {}).items():
            unit_component[str(unit_id)] = {
                str(component).upper(): float(k_hours)
                for component, k_hours in values.items()
            }

        reach_muskingum = {
            str(reach_id): MuskingumParameters.from_mapping(value)
            for reach_id, value in data.get("reach_muskingum", {}).items()
        }

        return cls(
            time_step_hours=float(data["time_step_hours"]),
            default_slope_k_hours=default_slope,
            unit_component_slope_k_hours=unit_component,
            reach_muskingum=reach_muskingum,
        )

    @property
    def time_step_seconds(self) -> float:
        return self.time_step_hours * 3600.0

    def slope_k_seconds(self, unit_id: str, component_type: str) -> float:
        component = component_type.upper()
        unit_values = self.unit_component_slope_k_hours.get(unit_id, {})
        k_hours = unit_values.get(component, self.default_slope_k_hours[component])
        return k_hours * 3600.0


@dataclass
class RoutingState:
    slope_outflow_cms: dict[str, dict[str, float]] = field(default_factory=dict)
    reach_inflow_cms: dict[str, float] = field(default_factory=dict)
    reach_outflow_cms: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RoutingState":
        if not data:
            return cls()
        return cls(
            slope_outflow_cms={
                str(unit_id): {
                    str(component).upper(): float(value)
                    for component, value in components.items()
                }
                for unit_id, components in data.get("slope_outflow_cms", {}).items()
            },
            reach_inflow_cms={
                str(reach_id): float(value)
                for reach_id, value in data.get("reach_inflow_cms", {}).items()
            },
            reach_outflow_cms={
                str(reach_id): float(value)
                for reach_id, value in data.get("reach_outflow_cms", {}).items()
            },
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "slope_outflow_cms": self.slope_outflow_cms,
            "reach_inflow_cms": self.reach_inflow_cms,
            "reach_outflow_cms": self.reach_outflow_cms,
        }


@dataclass(frozen=True)
class ComponentFlow:
    time: datetime
    unit_id: str
    reach_id: str | None
    component_type: str
    runoff_mm: float
    inflow_cms: float
    slope_outflow_cms: float


@dataclass(frozen=True)
class ReachFlow:
    time: datetime
    reach_id: str
    local_inflow_cms: float
    total_inflow_cms: float
    outflow_cms: float


@dataclass(frozen=True)
class OutletFlow:
    time: datetime
    outlet_id: str
    outflow_cms: float


@dataclass(frozen=True)
class ControlPoint:
    control_id: str
    reach_id: str
    name: str = ""

    def __post_init__(self) -> None:
        if not self.control_id:
            raise ValueError("control_id is required")
        if not self.reach_id:
            raise ValueError("control point reach_id is required")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ControlPoint":
        snapped = data.get("snapped") or {}
        control_id = data.get("control_id") or data.get("id") or data.get("code")
        reach_id = data.get("reach_id") or snapped.get("reach_id")
        name = data.get("name") or control_id
        return cls(
            control_id=str(control_id or "").strip(),
            reach_id=str(reach_id or "").strip(),
            name=str(name or "").strip(),
        )


@dataclass(frozen=True)
class ControlFlow:
    time: datetime
    control_id: str
    control_name: str
    reach_id: str
    outflow_cms: float


@dataclass(frozen=True)
class RoutingResult:
    component_flows: list[ComponentFlow]
    reach_flows: list[ReachFlow]
    outlet_flows: list[OutletFlow]
    final_state: RoutingState
    control_flows: list[ControlFlow] = field(default_factory=list)
