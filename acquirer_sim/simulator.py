"""Core simulation engine, state machine, and multi-acquirer registry."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import numpy as np

from acquirer_sim.models import (
    AcquirerConfig,
    AdminStateResponse,
    AuthorizeRequest,
    AuthorizeResponse,
    LatencyConfig,
    OutageBehavior,
    ResetResponse,
)


class AcquirerOutageHttpException(Exception):
    """Exception raised when an authorization encounters an HTTP 503 outage failure."""

    def __init__(self, acquirer_id: str, message: str = "Acquirer service unavailable") -> None:
        """Initialize outage exception with acquirer identifier."""
        super().__init__(message)
        self.acquirer_id: str = acquirer_id
        self.message: str = message


class AcquirerSimulator:
    """Encapsulates runtime state, PRNG logic, and telemetry counters for an acquirer."""

    def __init__(
        self,
        config: AcquirerConfig,
        rng: np.random.Generator | None = None,
    ) -> None:
        """Initialize acquirer simulator instance with initial configuration."""
        self._config: AcquirerConfig = config
        self._acquirer_id: str = config.acquirer_id
        self._base_success_rate: float = config.base_success_rate
        self._latency_config: LatencyConfig = config.latency

        self._outage_active: bool = False
        self._outage_behavior: OutageBehavior = OutageBehavior.RETURN_DECLINE
        self._start_time: float = time.time()

        self._total_requests: int = 0
        self._authorized_count: int = 0
        self._declined_count: int = 0
        self._outage_declines: int = 0

        self._lock: threading.Lock = threading.Lock()
        self._rng: np.random.Generator = rng if rng is not None else np.random.default_rng()

    @property
    def acquirer_id(self) -> str:
        """Return the unique acquirer identifier."""
        return self._acquirer_id

    @property
    def base_success_rate(self) -> float:
        """Return the configured baseline success rate."""
        return self._base_success_rate

    @property
    def effective_success_rate(self) -> float:
        """Return the current operational success rate (0.0 if outage active, else base)."""
        return 0.0 if self._outage_active else self._base_success_rate

    @property
    def is_outage_active(self) -> bool:
        """Return whether outage mode is currently engaged."""
        return self._outage_active

    @property
    def outage_behavior(self) -> OutageBehavior:
        """Return the current outage failure mode behavior."""
        return self._outage_behavior

    @property
    def latency_config(self) -> LatencyConfig:
        """Return current latency simulation parameters."""
        return self._latency_config

    def set_success_rate(self, rate: float) -> None:
        """Update the base success rate in [0.0, 1.0]."""
        if not (0.0 <= rate <= 1.0):
            raise ValueError(f"success_rate must be in [0.0, 1.0], got {rate}")
        with self._lock:
            self._base_success_rate = rate

    def set_outage(
        self,
        active: bool,
        behavior: OutageBehavior = OutageBehavior.RETURN_DECLINE,
        transition_seconds: float = 0.0,
    ) -> None:
        """Engage or disengage outage state."""
        if transition_seconds > 0.0:
            raise ValueError(
                "Gradual transition curves not supported in v1; use external orchestration script"
            )
        with self._lock:
            self._outage_active = active
            self._outage_behavior = behavior

    def set_latency_config(self, config: LatencyConfig) -> None:
        """Update the latency configuration."""
        with self._lock:
            self._latency_config = config

    def calculate_latency(self) -> float:
        """Calculate artificial processing latency in milliseconds."""
        base = self._latency_config.base_ms
        jitter = self._latency_config.jitter_ms
        delay = base
        if jitter > 0.0:
            delay += float(self._rng.uniform(-jitter, jitter))

        if self._outage_active and self._outage_behavior == OutageBehavior.LATENCY_SPIKE:
            delay += self._latency_config.outage_spike_ms

        return max(0.0, delay)

    async def execute_authorization(self, request: AuthorizeRequest) -> AuthorizeResponse:
        """Simulate authorization delay and return probabilistic or outage outcome."""
        simulated_delay_ms = self.calculate_latency()
        if simulated_delay_ms > 0.0:
            await asyncio.sleep(simulated_delay_ms / 1000.0)

        server_timestamp = time.time()

        # Check outage state first
        if self._outage_active:
            if self._outage_behavior == OutageBehavior.HTTP_503:
                with self._lock:
                    self._total_requests += 1
                    self._declined_count += 1
                    self._outage_declines += 1
                raise AcquirerOutageHttpException(
                    acquirer_id=self._acquirer_id,
                    message="Acquirer currently unavailable due to operational outage (HTTP 503)",
                )

            # Both RETURN_DECLINE and LATENCY_SPIKE produce standard decline payload
            with self._lock:
                self._total_requests += 1
                self._declined_count += 1
                self._outage_declines += 1

            return AuthorizeResponse(
                transaction_id=request.transaction_id,
                acquirer_id=self._acquirer_id,
                status="DECLINED",
                authorized=False,
                authorization_code=None,
                decline_code="ACQUIRER_OUTAGE",
                decline_message="Acquirer currently unavailable due to operational outage",
                simulated_latency_ms=round(simulated_delay_ms, 2),
                timestamp=server_timestamp,
            )

        # Normal probabilistic execution
        u = float(self._rng.uniform(0.0, 1.0))
        if u < self._base_success_rate:
            with self._lock:
                self._total_requests += 1
                self._authorized_count += 1

            auth_code = f"AUTH_{uuid.uuid4().hex[:6].upper()}"
            return AuthorizeResponse(
                transaction_id=request.transaction_id,
                acquirer_id=self._acquirer_id,
                status="AUTHORIZED",
                authorized=True,
                authorization_code=auth_code,
                decline_code=None,
                decline_message=None,
                simulated_latency_ms=round(simulated_delay_ms, 2),
                timestamp=server_timestamp,
            )

        with self._lock:
            self._total_requests += 1
            self._declined_count += 1

        return AuthorizeResponse(
            transaction_id=request.transaction_id,
            acquirer_id=self._acquirer_id,
            status="DECLINED",
            authorized=False,
            authorization_code=None,
            decline_code="DO_NOT_HONOR",
            decline_message="Transaction declined by issuing bank",
            simulated_latency_ms=round(simulated_delay_ms, 2),
            timestamp=server_timestamp,
        )

    def get_telemetry_snapshot(self) -> AdminStateResponse:
        """Return point-in-time telemetry snapshot."""
        with self._lock:
            empirical_rate = (
                (self._authorized_count / self._total_requests) if self._total_requests > 0 else 1.0
            )
            return AdminStateResponse(
                acquirer_id=self._acquirer_id,
                base_success_rate=self._base_success_rate,
                effective_success_rate=self.effective_success_rate,
                outage_active=self._outage_active,
                outage_behavior=self._outage_behavior,
                latency=self._latency_config,
                total_requests=self._total_requests,
                authorized_count=self._authorized_count,
                declined_count=self._declined_count,
                outage_declines=self._outage_declines,
                empirical_success_rate=round(empirical_rate, 4),
                uptime_seconds=round(max(0.0, time.time() - self._start_time), 2),
            )

    def reset(self) -> ResetResponse:
        """Reset counters and restore initial configuration."""
        with self._lock:
            self._total_requests = 0
            self._authorized_count = 0
            self._declined_count = 0
            self._outage_declines = 0
            self._outage_active = False
            self._outage_behavior = OutageBehavior.RETURN_DECLINE
            self._base_success_rate = self._config.base_success_rate
            self._start_time = time.time()

        return ResetResponse(
            acquirer_id=self._acquirer_id,
            message="Telemetry counters reset and default state restored",
            timestamp=time.time(),
        )


class MultiAcquirerSimulator:
    """Registry coordinating multiple simulated acquirers keyed by identifier."""

    def __init__(
        self,
        default_acquirers: list[str] | None = None,
        default_base_rate: float = 0.95,
        default_latency: LatencyConfig | None = None,
        seed: int | None = None,
    ) -> None:
        """Initialize multi-acquirer coordinator with optional default routes."""
        self._default_base_rate: float = default_base_rate
        self._default_latency: LatencyConfig = default_latency or LatencyConfig()
        self._seed: int | None = seed
        self._seed_counter: int = seed if seed is not None else 0
        self._lock: threading.Lock = threading.Lock()
        self._simulators: dict[str, AcquirerSimulator] = {}

        if default_acquirers:
            for acquirer_id in default_acquirers:
                self.get_or_create(acquirer_id)

    def _create_rng(self) -> np.random.Generator:
        """Produce an independent PRNG generator."""
        if self._seed is None:
            return np.random.default_rng()
        self._seed_counter += 1
        return np.random.default_rng(self._seed_counter)

    def register(self, config: AcquirerConfig) -> AcquirerSimulator:
        """Register a new acquirer route; raises ValueError if already registered."""
        with self._lock:
            if config.acquirer_id in self._simulators:
                raise ValueError(f"Acquirer '{config.acquirer_id}' is already registered")
            simulator = AcquirerSimulator(config=config, rng=self._create_rng())
            self._simulators[config.acquirer_id] = simulator
            return simulator

    def get(self, acquirer_id: str) -> AcquirerSimulator | None:
        """Retrieve simulator for acquirer_id if registered, else None."""
        with self._lock:
            return self._simulators.get(acquirer_id)

    def get_or_create(self, acquirer_id: str) -> AcquirerSimulator:
        """Retrieve simulator for acquirer_id or create one on-demand with default settings."""
        if not acquirer_id or not acquirer_id.strip():
            raise ValueError("acquirer_id cannot be empty")
        with self._lock:
            if acquirer_id not in self._simulators:
                cfg = AcquirerConfig(
                    acquirer_id=acquirer_id,
                    base_success_rate=self._default_base_rate,
                    latency=self._default_latency,
                )
                self._simulators[acquirer_id] = AcquirerSimulator(
                    config=cfg,
                    rng=self._create_rng(),
                )
            return self._simulators[acquirer_id]

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all registered acquirer identifiers."""
        with self._lock:
            return list(self._simulators.keys())

    def get_all_telemetry(self) -> dict[str, AdminStateResponse]:
        """Return telemetry snapshots for all registered acquirers."""
        with self._lock:
            return {
                acquirer_id: sim.get_telemetry_snapshot()
                for acquirer_id, sim in self._simulators.items()
            }

    def reset_all(self) -> list[ResetResponse]:
        """Reset all registered acquirers and return reset reports."""
        with self._lock:
            return [sim.reset() for sim in self._simulators.values()]
