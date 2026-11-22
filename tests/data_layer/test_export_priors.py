"""Stage 2 warm start: ``data_layer.cli export-priors`` and the router's ``--priors`` flag."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from data_layer.cli import cmd_export_priors, cmd_init_db, export_priors, main
from router_core.server import build_router_config, load_warm_priors, parse_args

A, B = "acquirer_alpha", "acquirer_beta"
NOW = 1_000_000.0


def _ledger(tmp_path: Path, rows: list[tuple[str, float, str | None]]) -> str:
    """A schema-initialised ledger with (acquirer, timestamp, outcome) rows."""
    path = str(tmp_path / "ledger.db")
    assert cmd_init_db(db_path=path) == 0
    conn = sqlite3.connect(path)
    for i, (acq, ts, outcome) in enumerate(rows):
        ok = outcome == "AUTHORIZED"
        conn.execute(
            """INSERT INTO transactions (transaction_id, timestamp, chosen_acquirer,
               allocation_weight, status, authorized, success, routing_latency_ms,
               acquirer_latency_ms, total_latency_ms, smoothed_allocation_json,
               thompson_samples_json, outcome)
               VALUES (?, ?, ?, 0.5, ?, ?, ?, 0, 0, 0, '{}', '{}', ?)""",
            (f"tx{i}", ts, acq, "AUTHORIZED" if ok else "DECLINED", int(ok), int(ok), outcome),
        )
    conn.commit()
    conn.close()
    return path


def _rows() -> list[tuple[str, float, str | None]]:
    rows: list[tuple[str, float, str | None]] = []
    rows += [(A, NOW - 600 + i, "AUTHORIZED") for i in range(90)]
    rows += [(A, NOW - 500 + i, "ISSUER_DECLINE") for i in range(10)]
    rows += [(A, NOW - 400 + i, "TECHNICAL_FAILURE") for i in range(50)]  # not approval data
    rows += [(B, NOW - 300 + i, "AUTHORIZED") for i in range(40)]
    rows += [(B, NOW - 200 + i, "ISSUER_DECLINE") for i in range(10)]
    rows += [(B, NOW - 7200, "ISSUER_DECLINE")]  # outside a 1 h window
    rows += [(A, NOW - 100, None)]  # router-side error
    return rows


def test_export_counts_issuer_outcomes_only_within_the_window(tmp_path: Path) -> None:
    """Approvals and issuer declines per acquirer; technical failures and old rows excluded."""
    path = _ledger(tmp_path, _rows())
    out = export_priors(path, hours=1.0, now=NOW)
    assert out["acquirers"] == {
        A: {"approved": 90, "issuer_declined": 10, "observations": 100, "approval_rate": 0.9},
        B: {"approved": 40, "issuer_declined": 10, "observations": 50, "approval_rate": 0.8},
    }
    assert out["window_sec"] == pytest.approx(600.0)  # span of the counted rows


def test_export_never_writes_to_the_ledger(tmp_path: Path) -> None:
    """The ledger is append-only: export opens it read-only and leaves the file unchanged."""
    path = _ledger(tmp_path, _rows())
    before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    export_priors(path, hours=24.0, now=NOW)
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before


def test_export_errors_are_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing ledger or a bad window exits 1 with a message."""
    assert cmd_export_priors(db_path=str(tmp_path / "missing.db")) == 1
    path = _ledger(tmp_path, _rows())
    assert cmd_export_priors(db_path=path, hours=0.0) == 1
    assert "Could not export priors" in capsys.readouterr().err


def test_cli_writes_json_that_the_router_loads(tmp_path: Path) -> None:
    """export-priors --out feeds --priors: each listed route's approval belief is seeded."""
    path = _ledger(tmp_path, _rows())
    priors = tmp_path / "priors.json"
    assert main(["export-priors", "--db-path", path, "--hours", "1e9", "--out", str(priors)]) == 0
    data = json.loads(priors.read_text(encoding="utf-8"))
    assert set(data["acquirers"]) == {A, B}

    loaded = load_warm_priors(str(priors))
    assert loaded[B]["observations"] == 51
    cfg = build_router_config(
        parse_args(["--acquirers", A, B, "acquirer_gamma", "--priors", str(priors)])
    )
    states = {r.acquirer_id: r.state_config for r in cfg.routes}
    assert states[A].warm_approval_rate == pytest.approx(0.9)
    assert states[A].warm_observations == 100
    assert states[A].warm_window_sec == pytest.approx(data["window_sec"])
    assert states["acquirer_gamma"].warm_approval_rate is None  # not in the ledger: cold


def test_all_approved_ledger_still_gives_a_valid_prior(tmp_path: Path) -> None:
    """A rate of exactly 1 is pulled inside (0, 1) so the Beta stays proper."""
    path = _ledger(tmp_path, [(A, NOW - 10 + i, "AUTHORIZED") for i in range(5)])
    priors = tmp_path / "p.json"
    priors.write_text(json.dumps(export_priors(path, hours=1.0, now=NOW)), encoding="utf-8")
    rate = load_warm_priors(str(priors))[A]["rate"]
    assert 0.0 < rate < 1.0
