from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import LINE_TYPES, circle_center_radius, line_length

from .base import Exporter, dumps_with_line_items, load_export_data
from .options import ExportOptions


class WSIJSONExporter(Exporter):
    """The native format: every coordinate is a global WSI Level-0 pixel, with
    the patch each shape was drawn in kept as provenance."""

    format_id = "wsi_json"
    content_type = "application/json"
    file_extension = "json"

    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> dict:
        data = load_export_data(db, slide, options)
        config = data.config

        annotations = []
        for ann in data.annotations:
            patch = data.patch_by_id[ann.patch_id]
            entry = (
                {
                    "annotation_id": f"ann_{ann.id:06d}",
                    "type": ann.type,
                    "label": data.class_name(ann),
                    "unsure": ann.unsure,
                    "flagged": ann.flagged,
                    "source_patch": {
                        "patch_id": patch.id,
                        "x": patch.x,
                        "y": patch.y,
                        "width": patch.width_l0,
                        "height": patch.height_l0,
                        "level": patch.level,
                    },
                    "coordinates": ann.coordinates_level0,
                }
            )
            # A circle's coordinates are [centre, a point on the edge]; a line's are its vertices.
            # Both come with the derived measurement so a reader need not recompute it.
            if ann.type == "circle" and len(ann.coordinates_level0) >= 2:
                entry["radius"] = round(circle_center_radius(ann.coordinates_level0)[1], 2)
            elif ann.type in LINE_TYPES:
                entry["length"] = round(line_length(ann.coordinates_level0), 2)
            annotations.append(entry)

        return {
            "schema_version": "1.0",
            "project": {
                "project_id": slide.project.slug,
                "config_version": config.version_label if config else None,
            },
            "slide": {
                "slide_id": slide.filename,
                "filename": slide.filename,
                "width": slide.width_l0,
                "height": slide.height_l0,
                "coordinate_level": 0,
                "mpp_x": slide.mpp_x,
                "mpp_y": slide.mpp_y,
            },
            "patch_configuration": {
                "width": config.patch_width if config else None,
                "height": config.patch_height if config else None,
                "stride_x": config.stride_x if config else None,
                "stride_y": config.stride_y if config else None,
                "minimum_tissue_fraction": config.min_tissue_fraction if config else None,
            },
            # The patches the export covers -- with the empty ones when asked for -- so a
            # consumer can tell "nothing there" from "not exported".
            "patches": [
                {
                    "patch_id": p.id,
                    "patch_index": p.patch_index,
                    "x": p.x,
                    "y": p.y,
                    "width": p.width_l0,
                    "height": p.height_l0,
                    "level": p.level,
                    "width_px": p.width,
                    "height_px": p.height,
                    "tissue_fraction": round(p.tissue_fraction, 4),
                    "status": p.status,
                    "label": p.patch_label,
                    "annotation_count": data.annotation_count(p),
                    "unsure": p.unsure,
                    "flagged": p.flagged,
                    "excluded": p.excluded,
                }
                for p in data.patches
            ],
            "annotations": annotations,
        }

    def render(self, data: Any) -> str:
        return dumps_with_line_items(data, ("patches", "annotations"))
