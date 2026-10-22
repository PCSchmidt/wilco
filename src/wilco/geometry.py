"""Plane geometry for the safety monitor. Metres, local frame. Polygons are lists of (x, y) vertices."""

from __future__ import annotations

import math

Point = tuple[float, float]


def cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def point_seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n2 = dx * dx + dy * dy
    if n2 == 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = cross(a, b, c), cross(a, b, d)
    d3, d4 = cross(c, d, a), cross(c, d, b)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    def on(p: Point, q: Point, r: Point) -> bool:
        return (
            min(p[0], q[0]) <= r[0] <= max(p[0], q[0])
            and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
        )

    return (
        (d1 == 0 and on(a, b, c))
        or (d2 == 0 and on(a, b, d))
        or (d3 == 0 and on(c, d, a))
        or (d4 == 0 and on(c, d, b))
    )


def seg_seg_dist(a: Point, b: Point, c: Point, d: Point) -> float:
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(
        point_seg_dist(a, c, d),
        point_seg_dist(b, c, d),
        point_seg_dist(c, a, b),
        point_seg_dist(d, a, b),
    )


def point_in_polygon(p: Point, poly: list[Point]) -> bool:
    """Ray casting; points exactly on the boundary may go either way (callers always use a margin)."""
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                inside = not inside
    return inside


def dist_to_boundary(p: Point, poly: list[Point]) -> float:
    n = len(poly)
    return min(point_seg_dist(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def point_ok(p: Point, poly: list[Point], margin: float) -> bool:
    """Inside the polygon and at least `margin` from its boundary."""
    return point_in_polygon(p, poly) and dist_to_boundary(p, poly) >= margin


def segment_ok(a: Point, b: Point, poly: list[Point], margin: float) -> bool:
    """The whole segment is inside the polygon and at least `margin` from its boundary.

    Both ends inside plus distance to every edge at least `margin` (> 0) rules out any crossing, so this is
    exact for simple polygons, convex or not.
    """
    if not (point_ok(a, poly, margin) and point_ok(b, poly, margin)):
        return False
    n = len(poly)
    return all(seg_seg_dist(a, b, poly[i], poly[(i + 1) % n]) >= margin for i in range(n))


def circle_ok(c: Point, r: float, poly: list[Point], margin: float) -> bool:
    """The whole circle of radius r about c is inside the polygon with `margin` to spare."""
    return point_in_polygon(c, poly) and dist_to_boundary(c, poly) >= r + margin


def is_convex_ccw(poly: list[Point]) -> bool:
    n = len(poly)
    if n < 3:
        return False
    return all(cross(poly[i], poly[(i + 1) % n], poly[(i + 2) % n]) > 0 for i in range(n))


def is_simple(poly: list[Point]) -> bool:
    n = len(poly)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if segments_intersect(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                return False
    return True
