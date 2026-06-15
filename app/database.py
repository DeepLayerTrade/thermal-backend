"""Persistenz-Schicht: Redis (Live-State) + PostgreSQL (Historie).

Redis:
  - Thermiksäulen als JSON-Liste, TTL = thermal_ttl_seconds + 60 s Puffer.
  - Überlebt Server-Neustarts; Zustand in ~1 s wiederhergestellt.

PostgreSQL:
  - Tabelle thermal_history: eine Zeile pro Säulen-Snapshot (alle 60 s).
  - Basis für spätere Heatmap / Saisonauswertung.
"""

import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import Column, DateTime, Float, Integer, String, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings
from .models import ThermalCluster

log = logging.getLogger(__name__)

# ─── Redis ────────────────────────────────────────────────────────────────────

_redis: aioredis.Redis | None = None
REDIS_KEY = "thermal:live_clusters"


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def save_clusters_to_redis(clusters: list[ThermalCluster]) -> None:
    try:
        r = await get_redis()
        payload = json.dumps([c.model_dump(mode="json") for c in clusters])
        ttl = settings.thermal_ttl_seconds + 60
        await r.set(REDIS_KEY, payload, ex=ttl)
    except Exception as e:
        log.warning("Redis save fehlgeschlagen: %s", e)


async def load_clusters_from_redis() -> list[ThermalCluster]:
    try:
        r = await get_redis()
        raw = await r.get(REDIS_KEY)
        if not raw:
            return []
        data = json.loads(raw)
        return [ThermalCluster(**d) for d in data]
    except Exception as e:
        log.warning("Redis load fehlgeschlagen: %s", e)
        return []


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


# ─── PostgreSQL ───────────────────────────────────────────────────────────────

_engine = None
_session_factory: async_sessionmaker | None = None


class Base(DeclarativeBase):
    pass


class ThermalHistory(Base):
    """Snapshot einer Thermiksäule zu einem bestimmten Zeitpunkt."""
    __tablename__ = "thermal_history"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    thermal_id = Column(String, nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))
    lat        = Column(Float, nullable=False)
    lon        = Column(Float, nullable=False)
    climb_ms   = Column(Float, nullable=False)
    avg_climb_ms = Column(Float, nullable=False)
    glider_count = Column(Integer, nullable=False)
    alt_min_m  = Column(Float, nullable=False)
    alt_max_m  = Column(Float, nullable=False)
    region_id  = Column(String, nullable=True)


async def init_db() -> None:
    global _engine, _session_factory
    db_url = settings.database_url.replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    _engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("DB-Schema initialisiert")


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


async def record_cluster_snapshot(cluster: ThermalCluster) -> None:
    """Schreibt einen Säulen-Snapshot in die Historie."""
    if _session_factory is None:
        return
    try:
        async with _session_factory() as session:
            row = ThermalHistory(
                thermal_id=cluster.id,
                recorded_at=datetime.now(timezone.utc),
                lat=cluster.lat,
                lon=cluster.lon,
                climb_ms=cluster.climb_ms,
                avg_climb_ms=cluster.avg_climb_ms,
                glider_count=cluster.glider_count,
                alt_min_m=cluster.alt_min_m,
                alt_max_m=cluster.alt_max_m,
                region_id=cluster.region_id if cluster.region_id else None,
            )
            session.add(row)
            await session.commit()
    except Exception as e:
        log.warning("DB-Snapshot fehlgeschlagen: %s", e)


async def get_history(
    thermal_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Gibt historische Snapshots zurück (optional nach thermal_id gefiltert)."""
    if _session_factory is None:
        return []
    try:
        async with _session_factory() as session:
            if thermal_id:
                q = text(
                    "SELECT * FROM thermal_history WHERE thermal_id = :tid "
                    "ORDER BY recorded_at DESC LIMIT :lim"
                )
                result = await session.execute(q, {"tid": thermal_id, "lim": limit})
            else:
                q = text(
                    "SELECT * FROM thermal_history "
                    "ORDER BY recorded_at DESC LIMIT :lim"
                )
                result = await session.execute(q, {"lim": limit})
            return [dict(row._mapping) for row in result]
    except Exception as e:
        log.warning("DB-History-Query fehlgeschlagen: %s", e)
        return []
