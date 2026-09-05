"""Static baseline payment router executing priority-based route selection with circuit breaker."""

from __future__ import annotations

import inspect
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Literal

import httpx

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverThresholdType,
    RouteHealthStatus,
    StaticRouteStateSnapshot,
)
from router_core.models import AcquirerRouteConfig, RoutingResult
from router_core.state import AcquirerStateSnapshot

if TYPE_CHECKING:
    from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore

logger = logging.getLogger("loom.baseline_router")


class _MutableRouteState:
    """Internal mutable tracking for a static route's counters and circuit breaker."""

    __slots__ = (
        "acquirer_id",
        "consecutive_failures",
        "failure_count",
        "history",
        "last_updated_at",
        "priority",
        "status",
        "success_count",
        "total_count",
        "tripped_at_tx",
    )

    def __init__(self, acquirer_id: str, priority: int, window_size: int = 20) -> None:
        self.acquirer_id = acquirer_id
        self.priority = priority
        self.status = RouteHealthStatus.HEALTHY
        self.consecutive_failures = 0
        self.tripped_at_tx: int | None = None
        self.success_count = 0
        self.failure_count = 0
        self.total_count = 0
        self.last_updated_at = time.time()
        self.history: deque[float] = deque(maxlen=window_size)

    def to_snapshot(self) -> StaticRouteStateSnapshot:
        """Export immutable snapshot of this route's operational state."""
        return StaticRouteStateSnapshot(
            acquirer_id=self.acquirer_id,
            priority=self.priority,
            status=self.status,
            consecutive_failures=self.consecutive_failures,
            tripped_at_tx=self.tripped_at_tx,
            success_count=self.success_count,
            failure_count=self.failure_count,
            total_count=self.total_count,
            last_updated_at=self.last_updated_at,
        )


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

        self._route_states: dict[str, _MutableRouteState] = {
            aid: _MutableRouteState(
                acquirer_id=aid,
                priority=idx + 1,
                window_size=config.failover_policy.window_size,
            )
            for idx, aid in enumerate(self._priority_order)
        }

        self._global_tx_counter = 0
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
    def global_tx_counter(self) -> int:
        """Return the total number of transactions routed by this instance."""
        return self._global_tx_counter

    @property
    def metrics_logger(self) -> Any | None:
        """Return the optional metrics logger hook."""
        return self._metrics_logger

    @property
    def current_allocation(self) -> dict[str, float]:
        """Return the current static allocation vector."""
        _selected_id, alloc = self._peek_current_route()
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

    def _peek_current_route(self) -> tuple[str, dict[str, float]]:
        """Determine which route would be selected right now without state mutation."""
        # 1. Check if any higher-priority TRIPPED route has completed cooldown
        cooldown_limit = self._config.failover_policy.cooldown_transactions
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status == RouteHealthStatus.TRIPPED and state.tripped_at_tx is not None:
                if (self._global_tx_counter - state.tripped_at_tx) >= cooldown_limit:
                    alloc = {k: 1.0 if k == aid else 0.0 for k in self._priority_order}
                    return aid, alloc

        # 2. Check for route already in PROBATION or HEALTHY in priority order
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status in (RouteHealthStatus.HEALTHY, RouteHealthStatus.PROBATION):
                alloc = {k: 1.0 if k == aid else 0.0 for k in self._priority_order}
                return aid, alloc

        # 3. Exhaustion Fallback
        primary_id = self._priority_order[0]
        alloc = {k: 1.0 if k == primary_id else 0.0 for k in self._priority_order}
        return primary_id, alloc

    def select_route(self) -> tuple[str, dict[str, float]]:
        """Evaluate route states and return selected acquirer ID and static allocation vector.

        Returns:
            tuple[selected_id, allocation_vector] where allocation_vector is a 1-hot
            indicator dictionary (e.g. {"acquirer_alpha": 1.0, "acquirer_beta": 0.0}).
        """
        cooldown_limit = self._config.failover_policy.cooldown_transactions

        # 1. Check if any higher-priority TRIPPED route has completed cooldown
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status == RouteHealthStatus.TRIPPED and state.tripped_at_tx is not None:
                elapsed = self._global_tx_counter - state.tripped_at_tx
                if elapsed >= cooldown_limit:
                    if self._config.failover_policy.failback_mode == "snapback":
                        logger.info(
                            "Cooldown elapsed (%d >= %d) on %s; snapback to HEALTHY",
                            elapsed,
                            cooldown_limit,
                            aid,
                        )
                        state.status = RouteHealthStatus.HEALTHY
                        state.consecutive_failures = 0
                        state.tripped_at_tx = None
                    else:
                        logger.info(
                            "Cooldown elapsed (%d >= %d) on %s; dispatching PROBATION probe",
                            elapsed,
                            cooldown_limit,
                            aid,
                        )
                        state.status = RouteHealthStatus.PROBATION

                    alloc = {k: 1.0 if k == aid else 0.0 for k in self._priority_order}
                    return aid, alloc

        # 2. Select first available route in priority order (PROBATION probe or HEALTHY)
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status in (RouteHealthStatus.HEALTHY, RouteHealthStatus.PROBATION):
                alloc = {k: 1.0 if k == aid else 0.0 for k in self._priority_order}
                return aid, alloc

        # 3. Exhaustion Fallback: If all routes are TRIPPED, fall back to Primary with warning
        primary_id = self._priority_order[0]
        logger.warning(
            "All static routes are TRIPPED; activating exhaustion fallback to primary: %s",
            primary_id,
        )
        alloc = {k: 1.0 if k == primary_id else 0.0 for k in self._priority_order}
        return primary_id, alloc

    async def route(self, request: AuthorizeRequest) -> RoutingResult:
        """Execute static route selection, HTTP authorization dispatch, and state update."""
        t_start = time.perf_counter()
        self._global_tx_counter += 1

        # 1. Static Selection
        t_select_start = time.perf_counter()
        selected_id, static_allocation = self.select_route()
        t_select_end = time.perf_counter()
        routing_latency_ms = (t_select_end - t_select_start) * 1000.0

        logger.info(
            "Baseline routing decision: tx_id=%s -> selected=%s (tx_seq=%d, status=%s)",
            request.transaction_id,
            selected_id,
            self._global_tx_counter,
            self._route_states[selected_id].status.value,
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

        # 3. Circuit Breaker State Mutation
        state = self._route_states[selected_id]
        state.total_count += 1
        state.last_updated_at = time.time()
        state.history.append(1.0 if success else 0.0)

        if success:
            state.success_count += 1
            state.consecutive_failures = 0
            if state.status == RouteHealthStatus.PROBATION:
                logger.info(
                    "Canary probe succeeded on %s; promoting route to HEALTHY",
                    selected_id,
                )
                state.status = RouteHealthStatus.HEALTHY
                state.tripped_at_tx = None
        else:
            state.failure_count += 1
            state.consecutive_failures += 1

            if state.status == RouteHealthStatus.PROBATION:
                logger.warning(
                    "Canary probe failed on %s; re-tripping circuit breaker and resetting cooldown",
                    selected_id,
                )
                state.status = RouteHealthStatus.TRIPPED
                state.tripped_at_tx = self._global_tx_counter
            elif state.status == RouteHealthStatus.HEALTHY:
                tripped = False
                policy = self._config.failover_policy
                if policy.threshold_type == FailoverThresholdType.CONSECUTIVE_FAILURES:
                    if state.consecutive_failures >= policy.consecutive_failure_threshold:
                        tripped = True
                elif policy.threshold_type == FailoverThresholdType.WINDOW_FAILURE_RATE:
                    if len(state.history) >= policy.window_size:
                        err_rate = state.history.count(0.0) / len(state.history)
                        if err_rate >= policy.window_failure_rate_threshold:
                            tripped = True

                if tripped:
                    logger.warning(
                        "Circuit breaker TRIPPED on %s (consecutive_failures=%d, total=%d); "
                        "failing over to next priority tier",
                        selected_id,
                        state.consecutive_failures,
                        state.total_count,
                    )
                    state.status = RouteHealthStatus.TRIPPED
                    state.tripped_at_tx = self._global_tx_counter

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
        if acquirer_id not in self._route_states:
            raise KeyError(f"Acquirer route {acquirer_id} not configured in baseline router")
        return self._route_states[acquirer_id].to_snapshot()

    def get_all_states(self) -> dict[str, StaticRouteStateSnapshot]:
        """Return point-in-time state snapshots across all configured routes."""
        return {aid: self._route_states[aid].to_snapshot() for aid in self._priority_order}

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all registered acquirer identifiers in priority order."""
        return list(self._priority_order)
