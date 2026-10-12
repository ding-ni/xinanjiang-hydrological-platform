"""CAL-CONTRACT: configuration and result contracts for the independent layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CALIBRATION_MODES = ("ROUTING_ONLY", "RUNOFF_ONLY", "JOINT")


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    minimum: float
    maximum: float
    levels: int = 5
    values: list[float] | None = None
    calibratable: bool = True

    def __post_init__(self) -> None:
        if self.maximum < self.minimum:
            raise ValueError(f"invalid bounds for {self.name}")
        if self.values is None and self.levels < 2:
            raise ValueError(f"levels must be at least 2 for {self.name}")


@dataclass(frozen=True)
class ObjectiveSpec:
    metric: str
    weight: float = 1.0
    scale: float | None = None
    target: float = 0.0

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError(f"objective weight must be non-negative: {self.metric}")


@dataclass(frozen=True)
class CalibrationTask:
    task_id: str
    mode: str
    algorithm: str
    parameter_specs: list[ParameterSpec]
    objective_specs: list[ObjectiveSpec]
    algorithm_config: dict[str, Any] = field(default_factory=dict)
    base_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mode = self.mode.upper()
        if mode not in CALIBRATION_MODES:
            raise ValueError(f"unsupported calibration mode: {self.mode}")
        if not self.parameter_specs:
            raise ValueError("at least one parameter spec is required")
        if not self.objective_specs:
            raise ValueError("at least one objective spec is required")


@dataclass
class CalibrationTaskResult:
    task_id: str
    mode: str
    status: str
    best_parameters: dict[str, float]
    best_loss: float
    best_metrics: dict[str, Any]
    evaluations: int
    history: list[dict[str, Any]]
    output_files: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "status": self.status,
            "best_parameters": self.best_parameters,
            "best_loss": self.best_loss,
            "best_metrics": self.best_metrics,
            "evaluations": self.evaluations,
            "history": self.history,
            "output_files": self.output_files,
            "warnings": self.warnings,
        }
