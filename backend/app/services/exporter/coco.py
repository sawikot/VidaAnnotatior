from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import AREA_TYPES, LINE_TYPES, area_ring, has_extent, polygon_area, polygon_bounds

from .base import Exporter, dumps_with_line_items, load_export_data
from .options import ExportOptions
from .patch_images import patch_image_name


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
    mergeable = True

    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> dict:
        options = options or ExportOptions()
        data = load_export_data(db, slide, options)
        config = data.config

        skipped = {"point_annotations": 0, "line_annotations": 0, "unclassified": 0, "degenerate_geometry": 0}
        annotations: list[dict[str, Any]] = []
        used_patch_ids: list[int] = []

        for ann in data.annotations:
            if ann.type not in AREA_TYPES:
                skipped["line_annotations" if ann.type in LINE_TYPES else "point_annotations"] += 1
                continue
            if ann.class_id is None or ann.class_id not in data.classes:
                skipped["unclassified"] += 1
                continue
            if not has_extent(ann.type, ann.coordinates_patch_local):
                skipped["degenerate_geometry"] += 1
                continue
            # COCO only knows polygons, so a circle goes in as its 64-sided outline.
            local, level0 = area_ring(ann.type, ann.coordinates_patch_local), area_ring(ann.type, ann.coordinates_level0)

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

        # Slide-level annotations reach COCO through the patches they cover: the piece inside each patch,
        # in that patch's pixels. Each piece needs its own unique id (the annotation may span many
        # patches), so it is the annotation's id and the patch's id combined.
        for slide_ann in data.slide_annotations:
            if slide_ann.type not in AREA_TYPES:
                skipped["line_annotations" if slide_ann.type in LINE_TYPES else "point_annotations"] += 1
            elif slide_ann.class_id is None or slide_ann.class_id not in data.classes:
                skipped["unclassified"] += 1
            elif not has_extent(slide_ann.type, slide_ann.coordinates_level0):
                skipped["degenerate_geometry"] += 1

        for patch in data.patches:
            if patch.excluded:
                continue
            scale_x, scale_y = patch.width_l0 / patch.width, patch.height_l0 / patch.height
            for piece in data.projections.get(patch.id, []):
                ann = piece.annotation
                if not piece.is_area or ann.class_id is None or ann.class_id not in data.classes:
                    continue
                rings = [area_ring("circle", part) if piece.type == "circle" else part for part in piece.parts]
                xs = [x for ring in rings for x, _ in ring]
                ys = [y for ring in rings for _, y in ring]
                annotations.append(
                    {
                        "id": ann.id * 10**9 + patch.id,
                        "image_id": patch.id,
                        "category_id": ann.class_id,
                        "segmentation": [_flat(ring) for ring in rings],
                        "area": round(sum(polygon_area(ring) for ring in rings), 2),
                        "bbox": [round(min(xs), 2), round(min(ys), 2), round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)],
                        "iscrowd": 0,
                        "vp_level0_segmentation": [_flat([[x * scale_x + patch.x, y * scale_y + patch.y] for x, y in ring]) for ring in rings],
                        "vp_shape_type": piece.type,
                        "vp_unsure": ann.unsure,
                        "vp_flagged": ann.flagged,
                        # "slide": drawn on the whole slide; "patch": drawn in a patch of another grid.
                        "vp_scope": "slide" if ann.patch_id is None else "patch",
                        "vp_source_annotation_id": ann.id,
                        "vp_source_patch_id": ann.patch_id,
                        "vp_clipped": piece.clipped,
                    }
                )
                if patch.id not in used_patch_ids:
                    used_patch_ids.append(patch.id)

        # Which patches become COCO "images". Under the default scope that is those holding at
        # least one exported shape; under the others it is every patch in scope, so empty ones
        # (or reviewed negatives) appear as images without annotations.
        if options.patch_scope == "annotated":
            image_ids = sorted(used_patch_ids)
        else:
            image_ids = [p.id for p in data.patches if not p.excluded]

        default_ext = options.image_ext if options.with_images else "png"

        images = []
        for patch_id in image_ids:
            p = data.patch_by_id[patch_id]
            images.append(
                {
                    "id": p.id,
                    # Exactly the name written into the ZIP when images are exported alongside.
                    "file_name": options.image_names.get(p.id) or patch_image_name(slide, p, default_ext),
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

    def merge(self, results: list[Any]) -> dict:
        """One COCO dataset from several slides. Image, annotation and (for one
        configuration) category ids are database ids, so they are already unique."""
        first = results[0]
        categories: dict[int, Any] = {}
        skipped = {"point_annotations": 0, "line_annotations": 0, "unclassified": 0, "degenerate_geometry": 0}
        for doc in results:
            for category in doc["categories"]:
                categories.setdefault(category["id"], category)
            for key, count in doc["info"]["vp_skipped"].items():
                skipped[key] += count

        info = {k: v for k, v in first["info"].items() if k != "vp_slide"}
        labelled = sum(len(doc["images"]) for doc in results)
        info["description"] = f"{labelled} annotated images from {len(results)} in the project (VirtualPatch WSI Annotator)"
        info["vp_skipped"] = skipped
        return {
            "info": info,
            "licenses": [],
            "images": [image for doc in results for image in doc["images"]],
            "annotations": [ann for doc in results for ann in doc["annotations"]],
            "categories": list(categories.values()),
        }

    def render(self, data: Any) -> str:
        return dumps_with_line_items(data, ("images", "annotations", "categories"))
