from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import (
    AREA_TYPES,
    LINE_TYPES,
    area_ring,
    circle_center_radius,
    closed_ring,
    has_extent,
    is_simple_polygon,
    line_length,
    shape_area,
)

from .base import Exporter, dumps_with_line_items, load_export_data
from .options import ExportOptions


def hex_to_rgb(color_hex: str) -> list[int]:
    h = color_hex.lstrip("#")
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)]


class GeoJSONExporter(Exporter):
    """RFC 7946 FeatureCollection in **Level-0 pixel coordinates** (not
    longitude/latitude). Polygons become ``Polygon`` features, point
    annotations ``Point`` features. The property names follow QuPath's GeoJSON
    convention (``objectType`` / ``classification``), so the file opens there
    directly; GIS tools read it too, with the y axis pointing down as in the image.
    """

    format_id = "geojson"
    content_type = "application/geo+json"
    file_extension = "geojson"

    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> dict:
        data = load_export_data(db, slide, options)
        features: list[dict[str, Any]] = []
        skipped_degenerate = 0

        for ann in data.everything():
            coords = ann.coordinates_level0
            if ann.type != "point" and not has_extent(ann.type, coords):
                skipped_degenerate += 1  # collapsed: no area, zero length or zero radius
                continue
            if ann.type in AREA_TYPES:
                # a circle becomes a 64-sided polygon: GeoJSON has no circles
                geometry: dict[str, Any] = {"type": "Polygon", "coordinates": [closed_ring(area_ring(ann.type, coords))]}
            elif ann.type in LINE_TYPES:
                geometry = {"type": "LineString", "coordinates": [[round(x, 2), round(y, 2)] for x, y in coords]}
            elif ann.type == "point" and coords:
                geometry = {"type": "Point", "coordinates": [round(coords[0][0], 2), round(coords[0][1], 2)]}
            else:
                skipped_degenerate += 1
                continue

            patch = data.patch_by_id.get(ann.patch_id)  # None: drawn on the whole slide
            label = data.class_name(ann)
            properties: dict[str, Any] = {
                "objectType": "annotation",
                "annotation_id": f"ann_{ann.id:06d}",
                "shape_type": ann.type,
                "drawn_in": "patch" if patch is not None else "slide",
                "label": label,
                "patch_id": patch.id if patch else None,
                "patch_x": patch.x if patch else None,
                "patch_y": patch.y if patch else None,
                "patch_level": patch.level if patch else None,
                "unsure": ann.unsure,
                "flagged": ann.flagged,
                "created_by": ann.created_by,
            }
            if label:
                cls = data.classes[ann.class_id]
                properties["classification"] = {"name": label, "color": hex_to_rgb(cls.color_hex)}
            if ann.type in AREA_TYPES:
                properties["area_px2"] = round(shape_area(ann.type, coords), 2)
                # False for a self-crossing outline (usually a freehand slip);
                # the shape is exported as drawn so nothing is silently altered.
                properties["valid_geometry"] = is_simple_polygon(area_ring(ann.type, coords))
                if ann.type == "circle":
                    properties["radius_px"] = round(circle_center_radius(coords)[1], 2)
            elif ann.type in LINE_TYPES:
                properties["length_px"] = round(line_length(coords), 2)

            features.append({"type": "Feature", "id": f"ann_{ann.id:06d}", "geometry": geometry, "properties": properties})

        config = data.config
        return {
            "type": "FeatureCollection",
            "name": slide.filename,
            "virtualpatch": {
                "schema_version": "1.0",
                "coordinate_space": "level0_pixels",
                "note": "Coordinates are pixels in the slide's full-resolution (Level-0) image, y pointing down; not longitude/latitude.",
                "project_id": slide.project.slug,
                "config_version": config.version_label if config else None,
                "slide": {
                    "filename": slide.filename,
                    "width": slide.width_l0,
                    "height": slide.height_l0,
                    "mpp_x": slide.mpp_x,
                    "mpp_y": slide.mpp_y,
                },
                "feature_count": len(features),
                "skipped_degenerate_geometry": skipped_degenerate,
            },
            "features": features,
        }

    def render(self, data: Any) -> str:
        return dumps_with_line_items(data, ("features",))
