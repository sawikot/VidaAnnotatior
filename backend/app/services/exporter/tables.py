from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import AREA_TYPES, LINE_TYPES, line_length, shape_area

from .base import ExportData, Exporter, load_export_data
from .classify import class_areas, classify_all, folder_name
from .options import ExportOptions
from .patch_images import unique_names

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """One CSV cell. Text that a spreadsheet would treat as a formula (a note
    like ``=HYPERLINK(...)``) is prefixed with an apostrophe so opening the file
    in Excel/Sheets can't execute it. Numbers are never touched."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def to_csv(header: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return buf.getvalue()


def merge_csv(results: list[str], header: list[str]) -> str:
    """Concatenate CSV documents that share one header. The header line contains no
    quoting, so it can be cut off by length even when cells contain line breaks."""
    header_line = ",".join(header) + "\n"
    return header_line + "".join(text[len(header_line):] for text in results)


def _mm2(area_px2: float, slide: Slide) -> float | None:
    if not slide.mpp_x or not slide.mpp_y:
        return None
    return area_px2 * slide.mpp_x * slide.mpp_y / 1_000_000


class PatchCSVExporter(Exporter):
    """One row per patch in the requested scope: where it is (Level-0), how it is
    read, its tissue fraction and review state, and what was annotated in it.
    Patches flagged "exclude" appear (with ``excluded = true``) under scope "all",
    which makes the file a complete registry of the grid."""

    format_id = "patch_csv"
    content_type = "text/csv; charset=utf-8"
    file_extension = "csv"
    mergeable = True

    HEADER = [
        "slide", "patch_id", "patch_index", "level0_x", "level0_y", "width_level0", "height_level0",
        "read_level", "width_px", "height_px", "tissue_fraction", "status", "patch_label", "dominant_class",
        "n_annotations", "n_slide_annotations", "unsure", "flagged", "excluded", "reviewed_by", "notes",
    ]  # fmt: skip

    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> str:
        data = load_export_data(db, slide, options)

        per_patch: dict[int, list] = defaultdict(list)
        for ann in data.all_annotations:
            per_patch[ann.patch_id].append(ann)

        rows = []
        for p in data.patches:
            anns = per_patch.get(p.id, [])
            pieces = data.projections.get(p.id, [])  # slide-level annotations reaching this patch
            area_by_class = class_areas(data, p)
            dominant = max(area_by_class.items(), key=lambda kv: (kv[1], kv[0]))[0] if area_by_class else None
            rows.append([
                slide.filename, p.id, p.patch_index, p.x, p.y, p.width_l0, p.height_l0,
                p.level, p.width, p.height, round(p.tissue_fraction, 4), p.status, p.patch_label, dominant,
                len(anns) + len(pieces), len(pieces), p.unsure, p.flagged, p.excluded, p.reviewed_by, p.notes,
            ])  # fmt: skip
        return to_csv(self.HEADER, rows)

    def merge(self, results: list[str]) -> str:
        return merge_csv(results, self.HEADER)


class StatsCSVExporter(Exporter):
    """One row per diagnostic class for this slide (long/"tidy" layout, so
    Prism, SPSS, pandas or R can group and pivot it directly). Areas are the
    *sum of the individual annotation areas*, so shapes that overlap are counted
    twice; polygons that cross themselves are measured after repair. Circles count
    as polygons (exact pi*r^2); lines and freehand lines are counted in ``n_lines``
    and measured by ``summed_length_px`` / ``summed_length_um``."""

    format_id = "stats_csv"
    content_type = "text/csv; charset=utf-8"
    file_extension = "csv"
    mergeable = True

    HEADER = [
        "slide", "project_id", "config_version", "class", "n_annotations", "n_polygons", "n_points", "n_lines", "n_patches",
        "summed_area_px2", "summed_area_mm2", "mean_area_mm2", "summed_length_px", "summed_length_um",
        "pct_of_annotated_area", "pct_of_tissue_area",
        "n_unsure", "n_flagged", "slide_patches", "slide_annotated_patches", "slide_reviewed_patches",
        "slide_excluded_patches", "slide_tissue_area_mm2", "mpp_x", "mpp_y",
    ]  # fmt: skip

    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> str:
        data = load_export_data(db, slide, options)

        # Slide-level annotations count in full (not once per patch they cross); the patch scope selects patches.
        everything = data.everything()
        touched: dict[int, set[int]] = defaultdict(set)  # slide-level annotation id -> patches it reaches
        for patch_id, pieces in data.projections.items():
            if not data.patch_by_id[patch_id].excluded:
                for piece in pieces:
                    touched[piece.annotation.id].add(patch_id)

        buckets: dict[str | None, list] = defaultdict(list)
        for ann in everything:
            buckets[data.class_name(ann)].append(ann)

        class_order = [c.name for c in sorted(data.classes.values(), key=lambda c: (c.order_index, c.id))]
        names: list[str | None] = list(dict.fromkeys(class_order))
        if None in buckets:
            names.append(None)  # shapes drawn without a class

        # Slide-level counts describe the whole grid, whatever subset the rows are computed from.
        active = [p for p in data.grid if not p.excluded]
        annotated = sum(1 for p in active if p.status in ("annotated", "reviewed"))
        reviewed = sum(1 for p in active if p.status == "reviewed")
        excluded = len(data.grid) - len(active)

        def area_of(ann) -> float:
            return shape_area(ann.type, ann.coordinates_level0)


        total_area = sum(area_of(a) for a in everything)
        tissue_mm2 = slide.tissue_area_mm2
        config = data.config

        rows = []
        for name in names:
            anns = buckets.get(name, [])
            area_px2 = float(sum(area_of(a) for a in anns))
            n_polygons = sum(1 for a in anns if a.type in AREA_TYPES)
            n_points = sum(1 for a in anns if a.type == "point")
            lines = [a for a in anns if a.type in LINE_TYPES]
            length_px = float(sum(line_length(a.coordinates_level0) for a in lines))
            # per-axis scaling, so a diagonal line is right even for non-square pixels
            total_um = (
                sum(line_length(a.coordinates_level0, slide.mpp_x, slide.mpp_y) for a in lines)
                if slide.mpp_x and slide.mpp_y
                else None
            )
            area_mm2 = _mm2(area_px2, slide)
            mean_mm2 = _mm2(area_px2 / n_polygons, slide) if n_polygons else None
            rows.append([
                slide.filename, slide.project.slug, config.version_label if config else None,
                name if name is not None else "(unclassified)",
                len(anns), n_polygons, n_points, len(lines), len({a.patch_id for a in anns if a.patch_id is not None}.union(*(touched[a.id] for a in anns))),
                round(area_px2, 2),
                round(area_mm2, 6) if area_mm2 is not None else None,
                round(mean_mm2, 6) if mean_mm2 is not None else None,
                round(length_px, 2),
                round(total_um, 3) if total_um is not None else None,
                round(area_px2 / total_area * 100, 3) if total_area else None,
                round(area_mm2 / tissue_mm2 * 100, 3) if area_mm2 is not None and tissue_mm2 else None,
                sum(1 for a in anns if a.unsure), sum(1 for a in anns if a.flagged),
                len(active), annotated, reviewed, excluded, tissue_mm2, slide.mpp_x, slide.mpp_y,
            ])  # fmt: skip
        return to_csv(self.HEADER, rows)

    def merge(self, results: list[str]) -> str:
        return merge_csv(results, self.HEADER)


class PatchClassificationExporter(Exporter):
    """A patch-classification dataset: with images, ``images/<class>/<name>`` plus this ``labels.csv``.

    One row per patch that has a class (see classify.py): its Patch Label, else the drawn class
    covering at least ``min_coverage`` of it. Patches with no clear class are left out, or listed
    as ``unlabeled`` on request. ``file`` is the image's path inside the ZIP."""

    format_id = "patch_classification"
    content_type = "text/csv; charset=utf-8"
    file_extension = "csv"
    mergeable = True
    bundle_name = "labels.csv"

    HEADER = [
        "file", "class", "class_source", "coverage", "slide", "patch_id", "patch_index", "level0_x", "level0_y",
        "width_level0", "height_level0", "read_level", "width_px", "height_px", "patch_label",
    ]  # fmt: skip

    def image_folders(self, data: ExportData, options: ExportOptions) -> dict[int, str]:
        return {pid: folder_name(cls.name) for pid, cls in classify_all(data, options).items()}

    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> str:
        options = options or ExportOptions()
        data = load_export_data(db, slide, options)
        classes = classify_all(data, options)
        names = options.image_names
        if not names:  # the CSV on its own: the names the images would have
            local = unique_names([(slide, p) for p in data.patches if p.id in classes], options.image_ext)
            names = {pid: f"{folder_name(classes[pid].name)}/{n}" for pid, n in local.items()}
        rows = []
        for p in data.patches:
            cls = classes.get(p.id)
            if cls is None:
                continue
            rows.append([
                f"images/{names.get(p.id, '')}", cls.name, cls.source, cls.coverage, slide.filename, p.id, p.patch_index,
                p.x, p.y, p.width_l0, p.height_l0, p.level, p.width, p.height, p.patch_label,
            ])  # fmt: skip
        return to_csv(self.HEADER, rows)

    def merge(self, results: list[str]) -> str:
        return merge_csv(results, self.HEADER)
