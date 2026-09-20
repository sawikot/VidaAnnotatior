"""Showing slide-level annotations inside patches.

A slide-level annotation is stored once, in Level-0 pixels. A patch, though, is a window with its own
origin and downsample, so anything patch-based (COCO images, label masks, the patch tables) needs the
part of the annotation that falls inside each patch, in that patch's own pixels:

    local = (level0 - origin) / downsample          (the inverse of "global = origin + local * downsample")

The part is clipped to the patch. A shape that crosses several patches therefore appears in each of
them as the piece that lies there; one wholly inside a patch is kept as it was (a circle stays a circle).
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely import STRtree, make_valid
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from app.models.annotation import GeometryAnnotation
from app.models.patch import Patch
from app.services.geometry import AREA_TYPES, LINE_TYPES, area_ring, circle_center_radius, clean_ring


@dataclass
class Projected:
    """The part of one slide-level annotation that lies inside one patch, in that patch's pixels."""

    annotation: GeometryAnnotation
    patch: Patch
    type: str  # circle | point | polygon | freehand_line (a clipped shape becomes a polygon / a path)
    parts: list[list[list[float]]]  # polygon rings, line paths, [centre, edge] for a circle, [[x, y]] for a point
    clipped: bool  # True when the shape was cut by the patch border

    @property
    def is_area(self) -> bool:
        return self.type in AREA_TYPES


def _round(points, digits: int = 4) -> list[list[float]]:
    return [[round(x, digits), round(y, digits)] for x, y in points]


def level0_geometry(shape_type: str, coords: list[list[float]]) -> BaseGeometry | None:
    """The shape as a shapely geometry in Level-0 pixels (a circle as a 64-sided polygon), or None if it collapsed."""
    if shape_type == "point":
        return Point(coords[0]) if coords else None
    if shape_type in LINE_TYPES:
        geometry = LineString([tuple(p[:2]) for p in coords]) if len(coords) >= 2 else None
        return geometry if geometry is not None and geometry.length > 0 else None
    if shape_type in AREA_TYPES:
        ring = clean_ring(area_ring(shape_type, coords))
        if len(ring) < 3:
            return None
        polygon = Polygon(ring)
        polygon = polygon if polygon.is_valid else make_valid(polygon)
        return polygon if polygon.area > 0 else None
    return None


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry] if geometry.area > 1e-9 else []
    if isinstance(geometry, MultiPolygon):
        return [g for g in geometry.geoms if g.area > 1e-9]
    if hasattr(geometry, "geoms"):  # a collection: keep only its polygonal pieces
        return [poly for g in geometry.geoms for poly in _polygons(g)]
    return []


def _paths(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 1e-9 else []
    if isinstance(geometry, MultiLineString):
        return [g for g in geometry.geoms if g.length > 1e-9]
    if hasattr(geometry, "geoms"):
        return [line for g in geometry.geoms for line in _paths(g)]
    return []


def project_into_patch(annotation: GeometryAnnotation, geometry: BaseGeometry, patch: Patch) -> Projected | None:
    """The piece of ``annotation`` inside ``patch`` (None if it merely touches or misses it)."""
    downsample_x = patch.width_l0 / patch.width
    downsample_y = patch.height_l0 / patch.height
    footprint = box(patch.x, patch.y, patch.x + patch.width_l0, patch.y + patch.height_l0)

    def to_local(points) -> list[list[float]]:
        return _round([((x - patch.x) / downsample_x, (y - patch.y) / downsample_y) for x, y in points])

    kind = annotation.type
    if kind == "point":
        return Projected(annotation, patch, "point", [to_local(list(geometry.coords))], clipped=False) if footprint.intersects(geometry) else None

    if kind == "circle":
        (cx, cy), r = circle_center_radius(annotation.coordinates_level0)
        if cx - r >= patch.x and cx + r <= patch.x + patch.width_l0 and cy - r >= patch.y and cy + r <= patch.y + patch.height_l0:
            return Projected(annotation, patch, "circle", [to_local(annotation.coordinates_level0[:2])], clipped=False)

    inside = geometry.intersection(footprint)
    if inside.is_empty:
        return None
    clipped = not footprint.contains(geometry)

    if kind in LINE_TYPES:
        paths = [to_local(line.coords) for line in _paths(inside)]
        if not paths:
            return None
        return Projected(annotation, patch, kind if not clipped else "freehand_line", paths, clipped)

    if not clipped and kind != "circle":
        # Wholly inside: keep the shape exactly as drawn (its own vertices, in its own order).
        return Projected(annotation, patch, kind, [to_local(annotation.coordinates_level0)], clipped=False)

    rings = [to_local(list(poly.exterior.coords)[:-1]) for poly in _polygons(inside)]
    if not rings:
        return None
    return Projected(annotation, patch, "polygon" if clipped or kind == "circle" else kind, rings, clipped)


def project_slide_annotations(annotations: list[GeometryAnnotation], patches: list[Patch]) -> dict[int, list[Projected]]:
    """For every patch, the slide-level annotations that reach it (in creation order)."""
    result: dict[int, list[Projected]] = {}
    if not annotations or not patches:
        return result

    tree = STRtree([box(p.x, p.y, p.x + p.width_l0, p.y + p.height_l0) for p in patches])
    for annotation in sorted(annotations, key=lambda a: a.id):
        geometry = level0_geometry(annotation.type, annotation.coordinates_level0)
        if geometry is None:
            continue
        for index in sorted(int(i) for i in tree.query(geometry, predicate="intersects")):
            projected = project_into_patch(annotation, geometry, patches[index])
            if projected is not None:
                result.setdefault(patches[index].id, []).append(projected)
    return result
