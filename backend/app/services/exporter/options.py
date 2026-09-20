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
"""
from __future__ import annotations

from dataclasses import dataclass, field

PATCH_SCOPES = ("annotated", "all", "empty", "reviewed")
CONTENTS = ("annotations", "images")
IMAGE_FORMATS = ("jpg", "png")


@dataclass(frozen=True)
class ExportOptions:
    patch_scope: str = "annotated"
    content: str = "annotations"
    image_format: str = "jpg"
    masks: bool = False
    combine: bool | None = None  # None: the project type decides
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
    return ExportOptions(patch_scope=patches, content=content, image_format=image_format, masks=masks, combine=combine)
