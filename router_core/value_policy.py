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
    protect_lambda: float = Field(
        default=0.95,
        gt=0.0,
        le=1.0,
        description=(
            "Payments whose shrinkage lambda(V) is at least this value skip the PID and "
            "the deficit/categorical draw and go to the acquirer with the highest posterior "
            "mean (AUDIT F-10). The default 0.95 protects amounts >= tau * ln(20), about "
            "3 tau. 1.0 turns protection off."
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

    def protects(self, amount: float) -> bool:
        """Whether a payment of ``amount`` bypasses the allocation draw (AUDIT F-10)."""
        if not self.enabled or amount <= 0.0 or self.protect_lambda >= 1.0:
            # lambda(V) rounds to exactly 1.0 for V > ~37 tau, so 1.0 must be checked here
            return False
        return self.calculate_shrinkage(amount) >= self.protect_lambda


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


def best_posterior_mean(posterior_means: dict[str, float]) -> str:
    """Acquirer with the highest posterior mean; ties go to the larger id, as in sampling."""
    return max(posterior_means, key=lambda aid: (posterior_means[aid], aid))
