"""Finding whole-slide images inside uploads, archives and folders.

Most WSI formats are one file, but some are not: a MIRAX slide is
``Slide.mrxs`` *plus* a same-named ``Slide/`` folder of ``.dat`` files, and
Hamamatsu VMS/VMU slides ship with sibling tile files. This module works out
which files belong together (a "bundle"), refuses anything unsafe, and lets
OpenSlide itself decide what is really a slide.

Everything here works on plain paths so it can be tested without a database
or a web server.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import openslide

# Extensions that can be the "main" file of a slide, with what they usually are.
# A .tif/.tiff may be Aperio, Trestle, Ventana, Philips or a generic tiled TIFF;
# OpenSlide's detect_format() below is what actually decides.
PRIMARY_EXTENSIONS: dict[str, str] = {
    ".svs": "Aperio ScanScope",
    ".tif": "Aperio / Trestle / Ventana / generic tiled TIFF",
    ".tiff": "Philips / generic tiled TIFF",
    ".ndpi": "Hamamatsu NDPI",
    ".vms": "Hamamatsu VMS (needs its tile files)",
    ".vmu": "Hamamatsu VMU (needs its tile files)",
    ".scn": "Leica SCN",
    ".mrxs": "3DHISTECH MIRAX (needs its data folder)",
    ".bif": "Ventana BIF",
    ".svslide": "Sakura SVSlide",
}
ARCHIVE_EXTENSIONS = {".zip"}

# Sibling files that travel with a Hamamatsu VMS/VMU slide.
_VMS_COMPANION_EXTENSIONS = {".jpg", ".jpeg", ".opt", ".ngr"}

_MAX_PATH_DEPTH = 32
_MAX_PART_LENGTH = 200
_BAD_CHARS = set('<>:"|?*') | {chr(i) for i in range(32)}
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


class UnsafePathError(ValueError):
    """A file name that could escape its directory or isn't valid on Windows."""


class ArchiveError(Exception):
    """A whole archive that can't be used (corrupt, encrypted, too large...)."""


def safe_parts(raw: str) -> tuple[str, ...]:
    """Validate a relative path from an upload or archive entry.

    Names are kept exactly as given (sub-files are referenced by name from
    inside Slidedat.ini / .vms files, so they must not be rewritten) -- anything
    that could break out of the destination or that Windows can't store is
    rejected instead.
    """
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        raise UnsafePathError("absolute paths are not allowed")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafePathError("empty path")
    if len(parts) > _MAX_PATH_DEPTH:
        raise UnsafePathError("path is nested too deeply")
    for part in parts:
        if part == "..":
            raise UnsafePathError("'..' is not allowed")
        if any(c in _BAD_CHARS for c in part):
            raise UnsafePathError(f"'{part}' contains characters that are not allowed")
        if part != part.rstrip(" ."):
            raise UnsafePathError(f"'{part}' ends with a space or dot")
        if part.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise UnsafePathError(f"'{part}' is a reserved name")
        if len(part) > _MAX_PART_LENGTH:
            raise UnsafePathError("a name in the path is too long")
    return tuple(parts)


def is_junk(path_like: str) -> bool:
    """Files that operating systems add to archives/folders and that are never slides."""
    parts = path_like.replace("\\", "/").split("/")
    name = parts[-1].lower()
    return "__macosx" in (p.lower() for p in parts) or name in _JUNK_NAMES or name.startswith("._")


def extract_zip(zip_path: Path, dest: Path, *, max_bytes: int, max_files: int) -> list[str]:
    """Safely unpack ``zip_path`` into ``dest``; returns human-readable warnings.

    Guards against zip-slip (paths escaping ``dest``), symbolic links,
    encrypted archives, and decompression bombs (both the sizes the archive
    *claims* and the bytes actually written are capped).
    """
    warnings: list[str] = []
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("not a valid zip file") from exc

    with archive:
        entries = [i for i in archive.infolist() if not i.is_dir()]
        if len(entries) > max_files:
            raise ArchiveError(f"contains {len(entries):,} files (limit is {max_files:,})")
        if sum(i.file_size for i in entries) > max_bytes:
            raise ArchiveError(f"unpacks to more than the {max_bytes / 1e9:.0f} GB limit")

        dest_root = dest.resolve()
        written = 0
        for info in entries:
            name = info.filename
            if is_junk(name):
                continue
            if info.flag_bits & 0x1:
                raise ArchiveError("is password-protected")
            if stat.S_ISLNK(info.external_attr >> 16):
                warnings.append(f"{name}: symbolic link skipped")
                continue
            try:
                parts = safe_parts(name)
            except UnsafePathError as exc:
                warnings.append(f"{name}: skipped ({exc})")
                continue

            target = dest.joinpath(*parts)
            if dest_root not in target.resolve().parents:
                warnings.append(f"{name}: skipped (would be written outside the destination)")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as src, target.open("wb") as out:
                    while chunk := src.read(1 << 20):
                        written += len(chunk)
                        if written > max_bytes:
                            raise ArchiveError(f"unpacks to more than the {max_bytes / 1e9:.0f} GB limit")
                        out.write(chunk)
            except (NotImplementedError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ArchiveError(f"could not read '{name}' ({exc})") from exc
    return warnings


@dataclass
class SlideBundle:
    primary: Path
    members: list[Path]  # absolute paths, includes `primary`

    @property
    def base(self) -> Path:
        return self.primary.parent

    @property
    def name(self) -> str:
        return self.primary.name


@dataclass
class SkippedItem:
    name: str
    reason: str


@dataclass
class Discovery:
    bundles: list[SlideBundle] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    ignored_files: int = 0


Detector = Callable[[str], str | None]


def _find_sibling_dir(base: Path, stem: str) -> Path | None:
    exact = base / stem
    if exact.is_dir() and not exact.is_symlink():
        return exact
    try:
        for child in base.iterdir():
            if child.is_dir() and not child.is_symlink() and child.name.lower() == stem.lower():
                return child
    except OSError:
        pass
    return None


def _files_under(directory: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, _dirs, filenames in os.walk(directory, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            if not p.is_symlink() and not is_junk(name):
                found.append(p)
    return found


def _members_for(primary: Path) -> tuple[list[Path] | None, str | None]:
    """The files that make up the slide whose main file is ``primary``, or a
    problem description when required companions are missing."""
    ext = primary.suffix.lower()
    stem, base = primary.stem, primary.parent

    if ext == ".mrxs":
        data_dir = _find_sibling_dir(base, stem)
        if data_dir is None:
            return None, (
                f"MIRAX data folder '{stem}/' was not found next to {primary.name}. "
                "Upload the folder that contains both (use 'Choose folder'), or zip them together."
            )
        files = _files_under(data_dir)
        if not any(f.parent == data_dir and f.name.lower() == "slidedat.ini" for f in files):
            return None, f"MIRAX data folder '{data_dir.name}/' has no Slidedat.ini, so it can't be read."
        return [primary, *files], None

    if ext in {".vms", ".vmu"}:
        prefix = stem.lower()
        companions: list[Path] = []
        try:
            for child in base.iterdir():
                if (
                    child.is_file()
                    and not child.is_symlink()
                    and child != primary
                    and child.name.lower().startswith(prefix)
                    and child.suffix.lower() in _VMS_COMPANION_EXTENSIONS
                ):
                    companions.append(child)
        except OSError:
            pass
        extra_dir = _find_sibling_dir(base, stem)
        if extra_dir is not None:
            companions.extend(_files_under(extra_dir))
        if not companions:
            return None, f"{primary.name} needs its image tile files ({stem}*.jpg / .ngr) in the same folder."
        return [primary, *companions], None

    return [primary], None


def discover_bundles(
    root: Path,
    *,
    only: Path | None = None,
    detect: Detector = openslide.OpenSlide.detect_format,
    max_scan: int = 200_000,
) -> Discovery:
    """Find every whole-slide image under ``root`` (or just ``only``, plus its
    companions, when given).

    A candidate is kept only if its companions are present *and* OpenSlide
    recognizes it, so plain TIFFs, masks, or renamed files are reported as
    skipped with a reason instead of becoming broken slides.
    """
    result = Discovery()
    candidates: list[Path] = []
    scanned = 0

    if only is not None:
        candidates = [only]
    else:
        for dirpath, _dirs, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                scanned += 1
                if scanned > max_scan:
                    result.skipped.append(SkippedItem(str(root.name), f"stopped after scanning {max_scan:,} files"))
                    break
                path = Path(dirpath) / name
                if path.is_symlink() or is_junk(name):
                    continue
                ext = path.suffix.lower()
                if ext in PRIMARY_EXTENSIONS:
                    candidates.append(path)
                elif ext in ARCHIVE_EXTENSIONS:
                    result.skipped.append(
                        SkippedItem(name, "nested archive -- zips inside zips are not unpacked; upload it on its own")
                    )
            else:
                continue
            break

    bundles: list[SlideBundle] = []
    for primary in sorted(candidates):
        members, problem = _members_for(primary)
        if members is None:
            result.skipped.append(SkippedItem(primary.name, problem or "missing companion files"))
        else:
            bundles.append(SlideBundle(primary=primary, members=members))

    # A .tif that lives inside another slide's data folder is part of that
    # slide, not a slide of its own.
    claimed_by_other: set[Path] = set()
    for b in bundles:
        claimed_by_other.update(m for m in b.members if m != b.primary)
    bundles = [b for b in bundles if b.primary not in claimed_by_other]

    for b in bundles:
        if detect(str(b.primary)) is None:
            hint = (
                "not a tiled, pyramidal TIFF that OpenSlide recognizes (plain TIFFs and masks are skipped)"
                if b.primary.suffix.lower() in {".tif", ".tiff"}
                else "not recognized as a whole-slide image (the file may be corrupt or the wrong format)"
            )
            result.skipped.append(SkippedItem(b.name, hint))
        else:
            result.bundles.append(b)

    if only is None:
        claimed_files = {m for b in result.bundles for m in b.members}
        result.ignored_files = max(0, scanned - len(claimed_files) - len(result.skipped))
    return result


def slide_dir_name(stem: str) -> str:
    """A unique, filesystem-safe directory name for one slide's files."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:60] or "slide"
    return f"{readable}-{uuid.uuid4().hex[:8]}"


def install_bundle(bundle: SlideBundle, dest_dir: Path, *, move: bool) -> Path:
    """Place a bundle's files under ``dest_dir`` keeping their layout relative
    to the main file (so ``Slide.mrxs`` and ``Slide/`` stay side by side).
    Returns the new path of the main file."""
    for member in bundle.members:
        target = dest_dir / member.relative_to(bundle.base)
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(member), target)
        else:
            shutil.copy2(member, target)
    return dest_dir / bundle.primary.name
