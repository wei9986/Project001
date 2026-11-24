"""第 6 项：车道多边形和工位停靠点。未测量的场地不能当成一张假地图。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

from vision.障碍物 import CYLINDER_DIAMETER_MM

Point = Tuple[float, float]


@dataclass(frozen=True)
class LaneMap:
    measured: bool
    polygon_mm: Tuple[Point, ...]
    stations_mm: dict
    synthetic: bool = False
    qr_region_mm: Tuple[Point, ...] = ()
    qr_search_step_mm: Optional[float] = None
    home_zones_mm: Tuple[Tuple[Point, ...], ...] = ()
    junction_mm: Optional[Point] = None
    ring_centers_mm: Optional[dict] = None
    ring_align_tolerance_mm: Optional[float] = None
    heading_tolerance_deg: Optional[float] = None
    arrival_threshold_mm: Optional[float] = None

    def station(self, name: str) -> Optional[Point]:
        if not self.measured:
            return None
        value = self.stations_mm.get(name)
        if value is None or len(value) < 2:
            return None
        return (float(value[0]), float(value[1]))

    def heading_deg(self, name: str) -> Optional[float]:
        if not self.measured:
            return None
        value = self.stations_mm.get(name)
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        return float(value[2])

    def ring_center(self, area: str, ring: object) -> Optional[Point]:
        centers = self.ring_centers_mm or {}
        area_rings = centers.get(area) if isinstance(centers.get(area), dict) else {}
        value = area_rings.get(str(ring))
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        return (float(value[0]), float(value[1]))


def _points(raw) -> Tuple[Point, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple((float(point[0]), float(point[1])) for point in raw if isinstance(point, (list, tuple)) and len(point) >= 2)


def load_lane(path: str | Path) -> LaneMap:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    measured = bool(data.get("measured"))
    polygon = _points(data.get("polygon_mm") or [])
    stations = data.get("stations_mm") or {}
    if measured and len(polygon) < 3:
        raise ValueError("已标记实测的车道至少要有三个顶点")
    step = data.get("qr_search_step_mm")
    homes = data.get("home_zones_mm") if isinstance(data.get("home_zones_mm"), dict) else {}
    zones = tuple(zone for zone in (_points(homes.get(name)) for name in ("home_a", "home_b")) if len(zone) >= 3)
    junction = _points([data.get("junction_mm")]) if isinstance(data.get("junction_mm"), (list, tuple)) else ()
    tolerance = data.get("ring_align_tolerance_mm")
    heading = data.get("heading_tolerance_deg")
    arrival = data.get("arrival_threshold_mm")
    return LaneMap(
        measured,
        polygon,
        dict(stations),
        synthetic=bool(data.get("synthetic")),
        qr_region_mm=_points(data.get("qr_board_region_mm") or []),
        qr_search_step_mm=None if step is None else float(step),
        home_zones_mm=zones,
        junction_mm=junction[0] if junction else None,
        ring_centers_mm=data.get("ring_centers_mm") if isinstance(data.get("ring_centers_mm"), dict) else {},
        ring_align_tolerance_mm=None if tolerance is None else float(tolerance),
        heading_tolerance_deg=None if heading is None else float(heading),
        arrival_threshold_mm=None if arrival is None else float(arrival),
    )


def point_inside(point: Point, polygon: Sequence[Point]) -> bool:
    """射线法。顶点不足三个时，没有任何点算在车道内。"""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        crosses = (yi > y) != (yj > y)
        if crosses:
            edge_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < edge_x:
                inside = not inside
        j = i
    return inside


def segment_inside(start: Point, end: Point, polygon: Sequence[Point], samples: int = 8) -> bool:
    """直线采样。有一点出界，整段就拒绝。"""
    if samples < 1:
        raise ValueError("采样数至少为 1")
    for index in range(samples + 1):
        scale = index / samples
        point = (start[0] + (end[0] - start[0]) * scale, start[1] + (end[1] - start[1]) * scale)
        if not point_inside(point, polygon):
            return False
    return True


def _allowed(point: Point, lane: LaneMap) -> bool:
    if point_inside(point, lane.polygon_mm):
        return True
    return any(point_inside(point, zone) for zone in lane.home_zones_mm)


def _segment_allowed(start: Point, end: Point, lane: LaneMap) -> bool:
    samples = 8
    for index in range(samples + 1):
        scale = index / samples
        point = (start[0] + (end[0] - start[0]) * scale, start[1] + (end[1] - start[1]) * scale)
        if not _allowed(point, lane):
            return False
    return True


def qr_search_points(lane: LaneMap) -> Optional[Tuple[Point, ...]]:
    """从 qr_view 起，按实测步长走二维码板区域。步长或区域为空时不编造路径。"""
    step = lane.qr_search_step_mm
    dock = lane.station("qr_view")
    if not lane.measured or dock is None or step is None or step <= 0 or len(lane.qr_region_mm) < 3:
        return None
    if not _allowed(dock, lane):
        return None
    points = [dock]
    xs = [point[0] for point in lane.qr_region_mm]
    ys = [point[1] for point in lane.qr_region_mm]
    x = min(xs)
    while x <= max(xs) + 1e-6:
        y = min(ys)
        while y <= max(ys) + 1e-6:
            candidate = (x, y)
            if point_inside(candidate, lane.qr_region_mm) and _allowed(candidate, lane):
                previous = points[-1]
                same = abs(previous[0] - candidate[0]) < 1.0 and abs(previous[1] - candidate[1]) < 1.0
                if not same and _segment_allowed(previous, candidate, lane):
                    points.append(candidate)
            y += step
        x += step
    return tuple(points)


def segment_clear_of(start: Point, end: Point, obstacles: Sequence = (), clearance_mm: Optional[float] = None) -> bool:
    """没有绕行间距时，不把这段当成已经躲开圆柱。"""
    if not obstacles:
        return True
    if clearance_mm is None or float(clearance_mm) <= 0:
        return False
    radius = CYLINDER_DIAMETER_MM / 2.0 + float(clearance_mm)
    return _path_clear((start, end), obstacles, radius)


def route_to_point(lane: LaneMap, origin: Optional[Point], target: Optional[Point]) -> Optional[Tuple[Point, Point]]:
    """搜索步只接受仍在车道或启停区内的一段。出界就不发这一步。"""
    if origin is None or target is None or not lane.measured:
        return None
    if not _segment_allowed(origin, target, lane):
        return None
    return origin, target


def _segment_distance(point: Point, start: Point, end: Point) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    scale = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2
    scale = min(1.0, max(0.0, scale))
    nearest = (start[0] + scale * dx, start[1] + scale * dy)
    return ((point[0] - nearest[0]) ** 2 + (point[1] - nearest[1]) ** 2) ** 0.5


def _path_clear(path: Sequence[Point], obstacles: Sequence, radius: Optional[float]) -> bool:
    if radius is None:
        return True
    for sight in obstacles:
        center = getattr(sight, "center_mm", None)
        if center is None:
            return False
        for index in range(len(path) - 1):
            if _segment_distance(center, path[index], path[index + 1]) < radius:
                return False
    return True


def route_to_station(
    lane: LaneMap,
    origin: Optional[Point],
    station: str,
    obstacles: Sequence = (),
    clearance_mm: Optional[float] = None,
) -> Optional[Tuple[Point, ...]]:
    """直线在车道内就走直线。出界或被圆柱挡住时，只试仍在多边形内的折线。"""
    if origin is None or not lane.measured:
        return None
    dock = lane.station(station)
    if dock is None:
        return None
    candidates = []
    if segment_inside(origin, dock, lane.polygon_mm):
        candidates.append((origin, dock))
    junction = lane.junction_mm
    if (
        junction is not None
        and segment_inside(origin, junction, lane.polygon_mm)
        and segment_inside(junction, dock, lane.polygon_mm)
    ):
        candidates.append((origin, junction, dock))
    radius = None
    if clearance_mm is not None and float(clearance_mm) > 0 and obstacles:
        radius = CYLINDER_DIAMETER_MM / 2.0 + float(clearance_mm)
        span_x = dock[0] - origin[0]
        span_y = dock[1] - origin[1]
        span = (span_x * span_x + span_y * span_y) ** 0.5
        directions = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))
        if span > 0:
            directions = ((-span_y / span, span_x / span), (span_y / span, -span_x / span))
        for sight in obstacles:
            center = getattr(sight, "center_mm", None)
            if center is None:
                continue
            for scale in (1.5, 2.0, 2.5):
                for unit_x, unit_y in directions:
                    distance = radius * scale
                    waypoint = (center[0] + unit_x * distance, center[1] + unit_y * distance)
                    if not point_inside(waypoint, lane.polygon_mm):
                        continue
                    if segment_inside(origin, waypoint, lane.polygon_mm) and segment_inside(waypoint, dock, lane.polygon_mm):
                        candidates.append((origin, waypoint, dock))
    for path in candidates:
        if _path_clear(path, obstacles, radius):
            return path
    if radius is None:
        return candidates[0] if candidates else None
    return None


def body_left_lane(point: Optional[Point], lane: LaneMap, departed_homes: bool) -> Tuple[bool, bool]:
    """离开两个启停区之后，再落到灰色车道外就结束本轮。地图未测量时不停车。"""
    if point is None or not lane.measured or len(lane.polygon_mm) < 3:
        return False, departed_homes
    inside_home = any(point_inside(point, zone) for zone in lane.home_zones_mm)
    departed = departed_homes or not inside_home
    if departed and not point_inside(point, lane.polygon_mm):
        return True, departed
    return False, departed
