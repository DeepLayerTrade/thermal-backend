"""FastAPI-App: REST + WebSocket.

APRSClient läuft als asyncio-Background-Task (Lifespan).
ThermalDetector aggregiert kreisende Segler in-memory.
Redis: Live-State-Cache (überlebt Neustarts).
PostgreSQL: Historische Snapshots alle 60 s.
"""

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone

_SINGLE_GLIDER_GRACE = timedelta(minutes=3)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .aprs_client import APRSClient
from .config import settings
from .database import (
    close_db,
    close_redis,
    get_history,
    init_db,
    load_clusters_from_redis,
    record_cluster_snapshot,
    save_clusters_to_redis,
)
from .models import GliderBeacon, RegionSummary, ThermalCluster, ThermalUpdate
from .thermal_detector import ThermalDetector

log = logging.getLogger(__name__)
detector = ThermalDetector()

# ─── Hintergrundaufgaben ──────────────────────────────────────────────────────

async def _on_beacon(beacon: GliderBeacon) -> None:
    detector.add_beacon(beacon)


async def _persist_loop() -> None:
    """Speichert alle 30 s den Live-State nach Redis + schreibt DB-Snapshots."""
    snapshot_counter = 0
    while True:
        await asyncio.sleep(30)
        clusters = detector.clusters
        await save_clusters_to_redis(clusters)

        # DB-Snapshots alle 60 s (jeden 2. Durchlauf)
        snapshot_counter += 1
        if snapshot_counter % 2 == 0:
            for c in clusters:
                await record_cluster_snapshot(c)
            if clusters:
                log.info("DB: %d Snapshots geschrieben", len(clusters))


async def _decay_loop() -> None:
    """Entfernt abgelaufene Säulen alle 60 s."""
    while True:
        await asyncio.sleep(60)
        removed = detector.decay()
        if removed:
            log.info("Decay: %d Säulen entfernt", removed)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Persistenz initialisieren
    await init_db()

    # Live-State aus Redis wiederherstellen
    cached = await load_clusters_from_redis()
    if cached:
        for c in cached:
            detector._clusters[c.id] = c
            detector._climb_samples[c.id] = [c.avg_climb_ms]
        log.info("Redis: %d Säulen wiederhergestellt", len(cached))

    # Background-Tasks
    client = APRSClient(_on_beacon)
    tasks = [
        asyncio.create_task(client.run(),       name="aprs"),
        asyncio.create_task(_decay_loop(),      name="decay"),
        asyncio.create_task(_persist_loop(),    name="persist"),
    ]
    log.info("Lifespan: alle Tasks gestartet")

    try:
        yield
    finally:
        await client.stop()
        for t in tasks:
            t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)
        await save_clusters_to_redis(detector.clusters)  # finaler Snapshot
        await close_redis()
        await close_db()
        log.info("Lifespan: sauber gestoppt")


app = FastAPI(title="Thermal Backend", version="0.4.0", lifespan=lifespan)


def _visible_clusters() -> list[ThermalCluster]:
    """Gibt nur Säulen zurück, die die Confidence-Schwelle erfüllen.

    Einzelsegler (confidence < 0.4) werden erst nach 3 min angezeigt,
    um Kurvenflug-False-Positives herauszufiltern.
    """
    now = datetime.now(timezone.utc)
    return [
        c for c in detector.clusters
        if c.confidence >= 0.4 or (now - c.created_at) >= _SINGLE_GLIDER_GRACE
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "thermals": len(_visible_clusters()),
        "thermals_pending": len(detector.clusters) - len(_visible_clusters()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# ─── REST: Thermals ───────────────────────────────────────────────────────────

@app.get("/api/thermals", response_model=list[ThermalCluster])
async def list_thermals() -> list[ThermalCluster]:
    return _visible_clusters()


@app.get("/api/thermals/{thermal_id}", response_model=ThermalCluster)
async def get_thermal(thermal_id: str):
    for t in detector.clusters:
        if t.id == thermal_id:
            return t
    return JSONResponse(status_code=404, content={"detail": "not found"})


@app.get("/api/thermals/{thermal_id}/history")
async def thermal_history(thermal_id: str, limit: int = 100):
    rows = await get_history(thermal_id=thermal_id, limit=limit)
    return rows

# ─── REST: Regions ────────────────────────────────────────────────────────────

@app.get("/api/regions", response_model=list[RegionSummary])
async def list_regions() -> list[RegionSummary]:
    return []   # Phase 4 Teil 3 (DWD-Geometrien)


@app.get("/api/regions/{region_id}/histogram")
async def region_histogram(region_id: str):
    return JSONResponse(status_code=404, content={"detail": "not found"})

# ─── REST: History ────────────────────────────────────────────────────────────

@app.get("/api/history")
async def history(limit: int = 500):
    """Letzte DB-Snapshots aller Säulen."""
    return await get_history(limit=limit)

# ─── REST: Weather ────────────────────────────────────────────────────────────

@app.get("/api/weather/grid")
async def weather_grid() -> dict:
    return {"type": "FeatureCollection", "features": []}

# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/thermals")
async def ws_thermals(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            update = ThermalUpdate(
                thermals=_visible_clusters(),
                regions=[],
                timestamp=datetime.now(timezone.utc),
            )
            await websocket.send_text(update.model_dump_json())
            await asyncio.sleep(settings.ws_push_interval_seconds)
    except WebSocketDisconnect:
        return
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close()
