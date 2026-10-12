"""Importing a dataset: images (or slides) and their annotations, uploaded together or apart.

Everything of one import is first gathered in a folder of its own (``<storage>/_staging/import-<id>/
tree``), in whatever layout it came. ``analyse`` then works out what is there, without changing the
project:

* which files are images or slides, and which of those the project already has (by name);
* which files are annotations, in which format (services/dataset_formats.py), and which image each
  part of them is about -- by the name the file gives, else by the file's own name, among the images
  uploaded now *and* those already in the project (so annotations can be uploaded on their own);
* which images are really masks of other images -- by their name or folder and what is in them, or
  because the person said which folders hold the masks (``mask_folders``);
* where names say nothing (``case1/image.png`` beside ``case1/mask.png`` or ``case1/labels.json``, the
  same in every case's folder), which files sit alone together in a folder: those belong together;
* in an image project with no other annotations, whether the folders the images sit in name their
  classes.

Its result is a *plan* -- kept beside the files, so the import itself (api/dataset_import.py) does
exactly what was shown -- and a *preview* of it for the person to check and to match the files'
labels to the project's classes.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.models.config_version import ProjectConfigVersion
from app.models.project import Project
from app.models.slide import Slide
from app.services import dataset_formats as formats
from app.services.annotation_import import ImportFormatError, ParsedFile
from app.services.image_import import IMAGE_EXTENSIONS, discover_images
from app.services.wsi_bundles import discover_bundles, is_junk
from app.services.wsi_reader import OpenSlideReader

# Folders that say what kind of file is in them, not which image it is...
KIND_FOLDERS = formats.MASK_FOLDERS | {"images", "image", "img", "imgs", "jpegimages", "pictures", "photos", "data", "dataset", "datasets", "all"}
# ...and those that are no class either: the set an image is in.
GENERIC_FOLDERS = KIND_FOLDERS | {"train", "training", "val", "valid", "validation", "test", "testing"}
_LISTED = 50  # names shown of each kind of problem; the counts are always complete


@dataclass
class Target:
    """An image or slide annotations can go to: one uploaded now, or one the project has."""

    key: str  # "new:<n>" or "slide:<id>"
    name: str  # as the project calls it (an image: its path inside the upload)
    width: int | None = None
    height: int | None = None
    mpp: tuple[float | None, float | None] = (None, None)
    file: Path | None = None  # a new slide whose size is read when first needed
    folder: str | None = None  # where it sits in the upload (None: it is in the project already)

    @property
    def stem(self) -> str:
        return Path(self.name).stem.lower()

    @property
    def folders(self) -> set[str]:
        return {part.lower() for part in Path(self.name).parts[:-1]} - KIND_FOLDERS

    def size(self) -> formats.Size:
        if self.file is not None and self.width is None:
            try:
                reader = OpenSlideReader(self.file)
                meta = reader.get_metadata()
                reader.close()
                self.width, self.height, self.mpp = meta.width, meta.height, (meta.mpp_x, meta.mpp_y)
            except Exception:  # noqa: BLE001 - the import itself reports a slide that cannot be read
                self.file = None
        return formats.Size(self.width, self.height, self.mpp)


@dataclass
class _Analysis:
    targets: list[Target] = field(default_factory=list)
    by_stem: dict[str, list[Target]] = field(default_factory=dict)
    work: dict[str, dict] = field(default_factory=dict)  # target key -> {"entries": [...], "label": str | None}
    files: Counter = field(default_factory=Counter)  # format -> annotation files read
    unmatched: list[dict] = field(default_factory=list)
    unreadable: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    by_folder: dict[str, list[Target]] = field(default_factory=dict)
    paired_by_folder: list[str] = field(default_factory=list)  # files matched to the only image beside them

    def add_target(self, target: Target) -> None:
        self.targets.append(target)
        self.by_stem.setdefault(target.stem, []).append(target)
        if target.folder is not None:
            self.by_folder.setdefault(target.folder, []).append(target)

    def problem(self, file: str, reason: str) -> None:
        self.unmatched.append({"file": file, "reason": reason})

    def resolve(self, ref: str, near: Path, mask: bool = False) -> Target | None | str:
        """The image a file means by ``ref`` ("" for "the one named like me"): a target, None when no
        image has that name, or -- a string -- why it cannot be told which of several it is."""
        name = Path(ref.replace("\\", "/")) if ref else near
        stem = name.stem.lower()
        if mask:
            stem = formats.mask_stem(near.stem)[0].lower()  # named like its image, give or take "_mask"
        found = self.by_stem.get(stem)
        if not found and "-" in stem:
            found = self.by_stem.get(stem.split("-", 1)[1])  # Label Studio puts an id in front of an uploaded name
        here = self.by_folder.get(near.parent.as_posix(), [])
        if not found:
            return None
        if len(found) > 1 and ref and name.suffix:
            found = [t for t in found if t.name.lower().endswith(name.suffix.lower())] or found
        if len(found) == 1:
            return found[0]
        beside_it = [t for t in found if t in here]
        if len(beside_it) == 1:
            return beside_it[0]  # "case1/image.png" for "case1/image.json", not another case's
        # The same name in several folders ("train/a.png", "val/a.png"): the one whose folders the
        # file shares the most of ("labels/train/a.txt" is about the one in "train").
        near_folders = {part.lower() for part in (*near.parts[:-1], *name.parts[:-1])} - KIND_FOLDERS
        scored = sorted(((len(t.folders & near_folders), t) for t in found), key=lambda pair: -pair[0])
        if scored[0][0] > scored[1][0]:
            return scored[0][1]
        new = [t for t in found if t.key.startswith("new:")]
        if len(new) == 1:
            return new[0]  # among namesakes, the one that came with the file
        return f"{len(found)} images are named {name.stem}; it is not clear which one is meant"

    def take(self, file: str, kind: str, part: formats.ImagePart, target: Target) -> None:
        try:
            parsed: ParsedFile = part.read(target.size())
        except ImportFormatError as exc:
            self.problem(file, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not sink the whole upload
            self.problem(file, f"could not be read ({exc.__class__.__name__})")
            return
        slot = self.work.setdefault(target.key, {"entries": [], "label": None})
        slot["entries"] += parsed.entries
        if part.label:
            slot["label"] = part.label
        self.unreadable.update(parsed.unreadable)
        for warning in parsed.warnings:
            if warning not in self.warnings:
                self.warnings.append(warning)


def _looks_like_mask(path: Path) -> bool:
    """A picture of classes rather than of tissue: a palette or grey image, or very few colours."""
    try:
        with Image.open(path) as image:
            if image.mode in ("P", "1", "L", "I", "I;16"):
                return len(image.getcolors(maxcolors=256) or []) in range(1, 65) if image.mode != "P" else True
            small = image.convert("RGB").resize((min(image.width, 256), min(image.height, 256)), Image.NEAREST)
            return small.getcolors(maxcolors=64) is not None
    except Exception:  # noqa: BLE001
        return False


def _rel(path: Path, tree: Path) -> str:
    return path.relative_to(tree).as_posix()


def _files(tree: Path) -> Iterator[Path]:
    for path in sorted((p for p in tree.rglob("*") if p.is_file()), key=lambda p: p.as_posix().lower()):
        if not is_junk(_rel(path, tree)):
            yield path


def _folder_label(name: str) -> str | None:
    """The class an image's folder names: the nearest folder that is not just "images" or "train"."""
    for part in reversed(Path(name).parts[:-1]):
        if part.lower() not in GENERIC_FOLDERS:
            return part
    return None


_MASK_WORD = re.compile(r"(.*[-_. ])?(mask|masks|label|labels|seg|segmentation|gt|groundtruth|annotation|anno)\d*", re.I)


def _within(folder: str, folders: set[str]) -> bool:
    return any(folder == f or folder.startswith(f + "/") for f in folders)


def analyse(db: Session, project: Project, tree: Path, max_image_pixels: int, mask_folders: list[str] | None = None) -> dict:
    """Work out what an upload holds (see the module's description). Returns the plan; its
    ``"preview"`` is what is shown before importing. Nothing in the project is changed.
    ``mask_folders``: folders of the upload (as the preview lists them) whose images are masks,
    whatever they look like or are called."""
    image_project = project.project_type == "image"
    said = {f.strip("/") for f in mask_folders or []}
    a = _Analysis()
    existing = {s.filename.lower(): s for s in db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.id.desc())}
    for slide in existing.values():
        a.add_target(Target(f"slide:{slide.id}", slide.filename, slide.width_l0, slide.height_l0, (slide.mpp_x, slide.mpp_y)))

    new: list[dict] = []
    skipped: list[dict] = []
    masks: list[Path] = []  # images that are class masks of other images
    folders: list[dict] = []  # an image project's folders with images, for saying which hold masks
    media: set[Path] = set()  # files that are (part of) an image or slide, whatever became of them
    already = 0

    if image_project:
        found = discover_images(tree, max_pixels=max_image_pixels)
        skipped += [{"name": s.name, "reason": s.reason} for s in found.skipped]
        media |= {p for p in _files(tree) if p.suffix.lower() in IMAGE_EXTENSIONS}
        by_stem: dict[str, list] = {}
        for item in found.items:
            by_stem.setdefault(Path(item.name).stem.lower(), []).append(item)
        folder_of = {item.path: Path(_rel(item.path, tree)).parent.as_posix() for item in found.items}
        for folder in sorted(set(folder_of.values())):
            folders.append({"folder": folder, "images": sum(1 for f in folder_of.values() if f == folder), "masks": folder in said})
        # Masks, three ways: the person said so; the name or folder says so and the content agrees...
        mask_of: set[Path] = {item.path for item in found.items if _within(folder_of[item.path], said)}
        for item in found.items:
            base, named = formats.mask_stem(Path(item.name).stem)
            in_mask_folder = any(part.lower() in formats.MASK_FOLDERS for part in Path(item.name).parts[:-1])
            if item.path in mask_of or not (named or in_mask_folder):
                continue
            partners = [o for o in by_stem.get(base.lower(), []) if o is not item and o.path not in mask_of and (o.width, o.height) == (item.width, item.height)]
            partners = [o for o in partners if not any(p.lower() in formats.MASK_FOLDERS for p in Path(o.name).parts[:-1])]
            in_project = [s for s in existing.values() if Path(s.filename).stem.lower() == base.lower() and (s.width_l0, s.height_l0) == (item.width, item.height)]
            if (partners or in_project) and _looks_like_mask(item.path):
                mask_of.add(item.path)
        # ...or it sits alone with one picture in a folder (a folder per case: "image.png", "mask.png"),
        # is the one of the two that is flat classes, and is called a mask -- or every case has the pair.
        pairs = []
        for folder in set(folder_of.values()):
            two = [item for item in found.items if folder_of[item.path] == folder and item.path not in mask_of]
            if len(two) == 2 and (two[0].width, two[0].height) == (two[1].width, two[1].height):
                flat = [item for item in two if _looks_like_mask(item.path)]
                if len(flat) == 1:
                    pairs.append(flat[0])
        for item in pairs:
            if len(pairs) >= 2 or _MASK_WORD.fullmatch(Path(item.name).stem):
                mask_of.add(item.path)
        for item in found.items:
            if item.path in mask_of:
                continue
            have = existing.get(item.name.lower())
            if have is not None and (have.width_l0, have.height_l0) == (item.width, item.height):
                already += 1  # the project has it: nothing to add, but annotations for it are welcome
                continue
            target = Target(f"new:{len(new)}", item.name, item.width, item.height, folder=folder_of[item.path])
            new.append({"key": target.key, "name": item.name, "rel": _rel(item.path, tree), "width": item.width, "height": item.height})
            a.add_target(target)
        masks = sorted(mask_of)
    else:
        found = discover_bundles(tree)
        skipped += [{"name": s.name, "reason": s.reason} for s in found.skipped]
        for bundle in found.bundles:
            media |= set(bundle.members)
        # A file stored like a slide may be the class mask of one: it has a slide's name (give or take
        # "_mask") and size, and a handful of flat values where a scan has thousands of colours.
        stems: dict[str, list] = {}
        for bundle in found.bundles:
            stems.setdefault(formats.mask_stem(bundle.primary.stem)[0].lower(), []).append(bundle)
        mask_bundles: set[Path] = set()
        lonely: list[tuple[Path, str]] = []  # masks no slide is named like
        for stem, group in stems.items():
            in_project = [s for s in existing.values() if Path(s.filename).stem.lower() == stem]
            sizes = {bundle.primary: formats.slide_mask_size(bundle.primary) for bundle in group}
            pictures = [b for b in group if sizes[b.primary] is None]
            for bundle in group:
                size = sizes[bundle.primary]
                if size is None:
                    continue
                mask_bundles.add(bundle.primary)  # never a slide to annotate, whether or not its slide is here
                if pictures or any((s.width_l0, s.height_l0) == size for s in in_project):
                    masks.append(bundle.primary)
                else:
                    lonely.append((bundle.primary, stem))
        slides = [b for b in found.bundles if b.primary not in mask_bundles]
        # A folder per case ("case1/slide.tif", "case2/slide.tif"): the slides are told apart by their folders.
        shared = {name for name, n in Counter(b.name.lower() for b in slides).items() if n > 1}
        for path, stem in lonely:
            beside = [b for b in slides if b.primary.parent == path.parent]
            if len(beside) == 1:
                masks.append(path)  # the mask of the one slide in its folder
            else:
                a.files["mask"] += 1
                a.problem(_rel(path, tree), f"a mask, but no slide named {stem} is in the upload or the project")
        masks.sort()
        for bundle in slides:
            name = f"{bundle.primary.parent.name}_{bundle.name}" if bundle.name.lower() in shared and bundle.primary.parent != tree else bundle.name
            if name.lower() in existing:
                already += 1
                continue
            target = Target(f"new:{len(new)}", name, file=bundle.primary, folder=Path(_rel(bundle.primary, tree)).parent.as_posix())
            new.append({"key": target.key, "name": name, "rel": _rel(bundle.primary, tree)})
            a.add_target(target)

    others = [p for p in _files(tree) if p not in media]
    class_lists = sorted((p for p in others if formats.read_class_names(p) is not None), key=lambda p: (len(p.parts), p.as_posix()))
    names = formats.read_class_names(class_lists[0]) if class_lists else None
    if len(class_lists) > 1:
        a.warnings.append(f"Several class lists were found; {_rel(class_lists[0], tree)} was used.")

    not_understood = 0
    nameless: list[tuple[str, str, formats.ImagePart]] = []  # files about one image that no image is named like
    for path in others:
        if path in class_lists:
            continue
        rel = _rel(path, tree)
        kind, content = formats.sniff(path, image_project)
        if kind is None:
            not_understood += 1
            continue
        reader = {
            "yolo": lambda: formats.read_yolo(content, names),
            "voc": lambda: formats.read_voc(content),
            "labelme": lambda: formats.read_labelme(content),
            "cvat": lambda: formats.read_cvat(content),
            "label_studio": lambda: formats.read_label_studio(content),
            "coco": lambda: formats.read_coco(content),
            "table": lambda: formats.read_table(content, path.name),
            "single": lambda: formats.read_single(path, content),
        }[kind]
        a.files[kind] += 1
        missing = 0
        for part in reader():
            target = a.resolve(part.ref, Path(rel))
            if isinstance(target, Target):
                a.take(rel, kind, part, target)
            elif target is None:
                missing += 1
                if not part.ref or kind in ("voc", "labelme"):  # a file about one image
                    nameless.append((rel, kind, part))
            else:
                a.problem(rel, target)
        if missing and kind not in ("yolo", "voc", "labelme", "single"):
            a.problem(rel, f"{missing} of its images are not in the upload or the project")

    for path in masks:
        rel = _rel(path, tree)
        a.files["mask"] += 1
        part = next((formats.read_mask if image_project else formats.read_slide_mask)(path, names))
        target = a.resolve("", Path(rel), mask=True)
        if isinstance(target, Target):
            a.take(rel, "mask", part, target)
        elif target is None:
            nameless.append((rel, "mask", part))
        else:
            a.problem(rel, target)

    # A folder per case: a file named like no image is about the one image in its own folder -- when
    # it is the only such file there, and that image has nothing of that kind yet.
    beside: dict[tuple[str, bool], list] = {}
    for rel, kind, part in nameless:
        beside.setdefault((Path(rel).parent.as_posix(), kind == "mask"), []).append((rel, kind, part))
    for (folder, _is_mask), group in beside.items():
        here = a.by_folder.get(folder, [])
        if len(group) == 1 and len(here) == 1 and (group[0][1] == "mask" or here[0].key not in a.work):
            rel, kind, part = group[0]
            a.paired_by_folder.append(rel)
            a.take(rel, kind, part, here[0])
        else:
            for rel, kind, part in group:
                a.problem(rel, f"no {'image' if image_project else 'slide'} named {Path(part.ref or rel).stem}" if kind != "mask" else "no image with the mask's name")

    # Folders as classes: only when nothing else says what the images are.
    folder_labels: dict[str, str] = {}
    annotated = any(slot["entries"] or slot["label"] for slot in a.work.values())
    if image_project and not annotated and new:
        named = {item["key"]: _folder_label(item["name"]) for item in new}
        labels = {label for label in named.values() if label}
        if len(labels) >= 2 and sum(1 for label in named.values() if label) >= 0.9 * len(new):
            folder_labels = {key: label for key, label in named.items() if label}
            a.files["folders"] = len(labels)

    shape_labels = Counter((entry["label"] or "") for slot in a.work.values() for entry in slot["entries"])
    image_labels = Counter(slot["label"] for slot in a.work.values() if slot["label"]) + Counter(folder_labels.values())
    config = db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None
    classes = sorted(config.annotation_classes, key=lambda c: (c.order_index, c.id)) if config else []
    class_by_name = {c.name.strip().lower(): c.id for c in classes}
    with_annotations = {key for key, slot in a.work.items() if slot["entries"] or slot["label"]} | set(folder_labels)

    if a.paired_by_folder:
        shown = ", ".join(a.paired_by_folder[:3]) + ("..." if len(a.paired_by_folder) > 3 else "")
        a.warnings.append(
            f"{len(a.paired_by_folder)} file(s) are not named like any {'image' if image_project else 'slide'} and were matched to "
            f"the only one in their folder ({shown}). Check that this is what you meant."
        )

    preview = {
        "image_project": image_project,
        "folders": folders if len(folders) > 1 else [],
        "new_images": len(new),
        "already_in_project": already,
        "skipped_images": skipped[:_LISTED],
        "skipped_image_count": len(skipped),
        "files_not_understood": not_understood,
        "annotation_files": [{"format": k, "name": formats.FORMAT_NAMES[k], "count": n} for k, n in a.files.most_common()],
        "shapes": sum(shape_labels.values()),
        "image_labels": sum(image_labels.values()),
        "images_with_annotations": len(with_annotations),
        "new_images_without_annotations": sum(1 for item in new if item["key"] not in with_annotations),
        "existing_images_annotated": sum(1 for key in with_annotations if key.startswith("slide:")),
        "labels": [
            {"label": label, "shapes": shape_labels.get(label, 0), "images": image_labels.get(label, 0), "class_id": class_by_name.get(label.strip().lower()) if label else None}
            for label in sorted(set(shape_labels) | set(image_labels), key=lambda l: (-(shape_labels.get(l, 0) + image_labels.get(l, 0)), l))
        ],
        "classes": [{"id": c.id, "name": c.name, "color_hex": c.color_hex} for c in classes],
        "folder_labels": bool(folder_labels),
        "class_list": _rel(class_lists[0], tree) if class_lists else None,
        "unmatched": a.unmatched[:_LISTED],
        "unmatched_count": len(a.unmatched),
        "unreadable": dict(a.unreadable),
        "warnings": a.warnings,
    }
    return {
        "project_id": project.id,
        "image_project": image_project,
        "new": new,
        "work": {key: slot for key, slot in a.work.items() if slot["entries"] or slot["label"]},
        "folder_labels": folder_labels,
        "preview": preview,
    }


def save_plan(folder: Path, plan: dict) -> None:
    (folder / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def load_plan(folder: Path) -> dict | None:
    file = folder / "plan.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
