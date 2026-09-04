"""Rate-controlled synthetic transaction generator for stress-testing Loom routing pipelines."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
import sys
import time
import uuid
from collections import deque
from typing import Any

import httpx
import numpy as np

from acquirer_sim.models import AuthorizeRequest
from router_core.models import RoutingResult
from router_core.router import BanditRouter

logger = logging.getLogger("loom.generator")


class GeneratorMetrics:
    """Thread-safe telemetry accumulator for real-time reporting."""

    def __init__(self, rolling_window_size: int = 100) -> None:
        """Initialize telemetry counters and rolling outcome windows."""
        self.rolling_window_size = rolling_window_size
        self.total_emitted: int = 0
        self.total_authorized: int = 0
        self.total_declined: int = 0
        self.total_errors: int = 0
        self.route_counts: dict[str, int] = {}
        self.recent_outcomes: deque[bool] = deque(maxlen=rolling_window_size)
        self.recent_latencies_ms: deque[float] = deque(maxlen=rolling_window_size)
        self.start_time: float = time.time()
        self.last_report_time: float = self.start_time
        self.last_emitted_count: int = 0

    def record_result(self, result: RoutingResult) -> None:
        """Record a completed routing result into cumulative and windowed telemetry."""
        self.total_emitted += 1
        acquirer = result.selected_acquirer
        self.route_counts[acquirer] = self.route_counts.get(acquirer, 0) + 1

        if result.status == "AUTHORIZED":
            self.total_authorized += 1
            self.recent_outcomes.append(True)
        elif result.status == "DECLINED":
            self.total_declined += 1
            self.recent_outcomes.append(False)
        else:
            self.total_errors += 1
            self.recent_outcomes.append(False)

        self.recent_latencies_ms.append(result.total_latency_ms)

    def get_summary(self) -> dict[str, Any]:
        """Compute point-in-time rates, PSR, percentiles, and route allocations."""
        now = time.time()
        interval_sec = max(0.001, now - self.last_report_time)
        interval_emitted = self.total_emitted - self.last_emitted_count
        actual_tps = interval_emitted / interval_sec

        lifetime_psr = (
            (self.total_authorized / self.total_emitted * 100.0) if self.total_emitted > 0 else 0.0
        )
        rolling_psr = (
            (sum(self.recent_outcomes) / len(self.recent_outcomes) * 100.0)
            if self.recent_outcomes
            else 0.0
        )

        alloc_pcts = {
            acq: (count / self.total_emitted * 100.0)
            for acq, count in sorted(self.route_counts.items())
        }

        latencies = sorted(self.recent_latencies_ms) if self.recent_latencies_ms else [0.0]
        n_lat = len(latencies)
        p50 = latencies[int(n_lat * 0.50)]
        p95 = latencies[min(n_lat - 1, int(n_lat * 0.95))]
        p99 = latencies[min(n_lat - 1, int(n_lat * 0.99))]

        self.last_report_time = now
        self.last_emitted_count = self.total_emitted

        return {
            "total_emitted": self.total_emitted,
            "actual_tps": actual_tps,
            "lifetime_psr": lifetime_psr,
            "rolling_psr": rolling_psr,
            "allocations": alloc_pcts,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
        }


class TransactionGenerator:
    """Generates synthetic transactions at controlled rates targeting HTTP or in-process routers."""

    def __init__(
        self,
        tps: float = 10.0,
        distribution: str = "fixed",
        target_url: str | None = None,
        router: BanditRouter | None = None,
        max_count: int | None = None,
        duration_sec: float | None = None,
        concurrency: int = 10,
        http_timeout_sec: float = 5.0,
        seed: int | None = None,
    ) -> None:
        """Initialize generator configuration, delivery channel, and pacing mechanism."""
        if tps <= 0.0:
            raise ValueError(f"tps must be > 0.0, got {tps}")
        if distribution not in ("fixed", "poisson"):
            raise ValueError(f"distribution must be 'fixed' or 'poisson', got {distribution}")
        if target_url is None and router is None:
            raise ValueError("Must provide either target_url or an in-process router instance")

        self.tps = tps
        self.distribution = distribution
        self.target_url = target_url
        self.router = router
        self.max_count = max_count
        self.duration_sec = duration_sec
        self.concurrency = max(1, concurrency)
        self.http_timeout_sec = http_timeout_sec
        self.metrics = GeneratorMetrics()
        self._running = False
        self._rng = random.Random(seed)
        self._np_rng = np.random.default_rng(seed)

    def generate_payload(self) -> AuthorizeRequest:
        """Construct a valid synthetic payment authorization payload."""
        tx_id = f"tx_{uuid.uuid4()}"
        # Log-normal distribution centered around ~$45-$60
        amount = round(max(1.0, float(self._np_rng.lognormal(mean=3.8, sigma=0.6))), 2)
        return AuthorizeRequest(
            transaction_id=tx_id,
            amount=amount,
            currency="USD",
            merchant_id="merchant_loom_default",
            payment_method="card",
            timestamp=time.time(),
        )

    def _next_delay(self) -> float:
        """Calculate wait time until next transaction emission in seconds."""
        if self.distribution == "fixed":
            return 1.0 / self.tps
        # Exponential distribution for Poisson arrivals
        return self._rng.expovariate(self.tps)

    async def _send_single(
        self,
        client: httpx.AsyncClient | None,
        request: AuthorizeRequest,
    ) -> RoutingResult:
        """Dispatch single transaction payload to HTTP endpoint or in-process router."""
        if self.router is not None:
            return await self.router.route(request)

        assert client is not None
        assert self.target_url is not None
        resp = await client.post(
            self.target_url,
            json=request.model_dump(),
            timeout=self.http_timeout_sec,
        )
        if resp.status_code == 200:
            return RoutingResult.model_validate(resp.json())
        raise RuntimeError(f"HTTP Router error {resp.status_code}: {resp.text}")

    async def run(
        self,
        report_interval_sec: float = 1.0,
        stop_event: asyncio.Event | None = None,
    ) -> GeneratorMetrics:
        """Execute rate-controlled generation loop until stop condition is reached."""
        self._running = True
        active_stop = stop_event or asyncio.Event()
        start_time = time.time()
        emitted = 0

        http_client: httpx.AsyncClient | None = None
        if self.target_url is not None:
            http_client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=self.concurrency * 2, max_keepalive_connections=self.concurrency
                )
            )

        sem = asyncio.Semaphore(self.concurrency)
        in_flight_tasks: set[asyncio.Task[None]] = set()

        async def worker_wrapper(req: AuthorizeRequest) -> None:
            async with sem:
                try:
                    res = await self._send_single(http_client, req)
                    self.metrics.record_result(res)
                except (httpx.HTTPError, RuntimeError, ValueError) as err:
                    logger.warning("Transaction error: %s (%s)", type(err).__name__, err)
                    self.metrics.total_errors += 1
                    self.metrics.recent_outcomes.append(False)

        # Reporter background task
        async def reporter_loop() -> None:
            while self._running and not active_stop.is_set():
                await asyncio.sleep(report_interval_sec)
                summary = self.metrics.get_summary()
                alloc_str = (
                    ", ".join(f"{k}: {v:.1f}%" for k, v in summary["allocations"].items()) or "None"
                )
                logger.info(
                    "[Rate: %5.1f TPS] Tx: %5d | PSR: %5.1f%% (Roll: %5.1f%%) | "
                    "Alloc: [%s] | Lat(ms): p50=%.1f p95=%.1f",
                    summary["actual_tps"],
                    summary["total_emitted"],
                    summary["lifetime_psr"],
                    summary["rolling_psr"],
                    alloc_str,
                    summary["p50_ms"],
                    summary["p95_ms"],
                )

        reporter_task = asyncio.create_task(reporter_loop())

        try:
            while self._running and not active_stop.is_set():
                if self.max_count is not None and emitted >= self.max_count:
                    break
                if (
                    self.duration_sec is not None
                    and (time.time() - start_time) >= self.duration_sec
                ):
                    break

                payload = self.generate_payload()
                emitted += 1

                task = asyncio.create_task(worker_wrapper(payload))
                in_flight_tasks.add(task)
                task.add_done_callback(in_flight_tasks.discard)

                delay = self._next_delay()
                if delay > 0:
                    await asyncio.sleep(delay)

            # Await remaining in-flight tasks
            if in_flight_tasks:
                await asyncio.gather(*in_flight_tasks, return_exceptions=True)

        finally:
            self._running = False
            reporter_task.cancel()
            try:
                await reporter_task
            except asyncio.CancelledError:
                pass

            if http_client is not None:
                await http_client.aclose()

        return self.metrics

    def stop(self) -> None:
        """Signal generation loop to terminate gracefully."""
        self._running = False


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for transaction generator."""
    parser = argparse.ArgumentParser(
        description="Loom Synthetic Transaction Generator — Traffic Harness"
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default="http://127.0.0.1:8000/route",
        help="Router HTTP authorization URL (default: http://127.0.0.1:8000/route)",
    )
    parser.add_argument(
        "--tps",
        type=float,
        default=10.0,
        help="Target transactions per second (default: 10.0)",
    )
    parser.add_argument(
        "--distribution",
        type=str,
        default="fixed",
        choices=["fixed", "poisson"],
        help="Arrival interval distribution: fixed or poisson (default: fixed)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Total transactions to emit before stopping (default: unlimited)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Duration in seconds to run before stopping (default: unlimited)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Maximum concurrent in-flight requests (default: 10)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Console logging verbosity (default: info)",
    )
    return parser.parse_args(args)


def main() -> None:
    """CLI launcher for transaction generator."""
    parsed = parse_args(sys.argv[1:])

    logging.basicConfig(
        level=getattr(logging, parsed.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    generator = TransactionGenerator(
        tps=parsed.tps,
        distribution=parsed.distribution,
        target_url=parsed.target_url,
        max_count=parsed.count,
        duration_sec=parsed.duration,
        concurrency=parsed.concurrency,
    )

    stop_event = asyncio.Event()

    def handle_signal(*_args: Any) -> None:
        logger.info("Received interrupt signal; shutting down generator...")
        stop_event.set()
        generator.stop()

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

    try:
        asyncio.run(generator.run(stop_event=stop_event))
    except KeyboardInterrupt:
        logger.info("Generator interrupted by user.")


if __name__ == "__main__":
    main()
