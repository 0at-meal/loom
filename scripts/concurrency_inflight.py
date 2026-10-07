"""How many concurrent requests reach a dead acquirer before any outcome arrives (AUDIT F-16).

Reproduces the audit's exp5 setup: three acquirers at 95% with 20 +/- 5 ms latency
(simulator seed 42), Loom with PID (router seed 7). After 100 sequential warm-up payments,
Alpha starts returning HTTP 503 and 200 payments are routed at once (asyncio.gather), so
every decision is made before any outcome of the burst is known. For comparison the same
200 payments are also routed one at a time. Repeat over several seeds with --seeds.

    python scripts/concurrency_inflight.py
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import uuid
from typing import Literal

import httpx
from fastapi import FastAPI

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageBehavior
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter

IDS = ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]


def _build(
    mode: Literal["stochastic", "deficit"], sim_seed: int, router_seed: int
) -> tuple[FastAPI, BanditRouter]:
    app = create_app(
        default_acquirers=IDS,
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=20, jitter_ms=5),
        seed=sim_seed,
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim")
    config = RouterConfig(
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://sim") for a in IDS],
        pid_config=PIDConfig(actuation_mode=mode),
        seed=router_seed,
    )
    return app, BanditRouter(config=config, http_client=client)


def _req() -> AuthorizeRequest:
    return AuthorizeRequest(transaction_id=f"tx_{uuid.uuid4().hex}", amount=100.0)


async def sent_to_dead(
    mode: Literal["stochastic", "deficit"],
    concurrent: bool,
    sim_seed: int = 42,
    router_seed: int = 7,
    burst: int = 200,
) -> int:
    """Return how many of ``burst`` payments went to Alpha during its 503 outage."""
    app, router = _build(mode, sim_seed, router_seed)
    for _ in range(100):
        await router.route(_req())
    app.state.registry.get("acquirer_alpha").set_outage(True, OutageBehavior.HTTP_503)
    if concurrent:
        results = await asyncio.gather(*[router.route(_req()) for _ in range(burst)])
    else:
        results = [await router.route(_req()) for _ in range(burst)]
    return sum(1 for r in results if r.selected_acquirer == "acquirer_alpha")


async def main(argv: list[str] | None = None) -> None:
    """Print payments sent to the dead acquirer, concurrent vs sequential, per actuation mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=1, help="Router seeds 7, 8, ... to average")
    parser.add_argument("--burst", type=int, default=200)
    args = parser.parse_args(argv)
    print(f"{args.burst} payments during a 503 outage on Alpha, router seeds 7..{6 + args.seeds}:")
    modes: tuple[Literal["stochastic", "deficit"], ...] = ("stochastic", "deficit")
    for mode in modes:
        for concurrent in (True, False):
            counts = [
                await sent_to_dead(mode, concurrent, router_seed=7 + k, burst=args.burst)
                for k in range(args.seeds)
            ]
            label = "concurrent" if concurrent else "sequential"
            print(
                f"  {mode:<10} {label:<10} sent to Alpha: mean {statistics.mean(counts):.1f} "
                f"(per seed: {counts})"
            )


if __name__ == "__main__":
    asyncio.run(main())
