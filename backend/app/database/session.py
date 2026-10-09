from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.database.base import Base
    from app.models import (  # noqa: F401
        annotation,
        config_version,
        patch,
        project,
        slide,
        training,
        user,
    )

    if settings.database_url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL + a busy timeout let concurrent request threads (FastAPI runs
            # sync endpoints in a threadpool) queue briefly instead of
            # immediately raising "database is locked" under light contention.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _backfill_grid_keys()
    _merge_config_versions()
    _link_patch_labels()
    _upgrade_enabled_tools()
    _allow_slide_level_annotations()


def _add_missing_columns(bind=None) -> None:
    """Tiny forward-only migration for databases created by an older version.

    ``create_all`` only creates missing *tables*, so a column added to an existing
    table has to be added by hand. Only additive, defaulted columns belong here.
    """
    from sqlalchemy import inspect, text

    additions = {
        "projects": [("project_type", "VARCHAR(20) NOT NULL DEFAULT 'wsi'"), ("split_config", "JSON")],
        "slides": [
            ("tissue_source", "VARCHAR(20) NOT NULL DEFAULT 'auto'"),
            ("tissue_regions", "JSON"),
            ("active_grid_key", "VARCHAR(80)"),
            ("split", "VARCHAR(10)"),
        ],
        "patches": [("grid_key", "VARCHAR(80)"), ("label_class_id", "INTEGER REFERENCES annotation_classes(id)"), ("reviewed_by_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL")],
        "annotation_classes": [("code", "BIGINT")],
        "project_config_versions": [("saved_grids", "JSON")],
        "trained_models": [("source", "VARCHAR(20) NOT NULL DEFAULT 'run'"), ("class_map", "JSON")],
        "geometry_annotations": [("whole_patch", "BOOLEAN NOT NULL DEFAULT 0"), ("created_by_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL")],
    }
    bind = bind or engine
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    with bind.begin() as conn:
        for table, columns in additions.items():
            if table not in tables:
                continue  # create_all makes it, with every column
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _backfill_grid_keys(bind=None) -> None:
    """Patches made before grids existed were cut with their config version's own settings: give
    them that grid's key, and put each slide on the grid of its active version. Only fills blanks,
    so it is safe to run on every startup."""
    from sqlalchemy.orm import Session as OrmSession

    from app.models.config_version import ProjectConfigVersion
    from app.models.patch import Patch
    from app.models.project import Project
    from app.models.slide import Slide
    from app.services.patch_grid import IMAGE_GRID_KEY, grid_key_of

    bind = bind or engine
    with OrmSession(bind) as db:
        if db.query(Patch.id).filter(Patch.grid_key.is_(None)).first() is None and (
            db.query(Slide.id).filter(Slide.active_grid_key.is_(None), Slide.active_config_version_id.isnot(None)).first() is None
        ):
            return
        image_projects = {pid for (pid,) in db.query(Project.id).filter(Project.project_type == "image")}
        for config in db.query(ProjectConfigVersion):
            key = IMAGE_GRID_KEY if config.project_id in image_projects else grid_key_of(config)
            db.query(Patch).filter(Patch.config_version_id == config.id, Patch.grid_key.is_(None)).update(
                {Patch.grid_key: key}, synchronize_session=False
            )
            for slide in db.query(Slide).filter(Slide.active_config_version_id == config.id, Slide.active_grid_key.is_(None)):
                if db.query(Patch.id).filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id).first():
                    slide.active_grid_key = key
        db.commit()


def _merge_config_versions(bind=None) -> None:
    """A project has one configuration. Projects made when a project could have several versions are
    merged into the one they use (the version new slides started on), without losing anything:

    * classes are matched by name; one the kept configuration lacks is added to it;
    * annotations and patches move over (a patch identical to one already there -- same slide, grid and
      place -- hands its annotations to that one and goes);
    * slides on another version move to the kept one; the other versions are then deleted.

    Safe to run on every startup: a project with one configuration is left alone.
    """
    from sqlalchemy import func
    from sqlalchemy.orm import Session as OrmSession

    from app.models.annotation import GeometryAnnotation
    from app.models.config_version import AnnotationClass, ProjectConfigVersion
    from app.models.patch import Patch
    from app.models.project import Project
    from app.models.slide import Slide

    bind = bind or engine
    with OrmSession(bind) as db:
        crowded = [
            pid
            for pid, n in db.query(ProjectConfigVersion.project_id, func.count(ProjectConfigVersion.id))
            .group_by(ProjectConfigVersion.project_id)
            .all()
            if n > 1
        ]
        for project in db.query(Project).filter(Project.id.in_(crowded)):
            configs = db.query(ProjectConfigVersion).filter(ProjectConfigVersion.project_id == project.id).order_by(ProjectConfigVersion.id).all()
            keep = next((c for c in configs if c.id == project.active_config_version_id), configs[0])
            if keep.status == "locked":
                keep.status = "draft"  # there is no locking any more; the one configuration is editable
            project.active_config_version_id = keep.id
            by_name = {c.name.strip().lower(): c for c in keep.annotation_classes}
            used_keys = {c.hotkey for c in keep.annotation_classes if c.hotkey}

            for other in (c for c in configs if c.id != keep.id):
                class_map = {}
                for cls in other.annotation_classes:
                    target = by_name.get(cls.name.strip().lower())
                    if target is None:
                        target = AnnotationClass(
                            config_version_id=keep.id,
                            name=cls.name,
                            color_hex=cls.color_hex,
                            hotkey=cls.hotkey if cls.hotkey and cls.hotkey not in used_keys else None,
                            code=cls.code,
                            order_index=len(by_name),
                        )
                        db.add(target)
                        db.flush()
                        by_name[cls.name.strip().lower()] = target
                        if target.hotkey:
                            used_keys.add(target.hotkey)
                    class_map[cls.id] = target.id

                for ann in db.query(GeometryAnnotation).filter(GeometryAnnotation.config_version_id == other.id):
                    ann.config_version_id = keep.id
                    if ann.class_id is not None:
                        ann.class_id = class_map.get(ann.class_id)

                for patch in db.query(Patch).filter(Patch.config_version_id == other.id).all():
                    twin = (
                        db.query(Patch)
                        .filter(
                            Patch.config_version_id == keep.id,
                            Patch.slide_id == patch.slide_id,
                            Patch.grid_key == patch.grid_key,
                            Patch.x == patch.x,
                            Patch.y == patch.y,
                            Patch.width_l0 == patch.width_l0,
                            Patch.height_l0 == patch.height_l0,
                        )
                        .first()
                    )
                    if twin is None:
                        patch.config_version_id = keep.id
                        continue
                    db.query(GeometryAnnotation).filter(GeometryAnnotation.patch_id == patch.id).update(
                        {GeometryAnnotation.patch_id: twin.id}, synchronize_session=False
                    )
                    db.flush()
                    db.query(Patch).filter(Patch.id == patch.id).delete(synchronize_session=False)

                db.query(Slide).filter(Slide.active_config_version_id == other.id).update(
                    {Slide.active_config_version_id: keep.id}, synchronize_session=False
                )
                db.query(ProjectConfigVersion).filter(ProjectConfigVersion.parent_version_id == other.id).update(
                    {ProjectConfigVersion.parent_version_id: None}, synchronize_session=False
                )
                db.flush()
                for cls in list(other.annotation_classes):
                    db.delete(cls)
                db.flush()
                db.delete(other)
                db.flush()
            keep.parent_version_id = None
        db.commit()


def _link_patch_labels(bind=None) -> None:
    """Patch labels set before labels were linked to classes: link each one that names a class of its
    configuration (so renaming the class renames it). Only fills blanks; safe on every startup."""
    from sqlalchemy import text

    bind = bind or engine
    with bind.begin() as conn:
        conn.execute(
            text(
                "UPDATE patches SET label_class_id = ("
                " SELECT c.id FROM annotation_classes c"
                " WHERE c.config_version_id = patches.config_version_id AND lower(trim(c.name)) = lower(trim(patches.patch_label))"
                " LIMIT 1)"
                " WHERE label_class_id IS NULL AND patch_label IS NOT NULL"
            )
        )


LEGACY_TOOLS = ["polygon", "rectangle", "point", "freehand"]
NEW_TOOLS = ["line", "freehand_line", "circle"]


def _upgrade_enabled_tools(bind=None) -> None:
    """Give existing projects the line/freehand-line/circle tools.

    Only configs still on the untouched legacy default (all four original tools) are upgraded;
    a config someone customised has made a choice and keeps it. Safe to run on every startup.
    """
    import json

    from sqlalchemy import text

    bind = bind or engine
    with bind.begin() as conn:
        for config_id, raw in conn.execute(text("SELECT id, enabled_tools FROM project_config_versions")).fetchall():
            tools = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(tools, list) and sorted(tools) == sorted(LEGACY_TOOLS):
                conn.execute(
                    text("UPDATE project_config_versions SET enabled_tools = :tools WHERE id = :id"),
                    {"tools": json.dumps(tools + NEW_TOOLS), "id": config_id},
                )


def _allow_slide_level_annotations(bind=None) -> None:
    """Let ``geometry_annotations.patch_id`` be NULL (annotations drawn on the whole slide).

    Older databases declared the column NOT NULL, and SQLite cannot drop a constraint in place, so the
    table is rebuilt: renamed aside, recreated from the current model, rows copied across, old table
    dropped -- all in one transaction with foreign keys switched off and re-checked before committing.
    A no-op once the column is nullable, so it is safe to run on every startup.
    """
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.models import annotation, config_version, patch, project, slide  # noqa: F401  (foreign keys resolve through all models)
    from app.models.annotation import GeometryAnnotation

    bind = bind or engine
    if bind.dialect.name != "sqlite":
        return  # a fresh database of any other kind is created nullable by create_all

    table = GeometryAnnotation.__table__
    raw = bind.raw_connection()
    original_isolation = raw.isolation_level
    try:
        raw.isolation_level = None  # explicit BEGIN/COMMIT below
        cur = raw.cursor()
        info = cur.execute(f"PRAGMA table_info({table.name})").fetchall()
        not_null = {row[1]: bool(row[3]) for row in info}
        if not info or not not_null.get("patch_id"):
            return

        old_columns = [row[1] for row in info]
        columns = ", ".join(c.name for c in table.columns if c.name in old_columns)
        old_indexes = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table.name,))]

        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("BEGIN")
        try:
            cur.execute(f"ALTER TABLE {table.name} RENAME TO {table.name}_old")
            for name in old_indexes:
                cur.execute(f'DROP INDEX "{name}"')
            cur.execute(str(CreateTable(table).compile(dialect=bind.dialect)))
            for index in table.indexes:
                cur.execute(str(CreateIndex(index).compile(dialect=bind.dialect)))
            cur.execute(f"INSERT INTO {table.name} ({columns}) SELECT {columns} FROM {table.name}_old")
            cur.execute(f"DROP TABLE {table.name}_old")
            if cur.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("foreign key violations after rebuilding geometry_annotations")
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise
    finally:
        # This connection goes back to the pool: leave it exactly as it was found.
        try:
            raw.cursor().execute("PRAGMA foreign_keys=ON")
            raw.isolation_level = original_isolation
        finally:
            raw.close()
