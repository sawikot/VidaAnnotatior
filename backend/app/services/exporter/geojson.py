from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import closed_ring, has_area, is_simple_polygon, polygon_area

from .base import POLYGON_TYPES, Exporter, dumps_with_line_items, load_export_data


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

    def export(self, db: Session, slide: Slide) -> dict:
        data = load_export_data(db, slide)
        features: list[dict[str, Any]] = []
        skipped_degenerate = 0

        for ann in data.annotations:
            coords = ann.coordinates_level0
            if ann.type in POLYGON_TYPES:
                if not has_area(coords):
                    skipped_degenerate += 1  # fewer than 3 distinct points, or all on one line
                    continue
                geometry: dict[str, Any] = {"type": "Polygon", "coordinates": [closed_ring(coords)]}
            elif ann.type == "point" and coords:
                geometry = {"type": "Point", "coordinates": [round(coords[0][0], 2), round(coords[0][1], 2)]}
            else:
                skipped_degenerate += 1
                continue

            patch = data.patch_by_id[ann.patch_id]
            label = data.class_name(ann)
            properties: dict[str, Any] = {
                "objectType": "annotation",
                "annotation_id": f"ann_{ann.id:06d}",
                "shape_type": ann.type,
                "label": label,
                "patch_id": patch.id,
                "patch_x": patch.x,
                "patch_y": patch.y,
                "patch_level": patch.level,
                "unsure": ann.unsure,
                "flagged": ann.flagged,
                "created_by": ann.created_by,
            }
            if label:
                cls = data.classes[ann.class_id]
                properties["classification"] = {"name": label, "color": hex_to_rgb(cls.color_hex)}
            if ann.type in POLYGON_TYPES:
                properties["area_px2"] = round(polygon_area(coords), 2)
                # False for a self-crossing outline (usually a freehand slip);
                # the shape is exported as drawn so nothing is silently altered.
                properties["valid_geometry"] = is_simple_polygon(coords)

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
