"""Tests for the compare_psr benchmark CLI: seeds, multi-seed statistics, JSON output, DB safety."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from router_core.router import BanditRouter
from scripts.compare_psr import (
    COMPARISONS,
    DEFAULT_BASE_DB,
    DEFAULT_LOOM_DB,
    build_parser,
    execute_scenario,
    format_report,
    main,
    summarize_paired,
)


def test_parser_defaults_and_flags() -> None:
    """Every documented flag parses; defaults keep the data layer's ledger name untouched."""
    defaults = build_parser().parse_args([])
    assert defaults.seed == 42
    assert defaults.loom_seed == 777
    assert defaults.n_seeds == 1
    assert defaults.out_json is None
    assert defaults.alpha_prior == 4.0
    assert defaults.base_db == DEFAULT_BASE_DB
    assert defaults.loom_db == DEFAULT_LOOM_DB
    assert "loom_metrics.db" not in (defaults.base_db, defaults.loom_db)

    args = build_parser().parse_args(
        ["--seed", "7", "--loom-seed", "9", "--n-seeds", "5", "--out-json", "x.json"]
    )
    assert (args.seed, args.loom_seed, args.n_seeds, args.out_json) == (7, 9, 5, "x.json")


def test_summarize_paired_counts_and_ci() -> None:
    """Mean, t-based CI and wins/ties/losses for a known set of differences."""
    s = summarize_paired([0.02, 0.0, -0.01, 0.03])
    assert s["n"] == 4
    assert s["mean_diff_pp"] == pytest.approx(1.0)
    assert (s["wins"], s["ties"], s["losses"]) == (2, 1, 1)
    assert s["ci95_low_pp"] < s["mean_diff_pp"] < s["ci95_high_pp"]
    # sd = 0.018257, t(0.975, 3) = 3.18245 -> half-width 2.905 pp
    assert s["ci95_high_pp"] - s["mean_diff_pp"] == pytest.approx(2.905, abs=1e-3)


async def test_refuses_data_layer_default_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The benchmark deletes its DB before each run, so it must never touch loom_metrics.db."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / "loom_metrics.db"
    ledger.write_bytes(b"keep me")
    with pytest.raises(SystemExit, match="default metrics ledger"):
        await execute_scenario("loom", db_path="loom_metrics.db")
    assert ledger.read_bytes() == b"keep me"


async def test_seed_changes_outcomes_and_memory_db_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--seed and --loom-seed reach the simulator and router; ':memory:' leaves no files."""
    monkeypatch.chdir(tmp_path)
    a = await execute_scenario("loom", db_path=":memory:", seed=42, loom_seed=777)
    b = await execute_scenario("loom", db_path=":memory:", seed=42, loom_seed=777)
    c = await execute_scenario("loom", db_path=":memory:", seed=52, loom_seed=778)
    assert a["global_metrics"]["psr"] == b["global_metrics"]["psr"]
    assert a["outage_flips"] == b["outage_flips"]
    signature = (a["global_metrics"]["authorized_count"], a["outage_flips"], a["max_step_delta"])
    other = (c["global_metrics"]["authorized_count"], c["outage_flips"], c["max_step_delta"])
    assert signature != other
    assert list(tmp_path.iterdir()) == []


async def test_gray_failure_and_raw_bandit_configurations() -> None:
    """Gray failure keeps Alpha partly working; the raw bandit has no smoothed allocation."""
    gray = await execute_scenario("baseline", db_path=":memory:", gray_rate=0.60)
    hard = await execute_scenario("baseline", db_path=":memory:")
    assert gray["outage_psr"] != hard["outage_psr"]
    raw = await execute_scenario("raw", db_path=":memory:")
    assert raw["max_step_delta"] == 1.0
    with pytest.raises(ValueError, match="unknown router_type"):
        await execute_scenario("nope", db_path=":memory:")


def test_report_header_prints_actual_m_and_n() -> None:
    """The report header reflects --threshold-m and --cooldown-n instead of a fixed string."""
    run = {
        "global_metrics": {
            "total_transactions": 150,
            "authorized_count": 120,
            "declined_count": 30,
            "psr": 0.8,
            "avg_routing_latency_ms": 0.01,
        },
        "warmup_psr": 0.9,
        "outage_psr": 0.7,
        "recovery_psr": 0.8,
        "outage_failures_alpha": 3,
        "max_step_delta": 1.0,
        "outage_flips": 2,
    }
    report = format_report(run, run, threshold_m=5, cooldown_n=12)
    assert "Threshold M=5, Cooldown N=12" in report
    assert "M=3" not in report


async def test_single_seed_cli_writes_dbs_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-seed mode logs to the named DBs and writes the headline numbers as JSON."""
    monkeypatch.chdir(tmp_path)
    await main(
        [
            "--threshold-m",
            "1",
            "--base-db",
            "b.db",
            "--loom-db",
            "l.db",
            "--out-json",
            "out.json",
        ]
    )
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["mode"] == "single_seed"
    assert data["threshold_m"] == 1
    assert data["loom"]["global_metrics"]["total_transactions"] == 150
    assert data["command"].startswith("python scripts/compare_psr.py")
    assert (tmp_path / "b.db").exists() and (tmp_path / "l.db").exists()


async def test_multi_seed_cli_is_paired_and_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--n-seeds N reports every comparison over N paired seeds and writes no DB files."""
    monkeypatch.chdir(tmp_path)
    argv = ["--n-seeds", "2", "--seed", "42", "--loom-seed", "777", "--out-json", "a.json"]
    await main(argv)
    await main([*argv[:-1], "b.json"])
    a = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert a["comparisons"] == b["comparisons"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json", "b.json"]

    assert a["mode"] == "multi_seed"
    assert [(r["sim_seed"], r["loom_seed"]) for r in a["rows"]] == [(42, 777), (52, 778)]
    assert set(a["comparisons"]) == {key for key, *_ in COMPARISONS}
    for cmp in a["comparisons"].values():
        assert cmp["n"] == 2
        assert cmp["wins"] + cmp["ties"] + cmp["losses"] == 2
        diffs = [r[cmp["first"]]["psr"] - r[cmp["second"]]["psr"] for r in a["rows"]]
        assert cmp["mean_diff_pp"] == pytest.approx(sum(diffs) / 2 * 100)

    # The k = 0 pair is the single-seed default run (seed 42 / 777).
    single = await execute_scenario("loom", db_path=":memory:", seed=42, loom_seed=777)
    assert a["rows"][0]["loom"]["psr"] == single["global_metrics"]["psr"]


async def test_n_seeds_must_be_positive() -> None:
    with pytest.raises(SystemExit):
        await main(["--n-seeds", "0"])


async def test_loom_runs_on_a_virtual_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loom's decay is timed by the benchmark's virtual 15 TPS clock, not the machine's."""
    import scripts.compare_psr as compare_psr

    readings: list[float] = []
    real_router = BanditRouter

    def recording_router(*args: Any, **kwargs: Any) -> BanditRouter:
        clock = kwargs["clock"]
        assert callable(clock)

        def spy() -> float:
            value = float(clock())
            readings.append(value)
            return value

        kwargs["clock"] = spy
        return real_router(*args, **kwargs)

    monkeypatch.setattr(compare_psr, "BanditRouter", recording_router)
    await execute_scenario("loom", ":memory:", 2, 2, 2)
    # Six transactions at 15 TPS: the clock ends at 6/15 s whatever the wall time.
    assert max(readings) == pytest.approx(6 / compare_psr.BENCH_TPS)


async def test_alpha_prior_flag_reaches_loom_and_recovery_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--alpha-prior changes Loom's prior only; the report includes recovery dispatches."""
    monkeypatch.chdir(tmp_path)
    await main(["--n-seeds", "2", "--alpha-prior", "1", "--out-json", "p1.json"])
    assert "technical prior Beta(1, 1)" in capsys.readouterr().out
    await main(["--n-seeds", "2", "--alpha-prior", "9", "--out-json", "p9.json"])
    p1 = json.loads((tmp_path / "p1.json").read_text(encoding="utf-8"))
    p9 = json.loads((tmp_path / "p9.json").read_text(encoding="utf-8"))
    assert (p1["alpha_prior"], p9["alpha_prior"]) == (1.0, 9.0)
    for cfg in ("static_m1", "static_m3", "static_m5", "static_m3_gray"):
        assert p1["configs"][cfg] == p9["configs"][cfg]
    assert p1["configs"]["loom"] != p9["configs"]["loom"]
    expected = sum(r["loom"]["recovery_txs_alpha"] for r in p1["rows"]) / 2
    assert p1["configs"]["loom"]["mean_recovery_txs_alpha"] == pytest.approx(expected)
