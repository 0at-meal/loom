"""DevOps and local environment CLI automation tooling for Loom Data Layer (Phase 5).

Provides subcommands for database initialization, health and latency probing,
runtime state inspection, and safe demo rehearsal resets:
    python -m data_layer.cli init-db
    python -m data_layer.cli ping
    python -m data_layer.cli status
    python -m data_layer.cli inspect-state
    python -m data_layer.cli reset-demo
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import sqlite3
import sys
import time
from typing import Any

import redis

from data_layer.config import DataLayerConfig
from data_layer.redis_state import RedisStateStore
from data_layer.sqlite_logger import load_schema_sql


def get_redis_client(
    config: DataLayerConfig,
    override_host: str | None = None,
    override_port: int | None = None,
    override_db: int | None = None,
    override_timeout: float | None = None,
) -> redis.Redis[Any]:
    """Create a synchronous Redis client with configured parameters."""
    raw_host = override_host or config.redis_host
    # On Windows, using 127.0.0.1 avoids IPv6 resolution delays when Redis is not running
    host = "127.0.0.1" if raw_host == "localhost" else raw_host
    port = override_port or config.redis_port
    db = override_db if override_db is not None else config.redis_db
    timeout = override_timeout or config.redis_timeout_sec

    return redis.Redis(
        host=host,
        port=port,
        db=db,
        password=config.redis_password,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        decode_responses=True,
    )


def probe_redis_connectivity(
    config: DataLayerConfig,
    redis_client: redis.Redis[Any] | None = None,
    timeout: float = 0.5,
) -> tuple[bool, redis.Redis[Any] | None, str]:
    """Test Redis connectivity fast with strict timeout to prevent socket hangs."""
    if redis_client is not None:
        try:
            redis_client.ping()
            return True, redis_client, ""
        except (redis.RedisError, OSError) as exc:
            return False, redis_client, str(exc)

    raw_host = config.redis_host
    host = "127.0.0.1" if raw_host == "localhost" else raw_host
    port = config.redis_port

    # Fast TCP socket pre-flight probe
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
    except OSError as exc:
        return (
            False,
            None,
            f"Port unreachable on {host}:{port} ({exc})",
        )

    # If TCP port is open, connect via redis-py
    try:
        client = get_redis_client(config, override_host=host)
        client.ping()
        return True, client, ""
    except (redis.RedisError, OSError) as exc:
        return False, None, str(exc)


# ==============================================================================
# Subcommand: init-db
# ==============================================================================
def cmd_init_db(
    db_path: str | None = None,
    config: DataLayerConfig | None = None,
) -> int:
    """Execute schema.sql to initialize SQLite tables, triggers, and indices idempotently."""
    cfg = config or DataLayerConfig()
    target_path = db_path or cfg.sqlite_db_path

    print(f"[*] Initializing SQLite schema at '{target_path}'...")
    try:
        # Ensure parent directory exists if target is a file path
        if target_path != ":memory:":
            parent_dir = pathlib.Path(target_path).parent
            if not parent_dir.exists():
                parent_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(target_path)
        schema_sql = load_schema_sql()
        conn.executescript(schema_sql)
        conn.commit()

        # Verify tables exist
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('transactions', 'acquirer_outcomes');"
        )
        tables = {row[0] for row in cursor.fetchall()}
        required_tables = {"transactions", "acquirer_outcomes"}
        if not required_tables.issubset(tables):
            missing = required_tables - tables
            print(
                f"[ERROR] Schema initialization failed: missing tables {missing}",
                file=sys.stderr,
            )
            conn.close()
            return 1

        # Verify triggers exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'prevent_%';"
        )
        triggers = {row[0] for row in cursor.fetchall()}
        expected_triggers = {
            "prevent_transactions_update",
            "prevent_transactions_delete",
            "prevent_acquirer_outcomes_update",
            "prevent_acquirer_outcomes_delete",
        }
        if not expected_triggers.issubset(triggers):
            missing_trig = expected_triggers - triggers
            print(
                f"[WARNING] Some constitutional triggers are missing: {missing_trig}",
                file=sys.stderr,
            )

        # Check journal mode
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        conn.close()

        print(
            f"[OK] SQLite database initialized successfully at '{target_path}' "
            f"(journal_mode: {journal_mode}, triggers active: {len(triggers)})."
        )
        return 0
    except (sqlite3.Error, OSError) as exc:
        print(
            f"[ERROR] Failed to initialize SQLite schema at '{target_path}': {exc}",
            file=sys.stderr,
        )
        return 1


# ==============================================================================
# Subcommand: ping
# ==============================================================================
def cmd_ping(
    redis_client: redis.Redis[Any] | None = None,
    db_path: str | None = None,
    config: DataLayerConfig | None = None,
) -> int:
    """Test connectivity and round-trip latency to Redis and SQLite.

    Returns 0 if both services are UP; returns 1 if either is degraded or unreachable.
    """
    cfg = config or DataLayerConfig()
    target_db = db_path or cfg.sqlite_db_path

    redis_ok = False
    sqlite_ok = False

    print("================================================================================")
    print(" Loom Data Layer Health & Readiness Check")
    print("================================================================================")

    # 1. Probe Redis
    t0 = time.perf_counter()
    ok, client, err = probe_redis_connectivity(cfg, redis_client=redis_client, timeout=0.5)
    t1 = time.perf_counter()
    rtt_ms = (t1 - t0) * 1000.0

    if ok and client is not None:
        redis_ok = True
        info: dict[str, Any] = {}
        try:
            info = dict(client.info(section="server") or {})
        except (redis.RedisError, OSError):
            pass
        version_str = f" v{info.get('redis_version', 'unknown')}" if info else ""
        print(
            f"  [UP]   Redis:  Host={cfg.redis_host}:{cfg.redis_port} "
            f"(db={cfg.redis_db}{version_str}) | RTT={rtt_ms:.2f}ms"
        )
    else:
        print(f"  [DOWN] Redis:  Host={cfg.redis_host}:{cfg.redis_port} | Error: {err}")

    # 2. Probe SQLite
    try:
        t0 = time.perf_counter()
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")
        res = cursor.fetchone()
        cursor.execute("PRAGMA journal_mode;")
        journal_row = cursor.fetchone()
        journal_mode = journal_row[0] if journal_row else "unknown"
        conn.close()
        t1 = time.perf_counter()
        query_ms = (t1 - t0) * 1000.0

        if res and res[0] == 1:
            sqlite_ok = True
            print(
                f"  [UP]   SQLite: Target='{target_db}' (journal_mode={journal_mode}) "
                f"| Latency={query_ms:.2f}ms"
            )
        else:
            print(
                f"  [DOWN] SQLite: Target='{target_db}' | Error: Unexpected query response: {res}"
            )
    except (sqlite3.Error, OSError) as exc:
        print(f"  [DOWN] SQLite: Target='{target_db}' | Error: {exc}")

    print("--------------------------------------------------------------------------------")
    if redis_ok and sqlite_ok:
        print("  [HEALTHY] All Phase 5 data layer dependencies are operational.")
        print("================================================================================")
        return 0
    else:
        print("  [UNHEALTHY] One or more data layer services are unreachable or degraded.")
        print("================================================================================")
        return 1


# ==============================================================================
# Subcommand: status
# ==============================================================================
def cmd_status(
    redis_client: redis.Redis[Any] | None = None,
    db_path: str | None = None,
    config: DataLayerConfig | None = None,
) -> int:
    """Display comprehensive status, connection metrics, and ledger row counts."""
    cfg = config or DataLayerConfig()
    target_db = db_path or cfg.sqlite_db_path

    print("================================================================================")
    print(" Loom Data Layer Status Report")
    print("================================================================================")

    # Redis Status
    ok, client, err = probe_redis_connectivity(cfg, redis_client=redis_client, timeout=0.5)
    if ok and client is not None:
        try:
            info_server: dict[str, Any] = {}
            info_clients: dict[str, Any] = {}
            info_memory: dict[str, Any] = {}
            try:
                info_server = dict(client.info(section="server") or {})
                info_clients = dict(client.info(section="clients") or {})
                info_memory = dict(client.info(section="memory") or {})
            except (redis.RedisError, OSError):
                pass

            store = RedisStateStore(redis_client=client, config=cfg)

            # Retrieve registered acquirers
            acquirers_set = client.smembers(store.acquirers_key())
            acquirers_list = sorted(list(acquirers_set))

            print("[Redis Persistence Store]")
            print(f"  Connection:         {cfg.redis_host}:{cfg.redis_port} (db={cfg.redis_db})")
            print(f"  Server Version:     {info_server.get('redis_version', 'N/A')}")
            print(f"  Uptime (seconds):   {info_server.get('uptime_in_seconds', 'N/A')}")
            print(f"  Connected Clients:  {info_clients.get('connected_clients', 'N/A')}")
            print(f"  Used Memory:        {info_memory.get('used_memory_human', 'N/A')}")
            print(f"  Key Prefix:         '{store.key_prefix}'")
            print(f"  Active Acquirers:   {len(acquirers_list)} {acquirers_list}")
        except (redis.RedisError, OSError) as exc:
            print(f"[Redis Persistence Store] [ERROR] Could not query Redis: {exc}")
    else:
        print(f"[Redis Persistence Store] [DOWN] Could not connect to Redis: {err}")

    print("--------------------------------------------------------------------------------")

    # SQLite Status
    print("[SQLite Metrics Ledger]")
    print(f"  Database Path:      {target_db}")
    try:
        if target_db != ":memory:" and os.path.exists(target_db):
            file_size_bytes = os.path.getsize(target_db)
            print(
                f"  File Size on Disk:  {file_size_bytes:,} bytes ({file_size_bytes / 1024:.1f} KB)"
            )
        elif target_db == ":memory:":
            print("  File Size on Disk:  In-Memory Database (:memory:)")
        else:
            print("  File Size on Disk:  File does not exist yet (run init-db)")

        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_row = cursor.fetchone()
        journal_mode = journal_row[0] if journal_row else "unknown"
        print(f"  Journal Mode:       {journal_mode}")

        # Count tables and records
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall() if not r[0].startswith("sqlite_")]

        tx_count = 0
        outcome_count = 0
        auth_count = 0
        if "transactions" in tables:
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(authorized), 0) FROM transactions;")
            row = cursor.fetchone()
            tx_count = int(row[0]) if row else 0
            auth_count = int(row[1]) if row else 0
        if "acquirer_outcomes" in tables:
            cursor.execute("SELECT COUNT(*) FROM acquirer_outcomes;")
            row = cursor.fetchone()
            outcome_count = int(row[0]) if row else 0

        psr = (auth_count / tx_count * 100.0) if tx_count > 0 else 0.0
        print(f"  Tables Found:       {', '.join(tables) if tables else 'None'}")
        print(f"  Logged Transactions:{tx_count:,}")
        print(f"  Authorized Count:   {auth_count:,}")
        print(f"  Current Overall PSR:{psr:.2f}%")
        print(f"  Acquirer Outcomes:  {outcome_count:,}")
        conn.close()
    except (sqlite3.Error, OSError) as exc:
        print(f"  [ERROR] Could not query SQLite database: {exc}")

    print("================================================================================")
    return 0


# ==============================================================================
# Subcommand: inspect-state
# ==============================================================================
def cmd_inspect_state(
    redis_client: redis.Redis[Any] | None = None,
    acquirer_id: str | None = None,
    as_json: bool = False,
    config: DataLayerConfig | None = None,
) -> int:
    """Dump current acquirer health and beta belief hashes from Redis."""
    cfg = config or DataLayerConfig()
    ok, client, err = probe_redis_connectivity(cfg, redis_client=redis_client, timeout=0.5)
    if not ok or client is None:
        print(f"[ERROR] Failed to inspect state from Redis: {err}", file=sys.stderr)
        return 1

    store = RedisStateStore(redis_client=client, config=cfg)

    try:
        # Determine acquirers to inspect
        if acquirer_id:
            acquirers = [acquirer_id]
        else:
            raw_acquirers_set = client.smembers(store.acquirers_key())
            acquirers = sorted(
                [k.decode("utf-8") if isinstance(k, bytes) else str(k) for k in raw_acquirers_set]
            )

        if not acquirers:
            # Fallback: scan for acquirer keys
            raw_keys = client.keys(f"{store.key_prefix}acquirer:*:health")
            found_ids: set[str] = set()
            for k_raw in raw_keys:
                k = k_raw.decode("utf-8") if isinstance(k_raw, bytes) else str(k_raw)
                # Key format: [prefix]acquirer:{id}:health
                parts = k.split("acquirer:")
                if len(parts) > 1:
                    aid = parts[1].split(":health")[0]
                    found_ids.add(aid)
            acquirers = sorted(list(found_ids))

        if not acquirers:
            if as_json:
                print(json.dumps({"acquirers": {}}, indent=2))
            else:
                print("No acquirer states found in Redis.")
            return 0

        report: dict[str, Any] = {}
        for aid in acquirers:
            h_key = store.health_key(aid)
            b_key = store.beta_key(aid)
            h_data = client.hgetall(h_key)
            b_data = client.hgetall(b_key)

            alpha = float(b_data.get("alpha", 1.0))
            beta = float(b_data.get("beta", 1.0))
            expected_mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
            variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1))

            report[aid] = {
                "health_key": h_key,
                "beta_key": b_key,
                "health_score": float(h_data.get("health_score", 1.0)),
                "health_last_updated": float(h_data.get("last_updated_at", 0.0)),
                "alpha": alpha,
                "beta": beta,
                "alpha_prior": float(b_data.get("alpha_prior", 1.0)),
                "beta_prior": float(b_data.get("beta_prior", 1.0)),
                "decay_factor": float(b_data.get("decay_factor", 0.98)),
                "success_count": int(b_data.get("success_count", 0)),
                "failure_count": int(b_data.get("failure_count", 0)),
                "total_count": int(b_data.get("total_count", 0)),
                "thompson_mean": round(expected_mean, 4),
                "thompson_variance": round(variance, 6),
                "state_last_updated": float(b_data.get("last_updated_at", 0.0)),
            }

        if as_json:
            print(json.dumps(report, indent=2))
        else:
            print(
                "================================================================================"
            )
            print(" Loom Acquirer Belief & Health State Inspection")
            print(
                "================================================================================"
            )
            for aid, d in report.items():
                print(f"Acquirer: {aid}")
                print(f"  Keys:                {d['health_key']} | {d['beta_key']}")
                print(f"  Health Score:        {d['health_score']:.4f}")
                print(f"  Beta Parameters:     alpha={d['alpha']:.3f}, beta={d['beta']:.3f}")
                print(
                    f"  Priors & Decay:      alpha0={d['alpha_prior']}, "
                    f"beta0={d['beta_prior']}, gamma={d['decay_factor']}"
                )
                print(
                    f"  Thompson Belief:     E[theta]={d['thompson_mean']:.4f}, "
                    f"Var[theta]={d['thompson_variance']:.6f}"
                )
                print(
                    f"  Counters:            Successes={d['success_count']}, "
                    f"Failures={d['failure_count']}, Total={d['total_count']}"
                )
                print(
                    "--------------------------------------------------------------------------------"
                )
            print(
                "================================================================================"
            )
        return 0
    except (redis.RedisError, ValueError, KeyError) as exc:
        print(f"[ERROR] Failed to inspect state from Redis: {exc}", file=sys.stderr)
        return 1


# ==============================================================================
# Subcommand: reset-demo
# ==============================================================================
def cmd_reset_demo(
    redis_client: redis.Redis[Any] | None = None,
    db_path: str | None = None,
    force: bool = False,
    skip_redis: bool = False,
    skip_sqlite: bool = False,
    config: DataLayerConfig | None = None,
) -> int:
    """Reset the local environment to a clean state for a demonstration rehearsal.

    Safely drops SQLite tables and re-executes schema.sql (safely respecting
    constitutional append-only triggers), and flushes/deletes acquirer keys in Redis.
    """
    cfg = config or DataLayerConfig()
    target_db = db_path or cfg.sqlite_db_path

    if not force:
        try:
            prompt = (
                f"\n[CAUTION] You are about to reset the demo environment.\n"
                f"This will wipe all SQLite records in '{target_db}' and clear Redis state keys.\n"
                f"Are you sure you want to proceed? [y/N]: "
            )
            resp = input(prompt).strip().lower()
            if resp not in ("y", "yes"):
                print("[ABORT] Rehearsal reset aborted by user.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\n[ABORT] Rehearsal reset aborted.")
            return 0

    print("================================================================================")
    print(" Resetting Loom Phase 5 Environment for Demo Rehearsal")
    print("================================================================================")

    # 1. Reset SQLite
    if not skip_sqlite:
        print(f"[*] Resetting SQLite ledger at '{target_db}'...")
        try:
            if target_db == ":memory:":
                conn = sqlite3.connect(":memory:")
                conn.executescript(load_schema_sql())
                conn.close()
                print("  [OK] In-memory SQLite database reset and re-initialized.")
            else:
                # If database file exists, drop tables safely bypassing BEFORE DELETE triggers
                if os.path.exists(target_db):
                    conn = sqlite3.connect(target_db)
                    cursor = conn.cursor()
                    cursor.execute("DROP TABLE IF EXISTS acquirer_outcomes;")
                    cursor.execute("DROP TABLE IF EXISTS transactions;")
                    conn.commit()
                    conn.close()

                # Re-apply schema with fresh tables, indices, and constitutional triggers
                init_res = cmd_init_db(db_path=target_db, config=cfg)
                if init_res != 0:
                    print(
                        f"  [FAIL] Failed to re-initialize schema at '{target_db}'.",
                        file=sys.stderr,
                    )
                    return 1
                print(
                    f"  [OK] SQLite metrics ledger cleaned and schema re-applied at '{target_db}'."
                )
        except (sqlite3.Error, OSError) as exc:
            print(f"  [FAIL] SQLite reset failed: {exc}", file=sys.stderr)
            return 1

    # 2. Reset Redis
    if not skip_redis:
        print("[*] Resetting Redis acquirer state keys...")
        ok, client, err = probe_redis_connectivity(cfg, redis_client=redis_client, timeout=0.5)
        if not ok or client is None:
            print(f"  [FAIL] Redis reset failed: {err}", file=sys.stderr)
            return 1

        store = RedisStateStore(redis_client=client, config=cfg)
        try:
            # Query registered acquirers
            raw_acquirers = client.smembers(store.acquirers_key())
            acquirers: list[str] = [
                a.decode("utf-8") if isinstance(a, bytes) else str(a) for a in raw_acquirers
            ]

            keys_to_delete: list[str] = [store.acquirers_key()]
            for aid in acquirers:
                keys_to_delete.append(store.health_key(aid))
                keys_to_delete.append(store.beta_key(aid))

            # Also scan for any orphaned keys matching prefix pattern
            prefix_pattern = f"{store.key_prefix}acquirer:*"
            orphans_raw = client.keys(prefix_pattern)
            for k_raw in orphans_raw:
                k = k_raw.decode("utf-8") if isinstance(k_raw, bytes) else str(k_raw)
                if k not in keys_to_delete:
                    keys_to_delete.append(k)

            if keys_to_delete:
                deleted_count = client.delete(*keys_to_delete)
                print(f"  [OK] Deleted {deleted_count} Redis keys matching '{prefix_pattern}'.")
            else:
                print("  [OK] No existing Redis acquirer state keys found.")
        except (redis.RedisError, OSError) as exc:
            print(f"  [FAIL] Redis reset failed: {exc}", file=sys.stderr)
            return 1

    print("--------------------------------------------------------------------------------")
    print("  [DEMO READY] Local environment is reset and ready for demonstration.")
    print("================================================================================")
    return 0


# ==============================================================================
# Main CLI Parser & Dispatcher
# ==============================================================================
def create_parser() -> argparse.ArgumentParser:
    """Build command-line parser for Loom Data Layer tooling."""
    parser = argparse.ArgumentParser(
        prog="python -m data_layer.cli",
        description="Loom Phase 5 Data Layer DevOps & Automation CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # init-db
    init_parser = subparsers.add_parser(
        "init-db",
        help="Initialize SQLite tables, triggers, and indices idempotently.",
    )
    init_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database file (defaults to SQLITE_DB_PATH).",
    )

    # ping
    ping_parser = subparsers.add_parser(
        "ping",
        help="Test connectivity and latency to Redis and SQLite.",
    )
    ping_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database file.",
    )

    # status
    status_parser = subparsers.add_parser(
        "status",
        help="Display Redis connection metrics and SQLite transaction counts.",
    )
    status_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database file.",
    )

    # inspect-state
    inspect_parser = subparsers.add_parser(
        "inspect-state",
        help="Dump current acquirer health and beta state keys from Redis.",
    )
    inspect_parser.add_argument(
        "--acquirer",
        default=None,
        help="Specific acquirer ID to inspect (e.g. 'stripe', 'adyen').",
    )
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output raw JSON format.",
    )

    # reset-demo
    reset_parser = subparsers.add_parser(
        "reset-demo",
        help="Cleanly reset SQLite tables and Redis keys for rehearsal.",
    )
    reset_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Bypass interactive confirmation prompt.",
    )
    reset_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database file.",
    )
    reset_parser.add_argument(
        "--skip-redis",
        action="store_true",
        help="Do not touch Redis keys.",
    )
    reset_parser.add_argument(
        "--skip-sqlite",
        action="store_true",
        help="Do not touch SQLite database.",
    )

    return parser


def main(args: list[str] | None = None) -> int:
    """Entry point for CLI execution."""
    parser = create_parser()
    parsed_args = parser.parse_args(args)

    if not parsed_args.command:
        parser.print_help()
        return 0

    if parsed_args.command == "init-db":
        return cmd_init_db(db_path=parsed_args.db_path)
    elif parsed_args.command == "ping":
        return cmd_ping(db_path=parsed_args.db_path)
    elif parsed_args.command == "status":
        return cmd_status(db_path=parsed_args.db_path)
    elif parsed_args.command == "inspect-state":
        return cmd_inspect_state(
            acquirer_id=parsed_args.acquirer,
            as_json=parsed_args.as_json,
        )
    elif parsed_args.command == "reset-demo":
        return cmd_reset_demo(
            db_path=parsed_args.db_path,
            force=parsed_args.force,
            skip_redis=parsed_args.skip_redis,
            skip_sqlite=parsed_args.skip_sqlite,
        )
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
