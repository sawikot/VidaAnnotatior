"""Small geometry helpers shared by the exporters.

Annotation shapes arrive as plain lists of [x, y]. Freehand drawing can easily
produce self-intersecting outlines, so areas are computed on a *repaired*
polygon (shapely.make_valid) instead of the naive shoelace sum, which gives a
wrong (signed, partly cancelling) answer for a figure-eight.
"""
from __future__ import annotations

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
