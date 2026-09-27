"""Patch grids: which patch size / stride / magnification / tissue threshold a set of patches was cut with.

A slide can hold several grids at once under the same configuration version (and so the same classes):
annotate at 2048 px, switch to 512 px, and everything drawn so far is still there, because every
annotation also lives in Level-0 pixels. Each patch records the key of the grid it belongs to; a slide
shows and exports the patches of its *active* grid.

The key is a short readable string that identifies a grid exactly, e.g. ``2048x2048_s1024x1024_m40_t0.04``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace

IMAGE_GRID_KEY = "image"  # image projects: every image is one whole-image patch

_KEY = re.compile(
    r"^(?P<pw>\d+)x(?P<ph>\d+)_s(?P<sx>\d+)x(?P<sy>\d+)_m(?P<mag>[0-9.]+|auto)_t(?P<t>[0-9.]+)(?P<edge>_e)?(?P<partial>_p)?$"
)


@dataclass(frozen=True)
class GridSpec:
    patch_width: int
    patch_height: int
    stride_x: int
    stride_y: int
    target_magnification: float | None
    min_tissue_fraction: float
    include_edge_patches: bool = False
    allow_partial_patches: bool = False

    @classmethod
    def from_config(cls, config) -> "GridSpec":  # noqa: ANN001 - a ProjectConfigVersion or anything shaped like one
        return cls(
            patch_width=int(config.patch_width),
            patch_height=int(config.patch_height),
            stride_x=int(config.stride_x),
            stride_y=int(config.stride_y),
            target_magnification=float(config.target_magnification) if config.target_magnification else None,
            min_tissue_fraction=round(float(config.min_tissue_fraction), 4),
            include_edge_patches=bool(getattr(config, "include_edge_patches", False)),
            allow_partial_patches=bool(getattr(config, "allow_partial_patches", False)),
        )

    @classmethod
    def from_key(cls, key: str) -> "GridSpec":
        m = _KEY.match(key)
        if not m:
            raise ValueError(f"Not a patch grid key: {key!r}")
        mag = m["mag"]
        return cls(
            patch_width=int(m["pw"]),
            patch_height=int(m["ph"]),
            stride_x=int(m["sx"]),
            stride_y=int(m["sy"]),
            target_magnification=None if mag == "auto" else float(mag),
            min_tissue_fraction=float(m["t"]),
            include_edge_patches=bool(m["edge"]),
            allow_partial_patches=bool(m["partial"]),
        )

    @property
    def key(self) -> str:
        mag = "auto" if not self.target_magnification else f"{self.target_magnification:g}"
        return (
            f"{self.patch_width}x{self.patch_height}_s{self.stride_x}x{self.stride_y}_m{mag}_t{self.min_tissue_fraction:g}"
            + ("_e" if self.include_edge_patches else "")
            + ("_p" if self.allow_partial_patches else "")
        )

    @property
    def label(self) -> str:
        size = f"{self.patch_width}" if self.patch_width == self.patch_height else f"{self.patch_width}x{self.patch_height}"
        stride = f"{self.stride_x}" if self.stride_x == self.stride_y else f"{self.stride_x}x{self.stride_y}"
        mag = f"{self.target_magnification:g}x" if self.target_magnification else "default magnification"
        area = "whole slide" if self.whole_slide else f"tissue >= {self.min_tissue_fraction:.0%}"
        return f"{size} px, stride {stride}, {mag}, {area}"

    @property
    def whole_slide(self) -> bool:
        """No tissue threshold: every patch of the slide is kept, glass included."""
        return self.min_tissue_fraction <= 0

    def over_whole_slide(self) -> "GridSpec":
        """The same patch size covering the whole slide, up to its edges, whatever the tissue."""
        return replace(self, min_tissue_fraction=0.0, include_edge_patches=True)

    def over_tissue(self, config) -> "GridSpec":  # noqa: ANN001 - a ProjectConfigVersion
        """The same patch size over the tissue only: a whole-slide grid takes the configuration's
        threshold (or 50% when that is none either)."""
        if not self.whole_slide:
            return self
        own = GridSpec.from_config(config)
        return replace(self, min_tissue_fraction=own.min_tissue_fraction if not own.whole_slide else 0.5, include_edge_patches=own.include_edge_patches)

    # generate_patch_grid reads these attribute names (the same as a config version's).
    target_level = None

    def as_dict(self) -> dict:
        return asdict(self)


def grid_key_of(config) -> str:  # noqa: ANN001
    return GridSpec.from_config(config).key


GRID_SPEC_FIELDS = ("patch_width", "patch_height", "stride_x", "stride_y", "target_magnification", "min_tissue_fraction", "include_edge_patches", "allow_partial_patches")


def saved_grid_keys(config) -> list[str]:  # noqa: ANN001
    """The patch sizes remembered for the project (made, or once the default), oldest first."""
    return list(config.saved_grids or [])


def remember_grid(config, key: str) -> None:  # noqa: ANN001
    keys = saved_grid_keys(config)
    if key not in keys:
        config.saved_grids = keys + [key]  # a new list, so the JSON column is seen as changed


def forget_grid(config, key: str) -> bool:  # noqa: ANN001
    keys = saved_grid_keys(config)
    if key not in keys:
        return False
    config.saved_grids = [k for k in keys if k != key]
    return True


def make_grid_default(config, spec: GridSpec) -> None:  # noqa: ANN001
    """Make `spec` the project's patch size (what Generate Coords cuts a slide with none); the size it
    replaces stays remembered, so it can be picked again."""
    from app.services.config_versioning import compute_config_hash  # (it imports the models; kept lazy)

    remember_grid(config, grid_key_of(config))
    remember_grid(config, spec.key)
    for field in GRID_SPEC_FIELDS:
        setattr(config, field, getattr(spec, field))
    config.config_hash = compute_config_hash(config)


def active_grid_filter(query, slide):  # noqa: ANN001, ANN201
    """Narrow a Patch query to the slide's active config version and active grid.

    A slide with no active grid yet (patches made before grids existed, or none made) is not narrowed
    by grid, so nothing it has disappears.
    """
    from app.models.patch import Patch

    if slide.active_config_version_id is not None:
        query = query.filter(Patch.config_version_id == slide.active_config_version_id)
    if slide.active_grid_key is not None:
        query = query.filter(Patch.grid_key == slide.active_grid_key)
    return query


def remove_grid(db, slides, key: str) -> dict:  # noqa: ANN001
    """Delete one grid's patches from `slides`, keeping every annotation drawn in them.

    Those annotations become whole-slide annotations at the same place (their Level-0 coordinates are the
    canonical ones anyway), so they still show in every other grid and in exports. A slide that was on
    the removed grid moves to another grid it has -- the configuration's own if it can -- or, with none
    left, back to the stage before patches. Not committed.
    """
    from app.models.annotation import GeometryAnnotation
    from app.models.config_version import ProjectConfigVersion
    from app.models.patch import Patch

    counts = {"slides": 0, "patches": 0, "annotations_kept": 0}
    for slide in slides:
        # Selected by the database, never as a list of ids: a grid can have tens of thousands of patches,
        # more than SQLite takes variables in one statement.
        in_grid = db.query(Patch.id).filter(Patch.slide_id == slide.id, Patch.grid_key == key)
        n = in_grid.count()
        if not n:
            continue
        counts["slides"] += 1
        counts["patches"] += n
        counts["annotations_kept"] += (
            db.query(GeometryAnnotation)
            .filter(GeometryAnnotation.patch_id.in_(in_grid.scalar_subquery()))
            .update({GeometryAnnotation.patch_id: None, GeometryAnnotation.coordinates_patch_local: []}, synchronize_session=False)
        )
        db.query(Patch).filter(Patch.slide_id == slide.id, Patch.grid_key == key).delete(synchronize_session=False)

        if slide.active_grid_key == key:
            left = sorted({k for (k,) in db.query(Patch.grid_key).filter(Patch.slide_id == slide.id).distinct() if k})
            config = db.get(ProjectConfigVersion, slide.active_config_version_id) if slide.active_config_version_id else None
            own = grid_key_of(config) if config else None
            slide.active_grid_key = own if own in left else (left[0] if left else None)
            if not left and slide.status in ("patches_generated", "annotating", "reviewed"):
                slide.status = "tissue_detected" if slide.tissue_mask_path else "imported"
    db.flush()
    return counts
