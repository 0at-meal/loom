"""Comprehensive unit tests for Phase 5 Ticket A: Redis-backed health state."""

from __future__ import annotations

import math
import time

import fakeredis
import httpx
import numpy as np
import pytest

from acquirer_sim.models import AuthorizeRequest
from data_layer.redis_state import RedisBanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """Provide a clean FakeRedis client with string decoding for each test."""
    return fakeredis.FakeRedis(decode_responses=True)


class TestRedisReadWriteImmediateConsistency:
    """Tests that a read immediately after a write returns the correct updated value."""

    def test_immediate_read_after_single_success(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify reading state immediately after recording success matches updated value."""
        config = AcquirerStateConfig(decay_factor=0.90)
        registry = RedisBanditStateRegistry(redis_client=fake_redis, default_config=config)
        registry.register_acquirer("acquirer_alpha")

        # Initial state should be priors
        init_snap = registry.get_state("acquirer_alpha")
        assert init_snap.alpha == 1.0
        assert init_snap.beta == 1.0
        assert init_snap.health_score == 1.0
        assert init_snap.success_count == 0
        assert init_snap.failure_count == 0
        assert init_snap.total_count == 0

        # Record single success at t=100.0
        write_snap = registry.record_outcome("acquirer_alpha", success=True, timestamp=100.0)
        assert write_snap.alpha == 2.0
        assert write_snap.beta == 1.0
        assert write_snap.health_score == 1.0
        assert write_snap.success_count == 1
        assert write_snap.failure_count == 0
        assert write_snap.total_count == 1

        # Immediate read via get_state()
        read_snap = registry.get_state("acquirer_alpha")
        assert read_snap.alpha == 2.0
        assert read_snap.beta == 1.0
        assert read_snap.health_score == 1.0
        assert read_snap.success_count == 1
        assert read_snap.failure_count == 0
        assert read_snap.total_count == 1
        assert read_snap.last_updated_at == 100.0
        assert math.isclose(read_snap.expected_success_rate, 2.0 / 3.0)

        # Directly inspect Redis hashes
        health_raw = fake_redis.hgetall("acquirer:acquirer_alpha:health")
        beta_raw = fake_redis.hgetall("acquirer:acquirer_alpha:beta")

        assert health_raw["health_score"] == "1.0"
        assert health_raw["last_updated_at"] == "100.0"
        assert beta_raw["alpha"] == "2.0"
        assert beta_raw["beta"] == "1.0"
        assert beta_raw["success_count"] == "1"
        assert beta_raw["failure_count"] == "0"
        assert beta_raw["total_count"] == "1"

    def test_immediate_read_after_failure_update(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify reading state immediately after recording failure matches updated value."""
        config = AcquirerStateConfig(decay_factor=0.90)
        registry = RedisBanditStateRegistry(redis_client=fake_redis, default_config=config)
        registry.register_acquirer("acquirer_alpha")

        # Record single failure at t=101.0
        write_snap = registry.record_outcome("acquirer_alpha", success=False, timestamp=101.0)
        assert write_snap.alpha == 1.0
        assert write_snap.beta == 2.0
        assert write_snap.health_score == 0.90  # 0.9*1.0 + 0.1*0.0
        assert write_snap.success_count == 0
        assert write_snap.failure_count == 1
        assert write_snap.total_count == 1

        # Immediate read
        read_snap = registry.get_state("acquirer_alpha")
        assert read_snap.alpha == 1.0
        assert read_snap.beta == 2.0
        assert read_snap.health_score == 0.90
        assert read_snap.success_count == 0
        assert read_snap.failure_count == 1
        assert read_snap.total_count == 1
        assert read_snap.last_updated_at == 101.0
        assert math.isclose(read_snap.expected_success_rate, 1.0 / 3.0)


class TestStateSurvivesProcessRestart:
    """Tests that state persists in Redis and survives complete process restart/re-instantiation."""

    def test_restart_restores_accumulated_belief_and_counters(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify a fresh registry instance hydra-loads accumulated state from Redis."""
        config = AcquirerStateConfig(decay_factor=0.90)

        # ---------------------------------------------------------------------
        # Process 1: Record 4 successes and 2 failures
        # ---------------------------------------------------------------------
        proc1_registry = RedisBanditStateRegistry(redis_client=fake_redis, default_config=config)
        proc1_registry.register_acquirer("stripe_us")

        t_base = 1000.0
        for i in range(4):
            proc1_registry.record_outcome("stripe_us", success=True, timestamp=t_base + i)
        for i in range(2):
            proc1_registry.record_outcome("stripe_us", success=False, timestamp=t_base + 4 + i)

        snap_before = proc1_registry.get_state("stripe_us")
        assert snap_before.success_count == 4
        assert snap_before.failure_count == 2
        assert snap_before.total_count == 6
        assert snap_before.last_updated_at == t_base + 5

        # Simulate process termination: delete process 1 registry
        del proc1_registry

        # ---------------------------------------------------------------------
        # Process 2: Fresh instance connecting to same Redis
        # ---------------------------------------------------------------------
        proc2_registry = RedisBanditStateRegistry(redis_client=fake_redis, default_config=config)

        # Register acquirer on the new instance — should hydrate from Redis!
        state_hydrated = proc2_registry.register_acquirer("stripe_us")
        snap_after = state_hydrated.get_state()

        # State MUST match the state from before the restart, NOT reset to priors
        assert snap_after.acquirer_id == "stripe_us"
        assert snap_after.success_count == 4
        assert snap_after.failure_count == 2
        assert snap_after.total_count == 6
        assert snap_after.last_updated_at == t_base + 5
        assert math.isclose(snap_after.alpha, snap_before.alpha, rel_tol=1e-9)
        assert math.isclose(snap_after.beta, snap_before.beta, rel_tol=1e-9)
        assert math.isclose(snap_after.health_score, snap_before.health_score, rel_tol=1e-9)
        assert math.isclose(
            snap_after.expected_success_rate,
            snap_before.expected_success_rate,
            rel_tol=1e-9,
        )

        # Process 2 records another outcome: counters should increment from 6 to 7
        snap_next = proc2_registry.record_outcome("stripe_us", success=True, timestamp=t_base + 6)
        assert snap_next.success_count == 5
        assert snap_next.failure_count == 2
        assert snap_next.total_count == 7

    def test_hydrate_all_from_redis_discovers_unregistered_routes(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify automatic discovery of routes established by a previous process."""
        # Process 1 registers two routes
        proc1 = RedisBanditStateRegistry(redis_client=fake_redis)
        proc1.register_acquirer("acquirer_1")
        proc1.register_acquirer("acquirer_2")
        proc1.record_outcome("acquirer_1", success=True)
        del proc1

        # Process 2 boots with empty local dict
        proc2 = RedisBanditStateRegistry(redis_client=fake_redis)
        assert len(proc2._redis_acquirers) == 0

        # Automatic hydration of all stored routes
        discovered_ids = proc2.hydrate_all_from_redis()
        assert discovered_ids == ["acquirer_1", "acquirer_2"]
        assert proc2.get_state("acquirer_1").success_count == 1
        assert proc2.get_state("acquirer_2").success_count == 0


class TestMultiProcessStateSharing:
    """Tests cross-process sharing and live visibility when multiple processes share Redis."""

    def test_concurrent_worker_visibility(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify updates made by worker A are immediately visible to worker B."""
        worker_a = RedisBanditStateRegistry(redis_client=fake_redis)
        worker_b = RedisBanditStateRegistry(redis_client=fake_redis)

        worker_a.register_acquirer("shared_arm")
        worker_b.register_acquirer("shared_arm")

        # Worker A records success
        snap_a = worker_a.record_outcome("shared_arm", success=True, timestamp=10.0)
        assert snap_a.success_count == 1

        # Worker B reads state — must see worker A's update!
        snap_b = worker_b.get_state("shared_arm")
        assert snap_b.success_count == 1
        assert snap_b.alpha == snap_a.alpha

        # Worker B records failure
        snap_b2 = worker_b.record_outcome("shared_arm", success=False, timestamp=11.0)
        assert snap_b2.failure_count == 1
        assert snap_b2.total_count == 2

        # Worker A reads state — must see worker B's update!
        snap_a2 = worker_a.get_state("shared_arm")
        assert snap_a2.failure_count == 1
        assert snap_a2.total_count == 2
        assert snap_a2.last_updated_at == 11.0


class TestKeyNamingConventions:
    """Tests strict adherence to docs/CONSTITUTION.md key naming conventions."""

    def test_constitution_key_naming_and_hash_fields(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify Redis key names strictly match acquirer:{id}:health and acquirer:{id}:beta."""
        registry = RedisBanditStateRegistry(redis_client=fake_redis)
        registry.register_acquirer("adyen_eu")
        registry.record_outcome("adyen_eu", success=True, timestamp=500.0)

        # 1. Health key: acquirer:{id}:health
        health_key = "acquirer:adyen_eu:health"
        assert fake_redis.exists(health_key)
        health_fields = fake_redis.hgetall(health_key)
        assert set(health_fields.keys()) == {"health_score", "last_updated_at"}

        # 2. Beta belief key: acquirer:{id}:beta
        beta_key = "acquirer:adyen_eu:beta"
        assert fake_redis.exists(beta_key)
        beta_fields = fake_redis.hgetall(beta_key)
        expected_beta_fields = {
            "alpha",
            "beta",
            "alpha_prior",
            "beta_prior",
            "decay_factor",
            "success_count",
            "failure_count",
            "total_count",
            "last_updated_at",
        }
        assert set(beta_fields.keys()) == expected_beta_fields

        # 3. Registry discovery key: acquirers
        acquirers_key = "acquirers"
        assert fake_redis.sismember(acquirers_key, "adyen_eu")

    def test_custom_key_prefix_namespacing(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify custom namespace prefix isolates keys properly."""
        registry = RedisBanditStateRegistry(redis_client=fake_redis, key_prefix="tenant_a:")
        registry.register_acquirer("arm_1")

        assert fake_redis.exists("tenant_a:acquirer:arm_1:health")
        assert fake_redis.exists("tenant_a:acquirer:arm_1:beta")
        assert fake_redis.sismember("tenant_a:acquirers", "arm_1")
        assert not fake_redis.exists("acquirer:arm_1:health")


class TestExactMathematicalTransitionsMatchPhase1:
    """Verifies that Redis-backed state produces identical values to Phase 1 test vectors."""

    def test_architect_spec_verification_vector(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify values match the Phase 1 architect spec test vector across 5 steps."""
        config = AcquirerStateConfig(
            alpha_prior=1.0,
            beta_prior=1.0,
            decay_factor=0.90,
            initial_health=1.0,
        )
        registry = RedisBanditStateRegistry(redis_client=fake_redis, default_config=config)
        registry.register_acquirer("acquirer_vector", config=config)

        # Sequence of 5 discrete outcomes: [True, True, False, True, False]
        outcomes = [True, True, False, True, False]

        expected_values = [
            # Step 1: True -> alpha=2.0, beta=1.0, health=1.0
            (2.0, 1.0, 1.0, 1, 0),
            # Step 2: True -> alpha=2.9, beta=1.0, health=1.0
            (2.9, 1.0, 1.0, 2, 0),
            # Step 3: False -> alpha=2.71, beta=2.0, health=0.90
            (2.71, 2.0, 0.90, 2, 1),
            # Step 4: True -> alpha=3.539, beta=1.9, health=0.91
            (3.539, 1.9, 0.91, 3, 1),
            # Step 5: False -> alpha=3.2851, beta=2.81, health=0.819
            (3.2851, 2.81, 0.819, 3, 2),
        ]

        pairs = zip(outcomes, expected_values, strict=True)
        for step, (success, expected) in enumerate(pairs, start=1):
            snap = registry.record_outcome(
                "acquirer_vector",
                success=success,
                timestamp=float(step),
            )
            exp_alpha, exp_beta, exp_health, exp_succ, exp_fail = expected

            assert math.isclose(snap.alpha, exp_alpha, rel_tol=1e-9)
            assert math.isclose(snap.beta, exp_beta, rel_tol=1e-9)
            assert math.isclose(snap.health_score, exp_health, rel_tol=1e-9)
            assert snap.success_count == exp_succ
            assert snap.failure_count == exp_fail
            assert snap.total_count == exp_succ + exp_fail

            # Also verify raw values in Redis
            b_hash = fake_redis.hgetall("acquirer:acquirer_vector:beta")
            h_hash = fake_redis.hgetall("acquirer:acquirer_vector:health")
            assert math.isclose(float(b_hash["alpha"]), exp_alpha, rel_tol=1e-9)
            assert math.isclose(float(b_hash["beta"]), exp_beta, rel_tol=1e-9)
            assert math.isclose(float(h_hash["health_score"]), exp_health, rel_tol=1e-9)


class TestDropInReplacementWithBanditRouter:
    """Tests plugging RedisBanditStateRegistry directly into BanditRouter."""

    @pytest.mark.asyncio
    async def test_bandit_router_with_redis_registry_persists_outcomes(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify BanditRouter successfully uses RedisBanditStateRegistry and updates Redis."""

        # 1. Setup mock HTTP transport for simulated acquirers
        def mock_handler(request: httpx.Request) -> httpx.Response:
            parts = request.url.path.strip("/").split("/")
            aid = parts[1] if len(parts) > 1 else "acquirer_alpha"
            content = {
                "transaction_id": "tx_test_123",
                "acquirer_id": aid,
                "status": "AUTHORIZED",
                "authorized": True,
                "simulated_latency_ms": 15.0,
                "timestamp": time.time(),
            }
            return httpx.Response(200, json=content)

        transport = httpx.MockTransport(mock_handler)
        client = httpx.AsyncClient(transport=transport)

        # 2. Instantiate router config and Redis registry
        routes = [
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="http://mock-acquirer:8001",
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="http://mock-acquirer:8001",
            ),
        ]
        router_config = RouterConfig(routes=routes, seed=42)
        redis_registry = RedisBanditStateRegistry(redis_client=fake_redis)

        # 3. Instantiate BanditRouter with Redis registry drop-in
        router = BanditRouter(
            config=router_config,
            http_client=client,
            registry=redis_registry,
        )
        assert router.registry is redis_registry

        # 4. Execute a transaction routing request
        req = AuthorizeRequest(
            transaction_id="tx_test_123",
            amount=50.0,
            currency="USD",
        )
        result = await router.route(req)

        assert result.status == "AUTHORIZED"
        assert result.authorized is True
        selected = result.selected_acquirer
        assert selected in ("acquirer_alpha", "acquirer_beta")

        # 5. Verify Redis state was updated for the selected acquirer
        selected_snap = redis_registry.get_state(selected)
        assert selected_snap.success_count == 1
        assert selected_snap.alpha > 1.0

        # Verify directly in Redis storage
        b_hash = fake_redis.hgetall(f"acquirer:{selected}:beta")
        assert b_hash["success_count"] == "1"

        # 6. Instantiate a second router instance (e.g. new worker process)
        router_2 = BanditRouter(
            config=router_config,
            http_client=client,
            registry=RedisBanditStateRegistry(redis_client=fake_redis),
        )
        # Verify router 2 immediately reflects the outcome from router 1
        router2_snap = router_2.get_state(selected)
        assert router2_snap.success_count == 1
        assert router2_snap.alpha == selected_snap.alpha

        await client.aclose()


class TestEdgeCasesAndValidation:
    """Tests error handling, invalid inputs, and boundary validations."""

    def test_invalid_acquirer_id_raises(self, fake_redis: fakeredis.FakeRedis) -> None:
        """Verify registering empty or whitespace acquirer_id raises ValueError."""
        registry = RedisBanditStateRegistry(redis_client=fake_redis)
        with pytest.raises(ValueError, match="acquirer_id must be a non-empty string"):
            registry.register_acquirer("   ")

    def test_duplicate_registration_on_same_instance_raises(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify registering duplicate ID on same registry instance fails."""
        registry = RedisBanditStateRegistry(redis_client=fake_redis)
        registry.register_acquirer("acquirer_1")
        with pytest.raises(ValueError, match="already registered"):
            registry.register_acquirer("acquirer_1")

    def test_record_outcome_unknown_acquirer_raises(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify recording outcome for non-existent acquirer raises KeyError."""
        registry = RedisBanditStateRegistry(redis_client=fake_redis)
        with pytest.raises(KeyError, match="not found in registry"):
            registry.record_outcome("unknown_acquirer", success=True)

    def test_sample_all_draws_valid_probabilities(
        self,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        """Verify sample_all draws values in (0, 1) across all arms."""
        registry = RedisBanditStateRegistry(redis_client=fake_redis)
        registry.register_acquirer("arm_1")
        registry.register_acquirer("arm_2")

        samples = registry.sample_all(rng=np.random.default_rng(123))
        assert set(samples.keys()) == {"arm_1", "arm_2"}
        for val in samples.values():
            assert 0.0 <= val <= 1.0
