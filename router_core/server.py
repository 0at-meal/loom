"""CLI entrypoint for running the bandit router service via Uvicorn."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import uvicorn

from data_layer.config import DataLayerConfig
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.state import DEFAULT_HALF_LIFE_SEC, AcquirerStateConfig


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
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
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

    pid_config: PIDConfig | None = None
    if getattr(parsed, "pid", True):
        pid_config = PIDConfig(
            kp=getattr(parsed, "kp", 0.12),
            ki=getattr(parsed, "ki", 0.005),
            kd=getattr(parsed, "kd", 0.25),
            min_allocation=getattr(parsed, "min_allocation", 0.03),
        )

    return RouterConfig(routes=routes, pid_config=pid_config)


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
