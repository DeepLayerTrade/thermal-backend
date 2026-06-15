"""Kreisflugerkennung + Thermik-Clustering.

Kern (in-memory; Persistenz folgt Phase 4):
  1. is_circling(): |rot| >= Schwelle UND vario >= Schwelle
  2. haversine_m(): Distanz zweier GPS-Punkte in Metern
  3. ThermalDetector.add_beacon(): kreisende Segler < cluster_radius zu einer
     Thermiksäule zusammenfassen, Peak/Avg/Höhen aktualisieren
  4. decay(): Säulen älter als TTL entfernen
"""

import math
import uuid
from datetime import datetime, timedelta, timezone

from .config import settings
from .models import GliderBeacon, ThermalCluster

FPM_TO_MS = 0.00508
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Großkreisdistanz in Metern."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def is_circling(beacon: GliderBeacon) -> bool:
    """True, wenn der Beacon kreisendes Steigen anzeigt."""
    return (
        abs(beacon.rot) >= settings.circling_rot_min
        and settings.circling_vario_fpm_min <= beacon.vario_fpm <= settings.circling_vario_fpm_max
    )


class ThermalDetector:
    """Aggregiert kreisende Segler-Beacons zu Thermiksäulen."""

    def __init__(self) -> None:
        self._clusters: dict[str, ThermalCluster] = {}
        # je Cluster die zuletzt gesehenen Steigwerte (m/s) für Avg/Peak
        self._climb_samples: dict[str, list[float]] = {}

    @property
    def clusters(self) -> list[ThermalCluster]:
        return list(self._clusters.values())

    def add_beacon(self, beacon: GliderBeacon) -> ThermalCluster | None:
        """Ordnet einen Beacon einer (ggf. neuen) Thermiksäule zu.

        Gibt die betroffene Säule zurück, oder None wenn der Beacon nicht kreist.
        """
        if not is_circling(beacon):
            return None

        climb_ms = beacon.vario_fpm * FPM_TO_MS
        cluster = self._nearest_cluster(beacon.lat, beacon.lon)

        if cluster is None:
            now = datetime.now(timezone.utc)
            cluster = ThermalCluster(
                id=str(uuid.uuid4()),
                lat=beacon.lat,
                lon=beacon.lon,
                created_at=now,
                updated_at=now,
            )
            self._clusters[cluster.id] = cluster
            self._climb_samples[cluster.id] = []

        self._update_cluster(cluster, beacon, climb_ms)
        return cluster

    def _nearest_cluster(self, lat: float, lon: float) -> ThermalCluster | None:
        best: ThermalCluster | None = None
        best_dist = settings.cluster_radius_m
        for c in self._clusters.values():
            d = haversine_m(lat, lon, c.lat, c.lon)
            if d <= best_dist:
                best, best_dist = c, d
        return best

    def _update_cluster(
        self, cluster: ThermalCluster, beacon: GliderBeacon, climb_ms: float
    ) -> None:
        samples = self._climb_samples[cluster.id]
        samples.append(climb_ms)
        del samples[:-200]  # Speicher begrenzen

        # gewichteter Centroid (gleitend Richtung neuer Position)
        cluster.lat = round((cluster.lat * 3 + beacon.lat) / 4, 6)
        cluster.lon = round((cluster.lon * 3 + beacon.lon) / 4, 6)

        cluster.climb_ms = round(max(samples), 2)
        cluster.avg_climb_ms = round(sum(samples) / len(samples), 2)
        cluster.alt_max_m = round(max(cluster.alt_max_m, beacon.alt_m), 1)
        cluster.alt_min_m = (
            round(min(cluster.alt_min_m, beacon.alt_m), 1)
            if cluster.alt_min_m > 0
            else round(beacon.alt_m, 1)
        )
        if beacon.callsign and beacon.callsign not in cluster.callsigns:
            cluster.callsigns.append(beacon.callsign)
        cluster.glider_count = len(cluster.callsigns)
        cluster.confidence = min(1.0, round(cluster.glider_count * 0.25, 2))
        cluster.updated_at = datetime.now(timezone.utc)

    def decay(self, now: datetime | None = None) -> int:
        """Entfernt Säulen, die seit TTL nicht aktualisiert wurden. Gibt Anzahl zurück."""
        now = now or datetime.now(timezone.utc)
        ttl = timedelta(seconds=settings.thermal_ttl_seconds)
        stale = [cid for cid, c in self._clusters.items() if now - c.updated_at > ttl]
        for cid in stale:
            del self._clusters[cid]
            self._climb_samples.pop(cid, None)
        return len(stale)
