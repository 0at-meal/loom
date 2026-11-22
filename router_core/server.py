"""CLI entrypoint for running the bandit router service via Uvicorn."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import uvicorn

from data_layer.config import DataLayerConfig
from router_core.app import create_router_app
from router_core.gate import GateConfig
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.state import (
    DEFAULT_HALF_LIFE_SEC,
    LONG_MEMORY_HALF_LIVES_SEC,
    LONG_MEMORY_SWITCH_RATE_PER_SEC,
    AcquirerStateConfig,
)

# 3% of 15 TPS, the traffic level the 3% floor was tuned at (Phase 4 Ticket B): the same floor
# there, a smaller share at higher TPS (Stage 1 decisions-log entry).
FAIL_FAST_FLOOR_RATE_PER_SEC = 0.45
# Stage 2: joint Thompson draws per decision for the win-probability setpoint.
LONG_MEMORY_ALLOCATION_DRAWS = 100


def load_warm_priors(path: str) -> dict[str, dict[str, float]]:
    """Read ``data_layer.cli export-priors`` JSON: per acquirer rate, observations, window.

    The rate is kept strictly inside (0, 1) so a ledger with only approvals still yields a
    valid Beta; acquirers without observations are skipped.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    window = float(data["window_sec"])
    out: dict[str, dict[str, float]] = {}
    for aid, row in data["acquirers"].items():
        n = float(row["observations"])
        if n <= 0.0:
            continue
        rate = min(max(float(row["approved"]) / n, 1e-4), 1.0 - 1e-4)
        out[aid] = {"rate": rate, "observations": n, "window_sec": window}
    return out


def apply_warm_priors(config: RouterConfig, priors: dict[str, dict[str, float]]) -> RouterConfig:
    """Seed each listed route's approval belief from ledger priors (Stage 2 warm start)."""
    routes = []
    for r in config.routes:
        p = priors.get(r.acquirer_id)
        if p is None:
            routes.append(r)
            continue
        state = replace(
            r.state_config,
            warm_approval_rate=p["rate"],
            warm_observations=p["observations"],
            warm_window_sec=p["window_sec"],
        )
        routes.append(r.model_copy(update={"state_config": state}))
    return config.model_copy(update={"routes": routes})


def apply_long_memory(config: RouterConfig) -> RouterConfig:
    """Stage 2 on top of ``config``: multi-timescale beliefs and a win-probability setpoint.

    Both beliefs of every route become mixtures over ``LONG_MEMORY_HALF_LIVES_SEC``; failures
    the gate already owns stay out of them; the PID tracks win probabilities with the strict
    integral clamp. Designed to run with the Stage 1 gate (``--fail-fast``): without it the
    5 s technical member is the only outage detector.
    """
    routes = [
        r.model_copy(
            update={
                "state_config": replace(
                    r.state_config,
                    approval_half_lives_sec=LONG_MEMORY_HALF_LIVES_SEC,
                    technical_half_lives_sec=LONG_MEMORY_HALF_LIVES_SEC,
                    mixture_switch_rate_per_sec=LONG_MEMORY_SWITCH_RATE_PER_SEC,
                )
            }
        )
        for r in config.routes
    ]
    update: dict[str, Any] = {
        "routes": routes,
        "allocation_draws": LONG_MEMORY_ALLOCATION_DRAWS,
        "withhold_gated_failures": True,
    }
    if config.pid_config is not None:
        update["pid_config"] = config.pid_config.model_copy(update={"strict_integral_clamp": True})
    return config.model_copy(update=update)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for bandit router server."""
    parser = argparse.ArgumentParser(
        description="Loom Bandit Router Service — Thompson Sampling Payment Router"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)",
    )
    parser.add_argument(
        "--acquirers",
        nargs="+",
        default=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
        help="Acquirer IDs or id=url pairs (default: acquirer_alpha acquirer_beta acquirer_gamma)",
    )
    parser.add_argument(
        "--acquirer-base-url",
        type=str,
        default="http://127.0.0.1:8001",
        help="Default base URL for acquirers if URL not specified per route (default: http://127.0.0.1:8001)",
    )
    decay = parser.add_mutually_exclusive_group()
    decay.add_argument(
        "--half-life-sec",
        type=float,
        default=None,
        help=(
            "Seconds for an observation's weight to halve (default: $DECAY_HALF_LIFE_SEC, "
            f"else {DEFAULT_HALF_LIFE_SEC})"
        ),
    )
    decay.add_argument(
        "--decay-factor",
        type=float,
        default=None,
        help="Use per-observation decay with this gamma in (0.0, 1.0) instead of a half-life",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Server logging level (default: info)",
    )
    parser.add_argument(
        "--pid",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable PID smoothing layer (default: True, use --no-pid to disable)",
    )
    parser.add_argument(
        "--kp",
        type=float,
        default=0.12,
        help="PID proportional gain (default: 0.12)",
    )
    parser.add_argument(
        "--ki",
        type=float,
        default=0.005,
        help="PID integral gain (default: 0.005)",
    )
    parser.add_argument(
        "--kd",
        type=float,
        default=0.25,
        help="PID derivative gain (default: 0.25)",
    )
    parser.add_argument(
        "--min-allocation",
        type=float,
        default=0.03,
        help="Minimum exploration allocation floor per acquirer (default: 0.03)",
    )
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=None,
        help=(
            "Most payments dispatched at once; 0 disables the limit "
            "(default: $ROUTER_MAX_IN_FLIGHT, else 50)"
        ),
    )
    parser.add_argument(
        "--ledger",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Log every decision to the SQLite ledger (default: $LEDGER_ENABLED, else on)",
    )
    parser.add_argument(
        "--ledger-path",
        default=None,
        help="SQLite ledger file (default: $SQLITE_DB_PATH, else loom_metrics.db)",
    )
    parser.add_argument(
        "--fail-fast",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Stage 1: technical gate with wall-clock probes, one cascade retry after a safe "
            "failure, and a rate-based exploration floor (default: off)"
        ),
    )
    parser.add_argument(
        "--long-memory",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Stage 2: multi-timescale approval beliefs (half-lives "
            f"{', '.join(f'{h:g}' for h in LONG_MEMORY_HALF_LIVES_SEC)} s) and a "
            "win-probability PID setpoint (default: off)"
        ),
    )
    parser.add_argument(
        "--priors",
        default=None,
        help=(
            "Stage 2 warm start: JSON from `python -m data_layer.cli export-priors`; seeds "
            "each listed acquirer's approval belief (default: none, cold start)"
        ),
    )
    parser.add_argument(
        "--redis",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Publish events and belief snapshots to Redis (default: $REDIS_ENABLED, else off)",
    )
    return parser.parse_args(args)


def build_data_config(parsed: argparse.Namespace) -> DataLayerConfig:
    """Data-layer settings from the environment, overridden by any CLI flags given."""
    overrides: dict[str, Any] = {}
    if getattr(parsed, "ledger", None) is not None:
        overrides["ledger_enabled"] = parsed.ledger
    if getattr(parsed, "ledger_path", None) is not None:
        overrides["sqlite_db_path"] = parsed.ledger_path
    if getattr(parsed, "redis", None) is not None:
        overrides["redis_enabled"] = parsed.redis
    return DataLayerConfig(**overrides)


def build_router_config(parsed: argparse.Namespace) -> RouterConfig:
    """Construct RouterConfig from parsed command line options."""
    routes: list[AcquirerRouteConfig] = []
    decay_factor = getattr(parsed, "decay_factor", None)
    if decay_factor is not None:
        state_cfg = AcquirerStateConfig(decay_factor=decay_factor)
    else:
        half_life = getattr(parsed, "half_life_sec", None)
        if half_life is None:
            half_life = float(os.environ.get("DECAY_HALF_LIFE_SEC", DEFAULT_HALF_LIFE_SEC))
        state_cfg = AcquirerStateConfig(half_life_sec=half_life)

    for item in parsed.acquirers:
        if "=" in item:
            acquirer_id, base_url = item.split("=", 1)
        else:
            acquirer_id = item
            base_url = parsed.acquirer_base_url

        routes.append(
            AcquirerRouteConfig(
                acquirer_id=acquirer_id.strip(),
                base_url=base_url.strip(),
                state_config=state_cfg,
            )
        )

    fail_fast = bool(getattr(parsed, "fail_fast", False))
    pid_config: PIDConfig | None = None
    if getattr(parsed, "pid", True):
        pid_config = PIDConfig(
            kp=getattr(parsed, "kp", 0.12),
            ki=getattr(parsed, "ki", 0.005),
            kd=getattr(parsed, "kd", 0.25),
            min_allocation=getattr(parsed, "min_allocation", 0.03),
            floor_rate_per_sec=FAIL_FAST_FLOOR_RATE_PER_SEC if fail_fast else None,
        )

    max_in_flight = getattr(parsed, "max_in_flight", None)
    if max_in_flight is None:
        max_in_flight = int(os.environ.get("ROUTER_MAX_IN_FLIGHT", "50"))
    config = RouterConfig(
        routes=routes,
        pid_config=pid_config,
        max_in_flight=max_in_flight if max_in_flight > 0 else None,
        gate_config=GateConfig() if fail_fast else None,
        cascade=fail_fast,
    )
    priors_path = getattr(parsed, "priors", None)
    if priors_path:
        config = apply_warm_priors(config, load_warm_priors(priors_path))
    if getattr(parsed, "long_memory", False):
        config = apply_long_memory(config)
    return config


def main() -> None:
    """Launch the bandit router application with parsed arguments."""
    parsed = parse_args(sys.argv[1:])

    logging.basicConfig(
        level=getattr(logging, parsed.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = build_router_config(parsed)
    app = create_router_app(config=config, data_config=build_data_config(parsed))

    uvicorn.run(
        app,
        host=parsed.host,
        port=parsed.port,
        log_level=parsed.log_level,
    )


if __name__ == "__main__":
    main()
