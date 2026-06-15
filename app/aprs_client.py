"""OGN/APRS-Client: verbindet zu aprs.glidernet.org, parst Segler-Beacons.

Liefert geparste GliderBeacon-Objekte per async Callback. Auto-Reconnect mit
exponentiellem Backoff. Read-only Login (N0CALL / passcode -1).

Format-Referenz (Live-Recon 2026-06-15), FLARM-Segler:
    ICA4B1B17>OGFLR,qAS,LSZM:/090410h4707.32N/00906.29E'115/090/A=007491 \
        !W69! id094B1B17 +1030fpm +0.2rot 23.5dB -10.9kHz gps2x3
Nur Pakete mit fpm+rot (FLARM/OGN-Segler) sind für Kreisflug relevant;
ADS-B-Pakete (OGADSB) haben diese Felder nicht und werden verworfen.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from .config import settings
from .models import GliderBeacon

logger = logging.getLogger(__name__)

FEET_TO_M = 0.3048
FPM_TO_MS = 0.00508  # 1 ft/min = 0.00508 m/s

# Position: DDMM.mmN / DDDMM.mmE  (APRS-Standardpräzision)
_RE_LATLON = re.compile(
    r"(?P<lat_d>\d{2})(?P<lat_m>\d{2}\.\d+)(?P<ns>[NS])"
    r"[/\\]"
    r"(?P<lon_d>\d{3})(?P<lon_m>\d{2}\.\d+)(?P<ew>[EW])"
)
_RE_ALT = re.compile(r"/A=(-?\d+)")
_RE_FPM = re.compile(r"([-+]?\d+)fpm")
_RE_ROT = re.compile(r"([-+]?\d+\.?\d*)rot")
_RE_ID = re.compile(r"\bid[0-9A-Fa-f]{2}([0-9A-Fa-f]{6})\b")

BeaconCallback = Callable[[GliderBeacon], Awaitable[None]]


def parse_aprs_packet(line: str) -> GliderBeacon | None:
    """Parst eine APRS-Zeile zu einem GliderBeacon, oder None wenn irrelevant.

    Verwirft Server-Kommentare (#), Nicht-Positionspakete und Pakete ohne
    fpm+rot (z. B. ADS-B), da diese für die Kreisflugerkennung nutzlos sind.
    """
    if not line or line.startswith("#"):
        return None
    if ">" not in line or ":" not in line:
        return None

    callsign, _, rest = line.partition(">")
    payload = rest.partition(":")[2]
    if not payload:
        return None

    m_pos = _RE_LATLON.search(payload)
    m_fpm = _RE_FPM.search(payload)
    m_rot = _RE_ROT.search(payload)
    # Nur FLARM/OGN-Segler (mit Steig- UND Drehrate) sind relevant.
    if not (m_pos and m_fpm and m_rot):
        return None

    lat = int(m_pos["lat_d"]) + float(m_pos["lat_m"]) / 60.0
    if m_pos["ns"] == "S":
        lat = -lat
    lon = int(m_pos["lon_d"]) + float(m_pos["lon_m"]) / 60.0
    if m_pos["ew"] == "W":
        lon = -lon

    m_alt = _RE_ALT.search(payload)
    alt_m = int(m_alt.group(1)) * FEET_TO_M if m_alt else 0.0

    m_id = _RE_ID.search(payload)
    beacon_id = m_id.group(1) if m_id else callsign

    return GliderBeacon(
        id=beacon_id,
        lat=round(lat, 6),
        lon=round(lon, 6),
        alt_m=round(alt_m, 1),
        vario_fpm=float(m_fpm.group(1)),
        rot=float(m_rot.group(1)),
        callsign=callsign,
        timestamp=datetime.now(timezone.utc),
    )


class APRSClient:
    """Persistente OGN-Verbindung mit Reconnect + Beacon-Callback."""

    def __init__(self, on_beacon: BeaconCallback) -> None:
        self._on_beacon = on_beacon
        self._running = False

    async def run(self) -> None:
        """Verbindet dauerhaft; reconnectet mit exponentiellem Backoff (2..60s)."""
        self._running = True
        backoff = 2.0
        while self._running:
            try:
                await self._connect_and_read()
                backoff = 2.0  # erfolgreiche Session → Backoff zurücksetzen
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — Reconnect bei allem
                logger.warning("APRS-Verbindung verloren: %s — retry in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def stop(self) -> None:
        self._running = False

    async def _connect_and_read(self) -> None:
        reader, writer = await asyncio.open_connection(settings.aprs_host, settings.aprs_port)
        login = (
            f"user {settings.aprs_callsign} pass {settings.aprs_passcode} "
            f"vers JuFlieThermal 0.1 filter {settings.aprs_filter}\r\n"
        )
        writer.write(login.encode())
        await writer.drain()
        logger.info("APRS verbunden (%s:%s)", settings.aprs_host, settings.aprs_port)

        try:
            while self._running:
                raw = await reader.readline()
                if not raw:
                    raise ConnectionError("Stream beendet")
                line = raw.decode("utf-8", "replace").strip()
                beacon = parse_aprs_packet(line)
                if beacon is not None:
                    await self._on_beacon(beacon)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
