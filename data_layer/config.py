"""Configuration settings for Redis and data layer persistence."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DataLayerConfig(BaseSettings):
    """Configuration settings for Redis and data layer persistence services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis connection parameters
    redis_host: str = Field(
        default="localhost",
        description="Redis host hostname or IP address.",
    )
    redis_port: int = Field(
        default=6379,
        ge=1,
        le=65535,
        description="Redis server port.",
    )
    redis_db: int = Field(
        default=0,
        ge=0,
        description="Redis database index.",
    )
    redis_password: str | None = Field(
        default=None,
        description="Optional Redis authentication password.",
    )
    redis_timeout_sec: float = Field(
        default=2.0,
        gt=0.0,
        description="Redis socket connection and operation timeout in seconds.",
    )
    redis_max_connections: int = Field(
        default=50,
        gt=0,
        description="Maximum pooled Redis client connections.",
    )

    # Key namespacing
    key_prefix: str = Field(
        default="",
        description="Optional key prefix for namespacing/isolation (e.g. 'test:').",
    )

    # Pub/Sub channels
    redis_channel_routing: str = Field(
        default="events:routing",
        description="Redis Pub/Sub channel for transaction routing events.",
    )
    redis_channel_health: str = Field(
        default="events:health",
        description="Redis Pub/Sub channel for acquirer health alerts.",
    )

    # SQLite configuration (reserved for Phase 5 Ticket C)
    sqlite_db_path: str = Field(
        default="loom_metrics.db",
        description="Path to SQLite metrics database file or ':memory:'.",
    )
    sqlite_batch_size: int = Field(
        default=20,
        ge=1,
        description="Number of records to accumulate before batch insert.",
    )
    sqlite_flush_interval_sec: float = Field(
        default=0.05,
        gt=0.0,
        description="Maximum seconds before flushing buffer to SQLite.",
    )
    sqlite_max_queue_size: int = Field(
        default=10_000,
        ge=100,
        description="Maximum in-memory buffer queue capacity before applying backpressure.",
    )
