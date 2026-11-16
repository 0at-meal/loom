"""HTTP simulator options added in Stage 0: CRN draws, background 503s, HTTP_429 outages.

All are off by default; the golden traces (tests/router_core/test_policy_golden.py) and the
compare_psr tests show default runs are unchanged.
"""

from __future__ import annotations

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.crn import ISSUER, crn_uniform
from acquirer_sim.models import (
    AcquirerConfig,
    AuthorizeRequest,
    LatencyConfig,
    OutageBehavior,
    OutageToggleRequest,
)
from acquirer_sim.simulator import AcquirerOutageHttpException, AcquirerSimulator

NO_LATENCY = LatencyConfig(base_ms=0.0, jitter_ms=0.0)


def _req(i: int) -> AuthorizeRequest:
    return AuthorizeRequest(transaction_id=f"tx{i}", amount=10.0)


async def test_crn_mode_outcome_depends_only_on_transaction() -> None:
    """With crn_seed, a payment's result is the same in any order and on any acquirer."""
    cfg = AcquirerConfig(acquirer_id="a", base_success_rate=0.7, latency=NO_LATENCY)
    sim1 = AcquirerSimulator(cfg, crn_seed=11)
    sim2 = AcquirerSimulator(cfg.model_copy(update={"acquirer_id": "b"}), crn_seed=11)
    forward = [(await sim1.execute_authorization(_req(i))).authorized for i in range(200)]
    backward = [
        (await sim2.execute_authorization(_req(i))).authorized for i in reversed(range(200))
    ]
    assert forward == list(reversed(backward))
    assert forward == [crn_uniform(11, f"tx{i}", ISSUER) < 0.7 for i in range(200)]


async def test_background_technical_errors_raise_503() -> None:
    """technical_error_rate fails that share of requests with a 503 exception."""
    cfg = AcquirerConfig(
        acquirer_id="a", base_success_rate=1.0, latency=NO_LATENCY, technical_error_rate=0.3
    )
    sim = AcquirerSimulator(cfg, crn_seed=5)
    errors = 0
    for i in range(2000):
        try:
            await sim.execute_authorization(_req(i))
        except AcquirerOutageHttpException as exc:
            assert exc.status_code == 503
            errors += 1
    assert errors / 2000 == pytest.approx(0.3, abs=0.03)
    sim.set_technical_error_rate(0.0)
    assert (await sim.execute_authorization(_req(1))).authorized
    with pytest.raises(ValueError):
        sim.set_technical_error_rate(1.5)


async def test_http_429_outage_answers_429() -> None:
    """The HTTP_429 outage behaviour returns 429 Too Many Requests over HTTP."""
    app = create_app(default_acquirers=["a"], default_latency=NO_LATENCY, seed=1)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await client.post(
            "/acquirers/a/admin/outage",
            json=OutageToggleRequest(active=True, behavior=OutageBehavior.HTTP_429).model_dump(
                mode="json"
            ),
        )
        resp = await client.post("/acquirers/a/authorize", json=_req(1).model_dump())
        assert resp.status_code == 429
        await client.post(
            "/acquirers/a/admin/outage", json=OutageToggleRequest(active=False).model_dump()
        )
        resp = await client.post("/acquirers/a/authorize", json=_req(2).model_dump())
        assert resp.status_code == 200
