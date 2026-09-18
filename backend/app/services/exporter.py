"""Export format registry. Only WSIJSONExporter is implemented for the MVP;
others are registered so the API surface is stable and future formats are a
matter of adding a class, not restructuring callers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from app.models.annotation import GeometryAnnotation
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide


class Exporter(ABC):
    format_id: str
    content_type: str
    file_extension: str

    @abstractmethod
    def export(self, db: Session, slide: Slide) -> Any: ...


class NotImplementedExporter(Exporter):
    def __init__(self, format_id: str):
        self.format_id = format_id
        self.content_type = "application/json"
        self.file_extension = "json"

    def export(self, db: Session, slide: Slide) -> Any:
        raise NotImplementedError(
            f"Exporter '{self.format_id}' is architected but not yet implemented in this MVP."
        )


class WSIJSONExporter(Exporter):
    format_id = "wsi_json"
    content_type = "application/json"
    file_extension = "json"

    def export(self, db: Session, slide: Slide) -> dict:
        project = slide.project
        config: ProjectConfigVersion | None = (
            db.get(ProjectConfigVersion, slide.active_config_version_id)
            if slide.active_config_version_id
            else None
        )

        annotations = (
            db.query(GeometryAnnotation)
            .join(Patch, GeometryAnnotation.patch_id == Patch.id)
            .filter(GeometryAnnotation.slide_id == slide.id)
            .filter(Patch.excluded.is_(False))
            .all()
        )

        annotation_payload = []
        for ann in annotations:
            patch: Patch = ann.patch
            annotation_payload.append(
                {
                    "annotation_id": f"ann_{ann.id:06d}",
                    "type": ann.type,
                    "label": ann.annotation_class.name if ann.annotation_class else None,
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
                "project_id": project.slug,
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
            "annotations": annotation_payload,
        }


REGISTRY: dict[str, Exporter] = {
    "wsi_json": WSIJSONExporter(),
    "geojson": NotImplementedExporter("geojson"),
    "coco": NotImplementedExporter("coco"),
    "patch_csv": NotImplementedExporter("patch_csv"),
    "stats_csv": NotImplementedExporter("stats_csv"),
}


def get_exporter(format_id: str) -> Exporter:
    if format_id not in REGISTRY:
        raise ValueError(f"Unknown export format '{format_id}'")
    return REGISTRY[format_id]
