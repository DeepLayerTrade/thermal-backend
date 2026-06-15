"""Pydantic-Datenmodelle für API + interne Verarbeitung.

Feldnamen bewusst snake_case → iOS dekodiert mit
JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GliderBeacon(BaseModel):
    """Ein einzelnes OGN/APRS-Positionspaket eines Seglers."""

    id: str
    lat: float
    lon: float
    alt_m: float
    vario_fpm: float = 0.0
    rot: float = 0.0                 # Drehrate aus OGN-Comment (Einheit kalibrieren)
    callsign: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)


class AltitudeBand(BaseModel):
    """Ein Höhenband für das LX9000-Histogramm."""

    alt_label: str                   # z. B. "1500m"
    avg_climb_ms: float
    sample_count: int = 0


class ThermalCluster(BaseModel):
    """Eine erkannte Thermiksäule (Aggregat kreisender Segler)."""

    id: str
    lat: float
    lon: float
    climb_ms: float = 0.0            # Peak-Steigwert
    avg_climb_ms: float = 0.0
    confidence: float = 0.0          # 0.0–1.0
    alt_max_m: float = 0.0
    alt_min_m: float = 0.0
    glider_count: int = 0
    callsigns: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utcnow)
    region_id: str = ""             # DWD-Segelflug-Gebiet
    weather_score: float = 0.0      # ICON-EU-Korrelation 0.0–1.0


class RegionSummary(BaseModel):
    """Zusammenfassung pro DWD-Segelflug-Gebiet (PCMet-Style-Übersicht)."""

    region_id: str
    name: str
    max_climb_ms: float = 0.0
    avg_climb_ms: float = 0.0
    thermal_count: int = 0
    histogram: list[AltitudeBand] = Field(default_factory=list)


class ThermalUpdate(BaseModel):
    """WebSocket-Nachricht (alle ws_push_interval_seconds)."""

    type: str = "thermal_update"
    thermals: list[ThermalCluster] = Field(default_factory=list)
    regions: list[RegionSummary] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utcnow)
