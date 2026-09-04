"""Automated test suite for Loom Phase 5 Data Layer DevOps CLI tooling."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from data_layer.cli import (
    cmd_init_db,
    cmd_inspect_state,
    cmd_ping,
    cmd_reset_demo,
    cmd_status,
    create_parser,
    main,
)
from data_layer.redis_state import RedisStateStore
from router_core.state import AcquirerStateConfig, AcquirerStateSnapshot


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """Fixture providing an isolated FakeRedis client."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """Fixture providing a temporary SQLite database file path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if os.path.exists(path):
        os.unlink(path)
    yield path
    # Cleanup after test
    for p in [path, f"{path}-wal", f"{path}-shm"]:
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass


class TestCLIInitDB:
    """Tests for `python -m data_layer.cli init-db`."""

    def test_init_db_creates_tables_and_triggers(self, temp_db_path: str) -> None:
        """Verify schema.sql is executed and establishes tables and triggers."""
        code = cmd_init_db(db_path=temp_db_path)
        assert code == 0
        assert os.path.exists(temp_db_path)

        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        # Verify tables
        cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('transactions', 'acquirer_outcomes');"
        )
        tables = {r[0] for r in cursor.fetchall()}
        assert "transactions" in tables
        assert "acquirer_outcomes" in tables

        # Verify triggers
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'prevent_%';"
        )
        triggers = {r[0] for r in cursor.fetchall()}
        assert "prevent_transactions_update" in triggers
        assert "prevent_transactions_delete" in triggers
        assert "prevent_acquirer_outcomes_update" in triggers
        assert "prevent_acquirer_outcomes_delete" in triggers
        conn.close()

    def test_init_db_is_idempotent(self, temp_db_path: str) -> None:
        """Verify running init-db multiple times on existing database succeeds."""
        assert cmd_init_db(db_path=temp_db_path) == 0
        assert cmd_init_db(db_path=temp_db_path) == 0

    def test_init_db_invalid_path_fails(self) -> None:
        """Verify init-db returns 1 when targeting an unwritable location."""
        invalid_path = "/nonexistent_dir_foo_bar_xyz/123/456/db.sqlite"
        mock_err = sqlite3.OperationalError("Unable to open database")
        with patch("sqlite3.connect", side_effect=mock_err):
            code = cmd_init_db(db_path=invalid_path)
            assert code == 1


class TestCLIPing:
    """Tests for `python -m data_layer.cli ping`."""

    def test_ping_all_healthy(self, fake_redis: fakeredis.FakeRedis, temp_db_path: str) -> None:
        """Verify ping returns exit code 0 when both Redis and SQLite are operational."""
        cmd_init_db(db_path=temp_db_path)
        code = cmd_ping(redis_client=fake_redis, db_path=temp_db_path)
        assert code == 0

    def test_ping_redis_down(self, temp_db_path: str) -> None:
        """Verify ping returns exit code 1 when Redis raises an exception."""
        cmd_init_db(db_path=temp_db_path)
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = ConnectionError("Redis server unavailable")

        code = cmd_ping(redis_client=mock_redis, db_path=temp_db_path)
        assert code == 1

    def test_ping_sqlite_down(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify ping returns exit code 1 when SQLite fails query execution."""
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Disk I/O error")):
            code = cmd_ping(redis_client=fake_redis, db_path="/invalid/path.db")
            assert code == 1


class TestCLIStatus:
    """Tests for `python -m data_layer.cli status`."""

    def test_status_empty_env(
        self,
        fake_redis: fakeredis.FakeRedis,
        temp_db_path: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify status output on fresh/empty environment."""
        cmd_init_db(db_path=temp_db_path)
        code = cmd_status(redis_client=fake_redis, db_path=temp_db_path)
        assert code == 0

        captured = capsys.readouterr().out
        assert "Loom Data Layer Status Report" in captured
        assert "Logged Transactions:0" in captured
        assert "Current Overall PSR:0.00%" in captured

    def test_status_populated_env(
        self,
        fake_redis: fakeredis.FakeRedis,
        temp_db_path: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify status output reflects populated Redis keys and SQLite records."""
        cmd_init_db(db_path=temp_db_path)

        # Populate Redis state
        store = RedisStateStore(redis_client=fake_redis)
        store.hydrate_or_init("stripe", AcquirerStateConfig())
        store.hydrate_or_init("adyen", AcquirerStateConfig())

        # Populate SQLite records
        conn = sqlite3.connect(temp_db_path)
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, timestamp, chosen_acquirer, allocation_weight,
                status, authorized, success, routing_latency_ms, acquirer_latency_ms,
                total_latency_ms, smoothed_allocation_json, thompson_samples_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("tx-1", 1000.0, "stripe", 0.6, "AUTHORIZED", 1, 1, 0.5, 50.0, 50.5, "{}", "{}"),
        )
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, timestamp, chosen_acquirer, allocation_weight,
                status, authorized, success, routing_latency_ms, acquirer_latency_ms,
                total_latency_ms, smoothed_allocation_json, thompson_samples_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("tx-2", 1001.0, "adyen", 0.4, "DECLINED", 0, 0, 0.4, 45.0, 45.4, "{}", "{}"),
        )
        conn.commit()
        conn.close()

        code = cmd_status(redis_client=fake_redis, db_path=temp_db_path)
        assert code == 0

        captured = capsys.readouterr().out
        assert "Active Acquirers:   2 ['adyen', 'stripe']" in captured
        assert "Logged Transactions:2" in captured
        assert "Authorized Count:   1" in captured
        assert "Current Overall PSR:50.00%" in captured


class TestCLIInspectState:
    """Tests for `python -m data_layer.cli inspect-state`."""

    def test_inspect_empty(
        self,
        fake_redis: fakeredis.FakeRedis,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify inspect-state handles empty Redis gracefully."""
        code = cmd_inspect_state(redis_client=fake_redis, as_json=False)
        assert code == 0
        assert "No acquirer states found" in capsys.readouterr().out

    def test_inspect_populated_json(
        self,
        fake_redis: fakeredis.FakeRedis,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify inspect-state produces valid JSON with Beta parameters."""
        store = RedisStateStore(redis_client=fake_redis)
        snap = AcquirerStateSnapshot(
            acquirer_id="stripe",
            alpha=12.5,
            beta=2.5,
            health_score=0.95,
            success_count=15,
            failure_count=2,
            total_count=17,
            last_updated_at=1700000000.0,
            alpha_prior=1.0,
            beta_prior=1.0,
        )
        store.write_snapshot(snap, decay_factor=0.98)

        code = cmd_inspect_state(redis_client=fake_redis, as_json=True)
        assert code == 0

        output = json.loads(capsys.readouterr().out)
        assert "stripe" in output
        assert output["stripe"]["alpha"] == 12.5
        assert output["stripe"]["beta"] == 2.5
        assert output["stripe"]["health_score"] == 0.95
        assert output["stripe"]["thompson_mean"] == round(12.5 / 15.0, 4)

    def test_inspect_filter_by_acquirer(
        self,
        fake_redis: fakeredis.FakeRedis,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify inspect-state --acquirer filters single acquirer."""
        store = RedisStateStore(redis_client=fake_redis)
        store.hydrate_or_init("stripe", AcquirerStateConfig())
        store.hydrate_or_init("adyen", AcquirerStateConfig())

        code = cmd_inspect_state(redis_client=fake_redis, acquirer_id="adyen")
        assert code == 0
        captured = capsys.readouterr().out
        assert "Acquirer: adyen" in captured
        assert "Acquirer: stripe" not in captured


class TestCLIResetDemo:
    """Tests for `python -m data_layer.cli reset-demo`."""

    def test_reset_demo_with_force(
        self,
        fake_redis: fakeredis.FakeRedis,
        temp_db_path: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify reset-demo wipes Redis keys and re-creates SQLite tables without aborts."""
        cmd_init_db(db_path=temp_db_path)

        # Populate Redis and SQLite
        store = RedisStateStore(redis_client=fake_redis)
        store.hydrate_or_init("stripe", AcquirerStateConfig())

        conn = sqlite3.connect(temp_db_path)
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, timestamp, chosen_acquirer, allocation_weight,
                status, authorized, success, routing_latency_ms, acquirer_latency_ms,
                total_latency_ms, smoothed_allocation_json, thompson_samples_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("tx-demo-1", 1000.0, "stripe", 1.0, "AUTHORIZED", 1, 1, 0.5, 50.0, 50.5, "{}", "{}"),
        )
        conn.commit()
        conn.close()

        # Confirm data is present
        assert len(fake_redis.keys("*")) > 0

        # Execute reset with force=True
        code = cmd_reset_demo(
            redis_client=fake_redis,
            db_path=temp_db_path,
            force=True,
        )
        assert code == 0

        # Verify Redis keys are removed
        assert len(fake_redis.keys("*")) == 0

        # Verify SQLite has 0 rows and triggers are intact
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM transactions;")
        assert cursor.fetchone()[0] == 0

        # Verify triggers are active by checking trigger count
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger';")
        assert cursor.fetchone()[0] >= 4

        # Verify that an attempt to delete an existing row raises abort trigger error
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, timestamp, chosen_acquirer, allocation_weight,
                status, authorized, success, routing_latency_ms, acquirer_latency_ms,
                total_latency_ms, smoothed_allocation_json, thompson_samples_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "tx-demo-test",
                1000.0,
                "stripe",
                1.0,
                "AUTHORIZED",
                1,
                1,
                0.5,
                50.0,
                50.5,
                "{}",
                "{}",
            ),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM transactions WHERE transaction_id = 'tx-demo-test';")

        conn.close()

        captured = capsys.readouterr().out
        assert "[DEMO READY]" in captured

    def test_reset_demo_aborted_by_prompt(
        self,
        fake_redis: fakeredis.FakeRedis,
        temp_db_path: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify reset-demo is safely aborted when user declines confirmation."""
        with patch("builtins.input", return_value="n"):
            code = cmd_reset_demo(
                redis_client=fake_redis,
                db_path=temp_db_path,
                force=False,
            )
            assert code == 0
            assert "[ABORT]" in capsys.readouterr().out


class TestCLIMainDispatcher:
    """Tests for main() and argument parsing."""

    def test_parser_creation(self) -> None:
        """Verify create_parser registers all subcommands."""
        parser = create_parser()
        assert parser.parse_args(["init-db"]).command == "init-db"
        assert parser.parse_args(["ping"]).command == "ping"
        assert parser.parse_args(["status"]).command == "status"
        assert parser.parse_args(["inspect-state"]).command == "inspect-state"
        assert parser.parse_args(["reset-demo"]).command == "reset-demo"

    def test_main_no_args_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify main with no args prints help and returns 0."""
        code = main([])
        assert code == 0
        assert "Loom Phase 5 Data Layer DevOps" in capsys.readouterr().out

    @patch("data_layer.cli.cmd_init_db", return_value=0)
    def test_main_dispatches_init_db(self, mock_cmd: MagicMock) -> None:
        """Verify dispatching init-db."""
        code = main(["init-db", "--db-path", "test.db"])
        assert code == 0
        mock_cmd.assert_called_once_with(db_path="test.db")

    @patch("data_layer.cli.cmd_ping", return_value=0)
    def test_main_dispatches_ping(self, mock_cmd: MagicMock) -> None:
        """Verify dispatching ping."""
        code = main(["ping", "--db-path", "test.db"])
        assert code == 0
        mock_cmd.assert_called_once_with(db_path="test.db")

    @patch("data_layer.cli.cmd_status", return_value=0)
    def test_main_dispatches_status(self, mock_cmd: MagicMock) -> None:
        """Verify dispatching status."""
        code = main(["status", "--db-path", "test.db"])
        assert code == 0
        mock_cmd.assert_called_once_with(db_path="test.db")

    @patch("data_layer.cli.cmd_inspect_state", return_value=0)
    def test_main_dispatches_inspect_state(self, mock_cmd: MagicMock) -> None:
        """Verify dispatching inspect-state."""
        code = main(["inspect-state", "--acquirer", "stripe", "--json"])
        assert code == 0
        mock_cmd.assert_called_once_with(acquirer_id="stripe", as_json=True)

    @patch("data_layer.cli.cmd_reset_demo", return_value=0)
    def test_main_dispatches_reset_demo(self, mock_cmd: MagicMock) -> None:
        """Verify dispatching reset-demo."""
        code = main(["reset-demo", "--force", "--db-path", "test.db", "--skip-redis"])
        assert code == 0
        mock_cmd.assert_called_once_with(
            db_path="test.db",
            force=True,
            skip_redis=True,
            skip_sqlite=False,
        )
