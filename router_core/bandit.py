"""Multi-acquirer registry and Thompson Sampling coordinator."""

from __future__ import annotations

import numpy as np

from router_core.state import AcquirerState, AcquirerStateConfig, AcquirerStateSnapshot


class BanditStateRegistry:
    """Manages state across all configured acquirers and coordinates Thompson Sampling."""

    def __init__(self, default_config: AcquirerStateConfig | None = None) -> None:
        """Initialize registry with optional default acquirer configuration."""
        self._default_config: AcquirerStateConfig = default_config or AcquirerStateConfig()
        self._acquirers: dict[str, AcquirerState] = {}

    def register_acquirer(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> AcquirerState:
        """Register a new acquirer route with its own state and prior beliefs."""
        if not isinstance(acquirer_id, str) or not acquirer_id.strip():
            raise ValueError("acquirer_id must be a non-empty string")

        clean_id = acquirer_id.strip()
        if clean_id in self._acquirers:
            raise ValueError(f"Acquirer '{clean_id}' is already registered")

        effective_config = config or self._default_config
        state = AcquirerState(
            acquirer_id=clean_id,
            config=effective_config,
            initial_timestamp=initial_timestamp,
        )
        self._acquirers[clean_id] = state
        return state

    def record_outcome(
        self,
        acquirer_id: str,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Record outcome for a specific acquirer and return updated snapshot."""
        state = self._acquirers.get(acquirer_id)
        if state is None:
            raise KeyError(f"Acquirer '{acquirer_id}' not found in registry")
        return state.record_outcome(success=success, timestamp=timestamp)

    def sample_all(
        self, rng: np.random.Generator | None = None, now: float | None = None
    ) -> dict[str, float]:
        """Draw independent Thompson samples, decaying beliefs to ``now`` when given."""
        generator = rng if rng is not None else np.random.default_rng()
        return {
            acquirer_id: state.sample(rng=generator, now=now)
            for acquirer_id, state in self._acquirers.items()
        }

    def get_state(self, acquirer_id: str, now: float | None = None) -> AcquirerStateSnapshot:
        """Return state snapshot for a single acquirer."""
        state = self._acquirers.get(acquirer_id)
        if state is None:
            raise KeyError(f"Acquirer '{acquirer_id}' not found in registry")
        return state.get_state(now=now)

    def get_all_states(self, now: float | None = None) -> dict[str, AcquirerStateSnapshot]:
        """Return state snapshots for all registered acquirers."""
        return {
            acquirer_id: state.get_state(now=now) for acquirer_id, state in self._acquirers.items()
        }

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all currently registered acquirer identifiers."""
        return list(self._acquirers.keys())
