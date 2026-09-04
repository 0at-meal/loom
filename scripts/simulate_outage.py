"""Admin script for triggering and clearing outages on simulated acquirer services."""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from acquirer_sim.models import OutageBehavior, OutageToggleRequest


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for outage simulation script."""
    parser = argparse.ArgumentParser(
        description="Loom Outage Simulation Utility — Trigger/Clear Acquirer Outages"
    )
    parser.add_argument(
        "--acquirer-url",
        type=str,
        default="http://127.0.0.1:8001",
        help="Base URL of simulated acquirer service (default: http://127.0.0.1:8001)",
    )
    parser.add_argument(
        "--acquirer-id",
        type=str,
        default="acquirer_alpha",
        help="Target acquirer identifier (default: acquirer_alpha)",
    )
    parser.add_argument(
        "--action",
        type=str,
        default="trigger",
        help=(
            "Action: trigger (engage outage), clear (restore normal), "
            "or pulse (outage for duration, then restore)"
        ),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=15.0,
        help="Duration in seconds for pulse action (default: 15.0)",
    )
    parser.add_argument(
        "--behavior",
        type=str,
        default="RETURN_DECLINE",
        choices=["RETURN_DECLINE", "HTTP_503", "LATENCY_SPIKE"],
        help="Outage failure response behavior (default: RETURN_DECLINE)",
    )
    return parser.parse_args(args)


def set_outage(base_url: str, acquirer_id: str, active: bool, behavior: str) -> None:
    """Send admin POST request to toggle outage state on target acquirer."""
    url = f"{base_url.rstrip('/')}/acquirers/{acquirer_id}/admin/outage"
    payload = OutageToggleRequest(
        active=active,
        behavior=OutageBehavior(behavior),
    )
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(url, json=payload.model_dump())
        if resp.status_code == 200:
            data = resp.json()
            status_str = "ACTIVE" if active else "CLEARED"
            rate = data["effective_success_rate"]
            print(f"[{acquirer_id}] Outage {status_str}: effective_rate={rate:.2f}")
        else:
            print(f"Error updating outage: HTTP {resp.status_code} - {resp.text}", file=sys.stderr)


def main() -> None:
    """Execute outage simulation command."""
    parsed = parse_args(sys.argv[1:])

    if parsed.action == "trigger":
        print(f"Triggering outage on {parsed.acquirer_id} (behavior: {parsed.behavior})...")
        set_outage(parsed.acquirer_url, parsed.acquirer_id, active=True, behavior=parsed.behavior)
    elif parsed.action == "clear":
        print(f"Clearing outage on {parsed.acquirer_id}...")
        set_outage(parsed.acquirer_url, parsed.acquirer_id, active=False, behavior=parsed.behavior)
    elif parsed.action == "pulse":
        print(f"Engaging outage on {parsed.acquirer_id} for {parsed.duration:.1f}s...")
        set_outage(parsed.acquirer_url, parsed.acquirer_id, active=True, behavior=parsed.behavior)
        time.sleep(parsed.duration)
        print(f"Pulse complete. Restoring normal operation on {parsed.acquirer_id}...")
        set_outage(parsed.acquirer_url, parsed.acquirer_id, active=False, behavior=parsed.behavior)


if __name__ == "__main__":
    main()
