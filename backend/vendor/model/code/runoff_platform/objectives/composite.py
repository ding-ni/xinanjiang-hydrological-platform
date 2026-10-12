"""Weighted objective assembled entirely from configuration."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .registry import get_metric


@dataclass(frozen=True)
class ObjectiveTerm:
    metric: str
    weight: float = 1.0
    scale: float | None = None
    target: float = 0.0


class CompositeObjective:
    """[OBJ-COMP-01] Convert heterogeneous metrics to one minimization loss."""

    def __init__(self, terms):
        self.terms = [term if isinstance(term, ObjectiveTerm) else ObjectiveTerm(**term) for term in terms]
        if not self.terms:
            raise ValueError("at least one objective term is required")
        if any(term.weight < 0 for term in self.terms) or sum(term.weight for term in self.terms) <= 0:
            raise ValueError("objective weights must be non-negative and not all zero")

    @classmethod
    def from_config(cls, config):
        method = str(config.get("method", "WEIGHTED_SUM")).upper()
        if method != "WEIGHTED_SUM":
            raise ValueError("currently supported composite method: WEIGHTED_SUM")
        return cls(config["terms"])

    def evaluate(self, observed, simulated, diagnostics=None):
        details = {}
        weighted = 0.0
        weight_sum = sum(term.weight for term in self.terms)
        for term in self.terms:
            definition = get_metric(term.metric)
            raw = float(definition.function(observed, simulated, diagnostics=diagnostics))
            if not math.isfinite(raw):
                utility = -1.0e6
            elif definition.direction == "MAXIMIZE":
                utility = raw
            else:
                scale = term.scale if term.scale is not None else definition.default_scale
                if scale <= 0:
                    raise ValueError(f"scale must be positive for {term.metric}")
                utility = -abs(raw - term.target) / scale
            weighted += term.weight * utility
            details[term.metric.upper()] = {
                "raw": raw,
                "utility": utility,
                "weight": term.weight,
            }
        score = weighted / weight_sum
        return {"score": score, "loss": -score, "terms": details}
