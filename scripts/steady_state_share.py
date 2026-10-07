"""Share of traffic Loom sends to the worse of two healthy acquirers (AUDIT F-03).

No outage: Alpha approves 95% of payments and Beta a little less. For each Beta rate
and seed, Loom routes ``--n-tx`` payments on a virtual 15 TPS clock, and the script
reports the share of payments after ``--skip`` that went to Beta. A router that could
tell the two apart would send Beta only its 3% exploration floor.

    python scripts/steady_state_share.py
    python scripts/steady_state_share.py --config served
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from typing import Any

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import DEFAULT_HALF_LIFE_SEC, AcquirerStateConfig
from scripts.compare_psr import BENCH_HALF_LIFE_SEC, BENCH_TPS

ALPHA_RATE = 0.95
CONFIGS: dict[str, dict[str, Any]] = {
    # The compare_psr.py benchmark: 0.9 s half-life, deficit actuation
    "benchmark": {"half_life_sec": BENCH_HALF_LIFE_SEC, "actuation_mode": "deficit"},
    # The served router's defaults: 2.3 s half-life, stochastic actuation
    "served": {"half_life_sec": DEFAULT_HALF_LIFE_SEC, "actuation_mode": "stochastic"},
}


async def worse_share(beta_rate: float, seed: int, n_tx: int, skip: int, config: str) -> float:
    """Route ``n_tx`` payments and return the share of those after ``skip`` sent to Beta."""
    settings = CONFIGS[config]
    sim = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=ALPHA_RATE,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42 + 10 * seed,
    )
    sim.state.registry.get("acquirer_alpha").set_success_rate(ALPHA_RATE)
    sim.state.registry.get("acquirer_beta").set_success_rate(beta_rate)
    now = [0.0]
    state_config = AcquirerStateConfig(half_life_sec=settings["half_life_sec"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim), base_url="http://testserver"
    ) as client:
        routes = [
            AcquirerRouteConfig(
                acquirer_id=aid, base_url="http://testserver", state_config=state_config
            )
            for aid in ("acquirer_alpha", "acquirer_beta")
        ]
        router_config = RouterConfig(
            routes=routes,
            pid_config=PIDConfig(
                kp=0.12,
                ki=0.005,
                kd=0.25,
                integral_max=1.0,
                min_allocation=0.03,
                actuation_mode=settings["actuation_mode"],
            ),
            seed=777 + seed,
        )
        router = BanditRouter(config=router_config, http_client=client, clock=lambda: now[0])
        to_beta = 0
        for i in range(n_tx):
            now[0] += 1.0 / BENCH_TPS
            result = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=50.0))
            if i >= skip and result.selected_acquirer == "acquirer_beta":
                to_beta += 1
    return to_beta / (n_tx - skip)


async def run(
    beta_rates: list[float], seeds: int, n_tx: int, skip: int, config: str
) -> dict[float, list[float]]:
    """Return the per-seed share sent to Beta for each Beta rate."""
    return {
        rate: [await worse_share(rate, k, n_tx, skip, config) for k in range(seeds)]
        for rate in beta_rates
    }


def build_parser() -> argparse.ArgumentParser:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--beta-rates", type=float, nargs="+", default=[0.94, 0.92, 0.90])
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--n-tx", type=int, default=2000)
    parser.add_argument("--skip", type=int, default=500)
    parser.add_argument("--config", choices=sorted(CONFIGS), default="benchmark")
    return parser


async def main(argv: list[str] | None = None) -> None:
    """Print the mean and standard deviation of the worse acquirer's share per Beta rate."""
    args = build_parser().parse_args(argv)
    if not 0 <= args.skip < args.n_tx:
        raise SystemExit("--skip must be in [0, --n-tx)")
    shares = await run(args.beta_rates, args.seeds, args.n_tx, args.skip, args.config)
    print(
        f"Alpha {ALPHA_RATE:.0%}, {args.config} config, {args.seeds} seeds, "
        f"share of transactions {args.skip + 1}-{args.n_tx} sent to Beta:"
    )
    for rate, values in shares.items():
        sd = statistics.stdev(values) if len(values) > 1 else 0.0
        print(f"  Beta {rate:.0%}: {statistics.mean(values):.1%} (sd {sd:.1%})")


if __name__ == "__main__":
    asyncio.run(main())
