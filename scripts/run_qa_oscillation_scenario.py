"""QA Scripted Scenario: Trace bandit oscillation and hard-switching near boundary health."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


async def run_scenario() -> None:
    """Execute scripted scenario: Warmup -> Outage on Leader -> Recovery."""
    # Setup simulated acquirers with close health: Alpha @ 95%, Beta @ 94%
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Pinned seed for 100% reproducible QA trace
        router_config = RouterConfig(
            routes=[
                AcquirerRouteConfig(
                    acquirer_id="acquirer_alpha",
                    base_url="http://testserver",
                    state_config=AcquirerStateConfig(
                        alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                    ),
                ),
                AcquirerRouteConfig(
                    acquirer_id="acquirer_beta",
                    base_url="http://testserver",
                    state_config=AcquirerStateConfig(
                        alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                    ),
                ),
            ],
            seed=777,
        )
        router = BanditRouter(config=router_config, http_client=client)

        history: list[dict[str, Any]] = []

        print("=" * 80)
        print("STAGE 1: WARMUP PHASE (50 TRANSACTIONS, HEALTH: Alpha 95% vs Beta 94%)")
        print("=" * 80)

        for i in range(1, 51):
            req = AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0)
            res = await router.route(req)
            history.append(
                {
                    "tx": i,
                    "stage": "WARMUP",
                    "route": res.selected_acquirer,
                    "status": res.status,
                    "samples": res.thompson_samples,
                    "state_a": router.get_state("acquirer_alpha"),
                    "state_b": router.get_state("acquirer_beta"),
                }
            )

        snap_a = router.get_state("acquirer_alpha")
        snap_b = router.get_state("acquirer_beta")
        leader = (
            "acquirer_alpha"
            if snap_a.expected_success_rate >= snap_b.expected_success_rate
            else "acquirer_beta"
        )
        backup = "acquirer_beta" if leader == "acquirer_alpha" else "acquirer_alpha"
        print("End of Warmup:")
        print(
            f"  Alpha: alpha={snap_a.alpha:.2f}, beta={snap_a.beta:.2f}, "
            f"health={snap_a.health_score:.3f}, mean={snap_a.expected_success_rate:.3f}"
        )
        print(
            f"  Beta:  alpha={snap_b.alpha:.2f}, beta={snap_b.beta:.2f}, "
            f"health={snap_b.health_score:.3f}, mean={snap_b.expected_success_rate:.3f}"
        )
        print(f"  Current Leader: {leader}")

        # Warmup allocation
        warmup_routes = [h["route"] for h in history]
        print(
            f"  Warmup split: Alpha={warmup_routes.count('acquirer_alpha')}, "
            f"Beta={warmup_routes.count('acquirer_beta')}"
        )

        print("\n" + "=" * 80)
        print(f"STAGE 2: TRIGGER OUTAGE ON LEADER ({leader}) VIA ADMIN ENDPOINT")
        print("=" * 80)

        outage_resp = await client.post(
            f"/acquirers/{leader}/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )
        assert outage_resp.status_code == 200, f"Outage trigger failed: {outage_resp.text}"
        eff_rate = outage_resp.json()["effective_success_rate"]
        print(f"Outage successfully activated on {leader} (effective_rate={eff_rate})")

        print("\n" + "=" * 80)
        print("STAGE 3: OUTAGE EXECUTION (50 TRANSACTIONS: TX 51 TO 100)")
        print("=" * 80)
        header = (
            f"{'Tx':<4} | {'Picked Route':<15} | {'Status':<10} | "
            f"{'Thompson Samples':<28} | {'Belief Means':<24} | {'Switch?'}"
        )
        print(header)
        print("-" * 95)

        prev_route = warmup_routes[-1]
        outage_flips = 0

        for i in range(51, 101):
            req = AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0)
            res = await router.route(req)
            curr_route = res.selected_acquirer
            is_flip = curr_route != prev_route
            if is_flip:
                outage_flips += 1
            flip_tag = "<--- FLIP!" if is_flip else ""

            s_a = router.get_state("acquirer_alpha")
            s_b = router.get_state("acquirer_beta")

            history.append(
                {
                    "tx": i,
                    "stage": "OUTAGE",
                    "route": curr_route,
                    "status": res.status,
                    "samples": res.thompson_samples,
                    "state_a": s_a,
                    "state_b": s_b,
                    "is_flip": is_flip,
                }
            )

            s_alpha = res.thompson_samples["acquirer_alpha"]
            s_beta = res.thompson_samples["acquirer_beta"]
            samples_str = f"A={s_alpha:.4f}, B={s_beta:.4f}"
            means_str = f"A={s_a.expected_success_rate:.3f}, B={s_b.expected_success_rate:.3f}"
            row = (
                f"{i:<4} | {curr_route:<15} | {res.status:<10} | "
                f"{samples_str:<28} | {means_str:<24} | {flip_tag}"
            )
            print(row)
            prev_route = curr_route

        outage_routes = [h["route"] for h in history[50:100]]
        print("-" * 95)
        print("Outage Phase Summary:")
        print(f"  Total flips during outage: {outage_flips}")
        leader_txs = outage_routes.count(leader)
        backup_txs = outage_routes.count(backup)
        print(f"  Total transactions routed to dead leader ({leader}): {leader_txs}")
        print(f"  Total transactions routed to backup ({backup}): {backup_txs}")

        # Find first flip to backup
        first_backup_idx = next(i for i, r in enumerate(outage_routes, start=51) if r == backup)
        fails_before = first_backup_idx - 50
        print(f"  First switch to backup occurred at Tx {first_backup_idx} ({fails_before} fails)")

        # Analyze boundary zone
        print("\n" + "=" * 80)
        print("STAGE 4: RECOVERY PHASE (50 TRANSACTIONS: TX 101 TO 150)")
        print("=" * 80)

        rec_resp = await client.post(
            f"/acquirers/{leader}/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )
        assert rec_resp.status_code == 200
        rec_rate = rec_resp.json()["effective_success_rate"]
        print(f"Outage cleared on {leader} (effective_rate={rec_rate})")

        rec_flips = 0
        for i in range(101, 151):
            req = AuthorizeRequest(transaction_id=f"tx_recovery_{i}", amount=50.0)
            res = await router.route(req)
            curr_route = res.selected_acquirer
            is_flip = curr_route != prev_route
            if is_flip:
                rec_flips += 1

            s_a = router.get_state("acquirer_alpha")
            s_b = router.get_state("acquirer_beta")

            history.append(
                {
                    "tx": i,
                    "stage": "RECOVERY",
                    "route": curr_route,
                    "status": res.status,
                    "samples": res.thompson_samples,
                    "state_a": s_a,
                    "state_b": s_b,
                    "is_flip": is_flip,
                }
            )
            prev_route = curr_route

        rec_routes = [h["route"] for h in history[100:150]]
        print("Recovery Phase Summary:")
        print(f"  Total flips during recovery: {rec_flips}")
        print(f"  Leader ({leader}) traffic: {rec_routes.count(leader)}")
        print(f"  Backup ({backup}) traffic: {rec_routes.count(backup)}")

        print("\n" + "=" * 80)
        print("PIPELINE AUDIT: REAL BUGS VS INTENDED INSTABILITY")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_scenario())
