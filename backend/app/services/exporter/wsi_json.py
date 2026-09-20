from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide

from .base import Exporter, dumps_with_line_items, load_export_data


class WSIJSONExporter(Exporter):
    """The native format: every coordinate is a global WSI Level-0 pixel, with
    the patch each shape was drawn in kept as provenance."""

    format_id = "wsi_json"
    content_type = "application/json"
    file_extension = "json"

    def export(self, db: Session, slide: Slide) -> dict:
        data = load_export_data(db, slide)
        config = data.config

        annotations = []
        for ann in data.annotations:
            patch = data.patch_by_id[ann.patch_id]
            annotations.append(
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
            "annotations": annotations,
        }

    def render(self, data: Any) -> str:
        return dumps_with_line_items(data, ("annotations",))
