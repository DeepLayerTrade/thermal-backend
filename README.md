# thermal-backend

Live-Thermik-Erkennung aus OGN/FLARM (APRS) kombiniert mit dem DWD-Wettermodell
ICON-EU. Liefert Thermiksäulen + Gebietsübersichten an die JuFlie iOS-App via
REST und WebSocket.

> Status: **Phase 1 — Gerüst** (leere Endpoints, lauffähig). Siehe
> `../JuFlie/tasks/todo.md` für den Gesamtplan.

## Lokal starten (ohne Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Test:

```bash
curl http://localhost:8000/healthz          # {"status":"ok",...}
curl http://localhost:8000/api/thermals     # []
# WebSocket (alle 10s ein leeres thermal_update):
# websocat ws://localhost:8000/ws/thermals
```

## Mit Docker (für Server / Phase 4)

```bash
cp .env.example .env
docker compose up --build
```

## Struktur

```
app/
  main.py             FastAPI: REST /api/* + WS /ws/thermals
  models.py           Pydantic-Modelle (snake_case API)
  config.py           Settings via .env
  aprs_client.py      (Phase 2) OGN TCP-Client
  thermal_detector.py (Phase 2) Kreisflug + Clustering
  weather_fetcher.py  (Phase 3) ICON-EU GRIB2
  thermal_scorer.py   (Phase 3) Scoring
  database.py         (Phase 4) Redis + PostGIS
```

## Endpoints

| Methode | Pfad | Zweck |
|---------|------|-------|
| GET | `/healthz` | Health-Check |
| GET | `/api/thermals` | aktive Thermiksäulen |
| GET | `/api/thermals/{id}` | Detail einer Säule |
| GET | `/api/regions` | DWD-Gebietsübersicht |
| GET | `/api/regions/{id}/histogram` | LX9000-Histogrammdaten |
| GET | `/api/weather/grid` | Wettermodell als GeoJSON |
| WS  | `/ws/thermals` | Live-Stream (Push alle 10 s) |
