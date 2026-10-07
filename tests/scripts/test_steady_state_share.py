"""Tests for scripts/steady_state_share.py (AUDIT F-03 measurement)."""

from __future__ import annotations

import pytest

from scripts.steady_state_share import main, worse_share


async def test_share_is_reproducible_and_bounded() -> None:
    """Same seed, same share; the share is a fraction of the counted payments."""
    first = await worse_share(0.90, seed=0, n_tx=60, skip=20, config="benchmark")
    again = await worse_share(0.90, seed=0, n_tx=60, skip=20, config="benchmark")
    assert first == again
    assert 0.0 <= first <= 1.0


async def test_cli_prints_one_line_per_rate(capsys: pytest.CaptureFixture[str]) -> None:
    """The report lists each requested Beta rate."""
    await main(["--beta-rates", "0.9", "0.7", "--seeds", "2", "--n-tx", "40", "--skip", "10"])
    out = capsys.readouterr().out
    assert "Beta 90%:" in out
    assert "Beta 70%:" in out


async def test_skip_must_leave_payments_to_count() -> None:
    """A skip at or beyond n-tx is rejected."""
    with pytest.raises(SystemExit):
        await main(["--n-tx", "10", "--skip", "10"])
