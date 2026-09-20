from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import has_area, polygon_area, polygon_bounds

from .base import POLYGON_TYPES, Exporter, dumps_with_line_items, load_export_data


def _flat(points: list[list[float]]) -> list[float]:
    return [round(v, 2) for p in points for v in p[:2]]


class COCOExporter(Exporter):
    """COCO instance-segmentation JSON.

    Patches are virtual, so each patch that has annotations becomes one COCO
    "image": its ``width``/``height`` are the patch's pixel size at its read
    level and every ``segmentation``/``bbox``/``area`` is in **that image's own
    pixel space**, which is what training frameworks (Detectron2, MMDetection,
    YOLO converters) expect. The pixels are not stored anywhere; ``coco_url`` is
    the (relative) API path that renders that exact patch from the original slide.

    Nothing about position on the slide is lost: each image records its
    Level-0 origin, and each annotation carries ``vp_level0_segmentation``, the
    same polygon in global Level-0 pixels.

    COCO has no point type and requires a category, so point annotations and
    unclassified shapes are left out and counted in ``info.vp_skipped`` rather
    than dropped silently.
    """

    format_id = "coco"
    content_type = "application/json"
    file_extension = "json"

    def export(self, db: Session, slide: Slide) -> dict:
        data = load_export_data(db, slide)
        config = data.config
        stem = slide.filename.rsplit(".", 1)[0]

        skipped = {"point_annotations": 0, "unclassified": 0, "degenerate_geometry": 0}
        annotations: list[dict[str, Any]] = []
        used_patch_ids: list[int] = []

        for ann in data.annotations:
            if ann.type not in POLYGON_TYPES:
                skipped["point_annotations"] += 1
                continue
            if ann.class_id is None or ann.class_id not in data.classes:
                skipped["unclassified"] += 1
                continue
            local, level0 = ann.coordinates_patch_local, ann.coordinates_level0
            if not has_area(local):
                skipped["degenerate_geometry"] += 1
                continue

            min_x, min_y, max_x, max_y = polygon_bounds(local)
            annotations.append(
                {
                    "id": ann.id,
                    "image_id": ann.patch_id,
                    "category_id": ann.class_id,
                    "segmentation": [_flat(local)],
                    "area": round(polygon_area(local), 2),
                    "bbox": [round(min_x, 2), round(min_y, 2), round(max_x - min_x, 2), round(max_y - min_y, 2)],
                    "iscrowd": 0,
                    "vp_level0_segmentation": [_flat(level0)],
                    "vp_shape_type": ann.type,
                    "vp_unsure": ann.unsure,
                    "vp_flagged": ann.flagged,
                }
            )
            if ann.patch_id not in used_patch_ids:
                used_patch_ids.append(ann.patch_id)

        images = []
        for patch_id in sorted(used_patch_ids):
            p = data.patch_by_id[patch_id]
            images.append(
                {
                    "id": p.id,
                    "file_name": f"{stem}_p{p.patch_index}_x{p.x}_y{p.y}_L{p.level}.png",
                    "width": p.width,
                    "height": p.height,
                    "coco_url": f"/api/slides/{slide.id}/patch?x={p.x}&y={p.y}&width={p.width}&height={p.height}&level={p.level}",
                    "vp_slide": slide.filename,
                    "vp_patch_index": p.patch_index,
                    "vp_origin_level0": [p.x, p.y],
                    "vp_read_level": p.level,
                    "vp_downsample": round(p.width_l0 / p.width, 6) if p.width else 1.0,
                    "vp_footprint_level0": [p.width_l0, p.height_l0],
                }
            )

        categories = [
            {"id": c.id, "name": c.name, "supercategory": "tissue", "vp_color": c.color_hex}
            for c in sorted(data.classes.values(), key=lambda c: (c.order_index, c.id))
        ]

        return {
            "info": {
                "description": f"{slide.filename} annotations (VirtualPatch WSI Annotator)",
                "version": "1.0",
                "year": datetime.now(timezone.utc).year,
                "contributor": "VirtualPatch WSI Annotator",
                "date_created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "vp_project_id": slide.project.slug,
                "vp_config_version": config.version_label if config else None,
                "vp_slide": {
                    "filename": slide.filename,
                    "width_level0": slide.width_l0,
                    "height_level0": slide.height_l0,
                    "mpp_x": slide.mpp_x,
                    "mpp_y": slide.mpp_y,
                },
                "vp_coordinate_note": (
                    "segmentation/bbox/area are in each image's own pixel space (the patch at its read level). "
                    "vp_level0_segmentation gives the same polygon in global Level-0 pixels."
                ),
                "vp_skipped": skipped,
            },
            "licenses": [],
            "images": images,
            "annotations": annotations,
            "categories": categories,
        }

    def render(self, data: Any) -> str:
        return dumps_with_line_items(data, ("images", "annotations", "categories"))
