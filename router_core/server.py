"""CLI entrypoint for running the bandit router service via Uvicorn."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.state import AcquirerStateConfig


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
    parser.add_argument(
        "--decay-factor",
        type=float,
        default=0.98,
        help="Bandit decay factor gamma in (0.0, 1.0) (default: 0.98)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Server logging level (default: info)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    return parser.parse_args(args)


def build_router_config(parsed: argparse.Namespace) -> RouterConfig:
    """Construct RouterConfig from parsed command line options."""
    routes: list[AcquirerRouteConfig] = []
    state_cfg = AcquirerStateConfig(decay_factor=parsed.decay_factor)

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

    return RouterConfig(routes=routes)


def main() -> None:
    """Launch the bandit router application with parsed arguments."""
    parsed = parse_args(sys.argv[1:])

    logging.basicConfig(
        level=getattr(logging, parsed.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = build_router_config(parsed)
    app = create_router_app(config=config)

    uvicorn.run(
        app,
        host=parsed.host,
        port=parsed.port,
        log_level=parsed.log_level,
    )


if __name__ == "__main__":
    main()
