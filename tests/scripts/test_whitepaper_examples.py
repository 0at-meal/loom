"""Pin the figures quoted in docs/WHITEPAPER_L1.md to scripts/whitepaper_examples.py."""

from __future__ import annotations

import pytest

from scripts.whitepaper_examples import main


async def test_whitepaper_figures_are_reproduced(capsys: pytest.CaptureFixture[str]) -> None:
    """If one of these lines changes, update the whitepaper in the same commit."""
    await main()
    out = capsys.readouterr().out
    expected = [
        "draw 1: A=0.936 B=0.367 winner=A",
        "draw 2: A=0.910 B=0.875 winner=A",
        "B wins 5642 of 100,000 pairs = 5.6%",
        "F: alpha=3.8816 beta=2.0000 mean=0.660",
        "gamma=0.98: half-life 34.3 updates, alpha ceiling 51",
        "after 1 failures 0.909 after 3 failures 0.825 after 5 failures 0.749 "
        "after 10 failures 0.590",
        "gamma=0.98: after 200 successes Beta(50.1,1.0) mean=0.980; after 10 failures 0.802",
        "arm at 100% of 15 TPS: steady-state memory 49.8 observations",
        "arm at 97% of 15 TPS: steady-state memory 48.3 observations",
        "arm at 3% of 15 TPS: steady-state memory 1.5 observations",
        "all 15 TPS: after 200 successes Beta(53.4,1.0) mean=0.982; after 1 failures 0.963 "
        "after 3 failures 0.928 after 5 failures 0.893 after 10 failures 0.814",
        "idle arm after 20 failures, 2.3 s later: Beta(4.00,11.00) mean=0.267",
        "idle arm after 20 failures, 10.0 s later: Beta(4.00,1.98) mean=0.669",
        "idle arm after 20 failures, 30.0 s later: Beta(4.00,1.00) mean=0.800",
        "300 approvals then 10 issuer declines at 15 TPS: technical 0.982 approval 0.961 "
        "expected PSR 0.943",
        "300 approvals then 10 technical failures at 15 TPS: technical 0.816 approval 0.996 "
        "expected PSR 0.813",
        "at 15 TPS that is 3.4 s for an acquirer at 97% of traffic and 111 s for one at the 3% "
        "floor",
        "step 2: e_A=+0.4375 P=+0.0525 I=+0.0047 D=-0.0156 w_A=0.6041",
        "step 5: e_A=+0.3169 P=+0.0380 I=+0.0050 D=-0.0092 w_A=0.7169",
        "(0.98, 0.0, 0.1) -> (0.878, 0.03, 0.092)",
        "sequence A B A A B A A A B A; counts A=7 B=3",
        "A = [5, 7, 9, 9, 6, 8, 9, 6]",
        "V=10: 0.095, V=100: 0.632, V=250: 0.918, V=1000: 1.000",
        "static M=3               PSR  92.00% (138/150) outage  82.0% fail_alpha  4",
        "static M=1               PSR  76.00% (114/150) outage  38.0% fail_alpha 30",
        "Loom with PID            PSR  86.00% (129/150) outage  72.0% fail_alpha 11 "
        "dw_max  11.65% outage_flips 15 recovery_alpha_tx 6",
        "Loom raw bandit          PSR  91.33% (137/150) outage  88.0% fail_alpha  3 "
        "dw_max 100.00% outage_flips  5 recovery_alpha_tx 13",
        "Loom with PID, gray 60%  PSR  89.33% (134/150) outage  84.0% fail_alpha  7 "
        "dw_max  11.57% outage_flips 31",
        "Alpha share at Tx 50-62: 0.68, 0.73, 0.63, 0.69, 0.71, 0.74, 0.64, 0.71, 0.60, 0.55",
        "Alpha share at Tx 150: 0.14",
    ]
    for line in expected:
        assert line in out, line
