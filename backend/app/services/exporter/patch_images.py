"""Cutting patch images (and label masks) out of a slide for an export bundle.

Everything here is computed on demand from the original slide and streamed into the
download; nothing is kept on the server, so the "no permanent patch extraction" rule holds.
"""
from __future__ import annotations

import io
import re
from typing import Iterable

from PIL import Image, ImageDraw

from app.models.annotation import GeometryAnnotation
from app.models.patch import Patch
from app.models.slide import Slide
from app.services.wsi_reader import WSIReader

from app.services.geometry import AREA_TYPES, circle_center_radius
from app.services.projection import Projected

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _stem(filename: str) -> str:
    return _UNSAFE.sub("_", filename.rsplit(".", 1)[0]).strip("._") or "slide"


def patch_image_name(slide: Slide, patch: Patch, ext: str) -> str:
    """Deterministic file name of one patch's image.

    A WSI patch is named after its slide and position. In an image project the patch *is* the
    image, so it keeps the dataset's own relative path (``tumor/001.png`` -> ``tumor/001.<ext>``).
    """
    if slide.project.project_type == "image":
        path = slide.filename.replace("\\", "/")
        base = path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
        return f"{base}.{ext}"
    return f"{_stem(slide.filename)}_p{patch.patch_index}_x{patch.x}_y{patch.y}_L{patch.level}.{ext}"


def unique_names(entries: Iterable[tuple[Slide, Patch]], ext: str) -> dict[int, str]:
    """A file name for every patch, guaranteed distinct across the whole bundle: two slides
    (or two imports of one image) sharing a file name get their slide id spliced in."""
    names: dict[int, str] = {}
    used: set[str] = set()
    for slide, patch in entries:
        name = patch_image_name(slide, patch, ext)
        if name.lower() in used:
            head, dot, tail = name.rpartition(".")
            name = f"{head}__s{slide.id}{dot}{tail}"
            n = 2
            while name.lower() in used:
                name = f"{head}__s{slide.id}_{n}{dot}{tail}"
                n += 1
        used.add(name.lower())
        names[patch.id] = name
    return names


def render_patch(reader: WSIReader, patch: Patch) -> Image.Image:
    """The patch exactly as the annotator saw it: its Level-0 origin read at its level."""
    return reader.read_region(patch.x, patch.y, patch.level, patch.width, patch.height)


def encode(image: Image.Image, image_format: str) -> bytes:
    buf = io.BytesIO()
    if image_format == "png":
        image.save(buf, format="PNG", compress_level=3)
    else:
        image.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def render_mask(patch: Patch, annotations: Iterable[GeometryAnnotation], class_index: dict[int, int], projected: Iterable[Projected] = ()) -> bytes:
    """A single-channel label mask, same size as the patch image.

    Pixel value 0 is background and ``class_index[class_id]`` (1..N, by class order) marks
    the class covering that pixel. Shapes are painted in creation order, so where two overlap
    the later one wins. Points and lines have no area and are not painted; unclassified shapes are skipped.
    ``projected`` are the slide-level annotations reaching this patch, already in its pixels; they are
    painted in the same creation order as the patch's own shapes.
    """
    layers = [(a.id, a.type, a.class_id, [a.coordinates_patch_local]) for a in annotations]
    layers += [(piece.annotation.id, piece.type, piece.annotation.class_id, piece.parts) for piece in projected]

    mask = Image.new("L", (patch.width, patch.height), 0)
    draw = ImageDraw.Draw(mask)
    for _, kind, class_id, parts in sorted(layers, key=lambda layer: layer[0]):
        value = class_index.get(class_id) if class_id is not None else None
        if not value or kind not in AREA_TYPES:
            continue
        for part in parts:
            if kind == "circle":
                (cx, cy), r = circle_center_radius(part)
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=value)
            elif len(part) >= 3:
                draw.polygon([(x, y) for x, y in part], fill=value)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()
