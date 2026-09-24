"""The "annotations + patch images" download: one ZIP, built into a temporary file.

Layout::

    annotations/<file>           the annotation file(s) in the chosen format
    images/<name>.<jpg|png>      one image per patch in scope (cut from the original slide)
                                 -- patch classification: images/<class>/<name>, beside labels.csv
    masks/<name>.png             optional label masks, same size as the images
    mask_classes.json            optional: pixel value -> class name for the masks
    manifest.json                what was written, and anything that had to be skipped

The COCO ``file_name`` of every image is exactly its name under ``images/``, so the ZIP is a
ready-to-train COCO dataset. Images are generated on the fly; nothing is kept on the server.
"""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import IO

from sqlalchemy.orm import Session

from app.models.slide import Slide
from app.services import reader_cache

from .base import Exporter, load_export_data
from .options import ExportOptions
from .patch_images import encode, render_mask, render_patch, unique_names

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ExportTooLarge(Exception):
    """More images were requested than one download may hold."""


@dataclass
class Summary:
    slides: int = 0
    patches: int = 0  # in scope (excluded ones count only under scope "all")
    annotations: int = 0
    images: int = 0  # patches that would get an image (excluded ones never do)
    image_pixels: int = 0


def _image_folders(slides: list[Slide], datas: dict, exporter: Exporter | None, options: ExportOptions) -> dict[int, str] | None:
    """Patch id -> folder, when the format sorts images into folders (and leaves some patches out)."""
    if exporter is None:
        return None
    folders: dict[int, str] | None = None
    for slide in slides:
        part = exporter.image_folders(datas[slide.id], options)
        if part is not None:
            folders = {**(folders or {}), **part}
    return folders


def summarize(db: Session, slides: list[Slide], options: ExportOptions, exporter: Exporter | None = None) -> Summary:
    total = Summary()
    for slide in slides:
        data = load_export_data(db, slide, options)
        folders = exporter.image_folders(data, options) if exporter is not None else None
        drawable = [p for p in data.patches if not p.excluded and (folders is None or p.id in folders)]
        total.slides += 1
        total.patches += len(data.patches)
        total.annotations += len(data.annotations) + len(data.slide_annotations)
        total.images += len(drawable)
        total.image_pixels += sum(p.width * p.height for p in drawable)
    return total


def _safe(name: str, fallback: str) -> str:
    return _UNSAFE.sub("_", name).strip("._") or fallback


@dataclass
class Bundle:
    file: IO[bytes]
    size: int
    manifest: dict = field(default_factory=dict)


def build_bundle(
    db: Session,
    slides: list[Slide],
    exporter: Exporter,
    options: ExportOptions,
    *,
    combine: bool,
    label: str,
    max_images: int,
    skipped_slides: list[dict] | None = None,
) -> Bundle:
    """Write the ZIP for ``slides`` into a temporary file (deleted when closed)."""
    datas = {slide.id: load_export_data(db, slide, options) for slide in slides}

    folders = _image_folders(slides, datas, exporter, options)
    pairs = [(slide, p) for slide in slides for p in datas[slide.id].patches if not p.excluded and (folders is None or p.id in folders)]
    if len(pairs) > max_images:
        raise ExportTooLarge(
            f"{len(pairs):,} images were requested but one download holds at most {max_images:,}. "
            "Narrow the patch selection (for example annotated or reviewed only) or export one slide at a time."
        )
    names = unique_names(pairs, options.image_ext)
    if folders is not None:  # e.g. patch classification: images/<class>/<name>
        names = {pid: f"{folders[pid]}/{name}" for pid, name in names.items()}
    options = replace(options, image_names=names)

    skipped: list[dict] = list(skipped_slides or [])  # slides the caller already left out (e.g. no patch grid)
    image_errors: list[dict] = []
    tmp = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # -------- annotation file(s)
            files: list[dict] = []
            results = []
            for slide in slides:
                try:
                    results.append((slide, exporter.export(db, slide, options)))
                except Exception as exc:  # noqa: BLE001 - one bad slide must not lose the rest
                    skipped.append({"slide_id": slide.id, "slide": slide.filename, "reason": f"annotation export failed: {exc}"})

            if combine and exporter.mergeable and results:
                body = exporter.render(exporter.merge([r for _, r in results])).encode("utf-8")
                name = exporter.bundle_name or f"annotations/{_safe(label, 'project')}_{exporter.format_id}.{exporter.file_extension}"
                archive.writestr(name, body)
                files.append({"file": name, "slides": len(results), "bytes": len(body)})
            else:
                used: set[str] = set()
                for slide, result in results:
                    stem = _safe(slide.filename.rsplit(".", 1)[0], "slide")
                    name = f"annotations/{stem}_{exporter.format_id}.{exporter.file_extension}"
                    if exporter.bundle_name and len(results) == 1:
                        name = exporter.bundle_name
                    elif name in used:
                        name = f"annotations/{stem}_{slide.id}_{exporter.format_id}.{exporter.file_extension}"
                    used.add(name)
                    body = exporter.render(result).encode("utf-8")
                    archive.writestr(name, body)
                    files.append({"slide_id": slide.id, "slide": slide.filename, "file": name, "bytes": len(body)})

            # -------- images (and masks)
            class_index: dict[int, int] = {}
            if options.masks:
                known = {}
                for data in datas.values():
                    known.update(data.classes)
                ranked = sorted(known.values(), key=lambda c: (c.order_index, c.id))
                class_index = {cls.id: i for i, cls in enumerate(ranked, start=1)}
                classes_doc = {"0": "background", **{str(i): cls.name for i, cls in enumerate(ranked, start=1)}}
                archive.writestr("mask_classes.json", json.dumps(classes_doc, indent=2))

            written = 0
            for slide in slides:
                data = datas[slide.id]
                drawable = [p for p in data.patches if p.id in names]
                if not drawable:
                    continue
                try:
                    reader = reader_cache.get_reader_for_slide(slide)
                except Exception as exc:  # noqa: BLE001 - e.g. the slide file is gone from disk
                    image_errors.append({"slide_id": slide.id, "slide": slide.filename, "reason": str(exc), "patches": len(drawable)})
                    continue

                by_patch: dict[int, list] = {}
                if options.masks:
                    for ann in data.annotations:
                        by_patch.setdefault(ann.patch_id, []).append(ann)

                for patch in drawable:
                    name = names[patch.id]
                    try:
                        image = render_patch(reader, patch)
                        payload = encode(image, options.image_format)
                        mask = render_mask(patch, by_patch.get(patch.id, []), class_index, data.projections.get(patch.id, [])) if options.masks else None
                    except Exception as exc:  # noqa: BLE001
                        image_errors.append({"slide_id": slide.id, "patch_id": patch.id, "reason": str(exc)})
                        continue
                    # Already-compressed formats: storing them is faster and no larger.
                    archive.writestr(f"images/{name}", payload, compress_type=zipfile.ZIP_STORED)
                    if mask is not None:
                        mask_name = name.rsplit(".", 1)[0] + ".png"
                        archive.writestr(f"masks/{mask_name}", mask, compress_type=zipfile.ZIP_STORED)
                    written += 1

            manifest = {
                "label": label,
                "format": exporter.format_id,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "options": {
                    "patches": options.patch_scope,
                    "image_format": options.image_format,
                    "masks": options.masks,
                    "combined": bool(combine and exporter.mergeable),
                },
                "annotation_files": files,
                "image_count": written,
                "skipped_slides": skipped,
                "image_errors": image_errors,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    except BaseException:
        tmp.close()
        raise

    tmp.seek(0, 2)
    size = tmp.tell()
    tmp.seek(0)
    return Bundle(file=tmp, size=size, manifest=manifest)
