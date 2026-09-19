"""Value-scaled exploration policy layer for risk-aware Thompson Sampling routing."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field


class ValueScaledExplorationConfig(BaseModel):
    """Configuration for Phase 8 value-scaled exploration policy."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    enabled: bool = Field(
        default=False,
        description="Whether value-scaled exploration is active.",
    )
    tau: float = Field(
        default=100.0,
        gt=0.0,
        description=(
            "Reference transaction value threshold (tau) in currency units. "
            "Defines the scale where exploration width is compressed by 63.2%."
        ),
    )

    @property
    def sensitivity(self) -> float:
        """Return the value sensitivity kappa = 1.0 / tau."""
        return 1.0 / self.tau

    def calculate_shrinkage(self, amount: float) -> float:
        """Calculate the shrinkage factor lambda(V) in [0, 1)."""
        if not self.enabled or amount <= 0.0:
            return 0.0
        return 1.0 - math.exp(-amount / self.tau)


def apply_value_scaled_policy(
    samples: dict[str, float],
    posterior_means: dict[str, float],
    amount: float,
    config: ValueScaledExplorationConfig | None,
) -> tuple[dict[str, float], float]:
    """Adjust raw Thompson samples toward posterior means based on transaction value."""
    if config is None or not config.enabled or amount <= 0.0:
        return dict(samples), 0.0

    lam = config.calculate_shrinkage(amount)
    adjusted: dict[str, float] = {}
    for aid, sample in samples.items():
        mu = posterior_means.get(aid, sample)
        adjusted[aid] = (1.0 - lam) * sample + lam * mu

    return adjusted, lam
