"""Redis-backed health and belief state persistence for Loom (Phase 5 Ticket A)."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import redis
from redis.exceptions import WatchError

from data_layer.config import DataLayerConfig
from router_core.bandit import BanditStateRegistry
from router_core.state import (
    AcquirerState,
    AcquirerStateConfig,
    AcquirerStateSnapshot,
)

logger = logging.getLogger("loom.data_layer.redis_state")


def _to_str(val: str | bytes | None) -> str:
    """Convert string or bytes value to a string, returning empty string for None."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


class RedisStateStore:
    """Manages low-level Redis operations and key serialization for acquirer states."""

    def __init__(
        self,
        redis_client: redis.Redis[Any] | None = None,
        config: DataLayerConfig | None = None,
        key_prefix: str = "",
    ) -> None:
        """Initialize Redis store with client or connection parameters."""
        self._config = config or DataLayerConfig()
        effective_prefix = key_prefix or self._config.key_prefix
        if effective_prefix and not effective_prefix.endswith(":"):
            self._prefix = f"{effective_prefix}:"
        else:
            self._prefix = effective_prefix

        if redis_client is not None:
            self._redis: redis.Redis[Any] = redis_client
        else:
            self._redis = redis.Redis(
                host=self._config.redis_host,
                port=self._config.redis_port,
                db=self._config.redis_db,
                password=self._config.redis_password,
                socket_timeout=self._config.redis_timeout_sec,
                decode_responses=True,
            )

    @property
    def redis(self) -> redis.Redis[Any]:
        """Return the underlying Redis client."""
        return self._redis

    @property
    def key_prefix(self) -> str:
        """Return the active namespace key prefix."""
        return self._prefix

    def health_key(self, acquirer_id: str) -> str:
        """Generate namespaced Redis key for live health score (acquirer:{id}:health)."""
        return f"{self._prefix}acquirer:{acquirer_id}:health"

    def beta_key(self, acquirer_id: str) -> str:
        """Generate namespaced Redis key for Beta beliefs (acquirer:{id}:beta)."""
        return f"{self._prefix}acquirer:{acquirer_id}:beta"

    def acquirers_key(self) -> str:
        """Generate namespaced Redis key for registered acquirers set."""
        return f"{self._prefix}acquirers"

    def exists(self, acquirer_id: str) -> bool:
        """Check if state for an acquirer already exists in Redis."""
        b_key = self.beta_key(acquirer_id)
        h_key = self.health_key(acquirer_id)
        exists_count = int(self._redis.exists(b_key, h_key))
        return exists_count > 0

    def hydrate_or_init(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig,
        initial_timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Hydrate existing snapshot from Redis if present; otherwise initialize with priors."""
        b_key = self.beta_key(acquirer_id)
        h_key = self.health_key(acquirer_id)
        acquirers_k = self.acquirers_key()

        pipe = self._redis.pipeline()
        pipe.hgetall(b_key)
        pipe.hgetall(h_key)
        pipe.sadd(acquirers_k, acquirer_id)
        b_data_raw, h_data_raw, _ = pipe.execute()

        b_data: dict[str, str] = {_to_str(k): _to_str(v) for k, v in (b_data_raw or {}).items()}
        h_data: dict[str, str] = {_to_str(k): _to_str(v) for k, v in (h_data_raw or {}).items()}

        if b_data and "alpha" in b_data and "beta" in b_data:
            # Hydrate existing state from Redis
            alpha = float(b_data["alpha"])
            beta = float(b_data["beta"])
            alpha_prior = float(b_data.get("alpha_prior", config.alpha_prior))
            beta_prior = float(b_data.get("beta_prior", config.beta_prior))
            health_score = float(h_data.get("health_score", config.initial_health))
            success_count = int(b_data.get("success_count", 0))
            failure_count = int(b_data.get("failure_count", 0))
            total_count = int(b_data.get("total_count", success_count + failure_count))
            last_updated_at = float(
                b_data.get(
                    "last_updated_at",
                    initial_timestamp if initial_timestamp is not None else time.time(),
                )
            )

            logger.info(
                "Hydrated acquirer '%s' from Redis: alpha=%.3f, beta=%.3f, health=%.3f",
                acquirer_id,
                alpha,
                beta,
                health_score,
            )
            return AcquirerStateSnapshot(
                acquirer_id=acquirer_id,
                alpha=alpha,
                beta=beta,
                health_score=health_score,
                success_count=success_count,
                failure_count=failure_count,
                total_count=total_count,
                last_updated_at=last_updated_at,
                alpha_prior=alpha_prior,
                beta_prior=beta_prior,
            )

        # Keys don't exist yet: initialize Redis with prior defaults
        ts = initial_timestamp if initial_timestamp is not None else time.time()
        initial_snapshot = AcquirerStateSnapshot(
            acquirer_id=acquirer_id,
            alpha=config.alpha_prior,
            beta=config.beta_prior,
            health_score=config.initial_health,
            success_count=0,
            failure_count=0,
            total_count=0,
            last_updated_at=ts,
            alpha_prior=config.alpha_prior,
            beta_prior=config.beta_prior,
        )
        self.write_snapshot(initial_snapshot, decay_factor=config.decay_factor)
        logger.info("Initialized fresh acquirer '%s' state in Redis", acquirer_id)
        return initial_snapshot

    def read_snapshot(self, acquirer_id: str) -> AcquirerStateSnapshot | None:
        """Read and parse point-in-time snapshot directly from Redis."""
        b_key = self.beta_key(acquirer_id)
        h_key = self.health_key(acquirer_id)

        pipe = self._redis.pipeline()
        pipe.hgetall(b_key)
        pipe.hgetall(h_key)
        b_data_raw, h_data_raw = pipe.execute()

        b_data: dict[str, str] = {_to_str(k): _to_str(v) for k, v in (b_data_raw or {}).items()}
        h_data: dict[str, str] = {_to_str(k): _to_str(v) for k, v in (h_data_raw or {}).items()}

        if not b_data or "alpha" not in b_data:
            return None

        alpha = float(b_data["alpha"])
        beta = float(b_data["beta"])
        alpha_prior = float(b_data.get("alpha_prior", 1.0))
        beta_prior = float(b_data.get("beta_prior", 1.0))
        health_score = float(h_data.get("health_score", 1.0))
        success_count = int(b_data.get("success_count", 0))
        failure_count = int(b_data.get("failure_count", 0))
        total_count = int(b_data.get("total_count", success_count + failure_count))
        last_updated_at = float(b_data.get("last_updated_at", time.time()))

        return AcquirerStateSnapshot(
            acquirer_id=acquirer_id,
            alpha=alpha,
            beta=beta,
            health_score=health_score,
            success_count=success_count,
            failure_count=failure_count,
            total_count=total_count,
            last_updated_at=last_updated_at,
            alpha_prior=alpha_prior,
            beta_prior=beta_prior,
        )

    def write_snapshot(
        self,
        snapshot: AcquirerStateSnapshot,
        decay_factor: float = 0.98,
    ) -> None:
        """Persist a snapshot to Redis across both keys in an atomic pipeline."""
        b_key = self.beta_key(snapshot.acquirer_id)
        h_key = self.health_key(snapshot.acquirer_id)
        acquirers_k = self.acquirers_key()

        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(
            h_key,
            mapping={
                "health_score": str(snapshot.health_score),
                "last_updated_at": str(snapshot.last_updated_at),
            },
        )
        pipe.hset(
            b_key,
            mapping={
                "alpha": str(snapshot.alpha),
                "beta": str(snapshot.beta),
                "alpha_prior": str(snapshot.alpha_prior),
                "beta_prior": str(snapshot.beta_prior),
                "decay_factor": str(decay_factor),
                "success_count": str(snapshot.success_count),
                "failure_count": str(snapshot.failure_count),
                "total_count": str(snapshot.total_count),
                "last_updated_at": str(snapshot.last_updated_at),
            },
        )
        pipe.sadd(acquirers_k, snapshot.acquirer_id)
        pipe.execute()

    def record_outcome(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig,
        success: bool,
        timestamp: float | None = None,
        max_retries: int = 5,
    ) -> AcquirerStateSnapshot:
        """Record outcome atomically in Redis using optimistic locking and Phase 1 math."""
        b_key = self.beta_key(acquirer_id)
        h_key = self.health_key(acquirer_id)
        acquirers_k = self.acquirers_key()

        gamma = config.decay_factor
        a0 = config.alpha_prior
        b0 = config.beta_prior
        x = 1.0 if success else 0.0
        now_ts = timestamp if timestamp is not None else time.time()

        for attempt in range(max_retries):
            pipe = self._redis.pipeline()
            try:
                pipe.watch(b_key, h_key)
                b_raw: Any = pipe.hgetall(b_key)
                h_raw: Any = pipe.hgetall(h_key)

                b_data = {_to_str(k): _to_str(v) for k, v in (b_raw or {}).items()}
                h_data = {_to_str(k): _to_str(v) for k, v in (h_raw or {}).items()}

                if b_data and "alpha" in b_data:
                    curr_alpha = float(b_data["alpha"])
                    curr_beta = float(b_data["beta"])
                    curr_health = float(h_data.get("health_score", config.initial_health))
                    curr_success = int(b_data.get("success_count", 0))
                    curr_failure = int(b_data.get("failure_count", 0))
                else:
                    curr_alpha = a0
                    curr_beta = b0
                    curr_health = config.initial_health
                    curr_success = 0
                    curr_failure = 0

                # Phase 1: alpha_t = a0 + gamma*(alpha_{t-1} - a0) + x
                new_alpha = max(a0, a0 + gamma * (curr_alpha - a0) + x)
                new_beta = max(b0, b0 + gamma * (curr_beta - b0) + (1.0 - x))

                # 2. EWMA health score update: H_t = gamma*H_{t-1} + (1 - gamma)*x
                new_health = max(0.0, min(1.0, gamma * curr_health + (1.0 - gamma) * x))

                # 3. Counters
                new_success = curr_success + (1 if success else 0)
                new_failure = curr_failure + (0 if success else 1)
                new_total = new_success + new_failure

                pipe.multi()
                pipe.hset(
                    h_key,
                    mapping={
                        "health_score": str(new_health),
                        "last_updated_at": str(now_ts),
                    },
                )
                pipe.hset(
                    b_key,
                    mapping={
                        "alpha": str(new_alpha),
                        "beta": str(new_beta),
                        "alpha_prior": str(a0),
                        "beta_prior": str(b0),
                        "decay_factor": str(gamma),
                        "success_count": str(new_success),
                        "failure_count": str(new_failure),
                        "total_count": str(new_total),
                        "last_updated_at": str(now_ts),
                    },
                )
                pipe.sadd(acquirers_k, acquirer_id)
                pipe.execute()

                return AcquirerStateSnapshot(
                    acquirer_id=acquirer_id,
                    alpha=new_alpha,
                    beta=new_beta,
                    health_score=new_health,
                    success_count=new_success,
                    failure_count=new_failure,
                    total_count=new_total,
                    last_updated_at=now_ts,
                    alpha_prior=a0,
                    beta_prior=b0,
                )

            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        "Exceeded max retries (%d) for '%s' due to Redis contention",
                        max_retries,
                        acquirer_id,
                    )
                    raise
                time.sleep(0.001 * (attempt + 1))
                continue
            finally:
                pipe.reset()

        raise RuntimeError(f"Failed to record outcome for acquirer '{acquirer_id}'")

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all registered acquirer IDs tracked in Redis."""
        raw_members = self._redis.smembers(self.acquirers_key())
        return sorted([_to_str(m) for m in raw_members if _to_str(m)])

    def read_all_snapshots(self) -> dict[str, AcquirerStateSnapshot]:
        """Read state snapshots for all registered acquirers from Redis."""
        ids = self.list_acquirer_ids()
        results: dict[str, AcquirerStateSnapshot] = {}
        for aid in ids:
            snap = self.read_snapshot(aid)
            if snap is not None:
                results[aid] = snap
        return results


class RedisAcquirerState(AcquirerState):
    """Redis-backed drop-in replacement for AcquirerState."""

    def __init__(
        self,
        acquirer_id: str,
        store: RedisStateStore,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> None:
        """Initialize state, hydrating from Redis or setting prior defaults."""
        super().__init__(
            acquirer_id=acquirer_id,
            config=config,
            initial_timestamp=initial_timestamp,
        )
        self._store = store
        # Hydrate from Redis or initialize Redis keys
        self._snapshot = self._store.hydrate_or_init(
            acquirer_id=self._acquirer_id,
            config=self._config,
            initial_timestamp=initial_timestamp,
        )
        self._sync_from_snapshot(self._snapshot)

    def _sync_from_snapshot(self, snapshot: AcquirerStateSnapshot) -> None:
        """Synchronize internal attributes from an AcquirerStateSnapshot."""
        self._alpha = snapshot.alpha
        self._beta = snapshot.beta
        self._health_score = snapshot.health_score
        self._success_count = snapshot.success_count
        self._failure_count = snapshot.failure_count
        self._last_updated_at = snapshot.last_updated_at

    def record_outcome(
        self,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Atomically record outcome in Redis and return updated state snapshot."""
        snapshot = self._store.record_outcome(
            acquirer_id=self._acquirer_id,
            config=self._config,
            success=success,
            timestamp=timestamp,
        )
        self._snapshot = snapshot
        self._sync_from_snapshot(snapshot)
        return snapshot

    def sample(self, rng: np.random.Generator | None = None) -> float:
        """Draw a Thompson sample from the current Beta distribution belief."""
        # Read freshest state from Redis to ensure cross-process visibility
        fresh_snap = self.get_state()
        generator = rng if rng is not None else np.random.default_rng()
        return float(generator.beta(fresh_snap.alpha, fresh_snap.beta))

    def get_state(self) -> AcquirerStateSnapshot:
        """Return an immutable snapshot of current acquirer state from Redis."""
        snapshot = self._store.read_snapshot(self._acquirer_id)
        if snapshot is not None:
            self._snapshot = snapshot
            self._sync_from_snapshot(snapshot)
        return self._snapshot


class RedisBanditStateRegistry(BanditStateRegistry):
    """Redis-backed drop-in replacement for BanditStateRegistry."""

    def __init__(
        self,
        redis_client: redis.Redis[Any] | None = None,
        config: DataLayerConfig | None = None,
        default_config: AcquirerStateConfig | None = None,
        key_prefix: str = "",
    ) -> None:
        """Initialize registry with RedisStateStore and optional defaults."""
        super().__init__(default_config=default_config)
        self._store = RedisStateStore(
            redis_client=redis_client,
            config=config,
            key_prefix=key_prefix,
        )
        # Dictionary of local RedisAcquirerState wrapper instances
        self._redis_acquirers: dict[str, RedisAcquirerState] = {}

    @property
    def store(self) -> RedisStateStore:
        """Return the underlying RedisStateStore instance."""
        return self._store

    def register_acquirer(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> RedisAcquirerState:
        """Register a new acquirer route with state persisted in Redis."""
        if not isinstance(acquirer_id, str) or not acquirer_id.strip():
            raise ValueError("acquirer_id must be a non-empty string")

        clean_id = acquirer_id.strip()
        if clean_id in self._redis_acquirers:
            raise ValueError(f"Acquirer '{clean_id}' is already registered")

        effective_config = config or self._default_config
        state = RedisAcquirerState(
            acquirer_id=clean_id,
            store=self._store,
            config=effective_config,
            initial_timestamp=initial_timestamp,
        )
        self._redis_acquirers[clean_id] = state
        self._acquirers[clean_id] = state
        return state

    def record_outcome(
        self,
        acquirer_id: str,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Record outcome for a specific acquirer in Redis and return updated snapshot."""
        state = self._redis_acquirers.get(acquirer_id)
        if state is None:
            # Check if acquirer exists in Redis (e.g. registered by another process)
            if self._store.exists(acquirer_id):
                state = self.register_acquirer(acquirer_id)
            else:
                raise KeyError(f"Acquirer '{acquirer_id}' not found in registry")
        return state.record_outcome(success=success, timestamp=timestamp)

    def sample_all(self, rng: np.random.Generator | None = None) -> dict[str, float]:
        """Draw independent Thompson samples across all registered acquirers."""
        generator = rng if rng is not None else np.random.default_rng()
        return {
            acquirer_id: state.sample(rng=generator)
            for acquirer_id, state in self._redis_acquirers.items()
        }

    def get_state(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Return state snapshot for a single acquirer directly from Redis."""
        state = self._redis_acquirers.get(acquirer_id)
        if state is None:
            if self._store.exists(acquirer_id):
                state = self.register_acquirer(acquirer_id)
            else:
                raise KeyError(f"Acquirer '{acquirer_id}' not found in registry")
        return state.get_state()

    def get_all_states(self) -> dict[str, AcquirerStateSnapshot]:
        """Return state snapshots across all registered acquirers."""
        return {
            acquirer_id: state.get_state() for acquirer_id, state in self._redis_acquirers.items()
        }

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all registered acquirer identifiers."""
        # Union local instances with Redis discovery set
        redis_ids = set(self._store.list_acquirer_ids())
        local_ids = set(self._redis_acquirers.keys())
        return sorted(list(redis_ids.union(local_ids)))

    def hydrate_all_from_redis(
        self,
        default_config: AcquirerStateConfig | None = None,
    ) -> list[str]:
        """Discover all acquirers in Redis and register local state adapters."""
        all_ids = self._store.list_acquirer_ids()
        for aid in all_ids:
            if aid not in self._redis_acquirers:
                self.register_acquirer(aid, config=default_config)
        return self.list_acquirer_ids()
