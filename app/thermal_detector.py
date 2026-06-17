"""Kreisflugerkennung + Thermik-Clustering.

Statt dem instantanen OGN-`vario_fpm` zu vertrauen (das durch Böen, Abfang-
bögen, Windenstarts und Sensorrauschen kurzzeitig hohe Spitzen zeigt), wird
pro Segler ein gleitendes Zeitfenster geführt. Eine Thermiksäule entsteht nur,
wenn ein Segler darin NACHWEISLICH KREISEND GESTIEGEN ist:

  1. Kreise          = Σ |rot|/2 · dt/60   (rot ≈ ½-Turns/min)  ≥ min_circles
  2. Höhengewinn     = alt_ende − alt_min   in der Steigphase    ≥ min_alt_gain_m
  3. realisierter    = Höhengewinn / Dauer der Steigphase        ≥ min_realized_climb_ms
     Steig (Δh/Δt)                                               ≤ cluster_climb_max_ms

Der angezeigte `climb_ms` ist dieser realisierte Steig — ein Vario-Spike über
3 m Höhe ergibt ~0 m/s und erzeugt damit keine Säule mehr. Schwache, aber echte
Thermik (<1,5 m/s) bleibt sichtbar, solange wirklich gekreist und gestiegen wird.

Kern (in-memory; Persistenz in main.py über Redis/PostgreSQL):
  - haversine_m(): Distanz zweier GPS-Punkte in Metern
  - is_circling(): grober Pro-Beacon-Vorfilter (|rot| & vario im Plausibelbereich)
  - ThermalDetector.add_beacon(): Track fortschreiben, validieren, Säule bilden
  - decay(): abgelaufene Säulen + alte Tracks entfernen
"""

import math
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from .config import settings
from .models import GliderBeacon, ThermalCluster

FPM_TO_MS = 0.00508
EARTH_RADIUS_M = 6_371_000.0

# Ein Track-Sample: nur die für die Validierung nötigen Felder.
# (timestamp, alt_m, rot, lat, lon)
_Sample = tuple[datetime, float, float, float, float]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Großkreisdistanz in Metern."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def is_circling(beacon: GliderBeacon) -> bool:
    """Grober Vorfilter: Beacon zeigt kreisendes Steigen (plausibler Bereich).

    Nur ein Vorfilter zum Begrenzen der Track-Aufzeichnung — die eigentliche
    Entscheidung trifft die Track-Validierung in evaluate_track().
    """
    return (
        abs(beacon.rot) >= settings.circling_rot_min
        and settings.circling_vario_fpm_min <= beacon.vario_fpm <= settings.circling_vario_fpm_max
    )


def evaluate_track(track: "deque[_Sample]") -> tuple[float, float, float] | None:
    """Bewertet einen Segler-Track. Gibt (circles, alt_gain_m, realized_climb_ms)
    zurück, wenn er eine gültige Thermiksäule belegt, sonst None.

    - circles:        volle Kreise über das gesamte Fenster (rot-Integration)
    - alt_gain_m:     Höhengewinn vom tiefsten Punkt bis zum Ende (Steigphase)
    - realized_climb: alt_gain_m / Dauer der Steigphase  [m/s]
    """
    if len(track) < 2:
        return None

    samples = list(track)

    # 1) Kreise: |rot|/2 (volle Turns/min) über dt integriert.
    circles = 0.0
    for (t0, _a0, rot0, _la0, _lo0), (t1, *_rest) in zip(samples, samples[1:]):
        dt = (t1 - t0).total_seconds()
        if dt > 0:
            circles += abs(rot0) / 2.0 * (dt / 60.0)

    # 2) Steigphase: ab dem tiefsten Punkt im Fenster bis zum aktuellen Fix.
    alts = [s[1] for s in samples]
    i_min = min(range(len(alts)), key=alts.__getitem__)
    alt_gain = alts[-1] - alts[i_min]
    climb_duration = (samples[-1][0] - samples[i_min][0]).total_seconds()
    if climb_duration <= 0:
        return None
    realized_climb = alt_gain / climb_duration

    valid = (
        circles >= settings.min_circles
        and alt_gain >= settings.min_alt_gain_m
        and settings.min_realized_climb_ms <= realized_climb <= settings.cluster_climb_max_ms
    )
    return (circles, alt_gain, realized_climb) if valid else None


class ThermalDetector:
    """Aggregiert validierte, kreisend-steigende Segler zu Thermiksäulen."""

    def __init__(self) -> None:
        self._clusters: dict[str, ThermalCluster] = {}
        # pro Cluster der je Segler beste Beleg: glider_id -> (climb_ms, circles)
        self._cluster_gliders: dict[str, dict[str, tuple[float, float]]] = {}
        # pro Segler ein gleitendes Track-Fenster für die Validierung
        self._tracks: dict[str, deque[_Sample]] = {}

    @property
    def clusters(self) -> list[ThermalCluster]:
        return list(self._clusters.values())

    def add_beacon(self, beacon: GliderBeacon) -> ThermalCluster | None:
        """Schreibt den Segler-Track fort, validiert ihn und bildet ggf. eine Säule.

        Gibt die betroffene Säule zurück, oder None wenn der Segler (noch) keine
        gültige Thermik belegt.
        """
        if not is_circling(beacon):
            return None  # Geradeausflug/Datenmüll fließt gar nicht erst in den Track

        track = self._tracks.setdefault(beacon.id, deque())
        track.append((beacon.timestamp, beacon.alt_m, beacon.rot, beacon.lat, beacon.lon))
        self._trim_track(track, beacon.timestamp)

        metrics = evaluate_track(track)
        if metrics is None:
            return None
        _circles, _gain, realized_climb = metrics

        cluster = self._assign_cluster(beacon)
        self._update_cluster(cluster, beacon, realized_climb, _circles)
        return cluster

    def _trim_track(self, track: "deque[_Sample]", now: datetime) -> None:
        """Entfernt Samples außerhalb des gleitenden Fensters."""
        cutoff = now - timedelta(seconds=settings.track_window_seconds)
        while track and track[0][0] < cutoff:
            track.popleft()

    def _assign_cluster(self, beacon: GliderBeacon) -> ThermalCluster:
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
            self._cluster_gliders[cluster.id] = {}
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
        self, cluster: ThermalCluster, beacon: GliderBeacon, realized_climb: float, circles: float
    ) -> None:
        gliders = self._cluster_gliders[cluster.id]
        prev = gliders.get(beacon.id)
        # je Segler den stärksten belegten Steig und die meisten Kreise behalten
        best_climb = realized_climb if prev is None else max(prev[0], realized_climb)
        best_circles = circles if prev is None else max(prev[1], circles)
        gliders[beacon.id] = (best_climb, best_circles)

        # gewichteter Centroid (gleitend Richtung neuer Position)
        cluster.lat = round((cluster.lat * 3 + beacon.lat) / 4, 6)
        cluster.lon = round((cluster.lon * 3 + beacon.lon) / 4, 6)

        climbs = [v[0] for v in gliders.values()]
        cluster.climb_ms = round(max(climbs), 2)
        cluster.avg_climb_ms = round(sum(climbs) / len(climbs), 2)
        cluster.circles_max = round(max(v[1] for v in gliders.values()), 1)
        cluster.alt_max_m = round(max(cluster.alt_max_m, beacon.alt_m), 1)
        cluster.alt_min_m = (
            round(min(cluster.alt_min_m, beacon.alt_m), 1)
            if cluster.alt_min_m > 0
            else round(beacon.alt_m, 1)
        )
        if beacon.callsign and beacon.callsign not in cluster.callsigns:
            cluster.callsigns.append(beacon.callsign)
        cluster.glider_count = len(gliders)
        # 1 validierter Segler = bereits echte Säule (0.5); jeder weitere +0.25.
        cluster.confidence = min(1.0, round(0.5 + 0.25 * (cluster.glider_count - 1), 2))
        cluster.updated_at = datetime.now(timezone.utc)

    def decay(self, now: datetime | None = None) -> int:
        """Entfernt abgelaufene Säulen + alte Segler-Tracks. Gibt entfernte Säulen zurück."""
        now = now or datetime.now(timezone.utc)
        ttl = timedelta(seconds=settings.thermal_ttl_seconds)
        stale = [cid for cid, c in self._clusters.items() if now - c.updated_at > ttl]
        for cid in stale:
            del self._clusters[cid]
            self._cluster_gliders.pop(cid, None)

        window = timedelta(seconds=settings.track_window_seconds)
        dead_tracks = [gid for gid, t in self._tracks.items() if not t or now - t[-1][0] > window]
        for gid in dead_tracks:
            del self._tracks[gid]

        return len(stale)
