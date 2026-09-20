"""Finding annotatable images in an upload (loose files, a folder, or unpacked zips).

The image-project counterpart of ``wsi_bundles``: instead of grouping multi-file
slide formats, every readable image file is one item. Each is validated by really
decoding it, so a corrupt or mislabelled file is reported rather than imported.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.services.wsi_bundles import SkippedItem, is_junk

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")
# The patch endpoint serves at most this many pixels per side, and an image is shown as one patch.
MAX_IMAGE_SIDE = 8192


@dataclass
class ImageItem:
    path: Path
    name: str  # what the image is called in the project: its path inside the upload
    width: int
    height: int


@dataclass
class ImageDiscovery:
    items: list[ImageItem] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    ignored_files: int = 0


def inspect_image(path: Path, max_pixels: int) -> tuple[int, int]:
    """(width, height) after fully decoding the file; raises ValueError with a
    human-readable reason if it can't be used."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)  # treat as refusal, not a warning
            with Image.open(path) as image:
                width, height = image.size
                if image.getexif().get(0x0112) in (5, 6, 7, 8):  # EXIF says "stored sideways": upright size is swapped
                    width, height = height, width
                if max(width, height) > MAX_IMAGE_SIDE:
                    raise ValueError(f"is {width}x{height} px; the longest side may be at most {MAX_IMAGE_SIDE} px")
                if width * height > max_pixels:
                    raise ValueError(
                        f"is {width}x{height} px ({width * height / 1e6:.0f} MP); images over "
                        f"{max_pixels / 1e6:.0f} MP belong in a WSI project"
                    )
                image.load()  # a truncated or corrupt file fails here, not later in the viewer
    except ValueError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("is far too large to be a plain image; large images belong in a WSI project") from exc
    except Exception as exc:  # noqa: BLE001 - PIL raises many types; all mean "not usable"
        raise ValueError(f"could not be read as an image ({exc.__class__.__name__})") from exc
    return width, height


def _strip_common_folder(names: list[str]) -> list[str]:
    """A zip usually wraps everything in one folder ("dataset/a.png"); drop a
    leading folder shared by *all* names so images aren't all prefixed by it."""
    split = [n.split("/") for n in names]
    while split and all(len(parts) > 1 for parts in split) and len({parts[0] for parts in split}) == 1:
        split = [parts[1:] for parts in split]
    return ["/".join(parts) for parts in split]


def discover_images(root: Path, *, max_pixels: int, only: Path | None = None) -> ImageDiscovery:
    """Every image file under ``root`` (or just ``only``). Other files are counted
    and ignored; images that can't be decoded are reported in ``skipped``."""
    result = ImageDiscovery()
    if only is not None:
        candidates = [only]
    else:
        candidates = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix().lower())

    found: list[tuple[Path, str, int, int]] = []
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if is_junk(rel):
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            result.ignored_files += 1
            continue
        try:
            width, height = inspect_image(path, max_pixels)
        except ValueError as exc:
            result.skipped.append(SkippedItem(name=path.name, reason=str(exc)))
            continue
        found.append((path, rel, width, height))

    names = _strip_common_folder([rel for _, rel, _, _ in found])
    for (path, _, width, height), name in zip(found, names):
        result.items.append(ImageItem(path=path, name=name[-255:], width=width, height=height))
    return result
