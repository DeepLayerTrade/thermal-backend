"""Zentrale Konfiguration via Umgebungsvariablen (.env)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- OGN / APRS (Phase 2) ---
    aprs_host: str = "aprs.glidernet.org"
    aprs_port: int = 14580
    aprs_callsign: str = "N0CALL"        # read-only Login
    aprs_passcode: str = "-1"            # -1 = receive only
    # Filter: r/<lat>/<lon>/<radius_km> — Default grob DACH-Zentrum
    aprs_filter: str = "r/48.0/11.0/600"

    # --- Thermik-Erkennung (Phase 2) ---
    # |rot| in OGN-Einheit (~half-turns/min). Live-Recon 2026-06-15:
    # Geradeausflug |rot| < 2, klar kreisende Segler |rot| ~ 4-5.
    # → 3.0 statt Leitfaden-Wert 0.8 (der fast alles als "kreisend" werten würde).
    circling_rot_min: float = 3.0
    circling_vario_fpm_min: float = 50.0
    circling_vario_fpm_max: float = 2000.0   # >2000 fpm (~10 m/s) = Datenmüll/Windenstart
    cluster_climb_max_ms: float = 6.0        # Säulen über 6 m/s werden nicht angezeigt
    cluster_radius_m: float = 800.0
    thermal_ttl_seconds: int = 15 * 60   # 15 min

    # --- Persistenz (Phase 4) ---
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://thermal:thermal@localhost:5432/thermal"

    # --- API / WebSocket ---
    ws_push_interval_seconds: float = 10.0
    cors_origins: list[str] = ["*"]


settings = Settings()
