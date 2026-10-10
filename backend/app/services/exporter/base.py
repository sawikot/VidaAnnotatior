"""Shared plumbing for every export format.

An exporter turns one slide's data into a document. All formats read the same
``ExportData`` snapshot so they agree about *which* patches and annotations are
included:

* only the slide's **active** config version (older versions' patches and
  annotations stay in the database but are not mixed in),
* the patches of the slide's **active grid** -- or of a custom grid cut on the fly
  (``ExportOptions.grid``). Annotations drawn in another grid (or on the whole slide) are cut
  into the exported grid's patches, so every grid carries every annotation,
* never annotations on patches flagged "Exclude from training",
* only the patches selected by ``ExportOptions.patch_scope`` (see ``options.py``).
"""
from __future__ import annotations

import json
import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide

from app.services.patch_generator import generate_patch_grid
from app.services.patch_grid import GridSpec, active_grid_filter
from app.services.projection import Projected, project_slide_annotations

from .options import ExportOptions



@dataclass
class ExportData:
    slide: Slide
    config: ProjectConfigVersion | None
    patches: list[Patch]  # the patches in the requested scope (excluded ones only under scope "all")
    annotations: list[GeometryAnnotation]  # drawn in a patch, on the non-excluded patches in scope
    all_annotations: list[GeometryAnnotation]  # drawn in any patch of the grid (for per-patch counts)
    patch_by_id: dict[int, Patch]  # the whole grid, not just the scope
    classes: dict[int, AnnotationClass]
    grid: list[Patch] = field(default_factory=list)  # every patch of the active version
    counts: dict[int, int] = field(default_factory=dict)  # annotations per patch: its own plus slide-level ones reaching it
    # Not owned by a patch of this grid: drawn on the whole slide, or in another grid of the slide.
    # In Level-0 pixels; always part of the slide's own export, whatever patch scope was chosen (the
    # scope selects patches, and these belong to none of them). They reach patches via `projections`.
    slide_annotations: list[GeometryAnnotation] = field(default_factory=list)
    # Per patch, the part of each slide-level annotation lying inside it, in that patch's pixels.
    projections: dict[int, list[Projected]] = field(default_factory=dict)
    # Patches with nothing in them that were added on request (ExportOptions.empty_ratio).
    empty_ids: set[int] = field(default_factory=set)

    _own: dict[int, list[GeometryAnnotation]] | None = field(default=None, init=False, repr=False)

    def own_annotations(self, patch_id: int) -> list[GeometryAnnotation]:
        """The annotations drawn in this patch of the grid."""
        if self._own is None:
            self._own = {}
            for a in self.all_annotations:
                self._own.setdefault(a.patch_id, []).append(a)
        return self._own.get(patch_id, [])

    def annotation_count(self, patch: Patch) -> int:
        return self.counts.get(patch.id, 0)

    def everything(self) -> list[GeometryAnnotation]:
        """Patch-drawn (in scope) and slide-level annotations together, in creation order."""
        return sorted([*self.annotations, *self.slide_annotations], key=lambda a: a.id)

    def class_name(self, ann: GeometryAnnotation) -> str | None:
        cls = self.classes.get(ann.class_id) if ann.class_id is not None else None
        return cls.name if cls else None


def _in_scope(patch: Patch, n_annotations: int, scope: str) -> bool:
    if scope == "all":
        return True  # excluded patches too: the registries list them, flagged
    if patch.excluded:
        return False
    labelled = bool(getattr(patch, "patch_label", None))  # a Patch Label annotates the patch too
    if scope == "annotated":
        return n_annotations > 0 or labelled
    if scope == "empty":
        return n_annotations == 0 and not labelled
    if scope == "reviewed":
        return patch.status == "reviewed"
    raise ValueError(f"unknown patch scope '{scope}'")


@dataclass
class VirtualPatch:
    """A patch of a grid cut only for an export (never stored). Has what exporters read of a Patch."""

    id: int
    slide_id: int
    patch_index: int
    x: int
    y: int
    level: int
    width: int
    height: int
    width_l0: int
    height_l0: int
    tissue_fraction: float
    grid_key: str
    status: str = "unannotated"
    patch_label: str | None = None
    unsure: bool = False
    flagged: bool = False
    excluded: bool = False
    notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: None = None


# Ids of VirtualPatch: a range no stored patch reaches, unique across slides (COCO image ids, and the
# id * 1e9 + image id annotation ids, must not collide when slides are combined into one file).
VIRTUAL_ID_BASE = 500_000_000
MAX_VIRTUAL_PER_SLIDE = 100_000


def _custom_grid(slide: Slide, spec: GridSpec) -> list[VirtualPatch]:
    from app.core.config import get_settings
    from app.services import reader_cache
    from app.services.tissue_mask import load_mask

    meta = reader_cache.get_reader_for_slide(slide).get_metadata()
    mask = None
    if slide.tissue_mask_path:
        path = get_settings().wsi_storage_dir / slide.tissue_mask_path
        if path.exists():
            mask = load_mask(path)
    kept = [c for c in generate_patch_grid(meta, spec, mask, slide.tissue_mask_downsample) if c.kept]
    if len(kept) > MAX_VIRTUAL_PER_SLIDE:
        raise ValueError(
            f"That grid cuts {len(kept):,} patches from {slide.filename}; at most {MAX_VIRTUAL_PER_SLIDE:,} per slide. "
            "Use a larger patch or stride."
        )
    return [
        VirtualPatch(
            id=VIRTUAL_ID_BASE + slide.id * MAX_VIRTUAL_PER_SLIDE + i,
            slide_id=slide.id,
            patch_index=i,
            x=c.x,
            y=c.y,
            level=c.level,
            width=c.width,
            height=c.height,
            width_l0=c.width_l0,
            height_l0=c.height_l0,
            tissue_fraction=c.tissue_fraction,
            grid_key=spec.key,
        )
        for i, c in enumerate(kept)
    ]


def load_export_data(db: Session, slide: Slide, options: ExportOptions | None = None) -> ExportData:
    options = options or ExportOptions()
    config = db.get(ProjectConfigVersion, slide.active_config_version_id) if slide.active_config_version_id else None

    ann_query = db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id == slide.id)
    if config is not None:
        ann_query = ann_query.filter(GeometryAnnotation.config_version_id == config.id)
    stored = ann_query.order_by(GeometryAnnotation.id.asc()).all()
    if options.class_ids is not None:
        stored = [a for a in stored if a.class_id in options.class_ids]
    if options.skip_patch_fills:
        stored = [a for a in stored if not a.whole_patch]

    if options.grid is not None:
        grid: list = _custom_grid(slide, options.grid)
    else:
        grid = active_grid_filter(db.query(Patch).filter(Patch.slide_id == slide.id), slide).order_by(Patch.patch_index.asc(), Patch.id.asc()).all()
    patch_by_id = {p.id: p for p in grid}
    all_annotations = [a for a in stored if a.patch_id in patch_by_id]

    # Everything this grid does not own: slide-level annotations, and those drawn in another grid --
    # unless the patch they were drawn in is excluded from training. Their own patches are kept in
    # patch_by_id too, so coordinate formats still name the patch each was drawn in.
    loose = [a for a in stored if a.patch_id not in patch_by_id]
    owner_ids = {a.patch_id for a in loose if a.patch_id is not None}
    owners = {p.id: p for p in db.query(Patch).filter(Patch.id.in_(owner_ids))} if owner_ids else {}
    slide_annotations = [a for a in loose if a.patch_id is None or not owners.get(a.patch_id, None) or not owners[a.patch_id].excluded]
    projections = project_slide_annotations(slide_annotations, grid)
    lookup = {**owners, **patch_by_id}

    counts: dict[int, int] = {}
    for a in all_annotations:
        counts[a.patch_id] = counts.get(a.patch_id, 0) + 1
    for patch_id, pieces in projections.items():
        counts[patch_id] = counts.get(patch_id, 0) + len(pieces)

    if options.grid is not None:
        for p in grid:  # a grid cut for this export has no review state: annotated or not
            p.status = "annotated" if counts.get(p.id, 0) else "unannotated"

    patches = [p for p in grid if _in_scope(p, counts.get(p.id, 0), options.patch_scope)]
    if options.only_reviewed:
        patches = [p for p in patches if p.status == "reviewed"]
    empty_ids: set[int] = set()
    if options.empty_ratio is not None:
        have = {p.id for p in patches}
        candidates = [
            p for p in grid
            if p.id not in have and not p.excluded and not counts.get(p.id, 0) and not getattr(p, "patch_label", None)
            and (options.empty_from == "any" or p.status == "reviewed")
        ]
        wanted = min(len(candidates), math.ceil(options.empty_ratio * len(patches)))
        chosen = random.Random(f"{options.seed}:{slide.id}").sample(candidates, wanted)
        empty_ids = {p.id for p in chosen}
        patches = sorted([*patches, *chosen], key=lambda p: (p.patch_index, p.id))
    in_scope_ids = {p.id for p in patches if not p.excluded}
    annotations = [a for a in all_annotations if a.patch_id in in_scope_ids]

    class_ids = {a.class_id for a in [*annotations, *slide_annotations] if a.class_id is not None}
    if config is not None:
        classes = {c.id: c for c in config.annotation_classes}
    else:
        classes = {}
    missing = class_ids - classes.keys()
    if missing:
        classes.update({c.id: c for c in db.query(AnnotationClass).filter(AnnotationClass.id.in_(missing))})

    return ExportData(slide, config, patches, annotations, all_annotations, lookup, classes, grid, counts, slide_annotations, projections, empty_ids)


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
    # Dataset-level formats (COCO, the CSV tables) can be combined across slides into
    # one document; per-slide coordinate formats (GeoJSON, WSI JSON) cannot.
    mergeable: bool = False
    # In a ZIP with images: the name of the one annotation file, at the top (None: annotations/<slide>_<format>).
    bundle_name: str | None = None

    def image_folders(self, data: ExportData, options: ExportOptions) -> dict[int, str] | None:
        """With images: patch id -> folder under images/ for the patches that get one. None (the
        default): every patch in scope, straight in images/."""
        return None

    def merge(self, results: list[Any]) -> Any:
        """Combine several slides' ``export()`` results into one document."""
        raise NotImplementedError(f"{self.format_id} exports cannot be merged across slides")

    @abstractmethod
    def export(self, db: Session, slide: Slide, options: ExportOptions | None = None) -> Any:
        """The structured result (a dict for JSON formats, text for CSV)."""

    def render(self, data: Any) -> str:
        """Serialise ``export()``'s result for download."""
        return data if isinstance(data, str) else json.dumps(data, indent=2)
