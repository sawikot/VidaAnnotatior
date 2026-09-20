from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services.geometry import polygon_area

from .base import POLYGON_TYPES, Exporter, load_export_data

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


def _mm2(area_px2: float, slide: Slide) -> float | None:
    if not slide.mpp_x or not slide.mpp_y:
        return None
    return area_px2 * slide.mpp_x * slide.mpp_y / 1_000_000


class PatchCSVExporter(Exporter):
    """One row per patch of the active config version: where it is (Level-0),
    how it is read, its tissue fraction and review state, and what was
    annotated in it. Patches flagged "exclude" are listed (with ``excluded =
    true``) so the file is a complete registry of the grid."""

    format_id = "patch_csv"
    content_type = "text/csv; charset=utf-8"
    file_extension = "csv"

    HEADER = [
        "slide", "patch_id", "patch_index", "level0_x", "level0_y", "width_level0", "height_level0",
        "read_level", "width_px", "height_px", "tissue_fraction", "status", "patch_label", "dominant_class",
        "n_annotations", "unsure", "flagged", "excluded", "reviewed_by", "notes",
    ]  # fmt: skip

    def export(self, db: Session, slide: Slide) -> str:
        data = load_export_data(db, slide)

        per_patch: dict[int, list] = defaultdict(list)
        for ann in data.all_annotations:
            per_patch[ann.patch_id].append(ann)

        rows = []
        for p in data.patches:
            anns = per_patch.get(p.id, [])
            area_by_class: dict[str, float] = defaultdict(float)
            for a in anns:
                name = data.class_name(a)
                if name:
                    area_by_class[name] += polygon_area(a.coordinates_level0) if a.type in POLYGON_TYPES else 0.0
            dominant = max(area_by_class.items(), key=lambda kv: (kv[1], kv[0]))[0] if area_by_class else None
            rows.append([
                slide.filename, p.id, p.patch_index, p.x, p.y, p.width_l0, p.height_l0,
                p.level, p.width, p.height, round(p.tissue_fraction, 4), p.status, p.patch_label, dominant,
                len(anns), p.unsure, p.flagged, p.excluded, p.reviewed_by, p.notes,
            ])  # fmt: skip
        return to_csv(self.HEADER, rows)


class StatsCSVExporter(Exporter):
    """One row per diagnostic class for this slide (long/"tidy" layout, so
    Prism, SPSS, pandas or R can group and pivot it directly). Areas are the
    *sum of the individual annotation areas*, so shapes that overlap are counted
    twice; polygons that cross themselves are measured after repair."""

    format_id = "stats_csv"
    content_type = "text/csv; charset=utf-8"
    file_extension = "csv"

    HEADER = [
        "slide", "project_id", "config_version", "class", "n_annotations", "n_polygons", "n_points", "n_patches",
        "summed_area_px2", "summed_area_mm2", "mean_area_mm2", "pct_of_annotated_area", "pct_of_tissue_area",
        "n_unsure", "n_flagged", "slide_patches", "slide_annotated_patches", "slide_reviewed_patches",
        "slide_excluded_patches", "slide_tissue_area_mm2", "mpp_x", "mpp_y",
    ]  # fmt: skip

    def export(self, db: Session, slide: Slide) -> str:
        data = load_export_data(db, slide)

        buckets: dict[str | None, list] = defaultdict(list)
        for ann in data.annotations:
            buckets[data.class_name(ann)].append(ann)

        class_order = [c.name for c in sorted(data.classes.values(), key=lambda c: (c.order_index, c.id))]
        names: list[str | None] = list(dict.fromkeys(class_order))
        if None in buckets:
            names.append(None)  # shapes drawn without a class

        active = [p for p in data.patches if not p.excluded]
        annotated = sum(1 for p in active if p.status in ("annotated", "reviewed"))
        reviewed = sum(1 for p in active if p.status == "reviewed")
        excluded = len(data.patches) - len(active)

        def area_of(ann) -> float:
            return polygon_area(ann.coordinates_level0) if ann.type in POLYGON_TYPES else 0.0

        total_area = sum(area_of(a) for a in data.annotations)
        tissue_mm2 = slide.tissue_area_mm2
        config = data.config

        rows = []
        for name in names:
            anns = buckets.get(name, [])
            area_px2 = float(sum(area_of(a) for a in anns))
            n_polygons = sum(1 for a in anns if a.type in POLYGON_TYPES)
            area_mm2 = _mm2(area_px2, slide)
            mean_mm2 = _mm2(area_px2 / n_polygons, slide) if n_polygons else None
            rows.append([
                slide.filename, slide.project.slug, config.version_label if config else None,
                name if name is not None else "(unclassified)",
                len(anns), n_polygons, len(anns) - n_polygons, len({a.patch_id for a in anns}),
                round(area_px2, 2),
                round(area_mm2, 6) if area_mm2 is not None else None,
                round(mean_mm2, 6) if mean_mm2 is not None else None,
                round(area_px2 / total_area * 100, 3) if total_area else None,
                round(area_mm2 / tissue_mm2 * 100, 3) if area_mm2 is not None and tissue_mm2 else None,
                sum(1 for a in anns if a.unsure), sum(1 for a in anns if a.flagged),
                len(active), annotated, reviewed, excluded, tissue_mm2, slide.mpp_x, slide.mpp_y,
            ])  # fmt: skip
        return to_csv(self.HEADER, rows)
