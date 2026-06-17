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

    # --- Track-Validierung (echte Säule statt Vario-Spike) ---
    # Pro Segler wird ein gleitendes Fenster geführt. Eine Säule entsteht nur,
    # wenn ein Segler darin nachweislich kreisend GESTIEGEN ist. Das schlägt den
    # instantanen vario_fpm-Spike (Böe, Abfangbogen, Windenstart, Sensorrauschen):
    # dessen realisierter Steig (Δh/Δt) ist ~0.
    track_window_seconds: float = 120.0   # gleitendes Fenster je Segler
    # |rot| ist ~½-Turns/min → volle Kreise = Σ |rot|/2 · dt/60.
    min_circles: float = 3.0              # nötige volle Kreise für eine gültige Säule
    min_alt_gain_m: float = 30.0          # realer Höhengewinn in der Steigphase
    # Untergrenze für realisierten Steig — bewusst niedrig, damit schwache
    # Thermik (<1,5 m/s) sichtbar bleibt; filtert nur Nicht-Steiger raus.
    min_realized_climb_ms: float = 0.5

    # --- Persistenz (Phase 4) ---
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://thermal:thermal@localhost:5432/thermal"

    # --- API / WebSocket ---
    ws_push_interval_seconds: float = 10.0
    cors_origins: list[str] = ["*"]


settings = Settings()
