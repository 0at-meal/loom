"""Loom All-in-One Local Demo Launcher.

Boots:
1. Acquirer Simulator Service on http://127.0.0.1:8001
2. Loom Router Core (PID + Thompson Sampling) on http://127.0.0.1:8000
3. Background Synthetic Transaction Generator (15 TPS continuous stream)

Usage:
    python scripts/run_demo.py [--tps 15] [--no-traffic]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import threading
import time
import uuid

import httpx
import uvicorn

from acquirer_sim.app import app as sim_app
from router_core.app import app as router_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("loom.demo")


async def traffic_worker(tps: float, stop_event: threading.Event) -> None:
    """Send synthetic transactions to the router at the specified TPS."""
    delay = 1.0 / max(0.1, tps)
    url = "http://127.0.0.1:8000/route"
    logger.info("Traffic generator active: emitting ~%.1f TPS to %s", tps, url)

    async with httpx.AsyncClient(timeout=5.0) as client:
        while not stop_event.is_set():
            tx_id = f"demo_tx_{uuid.uuid4().hex[:8]}"
            amount = round(random.uniform(10.0, 250.0), 2)
            try:
                await client.post(url, json={"transaction_id": tx_id, "amount": amount})
            except (httpx.HTTPError, OSError):
                pass
            await asyncio.sleep(delay)


def run_traffic_loop(tps: float, stop_event: threading.Event) -> None:
    """Run traffic generation event loop in background thread."""
    asyncio.run(traffic_worker(tps, stop_event))


def main() -> None:
    """Launch full Loom demonstration cluster."""
    parser = argparse.ArgumentParser(description="Loom All-in-One Local Demo Launcher")
    parser.add_argument(
        "--tps",
        type=float,
        default=15.0,
        help="Continuous traffic TPS (default: 15)",
    )
    parser.add_argument(
        "--no-traffic",
        action="store_true",
        help="Start servers without auto traffic",
    )
    args = parser.parse_args()

    print("=" * 80)
    print(" LOOM MISSION CONTROL // ALL-IN-ONE DEMO CLUSTER LAUNCHER")
    print("=" * 80)
    print(" 1. Acquirer Simulator:   http://127.0.0.1:8001")
    print(" 2. Loom Router Engine:   http://127.0.0.1:8000")
    print(" 3. WebSocket Telemetry:  ws://127.0.0.1:8000/ws/telemetry")
    print(" 4. React Dashboard UI:   http://localhost:5173")
    print("=" * 80)

    # 1. Start Acquirer Simulator on 8001
    sim_cfg = uvicorn.Config(sim_app, host="127.0.0.1", port=8001, log_level="warning")
    sim_srv = uvicorn.Server(sim_cfg)
    t_sim = threading.Thread(target=sim_srv.run, daemon=True)
    t_sim.start()

    # 2. Start Router on 8000
    router_cfg = uvicorn.Config(router_app, host="127.0.0.1", port=8000, log_level="warning")
    router_srv = uvicorn.Server(router_cfg)
    t_router = threading.Thread(target=router_srv.run, daemon=True)
    t_router.start()

    # Wait for ports to come alive
    time.sleep(1.0)
    print("\n[OK] Backend services running!")
    print("\n>>> NEXT STEP (In a separate terminal):")
    print("    cd dashboard")
    print("    npm run dev")
    print("\n>>> Open your browser to: http://localhost:5173")
    print("=" * 80)

    stop_traffic = threading.Event()
    if not args.no_traffic:
        t_traffic = threading.Thread(
            target=run_traffic_loop,
            args=(args.tps, stop_traffic),
            daemon=True,
        )
        t_traffic.start()
        print(f"[OK] Continuous payment traffic running (~{args.tps} TPS).")
        print("     Use the on-screen Operator Deck to inject outages and watch Loom adapt!")
    else:
        print("[INFO] Traffic generation paused (--no-traffic).")

    print("\nPress Ctrl+C to stop the demo cluster.")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nShutting down Loom demo cluster...")
        stop_traffic.set()
        sim_srv.should_exit = True
        router_srv.should_exit = True
        t_sim.join(timeout=2.0)
        t_router.join(timeout=2.0)
        print("All services stopped.")


if __name__ == "__main__":
    main()
