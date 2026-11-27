"""Small geometry helpers shared by the exporters.

Annotation shapes arrive as plain lists of [x, y]. Freehand drawing can easily
produce self-intersecting outlines, so areas are computed on a *repaired*
polygon (shapely.make_valid) instead of the naive shoelace sum, which gives a
wrong (signed, partly cancelling) answer for a figure-eight.
"""
from __future__ import annotations

import math

from shapely import make_valid
from shapely.geometry import Polygon

Point = tuple[float, float]


def clean_ring(coords: list[list[float]]) -> list[Point]:
    """The polygon's vertices without consecutive duplicates or a repeated
    closing vertex (a double-click while drawing leaves near-duplicates)."""
    ring: list[Point] = []
    for x, y in coords:
        pt = (float(x), float(y))
        if not ring or pt != ring[-1]:
            ring.append(pt)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def is_simple_polygon(coords: list[list[float]]) -> bool:
    """True when the outline does not cross itself."""
    ring = clean_ring(coords)
    return len(ring) >= 3 and Polygon(ring).is_valid


def polygon_area(coords: list[list[float]]) -> float:
    ring = clean_ring(coords)
    if len(ring) < 3:
        return 0.0
    poly = Polygon(ring)
    return float((poly if poly.is_valid else make_valid(poly)).area)


def has_area(coords: list[list[float]]) -> bool:
    """False for fewer than three distinct vertices or a collapsed (collinear) outline."""
    return polygon_area(coords) > 0


def polygon_bounds(coords: list[list[float]]) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y)"""
    xs = [float(p[0]) for p in coords]
    ys = [float(p[1]) for p in coords]
    return min(xs), min(ys), max(xs), max(ys)


def closed_ring(coords: list[list[float]], digits: int = 2) -> list[list[float]]:
    """A GeoJSON linear ring: cleaned, rounded, and closed by repeating the first vertex."""
    ring = [[round(x, digits), round(y, digits)] for x, y in clean_ring(coords)]
    return ring + [ring[0]]


# --------------------------------------------------------------------------- shape kinds
#
# Every annotation is a ``type`` plus a list of [x, y] points:
#
#   point           1 point
#   line            2 points (start, end)
#   freehand_line   >= 2 points, an open path drawn by hand
#   rectangle       4 corners
#   circle          2 points: the centre, then any point on the circle
#   polygon         >= 3 vertices, clicked one by one
#   freehand        >= 3 vertices, a closed outline drawn by hand ("freehand polygon")
#
# A circle is stored as centre + edge point (not as a polygon) so it stays exact and stays a circle
# under the local -> Level-0 transform, which is a uniform scale plus a shift.

AREA_TYPES = ("polygon", "freehand", "rectangle", "circle")
LINE_TYPES = ("line", "freehand_line")
POINT_TYPES = ("point",)
SHAPE_TYPES = ("point", "line", "freehand_line", "rectangle", "circle", "polygon", "freehand")

CIRCLE_SEGMENTS = 64  # the polygon a circle becomes where a format has no circles (GeoJSON, COCO, masks)


def circle_center_radius(coords: list[list[float]]) -> tuple[Point, float]:
    (cx, cy), (ex, ey) = coords[0][:2], coords[1][:2]
    return (float(cx), float(cy)), math.hypot(float(ex) - float(cx), float(ey) - float(cy))


def circle_ring(coords: list[list[float]], segments: int = CIRCLE_SEGMENTS) -> list[list[float]]:
    """The circle as a regular polygon (open ring: the first vertex is not repeated)."""
    (cx, cy), r = circle_center_radius(coords)
    return [
        [cx + r * math.cos(2 * math.pi * i / segments), cy + r * math.sin(2 * math.pi * i / segments)]
        for i in range(segments)
    ]


def area_ring(shape_type: str, coords: list[list[float]]) -> list[list[float]]:
    """The outline of an area shape as a polygon ring (a circle is approximated)."""
    return circle_ring(coords) if shape_type == "circle" else coords


def shape_area(shape_type: str, coords: list[list[float]]) -> float:
    """Area in the squared units of ``coords``; 0 for points and lines."""
    if shape_type == "circle":
        return math.pi * circle_center_radius(coords)[1] ** 2 if len(coords) >= 2 else 0.0
    if shape_type in AREA_TYPES:
        return polygon_area(coords)
    return 0.0


def line_length(coords: list[list[float]], scale_x: float = 1.0, scale_y: float = 1.0) -> float:
    """Length of the path through the points. The scales convert pixels to a physical unit per
    axis (micrometres per pixel), so a diagonal line is right even when pixels aren't square."""
    return sum(
        math.hypot((b[0] - a[0]) * scale_x, (b[1] - a[1]) * scale_y) for a, b in zip(coords, coords[1:])
    )


def has_extent(shape_type: str, coords: list[list[float]]) -> bool:
    """False for a shape that collapsed to nothing: a zero-radius circle, a zero-length line,
    a polygon with no area. Points always have "extent"."""
    if shape_type == "point":
        return len(coords) >= 1
    if shape_type in LINE_TYPES:
        return line_length(coords) > 0
    return shape_area(shape_type, coords) > 0


def validate_shape(shape_type: str, coords: list[list[float]]) -> None:
    """Raises ValueError (with a message fit for the API) unless ``coords`` is a well-formed
    shape of that type. Degenerate-but-well-formed shapes (a self-crossing outline) are allowed."""
    if shape_type not in SHAPE_TYPES:
        raise ValueError(f"unknown shape type '{shape_type}' (expected one of: {', '.join(SHAPE_TYPES)})")
    for point in coords:
        if len(point) != 2 or not all(math.isfinite(v) for v in point):
            raise ValueError("every point must be a finite [x, y] pair")

    n = len(coords)
    if shape_type == "point" and n != 1:
        raise ValueError("a point has exactly 1 coordinate")
    if shape_type in ("line", "circle") and n != 2:
        raise ValueError(f"a {shape_type} has exactly 2 points ({'start and end' if shape_type == 'line' else 'centre and a point on the edge'})")
    if shape_type == "rectangle" and n != 4:
        raise ValueError("a rectangle has exactly 4 corner points")
    if shape_type in ("polygon", "freehand") and n < 3:
        raise ValueError(f"a {shape_type} needs at least 3 points")
    if shape_type == "freehand_line" and n < 2:
        raise ValueError("a freehand line needs at least 2 points")
    if shape_type in ("line", "circle") and not has_extent(shape_type, coords):
        raise ValueError("a line must have a length and a circle a radius greater than zero")


# Outlines are stored without points that lie within this distance (Level-0 px) of the line through the
# points kept around them. Imported outlines can carry tens of thousands of points a fraction of a pixel apart,
# which no zoom can show but every load reads. A quarter pixel keeps every corner, so an outline traced along
# pixel edges (a mask) still covers exactly the same pixels; only points on (nearly) straight runs go.
THIN_TOLERANCE_L0 = 0.25


def thin_indices(shape_type: str, coords: list, tolerance: float = THIN_TOLERANCE_L0) -> list[int] | None:
    """Which points of an outline to keep (Douglas-Peucker): a point goes when it lies within ``tolerance``
    of the segment between the points kept either side of it. The first and last always stay. None when all
    are kept (points, rectangles, circles, short shapes, or nothing to drop)."""
    n = len(coords)
    if shape_type in ("point", "rectangle", "circle") or n <= 4:
        return None
    tol2 = tolerance * tolerance
    keep = bytearray(n)
    keep[0] = keep[n - 1] = 1
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = coords[i][0], coords[i][1]
        dx, dy = coords[j][0] - ax, coords[j][1] - ay
        seg2 = dx * dx + dy * dy
        far, far_d2 = -1, tol2
        for k in range(i + 1, j):
            px, py = coords[k][0] - ax, coords[k][1] - ay
            if seg2 == 0:
                d2 = px * px + py * py
            else:
                t = (px * dx + py * dy) / seg2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                ex, ey = px - t * dx, py - t * dy
                d2 = ex * ex + ey * ey
            if d2 > far_d2:
                far, far_d2 = k, d2
        if far >= 0:
            keep[far] = 1
            stack.append((i, far))
            stack.append((far, j))
    kept = [k for k in range(n) if keep[k]]
    if len(kept) == n or len(kept) < (2 if shape_type in LINE_TYPES else 3):
        return None
    return kept


def _rounded(coords: list, digits: int) -> list:
    return [[round(p[0], digits), round(p[1], digits)] for p in coords]


def thin_outline(shape_type: str, coords: list, tolerance: float = THIN_TOLERANCE_L0, digits: int = 2) -> list:
    """An outline without the points thin_indices drops, rounded to ``digits`` decimals (float noise such
    as 45352.979999999996 goes). Its look is unchanged at any zoom, for a small part of the size."""
    if shape_type in ("point", "rectangle", "circle") or len(coords) <= 4:
        return coords
    keep = thin_indices(shape_type, coords, tolerance)
    return _rounded(coords if keep is None else [coords[i] for i in keep], digits)


def thin_annotation_coords(shape_type: str, level0: list, patch_local: list) -> tuple[list, list]:
    """Both copies of an annotation's outline thinned alike: points are chosen in Level-0 (where 1 px is
    the finest the slide has) and the same ones kept in the patch-local copy, so the two still match."""
    if shape_type in ("point", "rectangle", "circle") or len(level0) <= 4:
        return level0, patch_local
    keep = thin_indices(shape_type, level0)
    paired = len(patch_local) == len(level0)
    if keep is not None:
        level0 = [level0[i] for i in keep]
        if paired:
            patch_local = [patch_local[i] for i in keep]
    return _rounded(level0, 2), (_rounded(patch_local, 2) if paired else patch_local)


def thin_for_display(shape_type: str, coords: list, min_gap: float = 1.0) -> list:
    """A quick thinning for sending only, never stored: points under ``min_gap`` Level-0 px from the last one
    kept are dropped, rounded to 2 decimals. One pass, so cheap enough on millions of points per request;
    unlike thin_outline it may cut a corner by under a pixel, which no zoom can show."""
    if shape_type in ("point", "rectangle", "circle") or len(coords) <= 4:
        return coords
    gap2 = min_gap * min_gap
    kept = [coords[0]]
    lx, ly = coords[0][0], coords[0][1]
    for p in coords[1:-1]:
        x, y = p[0], p[1]
        if (x - lx) * (x - lx) + (y - ly) * (y - ly) >= gap2:
            kept.append(p)
            lx, ly = x, y
    kept.append(coords[-1])
    if len(kept) < (2 if shape_type in LINE_TYPES else 3):
        return coords
    return [[round(p[0], 2), round(p[1], 2)] for p in kept]
