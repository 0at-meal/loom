"""Bandit-only payment router executing Thompson Sampling route selection."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

import httpx
import numpy as np

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import PIDConfig, PIDDiagnostics, PIDState, calculate_pid_step
from router_core.state import AcquirerStateSnapshot, Outcome
from router_core.value_policy import ValueScaledExplorationConfig, apply_value_scaled_policy

if TYPE_CHECKING:
    from data_layer.redis_pubsub import AsyncEventPublisher, EventPublisher
    from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore

logger = logging.getLogger("loom.router")


class UnexpectedAcquirerResponse(Exception):
    """An acquirer answered with something the router cannot interpret."""


class BanditRouter:
    """Coordinates Thompson Sampling route selection and closed-loop state updates."""

    def __init__(
        self,
        config: RouterConfig,
        http_client: httpx.AsyncClient | None = None,
        rng: np.random.Generator | None = None,
        registry: BanditStateRegistry | None = None,
        event_publisher: EventPublisher | AsyncEventPublisher | Any | None = None,
        metrics_logger: MetricsLogger | SQLiteMetricsStore | Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Initialize router registry, HTTP client configuration, PRNG and clock.

        ``clock`` returns the current time in seconds; belief decay is measured with it.
        Benchmarks pass a virtual clock so results do not depend on machine speed.
        """
        self._config = config
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._routes: dict[str, AcquirerRouteConfig] = {r.acquirer_id: r for r in config.routes}
        self._technical_codes = frozenset(config.technical_decline_codes)
        self._registry = registry if registry is not None else BanditStateRegistry()
        self._event_publisher = event_publisher
        self._metrics_logger = metrics_logger
        for r in config.routes:
            if r.acquirer_id not in self._registry.list_acquirer_ids():
                self._registry.register_acquirer(
                    acquirer_id=r.acquirer_id,
                    config=r.state_config,
                    initial_timestamp=self._clock(),
                )

        self._rng = rng if rng is not None else np.random.default_rng(config.seed)
        # A caller-supplied client is shared by every route and never closed here.
        # Otherwise each acquirer gets its own pool (a bulkhead, AUDIT F-16).
        self._client = http_client
        self._owns_client = http_client is None
        self._acquirer_clients: dict[str, httpx.AsyncClient] = {}
        # Payments that failed because the router had no free connection (not booked
        # against any acquirer).
        self.pool_timeouts = 0
        # Admission limit (AUDIT F-16): requests beyond max_in_flight wait for a slot, so
        # their decisions see the outcomes of earlier requests.
        self._admission = (
            asyncio.Semaphore(config.max_in_flight) if config.max_in_flight is not None else None
        )
        self._in_flight = 0
        self._waiting = 0
        self._waited = 0
        self._max_wait_ms = 0.0

        # Phase 4 PID state initialization
        self._pid_config: PIDConfig | None = config.pid_config
        self._pid_state: PIDState | None = None
        self._current_allocation: dict[str, float] = {}
        self._cumulative_target: dict[str, float] = {}
        self._dispatched_count: dict[str, int] = {}
        self._last_diagnostics: PIDDiagnostics | None = None

        # Phase 8 Value-Scaled Exploration policy configuration
        self._value_scaled_config: ValueScaledExplorationConfig | None = config.value_scaled_config

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
    def value_scaled_config(self) -> ValueScaledExplorationConfig | None:
        """Return the value-scaled exploration policy configuration if set."""
        return self._value_scaled_config

    @property
    def registry(self) -> BanditStateRegistry:
        """Return the underlying state registry."""
        return self._registry

    @property
    def event_publisher(self) -> Any | None:
        """Return the optional event publisher hook."""
        return self._event_publisher

    @property
    def metrics_logger(self) -> Any | None:
        """Return the optional metrics logger hook."""
        return self._metrics_logger

    def attach_data_hooks(
        self,
        metrics_logger: MetricsLogger | SQLiteMetricsStore | Any | None = None,
        event_publisher: EventPublisher | AsyncEventPublisher | Any | None = None,
    ) -> None:
        """Set the ledger and event hooks after construction (used by the served app)."""
        if metrics_logger is not None:
            self._metrics_logger = metrics_logger
        if event_publisher is not None:
            self._event_publisher = event_publisher

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
        """Create one pooled HTTP client per acquirer unless a client was supplied."""
        if self._client is None and not self._acquirer_clients:
            limits = httpx.Limits(
                max_connections=self._config.max_connections,
                max_keepalive_connections=self._config.max_keepalive_connections,
            )
            for acquirer_id in self._routes:
                self._acquirer_clients[acquirer_id] = httpx.AsyncClient(limits=limits)
            logger.debug(
                "Initialized %d per-acquirer AsyncClients (max=%d, keepalive=%d each)",
                len(self._acquirer_clients),
                self._config.max_connections,
                self._config.max_keepalive_connections,
            )

    async def close(self) -> None:
        """Close the per-acquirer HTTP clients this router created."""
        if self._owns_client:
            for client in self._acquirer_clients.values():
                await client.aclose()
            self._acquirer_clients.clear()
            logger.debug("Closed per-acquirer AsyncClients")

    def client_for(self, acquirer_id: str) -> httpx.AsyncClient:
        """Return the HTTP client used to reach ``acquirer_id``."""
        if self._client is not None:
            return self._client
        return self._acquirer_clients[acquirer_id]

    async def __aenter__(self) -> BanditRouter:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    def select_route(self, amount: float = 0.0) -> tuple[str, dict[str, float]]:
        """Sample Beta beliefs across all candidate routes and select argmax arm."""
        raw_samples = self._registry.sample_all(rng=self._rng, now=self._clock())
        if (
            self._value_scaled_config is not None
            and self._value_scaled_config.enabled
            and amount > 0.0
        ):
            states = self._registry.get_all_states()
            means = {aid: s.expected_psr for aid, s in states.items()}
            adjusted_samples, _ = apply_value_scaled_policy(
                samples=raw_samples,
                posterior_means=means,
                amount=amount,
                config=self._value_scaled_config,
            )
            selected_id = max(
                adjusted_samples.keys(),
                key=lambda aid: (adjusted_samples[aid], aid),
            )
            return selected_id, adjusted_samples

        # Deterministic tie-breaking: max by sample value, then by acquirer_id
        selected_id = max(raw_samples.keys(), key=lambda aid: (raw_samples[aid], aid))
        return selected_id, raw_samples

    def in_flight_status(self) -> dict[str, Any]:
        """Admission counters for /health."""
        return {
            "max_in_flight": self._config.max_in_flight,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
            "waited": self._waited,
            "max_wait_ms": self._max_wait_ms,
        }

    async def route(self, request: AuthorizeRequest) -> RoutingResult:
        """Route one payment, waiting for an in-flight slot first if the limit is reached."""
        if self._admission is None:
            return await self._route(request)
        t_wait = time.perf_counter()
        self._waiting += 1
        try:
            await self._admission.acquire()
        finally:
            self._waiting -= 1
        wait_ms = (time.perf_counter() - t_wait) * 1000.0
        if wait_ms > 0.01:
            self._waited += 1
            self._max_wait_ms = max(self._max_wait_ms, wait_ms)
        self._in_flight += 1
        try:
            return await self._route(request, queue_wait_ms=wait_ms)
        finally:
            self._in_flight -= 1
            self._admission.release()

    async def _route(self, request: AuthorizeRequest, queue_wait_ms: float = 0.0) -> RoutingResult:
        """Execute end-to-end routing decision, acquirer dispatch, and state update."""
        t_start = time.perf_counter()

        # 1. Perception, Value-Scaled Policy, PID Smoothing & Selection
        t_sample_start = time.perf_counter()
        raw_samples = self._registry.sample_all(rng=self._rng, now=self._clock())
        effective_samples = raw_samples
        adjusted_samples: dict[str, float] | None = None
        shrinkage: float | None = None

        if (
            self._value_scaled_config is not None
            and self._value_scaled_config.enabled
            and request.amount > 0.0
        ):
            states = self._registry.get_all_states()
            means = {aid: s.expected_psr for aid, s in states.items()}
            adjusted_samples, shrinkage = apply_value_scaled_policy(
                samples=raw_samples,
                posterior_means=means,
                amount=request.amount,
                config=self._value_scaled_config,
            )
            effective_samples = adjusted_samples

        target_allocation: dict[str, float] | None = None
        smoothed_allocation: dict[str, float] | None = None
        diagnostics: PIDDiagnostics | None = None

        if self._pid_config is not None and self._pid_state is not None:
            # Thompson sampling target allocation using effective samples
            win_id = max(effective_samples.keys(), key=lambda aid: (effective_samples[aid], aid))
            target_allocation = {aid: 1.0 if aid == win_id else 0.0 for aid in effective_samples}

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
            # Winner-take-all argmax hard-switch (Phase 3 baseline using effective samples)
            selected_id = max(
                effective_samples.keys(),
                key=lambda aid: (effective_samples[aid], aid),
            )
            smoothed_allocation = {
                aid: 1.0 if aid == selected_id else 0.0 for aid in effective_samples
            }
            target_allocation = dict(smoothed_allocation)

        t_sample_end = time.perf_counter()
        routing_latency_ms = (t_sample_end - t_sample_start) * 1000.0

        sample_str = ", ".join(f"{k}={v:.4f}" for k, v in sorted(raw_samples.items()))
        if adjusted_samples is not None:
            adj_str = ", ".join(f"{k}={v:.4f}" for k, v in sorted(adjusted_samples.items()))
            logger.info(
                "Routing decision: tx_id=%s -> selected=%s "
                "(raw: [%s], adjusted: [%s], lambda=%.4f)",
                request.transaction_id,
                selected_id,
                sample_str,
                adj_str,
                shrinkage or 0.0,
            )
        else:
            logger.info(
                "Routing decision: tx_id=%s -> selected=%s (samples: [%s])",
                request.transaction_id,
                selected_id,
                sample_str,
            )

        route_info = self._routes[selected_id]
        url = route_info.get_authorize_url()

        # 2. HTTP Dispatch to Acquirer
        if self._client is None and not self._acquirer_clients:
            await self.start()
        client = self.client_for(selected_id)

        t_dispatch_start = time.perf_counter()
        status: Literal["AUTHORIZED", "DECLINED", "ERROR"]
        authorized: bool
        success: bool
        outcome: Outcome | None
        response_payload: AuthorizeResponse | None = None
        error_msg: str | None = None

        try:
            resp = await client.post(
                url,
                json=request.model_dump(),
                timeout=route_info.timeout_sec,
            )

            if resp.status_code == 200:
                try:
                    payload = AuthorizeResponse.model_validate(resp.json())
                except ValueError as exc:  # JSONDecodeError and ValidationError
                    raise UnexpectedAcquirerResponse(
                        f"Acquirer HTTP 200 with an unreadable body: {exc}"
                    ) from exc
                response_payload = payload
                authorized = payload.authorized
                success = payload.authorized  # True if AUTHORIZED, False if DECLINED
                status = "AUTHORIZED" if success else "DECLINED"
                if success:
                    outcome = Outcome.AUTHORIZED
                elif payload.decline_code in self._technical_codes:
                    outcome = Outcome.TECHNICAL_FAILURE
                else:
                    # Issuer declines (e.g. DO_NOT_HONOR) say nothing about the acquirer
                    outcome = Outcome.ISSUER_DECLINE
            elif resp.status_code == 503:
                status = "ERROR"
                authorized = False
                success = False
                outcome = Outcome.TECHNICAL_FAILURE
                error_msg = f"Acquirer HTTP 503 Outage: {resp.text}"
            elif resp.status_code == 422:
                # The router validated the request, so a 422 is an integration fault on this
                # route: book it against the acquirer instead of failing the payment call.
                logger.error(
                    "Acquirer rejected schema (HTTP 422): tx_id=%s payload=%s resp=%s",
                    request.transaction_id,
                    request.model_dump(),
                    resp.text,
                )
                status = "ERROR"
                authorized = False
                success = False
                outcome = Outcome.TECHNICAL_FAILURE
                error_msg = f"Acquirer rejected schema (HTTP 422): {resp.text}"
            else:
                status = "ERROR"
                authorized = False
                success = False
                outcome = Outcome.TECHNICAL_FAILURE
                error_msg = f"Acquirer HTTP {resp.status_code}: {resp.text}"

        except httpx.PoolTimeout as err:
            # The router had no free connection: the request never reached the acquirer,
            # so it is not evidence about the acquirer (AUDIT F-16).
            self.pool_timeouts += 1
            status = "ERROR"
            authorized = False
            success = False
            outcome = None
            error_msg = f"Router connection pool exhausted for {selected_id}: PoolTimeout ({err})"
        except (httpx.TransportError, UnexpectedAcquirerResponse) as err:
            # Timeouts, connection and protocol errors, and unreadable 200 bodies
            status = "ERROR"
            authorized = False
            success = False
            outcome = Outcome.TECHNICAL_FAILURE
            error_msg = f"Transport error to {selected_id}: {type(err).__name__} ({err})"

        t_dispatch_end = time.perf_counter()
        acquirer_latency_ms = (t_dispatch_end - t_dispatch_start) * 1000.0

        # 3. State feedback. The acquirer has already answered, so a failure here must not
        # turn an authorized payment into an error for the caller (AUDIT F-07).
        try:
            if outcome is None:
                updated_snapshot = self._registry.get_state(selected_id)
            else:
                updated_snapshot = self._registry.record_outcome(
                    acquirer_id=selected_id,
                    success=success,
                    timestamp=self._clock(),
                    outcome=outcome,
                )
        except Exception as exc:  # noqa: BLE001 - best-effort after dispatch
            logger.error(
                "State update failed after dispatch: tx_id=%s acquirer=%s outcome=%s: %s",
                request.transaction_id,
                selected_id,
                outcome,
                exc,
            )
            note = f"state update failed: {type(exc).__name__} ({exc})"
            error_msg = f"{error_msg}; {note}" if error_msg else note
            updated_snapshot = self._fallback_snapshot(selected_id)

        t_end = time.perf_counter()
        total_latency_ms = (t_end - t_start) * 1000.0 + queue_wait_ms

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

        routing_result = RoutingResult(
            transaction_id=request.transaction_id,
            selected_acquirer=selected_id,
            thompson_samples=raw_samples,
            status=status,
            authorized=authorized,
            success=success,
            outcome=outcome,
            response_payload=response_payload,
            error_message=error_msg,
            routing_latency_ms=routing_latency_ms,
            acquirer_latency_ms=acquirer_latency_ms,
            total_latency_ms=total_latency_ms,
            queue_wait_ms=queue_wait_ms,
            state_snapshot=updated_snapshot,
            smoothed_allocation=smoothed_allocation,
            target_allocation=target_allocation,
            pid_diagnostics=diagnostics,
            adjusted_samples=adjusted_samples,
            exploration_shrinkage=shrinkage,
            timestamp=time.time(),
        )

        if self._event_publisher is not None:
            try:
                pub_res = self._event_publisher.publish_routing_event(routing_result)
                if inspect.isawaitable(pub_res):
                    await pub_res
            except Exception as exc:  # noqa: BLE001 - Telemetry errors must never crash payment path
                logger.warning("Failed to publish routing telemetry event: %s", exc)

        if self._metrics_logger is not None:
            try:
                log_res = self._metrics_logger.log_routing_result(routing_result)
                if inspect.isawaitable(log_res):
                    await log_res
            except Exception as exc:  # noqa: BLE001 - Telemetry errors must never crash payment path
                logger.warning("Failed to log metrics for transaction: %s", exc)

        return routing_result

    def _fallback_snapshot(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Best available snapshot when the registry cannot be updated."""
        try:
            return self._registry.get_state(acquirer_id)
        except Exception:  # noqa: BLE001 - the registry itself may be down
            cfg = self._routes[acquirer_id].state_config
            return AcquirerStateSnapshot(
                acquirer_id=acquirer_id,
                alpha=cfg.alpha_prior,
                beta=cfg.beta_prior,
                health_score=cfg.initial_health,
                success_count=0,
                failure_count=0,
                total_count=0,
                last_updated_at=self._clock(),
                alpha_prior=cfg.alpha_prior,
                beta_prior=cfg.beta_prior,
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
