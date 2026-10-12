"""[CAL-BASE-01] Contracts shared by every calibration algorithm."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    minimum: float
    maximum: float
    calibratable: bool = True
    levels: int = 5
    values: Optional[List[float]] = None
    time_step_dependent: bool = False

    def __post_init__(self):
        if self.maximum < self.minimum:
            raise ValueError(f"invalid bounds for {self.name}")
        if self.levels < 2 and self.values is None:
            raise ValueError(f"levels must be at least 2 for {self.name}")


@dataclass
class ParameterSpace:
    parameters: List[ParameterSpec]

    @classmethod
    def from_config(cls, config):
        return cls([ParameterSpec(**item) for item in config["parameters"]])

    @property
    def active(self):
        return [item for item in self.parameters if item.calibratable]


@dataclass
class CalibrationConfig:
    algorithm: str = "RANDOM_SEARCH"
    max_evaluations: int = 100
    seed: int = 42
    population_size: int = 20
    generations: int = 20
    differential_weight: float = 0.8
    crossover_probability: float = 0.7

    @classmethod
    def from_dict(cls, config):
        return cls(**config)


@dataclass
class CalibrationResult:
    algorithm: str
    best_parameters: Dict[str, float]
    best_loss: float
    evaluations: int
    history: List[Dict] = field(default_factory=list)


ObjectiveCallable = Callable[[Dict[str, float]], float]


class CalibrationAlgorithm:
    name = "BASE"

    def optimize(self, space: ParameterSpace, objective: ObjectiveCallable, config: CalibrationConfig):
        raise NotImplementedError


def evaluate_candidate(candidate, objective, history):
    error = None
    try:
        loss = float(objective(dict(candidate)))
        if loss != loss:
            loss = float("inf")
            error = "objective returned NaN"
    except (ValueError, ArithmeticError, FloatingPointError) as exc:
        loss = float("inf")
        error = str(exc)
    record = {"evaluation": len(history) + 1, "loss": loss, "parameters": dict(candidate)}
    if error is not None:
        record["error"] = error
    history.append(record)
    return loss
