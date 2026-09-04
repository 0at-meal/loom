"""CLI entrypoint for running the simulated acquirer service via Uvicorn."""

from __future__ import annotations

import argparse
import sys

import uvicorn

from acquirer_sim.app import create_app


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for simulated acquirer server."""
    parser = argparse.ArgumentParser(
        description="Loom Simulated Acquirer Service — Mock payment gateway daemon"
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
        default=8001,
        help="Port to bind to (default: 8001)",
    )
    parser.add_argument(
        "--acquirers",
        nargs="+",
        default=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
        help="List of acquirer identifiers to register on startup",
    )
    parser.add_argument(
        "--base-rate",
        type=float,
        default=0.95,
        help="Default baseline payment success rate in [0.0, 1.0] (default: 0.95)",
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


def main() -> None:
    """Launch the simulated acquirer application with parsed arguments."""
    parsed = parse_args(sys.argv[1:])
    app = create_app(
        default_acquirers=parsed.acquirers,
        default_base_rate=parsed.base_rate,
    )
    uvicorn.run(
        app,
        host=parsed.host,
        port=parsed.port,
        log_level=parsed.log_level,
    )


if __name__ == "__main__":
    main()
