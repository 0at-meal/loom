"""Static baseline payment router executing priority-based route selection with circuit breaker."""

from __future__ import annotations

import inspect
import logging
import time
from typing import TYPE_CHECKING, Any, Literal

import httpx

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from baseline_router.models import (
    BaselineRouterConfig,
    RouteHealthStatus,
    StaticRouteStateSnapshot,
)
from baseline_router.policies import StaticBreakerPolicy
from router_core.models import AcquirerRouteConfig, RoutingResult
from router_core.policy import DecisionContext, Observation, classify_outcome
from router_core.state import AcquirerStateSnapshot, Outcome

# Decline codes that mean the acquirer did not process the payment (as in RouterConfig).
STATIC_TECHNICAL_DECLINE_CODES = frozenset({"ACQUIRER_OUTAGE"})

if TYPE_CHECKING:
    from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore

logger = logging.getLogger("loom.baseline_router")


class StaticBaselineRouter:
    """Production-representative static rule-based payment router.

    Executes an active-passive priority list with circuit breaker failover.
    Reproduces herd migration and slow failover failure modes under stress.
    Logs to the exact Phase 5 SQLite schema via MetricsLogger for apples-to-apples PSR comparison.
    """

    def __init__(
        self,
        config: BaselineRouterConfig,
        http_client: httpx.AsyncClient | None = None,
        metrics_logger: MetricsLogger | SQLiteMetricsStore | Any | None = None,
    ) -> None:
        """Initialize route states, priority hierarchy, and HTTP connection pool."""
        self._config = config
        self._routes: dict[str, AcquirerRouteConfig] = {r.acquirer_id: r for r in config.routes}
        self._priority_order: list[str] = list(config.priority_order)
        self._metrics_logger = metrics_logger
        # Route selection and breaker state live in the policy (Stage 0); this class is the
        # async shell: dispatch, response handling and logging.
        self._policy = StaticBreakerPolicy(
            priority_order=self._priority_order,
            failover_policy=config.failover_policy,
            initial_timestamp=time.time(),
        )
        self._client = http_client
        self._owns_client = http_client is None

    @property
    def config(self) -> BaselineRouterConfig:
        """Return the configuration parameters for this baseline router."""
        return self._config

    @property
    def priority_order(self) -> list[str]:
        """Return the configured priority order of acquirer IDs."""
        return list(self._priority_order)

    @property
    def policy(self) -> StaticBreakerPolicy:
        """The policy that selects routes and keeps breaker state."""
        return self._policy

    @property
    def global_tx_counter(self) -> int:
        """Return the total number of transactions routed by this instance."""
        return self._policy.global_tx_counter

    @property
    def metrics_logger(self) -> Any | None:
        """Return the optional metrics logger hook."""
        return self._metrics_logger

    @property
    def current_allocation(self) -> dict[str, float]:
        """Return the current static allocation vector."""
        _selected_id, alloc = self._policy.peek()
        return alloc

    async def start(self) -> None:
        """Initialize pooled HTTP client if owned."""
        if self._client is None:
            limits = httpx.Limits(
                max_connections=self._config.max_connections,
                max_keepalive_connections=self._config.max_keepalive_connections,
            )
            self._client = httpx.AsyncClient(limits=limits)
            logger.debug(
                "Initialized baseline router pooled AsyncClient (max=%d, keepalive=%d)",
                self._config.max_connections,
                self._config.max_keepalive_connections,
            )

    async def close(self) -> None:
        """Close pooled HTTP client if owned."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("Closed baseline router pooled AsyncClient")

    async def __aenter__(self) -> StaticBaselineRouter:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def route(self, request: AuthorizeRequest) -> RoutingResult:
        """Execute static route selection, HTTP authorization dispatch, and state update."""
        t_start = time.perf_counter()

        # 1. Static Selection (advances the breaker's transaction counter)
        t_select_start = time.perf_counter()
        decision = self._policy.decide(
            DecisionContext(transaction_id=request.transaction_id, amount=request.amount),
            now=time.time(),
        )
        selected_id = decision.acquirer_id
        static_allocation = dict(decision.propensities)
        t_select_end = time.perf_counter()
        routing_latency_ms = (t_select_end - t_select_start) * 1000.0

        logger.info(
            "Baseline routing decision: tx_id=%s -> selected=%s (tx_seq=%d, status=%s)",
            request.transaction_id,
            selected_id,
            self._policy.global_tx_counter,
            self._policy.route_state(selected_id).status.value,
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
                success = payload.authorized
                status = "AUTHORIZED" if success else "DECLINED"
            elif resp.status_code == 503:
                status = "ERROR"
                authorized = False
                success = False
                error_msg = f"Acquirer HTTP 503 Outage: {resp.text}"
            elif resp.status_code == 422:
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

        # 3. Circuit Breaker State Mutation. The static breaker counts every payment that was
        # not authorized as a failure (AUDIT F-01); the outcome kind is recorded for the record.
        observation = (
            classify_outcome(
                response_payload.authorized,
                response_payload.decline_code,
                STATIC_TECHNICAL_DECLINE_CODES,
            )
            if response_payload is not None
            else Observation(Outcome.TECHNICAL_FAILURE)
        )
        self._policy.feedback(decision, observation, now=time.time())
        state = self._policy.route_state(selected_id)

        # 4. Construct State Snapshot & Routing Result Envelope
        health_score = (
            1.0
            if state.status == RouteHealthStatus.HEALTHY
            else (0.5 if state.status == RouteHealthStatus.PROBATION else 0.0)
        )
        state_snapshot = AcquirerStateSnapshot(
            acquirer_id=selected_id,
            alpha=1.0 + float(state.success_count),
            beta=1.0 + float(state.failure_count),
            health_score=health_score,
            success_count=state.success_count,
            failure_count=state.failure_count,
            total_count=state.total_count,
            last_updated_at=state.last_updated_at,
        )

        t_end = time.perf_counter()
        total_latency_ms = (t_end - t_start) * 1000.0

        routing_result = RoutingResult(
            transaction_id=request.transaction_id,
            selected_acquirer=selected_id,
            thompson_samples=static_allocation,  # satisfies schema NOT NULL constraint
            status=status,
            authorized=authorized,
            success=success,
            response_payload=response_payload,
            error_message=error_msg,
            routing_latency_ms=routing_latency_ms,
            acquirer_latency_ms=acquirer_latency_ms,
            total_latency_ms=total_latency_ms,
            state_snapshot=state_snapshot,
            smoothed_allocation=static_allocation,
            target_allocation=static_allocation,
            pid_diagnostics=None,
            timestamp=time.time(),
        )

        # 5. Log to SQLite Metrics Logger if Configured
        if self._metrics_logger is not None:
            try:
                log_res = self._metrics_logger.log_routing_result(routing_result)
                if inspect.isawaitable(log_res):
                    await log_res
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to log metrics for transaction: %s", exc)

        return routing_result

    def get_route_state(self, acquirer_id: str) -> StaticRouteStateSnapshot:
        """Return point-in-time state snapshot for an individual route."""
        return self._policy.route_state(acquirer_id)

    def get_all_states(self) -> dict[str, StaticRouteStateSnapshot]:
        """Return point-in-time state snapshots across all configured routes."""
        return self._policy.all_states()

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all registered acquirer identifiers in priority order."""
        return list(self._priority_order)
