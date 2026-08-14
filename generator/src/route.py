"""Read a real route out of the database, to hang synthetic channels on.

DEC-18 asks synthetic data to follow the rules the real data sets. For the
magnetometer and the IMU that means more than plausible numbers: both depend
entirely on how the platform was moving, so the attitude has to come from
somewhere real. The geo-referenced particle rides are the only mobile
measurements in the whole project, so a ride supplies the path and the
synthetic channels are computed against it.

The coordinates are genuine. The timestamps are not, twice over: the particle
logs carry no clock at all (OPEN-10), and the synthetic mission re-anchors the
ride to a daylight hour so the UV curve means something.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class RoutePoint:
    seq: int
    latitude: float
    longitude: float
    heading_deg: float
    speed_ms: float
    yaw_rate_dps: float


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from one point to the next, 0 to 360."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _angle_difference(a: float, b: float) -> float:
    """Signed shortest turn from b to a, in degrees."""
    return (a - b + 180.0) % 360.0 - 180.0


def load_route(connection, mission_id: str, sample_period_s: float = 1.0) -> list[RoutePoint]:
    """Positions in order, with heading, speed and turn rate derived from them."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT seq, latitude, longitude
            FROM position
            WHERE mission_id = %s
            ORDER BY seq
            """,
            (mission_id,),
        )
        rows = cursor.fetchall()

    if len(rows) < 2:
        return []

    headings: list[float] = []
    speeds: list[float] = []
    for i in range(len(rows)):
        # The last point has no successor, so it reuses the previous step.
        j = min(i + 1, len(rows) - 1)
        k = i if j > i else i - 1
        _, lat_a, lon_a = rows[k]
        _, lat_b, lon_b = rows[j]
        if (lat_a, lon_a) == (lat_b, lon_b):
            headings.append(headings[-1] if headings else 0.0)
            speeds.append(speeds[-1] if speeds else 0.0)
        else:
            headings.append(bearing_deg(lat_a, lon_a, lat_b, lon_b))
            speeds.append(distance_m(lat_a, lon_a, lat_b, lon_b) / sample_period_s)

    points: list[RoutePoint] = []
    for i, (seq, latitude, longitude) in enumerate(rows):
        previous = headings[i - 1] if i > 0 else headings[i]
        yaw_rate = _angle_difference(headings[i], previous) / sample_period_s
        points.append(
            RoutePoint(
                seq=int(seq),
                latitude=float(latitude),
                longitude=float(longitude),
                heading_deg=headings[i],
                # A GPS trace of a bike ride is noisy enough to produce silly
                # instantaneous speeds; clamp to something a cyclist can do.
                speed_ms=min(speeds[i], 15.0),
                yaw_rate_dps=max(-90.0, min(90.0, yaw_rate)),
            )
        )
    return points


def smooth(points: list[RoutePoint], window: int = 5) -> list[RoutePoint]:
    """Rolling mean over speed and yaw rate.

    Per-sample GPS jitter would otherwise show up as violent steering that the
    rider never did, and the IMU is computed from exactly those two numbers.
    """
    if window < 2 or len(points) < window:
        return points

    half = window // 2
    smoothed: list[RoutePoint] = []
    for i, point in enumerate(points):
        lo, hi = max(0, i - half), min(len(points), i + half + 1)
        span = points[lo:hi]
        smoothed.append(
            RoutePoint(
                seq=point.seq,
                latitude=point.latitude,
                longitude=point.longitude,
                heading_deg=point.heading_deg,
                speed_ms=sum(p.speed_ms for p in span) / len(span),
                yaw_rate_dps=sum(p.yaw_rate_dps for p in span) / len(span),
            )
        )
    return smoothed
