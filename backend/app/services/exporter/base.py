"""Shared plumbing for every export format.

An exporter turns one slide's data into a document. All formats read the same
``ExportData`` snapshot so they agree about *which* patches and annotations are
included:

* only the slide's **active** config version (older versions' patches and
  annotations stay in the database but are not mixed in),
* never annotations on patches flagged "Exclude from training".
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide

POLYGON_TYPES = ("polygon", "freehand", "rectangle")


@dataclass
class ExportData:
    slide: Slide
    config: ProjectConfigVersion | None
    patches: list[Patch]  # every patch of the active version, including excluded ones
    annotations: list[GeometryAnnotation]  # on non-excluded patches only
    all_annotations: list[GeometryAnnotation]  # including those on excluded patches (for registries)
    patch_by_id: dict[int, Patch]
    classes: dict[int, AnnotationClass]

    def class_name(self, ann: GeometryAnnotation) -> str | None:
        cls = self.classes.get(ann.class_id) if ann.class_id is not None else None
        return cls.name if cls else None


def load_export_data(db: Session, slide: Slide) -> ExportData:
    config = db.get(ProjectConfigVersion, slide.active_config_version_id) if slide.active_config_version_id else None

    patch_query = db.query(Patch).filter(Patch.slide_id == slide.id)
    ann_query = db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id == slide.id)
    if config is not None:
        patch_query = patch_query.filter(Patch.config_version_id == config.id)
        ann_query = ann_query.filter(GeometryAnnotation.config_version_id == config.id)

    patches = patch_query.order_by(Patch.patch_index.asc(), Patch.id.asc()).all()
    patch_by_id = {p.id: p for p in patches}
    all_annotations = [a for a in ann_query.order_by(GeometryAnnotation.id.asc()).all() if a.patch_id in patch_by_id]
    annotations = [a for a in all_annotations if not patch_by_id[a.patch_id].excluded]

    class_ids = {a.class_id for a in annotations if a.class_id is not None}
    if config is not None:
        classes = {c.id: c for c in config.annotation_classes}
    else:
        classes = {}
    missing = class_ids - classes.keys()
    if missing:
        classes.update({c.id: c for c in db.query(AnnotationClass).filter(AnnotationClass.id.in_(missing))})

    return ExportData(slide, config, patches, annotations, all_annotations, patch_by_id, classes)


def dumps_with_line_items(doc: dict[str, Any], big_keys: tuple[str, ...]) -> str:
    """Valid JSON, laid out for people and for size.

    Plain ``indent=2`` puts every coordinate on its own line, which makes large
    exports huge and unreadable. Here the top level is indented normally, but
    each element of the big lists (``features``, ``annotations`` ...) is one
    compact line.
    """
    parts: list[str] = []
    for key, value in doc.items():
        name = json.dumps(key)
        if key in big_keys and isinstance(value, list):
            if not value:
                parts.append(f"  {name}: []")
                continue
            items = ",\n".join("    " + json.dumps(v, separators=(",", ":")) for v in value)
            parts.append(f"  {name}: [\n{items}\n  ]")
        else:
            body = json.dumps(value, indent=2).replace("\n", "\n  ")
            parts.append(f"  {name}: {body}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


class Exporter(ABC):
    format_id: str
    content_type: str
    file_extension: str

    @abstractmethod
    def export(self, db: Session, slide: Slide) -> Any:
        """The structured result (a dict for JSON formats, text for CSV)."""

    def render(self, data: Any) -> str:
        """Serialise ``export()``'s result for download."""
        return data if isinstance(data, str) else json.dumps(data, indent=2)
