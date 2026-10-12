"""Versions of a project's work.

The tables always hold one state of a project: the one being worked on. **Saving a version** writes
that state -- settings and classes, the slides with their tissue, every patch, annotation and
suggestion -- to a file of its own, which is never changed afterwards. **Opening a version** replaces
what is in the tables with what that file holds, so the rest of the app only ever sees the version
that is open, and work goes on from it: saving again makes a new version whose parent is the one that
was open (versions form a tree).

A version's files, in ``<storage>/<project id>/_versions/<version id>/``:

* ``state.jsonl.gz`` -- one ``[kind, row]`` per line, in the order they are put back: header,
  project, config, class, slide, patch, annotation, suggestion;
* ``masks/`` -- the slides' tissue masks as they were (they are remade whenever tissue is edited).

Slide files themselves are not copied: a slide's pixels never change. A slide removed from the work
keeps its files while a version still has it (:func:`release_slide_files`).

Rows go back under the ids they had, so models, links and cached images keep meaning the same thing.
SQLite hands the id of a deleted row to the next new one, so an id may by then belong to another
project's row; that row of the version then gets a new id, and what points at it follows. Classes are
also named by trained models, so their changed ids are remembered on the project (``class_aliases``).

Trained models and training runs are the project's, not a version's: they are the same in all of them.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, create_engine, delete, func, insert, select, update
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.models.training import Suggestion, TrainedModel
from app.models.user import User
from app.models.version import ProjectVersion
from app.services import project_storage, reader_cache
from app.services.deepzoom_service import purge_slide_tiles
from app.services.tissue_mask import auto_mask_name, final_mask_name

FORMAT = 1
_BATCH = 500  # rows put back at a time; also how many ids one "is it taken?" question names

# Not the version's to restore: they say which row it is, not what state it is in.
_CONFIG_IDENTITY = {"id", "project_id", "parent_version_id", "created_at", "updated_at"}


class VersionError(Exception):
    """A version that cannot be opened (its files are missing or from a newer release)."""


def versions_dir(storage_root: Path, project_id: int) -> Path:
    return project_storage.project_dir(storage_root, project_id) / "_versions"


def version_dir(storage_root: Path, version: ProjectVersion) -> Path:
    return versions_dir(storage_root, version.project_id) / str(version.id)


def _masks_dir(storage_root: Path, project_id: int) -> Path:
    return project_storage.project_dir(storage_root, project_id) / "_masks"  # tissue_mask.mask_dir


def _project_slides(project_id: int):
    return select(Slide.id).where(Slide.project_id == project_id)


def _config_of(db: Session, project: Project) -> ProjectConfigVersion | None:
    return db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None


def _plain(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _rows(db: Session, model, *where):
    """A table's rows as plain dicts, oldest id first, without loading them all at once."""
    table = model.__table__
    result = db.execute(select(table).where(*where).order_by(table.c.id).execution_options(yield_per=2000))
    for row in result.mappings():
        yield {key: _plain(value) for key, value in row.items()}


def _values(table, row: dict) -> dict:
    """A saved row as values to insert into ``table``."""
    return {
        c.name: datetime.fromisoformat(row[c.name]) if isinstance(c.type, DateTime) and isinstance(row[c.name], str) else row[c.name]
        for c in table.columns
        if c.name in row  # a column added since the version was saved takes its default
    }


# ------------------------------------------------------------------ unsaved changes


def working_state(db: Session, project: Project) -> str:
    """A fingerprint of the work as it is now. It changes whenever something a version holds is
    added, changed or removed, so comparing it with the one taken at the last save or open
    (``Project.saved_state``) tells whether there are unsaved changes."""
    db.flush()
    slides = _project_slides(project.id)
    parts: list = [project.split_config]
    config = _config_of(db, project)
    if config is not None:
        parts.append({col.name: _plain(getattr(config, col.name)) for col in ProjectConfigVersion.__table__.columns if col.name != "updated_at"})
        c = AnnotationClass
        classes = select(c.id, c.name, c.color_hex, c.hotkey, c.code, c.order_index).where(c.config_version_id == config.id).order_by(c.id)
        parts.append([list(row) for row in db.execute(classes)])
    for model, where in (
        (Slide, Slide.project_id == project.id),
        (Patch, Patch.slide_id.in_(slides)),
        (GeometryAnnotation, GeometryAnnotation.slide_id.in_(slides)),
        (Suggestion, Suggestion.slide_id.in_(slides)),
    ):
        # Every change made through the app stamps the row's updated_at; a removal changes the count.
        count, top_id, changed = db.execute(select(func.count(model.id), func.max(model.id), func.max(model.updated_at)).where(where)).one()
        parts.append([count, top_id, str(changed)])
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def has_unsaved_changes(db: Session, project: Project) -> bool:
    return project.current_version_id is None or project.saved_state != working_state(db, project)


# ------------------------------------------------------------------ saving


def _write_snapshot(db: Session, project: Project, folder: Path, storage_root: Path) -> tuple[dict, list[str]]:
    """Write the project's state into ``folder``. Returns its summary and its slides' stored files."""
    project_storage.remove_tree(folder)  # a deleted version's id can come round again
    (folder / "masks").mkdir(parents=True)
    masks = _masks_dir(storage_root, project.id)
    slides = _project_slides(project.id)
    config = _config_of(db, project)
    summary = {"slides": 0, "patches": 0, "annotated_patches": 0, "annotations": 0, "suggestions": 0, "classes": 0}
    files: list[str] = []

    partial = folder / "state.jsonl.gz.part"
    with gzip.open(partial, "wt", encoding="utf-8", compresslevel=5) as out:

        def put(kind: str, row: dict) -> None:
            out.write(json.dumps([kind, row], separators=(",", ":")))
            out.write("\n")

        put("header", {"format": FORMAT, "project_id": project.id, "project_type": project.project_type})
        put("project", {"split_config": project.split_config})
        if config is not None:
            for row in _rows(db, ProjectConfigVersion, ProjectConfigVersion.id == config.id):
                put("config", row)
            for row in _rows(db, AnnotationClass, AnnotationClass.config_version_id == config.id):
                put("class", row)
                summary["classes"] += 1
        for row in _rows(db, Slide, Slide.project_id == project.id):
            put("slide", row)
            summary["slides"] += 1
            if row["file_path"]:
                files.append(row["file_path"])
            auto = masks / auto_mask_name(row["id"])
            if auto.exists():
                shutil.copyfile(auto, folder / "masks" / auto.name)
            final = storage_root / row["tissue_mask_path"] if row["tissue_mask_path"] else None
            if final is not None and final.exists():
                shutil.copyfile(final, folder / "masks" / final_mask_name(row["id"]))
        for row in _rows(db, Patch, Patch.slide_id.in_(slides)):
            put("patch", row)
            summary["patches"] += 1
            summary["annotated_patches"] += row["status"] in ("annotated", "reviewed")
        for row in _rows(db, GeometryAnnotation, GeometryAnnotation.slide_id.in_(slides)):
            put("annotation", row)
            summary["annotations"] += 1
        for row in _rows(db, Suggestion, Suggestion.slide_id.in_(slides)):
            put("suggestion", row)
            summary["suggestions"] += row["status"] == "pending"
    partial.replace(folder / "state.jsonl.gz")
    return summary, files


def save_version(db: Session, project: Project, storage_root: Path, *, name: str | None, note: str | None, user: User | None) -> ProjectVersion:
    """Save the work as it is now as a new version, made from the version it was on."""
    db.flush()
    number = (db.scalar(select(func.max(ProjectVersion.number)).where(ProjectVersion.project_id == project.id)) or 0) + 1
    version = ProjectVersion(
        project_id=project.id,
        parent_id=project.current_version_id,
        number=number,
        name=(name or "").strip() or f"Version {number}",
        note=(note or "").strip() or None,
        created_by=user.name if user else None,
        created_by_id=user.id if user else None,
    )
    db.add(version)
    db.flush()
    folder = version_dir(storage_root, version)
    try:
        version.summary, version.slide_files = _write_snapshot(db, project, folder, storage_root)
        project.current_version_id = version.id
        project.saved_state = working_state(db, project)
        db.commit()
    except BaseException:
        db.rollback()
        project_storage.remove_tree(folder)
        raise
    return version


# ------------------------------------------------------------------ opening


class _Restorer:
    """Puts a version's rows back, one kind after another (each kind only points at earlier ones)."""

    TABLES = {"class": AnnotationClass, "slide": Slide, "patch": Patch, "annotation": GeometryAnnotation, "suggestion": Suggestion}

    def __init__(self, db: Session, project: Project, config: ProjectConfigVersion | None):
        self.db, self.project, self.config = db, project, config
        self.kind: str | None = None
        self.batch: list[dict] = []
        # Rows whose id is another project's by now, held back until their kind is in (the new id
        # must be above every id the version uses): (id it was saved with, row).
        self.held: list[tuple[int, dict]] = []
        self.moved: dict[str, dict[int, int]] = {kind: {} for kind in self.TABLES}  # saved id -> id now
        self.classes: set[int] = set()  # saved ids of the version's classes
        self.aliases: dict[str, int] = dict(project.class_aliases or {})
        self.new_aliases: dict[int, int] = {}
        self.slides: list[tuple[int, str | None]] = []  # (saved id, tissue_mask_path)
        self.users = set(db.scalars(select(User.id)))
        self.models = set(db.scalars(select(TrainedModel.id).where(TrainedModel.project_id == project.id)))

    def feed(self, kind: str, row: dict) -> None:
        if kind != self.kind:
            self._end_kind()
            self.kind = kind
        if kind == "header":
            if row.get("format", 1) > FORMAT:
                raise VersionError("This version was saved by a newer release of the app; update the app to open it.")
        elif kind == "project":
            self.project.split_config = row.get("split_config")
        elif kind == "config":
            if self.config is not None:
                for column in ProjectConfigVersion.__table__.columns:
                    if column.name in row and column.name not in _CONFIG_IDENTITY:
                        setattr(self.config, column.name, row[column.name])
        elif kind in self.TABLES:
            prepared = getattr(self, f"_{kind}")(row)
            if prepared is not None:
                self.batch.append(prepared)
                if len(self.batch) >= _BATCH:
                    self._insert_batch()

    def finish(self) -> None:
        self._end_kind()
        self.project.class_aliases = self.aliases or None

    # -- one row of each kind: point it at this project's rows as they are now

    def _now(self, kind: str, saved_id: int | None) -> int | None:
        return None if saved_id is None else self.moved[kind].get(saved_id, saved_id)

    def _class_now(self, saved_id: int | None) -> int | None:
        return self._now("class", saved_id) if saved_id in self.classes else None

    def _user(self, user_id: int | None) -> int | None:
        return user_id if user_id in self.users else None  # the account may be gone since

    def _class(self, row: dict) -> dict | None:
        if self.config is None:
            return None
        saved, target = row["id"], row["id"]
        while str(target) in self.aliases:  # the id it has had since an earlier opening
            target = self.aliases[str(target)]
        self.classes.add(saved)
        if target != saved:
            self.moved["class"][saved] = target
        row["_saved"] = saved
        row["id"] = target
        row["config_version_id"] = self.config.id
        return row

    def _slide(self, row: dict) -> dict:
        self.slides.append((row["id"], row.get("tissue_mask_path")))
        row["project_id"] = self.project.id
        row["active_config_version_id"] = self.config.id if self.config else None
        return row

    def _patch(self, row: dict) -> dict:
        row["slide_id"] = self._now("slide", row["slide_id"])
        row["config_version_id"] = self.config.id
        row["label_class_id"] = self._class_now(row.get("label_class_id"))
        row["reviewed_by_id"] = self._user(row.get("reviewed_by_id"))
        return row

    def _annotation(self, row: dict) -> dict:
        row["slide_id"] = self._now("slide", row["slide_id"])
        row["patch_id"] = self._now("patch", row.get("patch_id"))
        row["config_version_id"] = self.config.id
        row["class_id"] = self._class_now(row.get("class_id"))
        row["created_by_id"] = self._user(row.get("created_by_id"))
        return row

    def _suggestion(self, row: dict) -> dict | None:
        if row["model_id"] not in self.models:
            return None  # its model has been deleted since: nothing could say what it is
        row["slide_id"] = self._now("slide", row["slide_id"])
        row["patch_id"] = self._now("patch", row["patch_id"])
        row["class_id"] = self._class_now(row.get("class_id"))
        row["annotation_id"] = self._now("annotation", row.get("annotation_id"))
        row["decided_by_id"] = self._user(row.get("decided_by_id"))
        return row

    # -- writing

    def _insert_batch(self) -> None:
        rows, self.batch = self.batch, []
        if not rows:
            return
        table = self.TABLES[self.kind].__table__
        taken = set(self.db.scalars(select(table.c.id).where(table.c.id.in_([r["id"] for r in rows]))))
        free = [_values(table, r) for r in rows if r["id"] not in taken]
        if free:
            self.db.execute(insert(table), free)
        self.held += [(r.get("_saved", r["id"]), r) for r in rows if r["id"] in taken]

    def _end_kind(self) -> None:
        if self.kind not in self.TABLES:
            return
        self._insert_batch()
        table = self.TABLES[self.kind].__table__
        for saved, row in self.held:
            values = _values(table, row)
            taken_id = values.pop("id")
            new_id = self.db.execute(insert(table).values(**values)).inserted_primary_key[0]
            self.moved[self.kind][saved] = new_id
            if self.kind == "class":
                self.aliases[str(taken_id)] = new_id
                self.new_aliases[taken_id] = new_id
        self.held = []


def _follow_class_aliases(db: Session, project_id: int, changed: dict[int, int]) -> None:
    """The project's models name its classes by id: keep them pointing at the classes that moved."""
    if not changed:
        return
    for model in db.scalars(select(TrainedModel).where(TrainedModel.project_id == project_id)):
        if any(c.get("id") in changed for c in model.classes or []):
            model.classes = [{**c, "id": changed[c["id"]]} if c.get("id") in changed else c for c in model.classes]
        if model.class_map and any(v in changed for v in model.class_map.values()):
            model.class_map = {k: changed.get(v, v) for k, v in model.class_map.items()}


def switch_to(db: Session, project: Project, version: ProjectVersion, storage_root: Path) -> None:
    """Make ``version`` the work: the tables then hold exactly what it holds. Whatever was there and
    not saved in a version is gone -- the caller has asked the user about that."""
    folder = version_dir(storage_root, version)
    state_file = folder / "state.jsonl.gz"
    if not state_file.exists():
        raise VersionError(f"The files of version {version.number} are missing from the server's storage.")

    db.flush()
    was_there = db.execute(select(Slide.id, Slide.file_path).where(Slide.project_id == project.id)).all()
    slides = _project_slides(project.id)
    config = _config_of(db, project)
    masks = _masks_dir(storage_root, project.id)
    staged = masks.with_name("_masks.opening")
    try:
        db.execute(delete(Suggestion).where(Suggestion.slide_id.in_(slides)))
        db.execute(delete(GeometryAnnotation).where(GeometryAnnotation.slide_id.in_(slides)))
        db.execute(delete(Patch).where(Patch.slide_id.in_(slides)))
        db.execute(delete(Slide).where(Slide.project_id == project.id))
        if config is not None:
            db.execute(delete(AnnotationClass).where(AnnotationClass.config_version_id == config.id))
            db.expire(config, ["annotation_classes"])

        restorer = _Restorer(db, project, config)
        with gzip.open(state_file, "rt", encoding="utf-8") as lines:
            for line in lines:
                kind, row = json.loads(line)
                restorer.feed(kind, row)
        restorer.finish()
        _follow_class_aliases(db, project.id, restorer.new_aliases)

        # The masks as they were, under the ids the slides have now; swapped in once the rows are in.
        project_storage.remove_tree(staged)
        staged.mkdir(parents=True)
        now_there = []
        for saved, mask_path in restorer.slides:
            slide_id = restorer.moved["slide"].get(saved, saved)
            now_there.append(slide_id)
            for source, target in ((auto_mask_name(saved), auto_mask_name(slide_id)), (final_mask_name(saved), final_mask_name(slide_id))):
                if (folder / "masks" / source).exists():
                    shutil.copyfile(folder / "masks" / source, staged / target)
            current = (masks / final_mask_name(slide_id)).relative_to(storage_root).as_posix() if mask_path else None
            if current != mask_path:
                db.execute(update(Slide).where(Slide.id == slide_id).values(tissue_mask_path=current))

        project.current_version_id = version.id
        project.saved_state = working_state(db, project)
        db.commit()
    except BaseException:
        db.rollback()
        project_storage.remove_tree(staged)
        raise

    project_storage.remove_tree(masks)
    staged.rename(masks)
    # An id may now be another slide than a moment ago: nothing cached under it may be served.
    for slide_id in {row.id for row in was_there} | set(now_there):
        reader_cache.invalidate(slide_id)
        purge_slide_tiles(slide_id)
    release_slide_files(db, storage_root, project.id, [row.file_path for row in was_there])


# ------------------------------------------------------------------ reading a version beside the work


@contextmanager
def read_only(db: Session, project: Project, version: ProjectVersion, storage_root: Path) -> Iterator[tuple[Session, Project]]:
    """A version to read without opening it: a session on a throw-away database holding that version's
    rows, and the project in it. Whatever reads a project through a session -- exports, a training
    run's dataset -- reads the version this way, while the work stays as it is.

    Ids are those the version was saved with (classes: the ids they have now, see ``class_aliases``),
    so a slide id here may be another slide's in the work; only its file says which slide it is.
    Suggestions are left out. The database is deleted on leaving."""
    folder = version_dir(storage_root, version)
    state_file = folder / "state.jsonl.gz"
    if not state_file.exists():
        raise VersionError(f"The files of version {version.number} are missing from the server's storage.")

    scratch = storage_root / "_staging" / f"version-{uuid.uuid4().hex}"
    scratch.mkdir(parents=True)
    # (Used from whichever worker thread a request's steps run on, one at a time.)
    engine = create_engine(f"sqlite:///{(scratch / 'version.db').as_posix()}", connect_args={"check_same_thread": False})
    view = Session(engine, autoflush=False)
    try:
        Base.metadata.create_all(engine)
        # The project and its configuration as rows to hang the version's state on.
        view.execute(insert(Project.__table__), [_values(Project.__table__, row) for row in _rows(db, Project, Project.id == project.id)])
        config_id = project.active_config_version_id
        if config_id is not None:
            view.execute(insert(ProjectConfigVersion.__table__), [_values(ProjectConfigVersion.__table__, row) for row in _rows(db, ProjectConfigVersion, ProjectConfigVersion.id == config_id)])
        seen = view.get(Project, project.id)
        restorer = _Restorer(view, seen, _config_of(view, seen))
        with gzip.open(state_file, "rt", encoding="utf-8") as lines:
            for line in lines:
                kind, row = json.loads(line)
                if kind != "suggestion":
                    restorer.feed(kind, row)
        restorer.finish()
        for saved, mask_path in restorer.slides:
            if mask_path:  # its mask as it was, read from the version's own files
                kept = (folder / "masks" / final_mask_name(saved)).relative_to(storage_root).as_posix()
                view.execute(update(Slide).where(Slide.id == saved).values(tissue_mask_path=kept))
        view.commit()
        yield view, seen
    finally:
        view.close()
        engine.dispose()
        project_storage.remove_tree(scratch)


def describe(version: ProjectVersion) -> str:
    return f"v{version.number} · {version.name}"


def describe_work(db: Session, project: Project) -> str:
    """What the work is, in words, for a record of what something was made from."""
    current = db.get(ProjectVersion, project.current_version_id) if project.current_version_id else None
    if current is None:
        return "Unsaved work"
    return describe(current) + (" + unsaved changes" if has_unsaved_changes(db, project) else "")


# ------------------------------------------------------------------ files and deleting


def release_slide_files(db: Session, storage_root: Path, project_id: int, file_paths: list[str | None]) -> None:
    """Delete the stored files of slides that have left the work (``Slide.file_path`` values) --
    except those a saved version, or a slide still in the work, has. Call it after the commit."""
    kept = set(db.scalars(select(Slide.file_path).where(Slide.project_id == project_id, Slide.file_path.isnot(None))))
    for files in db.scalars(select(ProjectVersion.slide_files).where(ProjectVersion.project_id == project_id)):
        kept.update(files or [])
    storage = storage_root.resolve()
    project_dir = (storage / str(project_id)).resolve()
    for file_path in {f for f in file_paths if f} - kept:
        primary = (storage / file_path).resolve()
        slide_dir = primary.parent
        if slide_dir != project_dir and project_dir in slide_dir.parents:
            # Current layout: each slide owns a directory (a .mrxs slide keeps its data folder in it).
            shutil.rmtree(slide_dir, ignore_errors=True)
        elif project_dir in primary.parents:
            primary.unlink(missing_ok=True)  # older layout: one flat file per slide


def delete_version(db: Session, project: Project, version: ProjectVersion, storage_root: Path) -> None:
    """Delete a saved version. The work is untouched, even when it is on that version: it then counts
    as unsaved. Versions made from it hang from its parent instead."""
    files = list(version.slide_files or [])
    folder = version_dir(storage_root, version)
    db.execute(update(ProjectVersion).where(ProjectVersion.parent_id == version.id).values(parent_id=version.parent_id))
    if project.current_version_id == version.id:
        project.current_version_id = version.parent_id
        project.saved_state = None
    db.delete(version)
    db.commit()
    project_storage.remove_tree(folder)
    release_slide_files(db, storage_root, project.id, files)
