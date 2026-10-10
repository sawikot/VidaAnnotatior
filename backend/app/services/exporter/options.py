"""What the person asked for at export time, beyond "which format".

* ``patch_scope`` -- which patches the export covers:
    ``annotated``  patches with at least one annotation (the default)
    ``all``        every patch of the grid, empty ones too
    ``empty``      patches with no annotation at all (negatives, or the work still to do)
    ``reviewed``   patches marked Reviewed/QA (includes confirmed negatives)
  Patches flagged "Exclude from training" never contribute annotations or images; they only
  appear in the patch registries (WSI JSON ``patches``, patch CSV) under ``all``.
* ``content`` -- ``annotations`` (just the annotation file) or ``images`` (a ZIP holding the
  annotation file plus the patch images cut from the slide, optionally with masks).
  The images are rendered on the fly into the download and never stored by the server.
* ``combine`` -- for dataset-level formats, one file for the whole project instead of one per slide.
* ``min_coverage``, ``unlabeled``, ``other_labels`` -- patch classification only (see classify.py):
  the share of a patch a drawn class must cover to name it, what happens to patches with no clear
  class (``skip``, or ``folder``: an ``unlabeled`` folder), and whether labels that are not a class
  ("Mixed", "Artifact / Background") get folders of their own.
* ``grid`` -- export in another patch grid than the one annotated (a grid key, see
  services/patch_grid.py): the slide is re-cut with that patch size / stride / magnification /
  tissue threshold on the fly, and every annotation is cut into the new patches. Nothing is stored.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.patch_grid import GridSpec

PATCH_SCOPES = ("annotated", "all", "empty", "reviewed")
CONTENTS = ("annotations", "images")
IMAGE_FORMATS = ("jpg", "png")
UNLABELED_MODES = ("skip", "folder")


@dataclass(frozen=True)
class ExportOptions:
    patch_scope: str = "annotated"
    content: str = "annotations"
    image_format: str = "jpg"
    masks: bool = False
    combine: bool | None = None  # None: the project type decides
    grid: GridSpec | None = None  # None: the grid each slide was annotated in
    min_coverage: float = 0.9
    unlabeled: str = "skip"
    other_labels: bool = True
    # ---- Used when a training dataset is cut (services/training_data.py); exports leave them alone.
    # Of the patches in scope, keep only those marked Reviewed.
    only_reviewed: bool = False
    # Add patches with nothing in them, as examples of background: this many per annotated patch
    # (0.5: one for every two), picked at random but the same every time for the same ``seed``.
    # None: add none. ``empty_from``: "any" patch without annotations, or only "reviewed" ones --
    # those a person has confirmed to be empty.
    empty_ratio: float | None = None
    empty_from: str = "any"
    seed: int = 0
    # Leave out annotations of any other class (None: every class).
    class_ids: frozenset[int] | None = None
    # Leave out the whole-patch shape a Patch Label makes: it is a label, not an object in the patch.
    skip_patch_fills: bool = False
    # patch id -> file name inside images/; filled in by the bundle builder so that an
    # annotation file (COCO) refers to exactly the names that were written into the ZIP.
    image_names: dict[int, str] = field(default_factory=dict, compare=False)

    @property
    def with_images(self) -> bool:
        return self.content == "images"

    @property
    def image_ext(self) -> str:
        return self.image_format


def parse_options(
    patches: str = "annotated",
    content: str = "annotations",
    image_format: str = "jpg",
    masks: bool = False,
    combine: bool | None = None,
    grid: str | None = None,
    min_coverage: float = 0.9,
    unlabeled: str = "skip",
    other_labels: bool = True,
) -> ExportOptions:
    """Validated options; raises ValueError (turned into HTTP 422) on anything unknown."""
    if patches not in PATCH_SCOPES:
        raise ValueError(f"patches must be one of: {', '.join(PATCH_SCOPES)}")
    if content not in CONTENTS:
        raise ValueError(f"content must be one of: {', '.join(CONTENTS)}")
    if image_format not in IMAGE_FORMATS:
        raise ValueError(f"image_format must be one of: {', '.join(IMAGE_FORMATS)}")
    if masks and content != "images":
        raise ValueError("masks can only be included together with patch images (content=images)")
    if not 0.5 <= min_coverage <= 1:
        raise ValueError("min_coverage must be between 0.5 and 1")
    if unlabeled not in UNLABELED_MODES:
        raise ValueError(f"unlabeled must be one of: {', '.join(UNLABELED_MODES)}")
    spec = None
    if grid:
        spec = GridSpec.from_key(grid)  # ValueError on a malformed key
        if not (16 <= spec.patch_width <= 8192 and 16 <= spec.patch_height <= 8192):
            raise ValueError("grid patch size must be between 16 and 8192 px")
        if not (1 <= spec.stride_x <= 8192 and 1 <= spec.stride_y <= 8192):
            raise ValueError("grid stride must be between 1 and 8192 px")
        if not 0 <= spec.min_tissue_fraction <= 1:
            raise ValueError("grid tissue threshold must be between 0 and 1")
    return ExportOptions(
        patch_scope=patches, content=content, image_format=image_format, masks=masks, combine=combine, grid=spec,
        min_coverage=min_coverage, unlabeled=unlabeled, other_labels=other_labels,
    )
