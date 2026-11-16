"""Golden parity: the policy extraction leaves every routing decision bit-for-bit unchanged.

The reference was recorded from the pre-extraction router (see ``loom_golden.py``) at
simulator seed 42 and Loom seed 777, covering PID deficit and stochastic actuation, the raw
bandit, value-scaled protection and the static breaker at M=1 and M=3.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from tests.router_core.loom_golden import CASES, GOLDEN_PATH, run_case


@pytest.fixture(scope="module")
def golden() -> dict[str, list[dict[str, Any]]]:
    """The recorded reference traces."""
    data: dict[str, list[dict[str, Any]]] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("name", sorted(CASES))
async def test_decisions_match_golden_trace(
    name: str, golden: dict[str, list[dict[str, Any]]], caplog: pytest.LogCaptureFixture
) -> None:
    """Every payment's acquirer, outcome, samples, allocations and beliefs match exactly."""
    caplog.set_level(logging.ERROR)
    trace = await run_case(name)
    expected = golden[name]
    assert len(trace) == len(expected)
    for got, want in zip(trace, expected, strict=True):
        assert got == want, f"{name}: first divergence at payment {want['i']}"
