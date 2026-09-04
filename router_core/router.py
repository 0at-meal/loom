"""Bandit-only payment router executing Thompson Sampling route selection."""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

import httpx
import numpy as np

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import PIDConfig, PIDDiagnostics, PIDState, calculate_pid_step
from router_core.state import AcquirerStateSnapshot

logger = logging.getLogger("loom.router")


class BanditRouter:
    """Coordinates Thompson Sampling route selection and closed-loop state updates."""

    def __init__(
        self,
        config: RouterConfig,
        http_client: httpx.AsyncClient | None = None,
        rng: np.random.Generator | None = None,
    ) -> None:
        """Initialize router registry, HTTP client configuration, and PRNG."""
        self._config = config
        self._routes: dict[str, AcquirerRouteConfig] = {r.acquirer_id: r for r in config.routes}
        self._registry = BanditStateRegistry()
        for r in config.routes:
            self._registry.register_acquirer(
                acquirer_id=r.acquirer_id,
                config=r.state_config,
            )

        self._rng = rng if rng is not None else np.random.default_rng(config.seed)
        self._client = http_client
        self._owns_client = http_client is None

        # Phase 4 PID state initialization
        self._pid_config: PIDConfig | None = config.pid_config
        self._pid_state: PIDState | None = None
        self._current_allocation: dict[str, float] = {}
        self._cumulative_target: dict[str, float] = {}
        self._dispatched_count: dict[str, int] = {}
        self._last_diagnostics: PIDDiagnostics | None = None

        if self._pid_config is not None:
            acquirer_ids = [r.acquirer_id for r in config.routes]
            self._pid_state = PIDState.initialize(acquirer_ids)
            self._current_allocation = dict(self._pid_state.previous_allocation)
            self._cumulative_target = {aid: 0.0 for aid in acquirer_ids}
            self._dispatched_count = {aid: 0 for aid in acquirer_ids}

    @property
    def config(self) -> RouterConfig:
        """Return the configuration parameters for this router."""
        return self._config

    @property
    def current_allocation(self) -> dict[str, float]:
        """Return current actual smoothed allocation vector across acquirers."""
        return dict(self._current_allocation)

    @property
    def pid_state(self) -> PIDState | None:
        """Return internal snapshot of PID state if PID is configured."""
        return self._pid_state

    @property
    def last_diagnostics(self) -> PIDDiagnostics | None:
        """Return diagnostics from the most recent PID step."""
        return self._last_diagnostics

    async def start(self) -> None:
        """Initialize pooled HTTP client if owned."""
        if self._client is None:
            limits = httpx.Limits(
                max_connections=self._config.max_connections,
                max_keepalive_connections=self._config.max_keepalive_connections,
            )
            self._client = httpx.AsyncClient(limits=limits)
            logger.debug(
                "Initialized pooled AsyncClient (max=%d, keepalive=%d)",
                self._config.max_connections,
                self._config.max_keepalive_connections,
            )

    async def close(self) -> None:
        """Close pooled HTTP client if owned."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("Closed pooled AsyncClient")

    async def __aenter__(self) -> BanditRouter:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    def select_route(self) -> tuple[str, dict[str, float]]:
        """Sample Beta beliefs across all candidate routes and select argmax arm."""
        samples = self._registry.sample_all(rng=self._rng)
        # Deterministic tie-breaking: max by sample value, then by acquirer_id
        selected_id = max(samples.keys(), key=lambda aid: (samples[aid], aid))
        return selected_id, samples

    async def route(self, request: AuthorizeRequest) -> RoutingResult:
        """Execute end-to-end routing decision, acquirer dispatch, and state update."""
        t_start = time.perf_counter()

        # 1. Perception, PID Smoothing & Selection
        t_sample_start = time.perf_counter()
        samples = self._registry.sample_all(rng=self._rng)
        target_allocation: dict[str, float] | None = None
        smoothed_allocation: dict[str, float] | None = None
        diagnostics: PIDDiagnostics | None = None

        if self._pid_config is not None and self._pid_state is not None:
            # Thompson sampling target allocation
            win_id = max(samples.keys(), key=lambda aid: (samples[aid], aid))
            target_allocation = {aid: 1.0 if aid == win_id else 0.0 for aid in samples}

            # PID smoothing step
            step_result = calculate_pid_step(
                target_allocation=target_allocation,
                current_allocation=self._current_allocation,
                state=self._pid_state,
                config=self._pid_config,
                dt=1.0,
            )
            self._current_allocation = step_result.smoothed_allocation
            self._pid_state = step_result.next_state
            self._last_diagnostics = step_result.diagnostics
            smoothed_allocation = dict(self._current_allocation)
            diagnostics = step_result.diagnostics

            # Discrete Actuation (Stochastic or Deficit Round-Robin)
            if self._pid_config.actuation_mode == "deficit":
                for aid in self._routes:
                    self._cumulative_target[aid] += self._current_allocation[aid]
                selected_id = max(
                    sorted(self._routes.keys()),
                    key=lambda aid: (
                        self._cumulative_target[aid] - self._dispatched_count[aid],
                        aid,
                    ),
                )
                self._dispatched_count[selected_id] += 1
            else:
                keys = sorted(self._current_allocation.keys())
                probs = [self._current_allocation[k] for k in keys]
                selected_id = str(self._rng.choice(keys, p=probs))
        else:
            # Winner-take-all argmax hard-switch (Phase 3 baseline)
            selected_id = max(samples.keys(), key=lambda aid: (samples[aid], aid))
            smoothed_allocation = {aid: 1.0 if aid == selected_id else 0.0 for aid in samples}
            target_allocation = dict(smoothed_allocation)

        t_sample_end = time.perf_counter()
        routing_latency_ms = (t_sample_end - t_sample_start) * 1000.0

        sample_str = ", ".join(f"{k}={v:.4f}" for k, v in sorted(samples.items()))
        logger.info(
            "Routing decision: tx_id=%s -> selected=%s (samples: [%s])",
            request.transaction_id,
            selected_id,
            sample_str,
        )

        route_info = self._routes[selected_id]
        url = route_info.get_authorize_url()

        # 2. HTTP Dispatch to Acquirer
        if self._client is None:
            await self.start()
        assert self._client is not None

        t_dispatch_start = time.perf_counter()
        status: Literal["AUTHORIZED", "DECLINED", "ERROR"]
        authorized: bool
        success: bool
        response_payload: AuthorizeResponse | None = None
        error_msg: str | None = None

        try:
            resp = await self._client.post(
                url,
                json=request.model_dump(),
                timeout=route_info.timeout_sec,
            )

            if resp.status_code == 200:
                payload = AuthorizeResponse.model_validate(resp.json())
                response_payload = payload
                authorized = payload.authorized
                success = payload.authorized  # True if AUTHORIZED, False if DECLINED
                status = "AUTHORIZED" if success else "DECLINED"
            elif resp.status_code == 503:
                status = "ERROR"
                authorized = False
                success = False
                error_msg = f"Acquirer HTTP 503 Outage: {resp.text}"
            elif resp.status_code == 422:
                # Schema bug from client; do not penalize acquirer
                logger.error(
                    "Acquirer rejected schema (HTTP 422): tx_id=%s payload=%s resp=%s",
                    request.transaction_id,
                    request.model_dump(),
                    resp.text,
                )
                raise ValueError(f"Acquirer rejected schema (HTTP 422): {resp.text}")
            else:
                status = "ERROR"
                authorized = False
                success = False
                error_msg = f"Acquirer HTTP {resp.status_code}: {resp.text}"

        except (httpx.TimeoutException, httpx.NetworkError) as err:
            status = "ERROR"
            authorized = False
            success = False
            error_msg = f"Transport error to {selected_id}: {type(err).__name__} ({err})"

        t_dispatch_end = time.perf_counter()
        acquirer_latency_ms = (t_dispatch_end - t_dispatch_start) * 1000.0

        # 3. Closed-Loop State Feedback Update (Phase 1 mean-reverting offset decay)
        updated_snapshot = self._registry.record_outcome(
            acquirer_id=selected_id,
            success=success,
            timestamp=time.time(),
        )

        t_end = time.perf_counter()
        total_latency_ms = (t_end - t_start) * 1000.0

        logger.info(
            "Routing outcome: tx_id=%s acquirer=%s status=%s authorized=%s "
            "acquirer_lat=%.2fms total_lat=%.2fms -> "
            "updated state: alpha=%.3f beta=%.3f health=%.3f mean=%.3f",
            request.transaction_id,
            selected_id,
            status,
            authorized,
            acquirer_latency_ms,
            total_latency_ms,
            updated_snapshot.alpha,
            updated_snapshot.beta,
            updated_snapshot.health_score,
            updated_snapshot.expected_success_rate,
        )

        return RoutingResult(
            transaction_id=request.transaction_id,
            selected_acquirer=selected_id,
            thompson_samples=samples,
            status=status,
            authorized=authorized,
            success=success,
            response_payload=response_payload,
            error_message=error_msg,
            routing_latency_ms=routing_latency_ms,
            acquirer_latency_ms=acquirer_latency_ms,
            total_latency_ms=total_latency_ms,
            state_snapshot=updated_snapshot,
            smoothed_allocation=smoothed_allocation,
            target_allocation=target_allocation,
            pid_diagnostics=diagnostics,
            timestamp=time.time(),
        )

    def get_state(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Return point-in-time state snapshot for a single acquirer."""
        return self._registry.get_state(acquirer_id)

    def get_all_states(self) -> dict[str, AcquirerStateSnapshot]:
        """Return state snapshots across all registered acquirers."""
        return self._registry.get_all_states()

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all registered acquirer identifiers."""
        return self._registry.list_acquirer_ids()
