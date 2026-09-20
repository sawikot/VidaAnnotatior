from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import forbid_for_image_project, get_project_or_404, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.slide import (
    SkippedItemOut,
    SlideActiveConfigRequest,
    SlideBatchImportResult,
    SlideCreateDemo,
    SlideImportPathRequest,
    SlideOut,
    WsiFormatOut,
    WsiFormatsOut,
)
from app.services import reader_cache
from app.services.image_import import IMAGE_EXTENSIONS, ImageDiscovery, ImageItem, discover_images
from app.services.deepzoom_service import DeepZoomAdapter, purge_slide_tiles, render_thumbnail_bytes, render_tile_bytes
from app.services.multipart_stream import ReceivedFile, receive_multipart
from app.services.wsi_bundles import (
    ARCHIVE_EXTENSIONS,
    PRIMARY_EXTENSIONS,
    ArchiveError,
    Discovery,
    SlideBundle,
    UnsafePathError,
    discover_bundles,
    extract_zip,
    install_bundle,
    safe_parts,
    slide_dir_name,
)

router = APIRouter(tags=["slides"])


def _extract_and_store_metadata(db: Session, slide: Slide) -> None:
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        meta = reader.get_metadata()
        slide.width_l0 = meta.width
        slide.height_l0 = meta.height
        slide.level_count = meta.level_count
        slide.level_dimensions = [list(d) for d in meta.level_dimensions]
        slide.level_downsamples = meta.level_downsamples
        slide.mpp_x = meta.mpp_x
        slide.mpp_y = meta.mpp_y
        slide.magnification = meta.magnification
        slide.status = "imported"
        slide.error_message = None
    except Exception as exc:  # noqa: BLE001
        slide.status = "error"
        slide.error_message = f"Failed to read WSI metadata: {exc}"
    db.commit()


@router.get("/projects/{project_id}/slides", response_model=list[SlideOut])
def list_slides(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)):
    return (
        db.query(Slide)
        .filter(Slide.project_id == project.id)
        .order_by(Slide.created_at.desc())
        .all()
    )


@router.get("/wsi-formats", response_model=WsiFormatsOut)
def get_wsi_formats():
    """What the importer accepts -- the UI builds its hints from this so the
    list of formats lives in exactly one place."""
    settings = get_settings()
    return WsiFormatsOut(
        formats=[WsiFormatOut(extension=e, description=d) for e, d in PRIMARY_EXTENSIONS.items()],
        archives=sorted(ARCHIVE_EXTENSIONS),
        max_upload_bytes=settings.max_upload_bytes,
        max_upload_files=settings.max_upload_files,
    )


def _register_bundle(
    db: Session,
    project: Project,
    bundle: SlideBundle,
    config_version_id: int | None,
    source_type: str,
    move: bool,
) -> Slide | SkippedItemOut:
    """Store one bundle in its own directory and create its Slide row. If
    OpenSlide can't actually open it, nothing is left behind and the reason is
    returned instead."""
    settings = get_settings()
    slide_dir = settings.wsi_storage_dir / str(project.id) / slide_dir_name(bundle.primary.stem)
    try:
        primary = install_bundle(bundle, slide_dir, move=move)
    except OSError as exc:
        shutil.rmtree(slide_dir, ignore_errors=True)
        return SkippedItemOut(name=bundle.name, reason=f"could not store the files: {exc}")

    slide = Slide(
        project_id=project.id,
        filename=bundle.name,
        file_path=primary.relative_to(settings.wsi_storage_dir).as_posix(),
        source_type=source_type,
        format=bundle.primary.suffix.lower(),
        status="imported",
        active_config_version_id=config_version_id or project.active_config_version_id,
    )
    db.add(slide)
    db.commit()
    db.refresh(slide)

    _extract_and_store_metadata(db, slide)
    db.refresh(slide)
    if slide.status == "error":
        reason = slide.error_message or "could not be read"
        reader_cache.invalidate(slide.id)
        db.delete(slide)
        db.commit()
        shutil.rmtree(slide_dir, ignore_errors=True)
        return SkippedItemOut(name=bundle.name, reason=reason)
    return slide


def _import_discovered(
    db: Session,
    project: Project,
    discovery: Discovery,
    config_version_id: int | None,
    source_type: str,
    move: bool,
    warnings: list[str],
    skipped: list[SkippedItemOut],
) -> SlideBatchImportResult:
    slides: list[Slide] = []
    for item in discovery.skipped:
        skipped.append(SkippedItemOut(name=item.name, reason=item.reason))
    for bundle in discovery.bundles:
        outcome = _register_bundle(db, project, bundle, config_version_id, source_type, move)
        if isinstance(outcome, Slide):
            slides.append(outcome)
        else:
            skipped.append(outcome)
    return SlideBatchImportResult(
        slides=slides, skipped=skipped, ignored_file_count=discovery.ignored_files, warnings=warnings
    )


def _register_image(
    db: Session, project: Project, item: ImageItem, source_type: str, move: bool
) -> Slide | SkippedItemOut:
    """One image becomes one slide whose Level-0 grid is the image itself, holding a
    single virtual patch that covers all of it. Annotating it therefore needs no tissue
    detection or patch generation, and every coordinate is simply an image pixel."""
    settings = get_settings()
    if project.active_config_version_id is None:
        return SkippedItemOut(name=item.name, reason="the project has no configuration to annotate against")

    image_dir = settings.wsi_storage_dir / str(project.id) / slide_dir_name(item.path.stem)
    try:
        image_dir.mkdir(parents=True)
        stored = image_dir / item.path.name
        (shutil.move if move else shutil.copy2)(item.path, stored)
    except OSError as exc:
        shutil.rmtree(image_dir, ignore_errors=True)
        return SkippedItemOut(name=item.name, reason=f"could not store the file: {exc}")

    try:
        slide = Slide(
            project_id=project.id,
            filename=item.name,
            file_path=stored.relative_to(settings.wsi_storage_dir).as_posix(),
            source_type=source_type,
            format=stored.suffix.lower(),
            status="patches_generated",  # nothing to detect or generate: the image is its own patch
            width_l0=item.width,
            height_l0=item.height,
            level_count=1,
            level_dimensions=[[item.width, item.height]],
            level_downsamples=[1.0],
            active_config_version_id=project.active_config_version_id,
        )
        db.add(slide)
        db.flush()
        db.add(
            Patch(
                slide_id=slide.id,
                config_version_id=project.active_config_version_id,
                patch_index=0,
                x=0,
                y=0,
                level=0,
                width=item.width,
                height=item.height,
                width_l0=item.width,
                height_l0=item.height,
                tissue_fraction=1.0,
                status="unannotated",
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 - never leave a stored file without its rows
        db.rollback()
        shutil.rmtree(image_dir, ignore_errors=True)
        raise
    db.refresh(slide)
    return slide


def _import_images(
    db: Session,
    project: Project,
    discovery: ImageDiscovery,
    source_type: str,
    move: bool,
    warnings: list[str],
    skipped: list[SkippedItemOut],
) -> SlideBatchImportResult:
    slides: list[Slide] = []
    skipped.extend(SkippedItemOut(name=i.name, reason=i.reason) for i in discovery.skipped)
    for item in discovery.items:
        outcome = _register_image(db, project, item, source_type, move)
        if isinstance(outcome, Slide):
            slides.append(outcome)
        else:
            skipped.append(outcome)
    return SlideBatchImportResult(
        slides=slides, skipped=skipped, ignored_file_count=discovery.ignored_files, warnings=warnings
    )


def _place_upload(received: ReceivedFile, target: Path) -> None:
    """Move a received file to its final spot. Both sit inside the same staging folder,
    so this is a rename, not a second copy of what may be a multi-gigabyte slide."""
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(received.path, target)


def _ingest_uploads(
    db: Session,
    project: Project,
    uploads: list[ReceivedFile],
    names: list[str],
    config_version_id: int | None,
    staging: Path,
) -> SlideBatchImportResult:
    """Sort what was received into slides/images. ``staging`` holds the received files and is
    the caller's to delete afterwards."""
    settings = get_settings()
    tree = staging / "tree"
    archives = staging / "archives"
    tree.mkdir(parents=True)
    archives.mkdir()

    skipped: list[SkippedItemOut] = []
    warnings: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for index, (upload, raw_name) in enumerate(zip(uploads, names)):
        try:
            parts = safe_parts(raw_name)
        except UnsafePathError as exc:
            skipped.append(SkippedItemOut(name=raw_name, reason=f"unsafe file name ({exc})"))
            continue
        key = tuple(p.lower() for p in parts)
        if key in seen:
            skipped.append(SkippedItemOut(name=raw_name, reason="the same file was selected twice"))
            continue
        seen.add(key)

        if Path(parts[-1]).suffix.lower() in ARCHIVE_EXTENSIONS:
            archive_path = archives / f"{index}.zip"
            _place_upload(upload, archive_path)
            try:
                warnings += [
                    f"{raw_name}: {w}"
                    for w in extract_zip(
                        archive_path,
                        tree / f"zip-{index}",
                        max_bytes=settings.max_upload_bytes,
                        max_files=settings.max_upload_files,
                    )
                ]
            except ArchiveError as exc:
                skipped.append(SkippedItemOut(name=raw_name, reason=f"archive {exc}"))
            finally:
                archive_path.unlink(missing_ok=True)
        else:
            _place_upload(upload, tree.joinpath(*parts))

    if project.project_type == "image":
        images = discover_images(tree, max_pixels=settings.max_image_pixels)
        return _import_images(db, project, images, "upload", True, warnings, skipped)
    discovery = discover_bundles(tree)
    return _import_discovered(db, project, discovery, config_version_id, "upload", True, warnings, skipped)


@router.post("/projects/{project_id}/slides/upload", response_model=SlideBatchImportResult)
async def upload_slides(
    request: Request,
    config_version_id: int | None = None,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    """Import slides from any mix of loose files, a whole folder, and .zip
    archives in one request.

    Multipart fields: ``files`` (repeatable) and an optional ``manifest`` --
    a JSON list of relative paths, one per file, in the same order. Browsers
    only send a bare file name for each part, so the manifest is how a picked
    *folder* keeps its structure (needed for .mrxs, whose data folder must sit
    next to the .mrxs file).
    """
    settings = get_settings()
    staging = settings.wsi_storage_dir / "_staging" / uuid.uuid4().hex
    try:
        # Streamed to disk as it arrives (see multipart_stream for why not request.form()).
        form = await receive_multipart(
            request, staging / "received", max_files=settings.max_upload_files, max_bytes=settings.max_upload_bytes
        )
        uploads = form.files
        if not uploads:
            raise HTTPException(status_code=422, detail="No files were uploaded")

        names = [u.filename or f"file-{i}" for i, u in enumerate(uploads)]
        manifest_raw = form.fields.get("manifest")
        if manifest_raw:
            try:
                manifest = json.loads(manifest_raw)
            except json.JSONDecodeError:
                raise HTTPException(status_code=422, detail="manifest must be a JSON list of paths") from None
            if not (isinstance(manifest, list) and all(isinstance(m, str) for m in manifest)) or len(manifest) != len(uploads):
                raise HTTPException(status_code=422, detail="manifest must list one path per uploaded file")
            names = manifest

        return await run_in_threadpool(_ingest_uploads, db, project, uploads, names, config_version_id, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _import_images_from_path(db: Session, project: Project, src: Path) -> SlideBatchImportResult:
    """Path import for image projects: a single image, a folder of images, or a .zip of them."""
    settings = get_settings()
    ext = src.suffix.lower()
    max_pixels = settings.max_image_pixels
    if src.is_dir():
        return _import_images(db, project, discover_images(src, max_pixels=max_pixels), "path", False, [], [])
    if ext in IMAGE_EXTENSIONS:
        return _import_images(db, project, discover_images(src.parent, max_pixels=max_pixels, only=src), "path", False, [], [])
    if ext in ARCHIVE_EXTENSIONS:
        staging = settings.wsi_storage_dir / "_staging" / uuid.uuid4().hex
        try:
            try:
                warnings = extract_zip(
                    src, staging / "tree", max_bytes=settings.max_upload_bytes, max_files=settings.max_upload_files
                )
            except ArchiveError as exc:
                raise HTTPException(status_code=422, detail=f"{src.name}: archive {exc}") from exc
            images = discover_images(staging / "tree", max_pixels=max_pixels)
            return _import_images(db, project, images, "path", True, warnings, [])
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type '{ext}'. Expected an image ({', '.join(IMAGE_EXTENSIONS)}), a .zip, or a folder.",
    )


@router.post("/projects/{project_id}/slides/import-path", response_model=SlideBatchImportResult)
def import_slides_by_path(
    payload: SlideImportPathRequest,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    """Register slides that already sit under the server's watch directory:
    a slide file (its data folder / tile files are picked up automatically), a
    folder of slides, or a .zip. The watch directory is only read, never changed."""
    settings = get_settings()
    src = Path(payload.path).resolve()
    watch_root = settings.wsi_watch_dir.resolve()
    try:
        src.relative_to(watch_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Path must be inside the configured WSI watch directory ({watch_root}).",
        )
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {src}")

    ext = src.suffix.lower()
    if project.project_type == "image":
        return _import_images_from_path(db, project, src)

    if src.is_dir():
        discovery = discover_bundles(src)
        return _import_discovered(db, project, discovery, payload.config_version_id, "path", False, [], [])

    if ext in PRIMARY_EXTENSIONS:
        discovery = discover_bundles(src.parent, only=src)
        return _import_discovered(db, project, discovery, payload.config_version_id, "path", False, [], [])

    if ext in ARCHIVE_EXTENSIONS:
        staging = settings.wsi_storage_dir / "_staging" / uuid.uuid4().hex
        try:
            try:
                warnings = extract_zip(
                    src, staging / "tree", max_bytes=settings.max_upload_bytes, max_files=settings.max_upload_files
                )
            except ArchiveError as exc:
                raise HTTPException(status_code=422, detail=f"{src.name}: archive {exc}") from exc
            discovery = discover_bundles(staging / "tree")
            return _import_discovered(db, project, discovery, payload.config_version_id, "path", True, warnings, [])
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type '{ext}'. Expected a slide ({', '.join(PRIMARY_EXTENSIONS)}), a .zip, or a folder.",
    )


@router.post("/projects/{project_id}/slides/demo", response_model=SlideOut, status_code=201)
def create_demo_slide(
    payload: SlideCreateDemo,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    if project.project_type == "image":
        raise HTTPException(status_code=422, detail="Demo slides are for WSI projects; add images to this project instead.")
    slide = Slide(
        project_id=project.id,
        filename=payload.filename,
        file_path=None,
        source_type="demo",
        format="demo",
        status="imported",
        active_config_version_id=payload.config_version_id or project.active_config_version_id,
    )
    db.add(slide)
    db.commit()
    db.refresh(slide)

    _extract_and_store_metadata(db, slide)
    db.refresh(slide)
    return slide


@router.get("/slides/{slide_id}", response_model=SlideOut)
def get_slide(slide: Slide = Depends(get_slide_or_404)):
    return slide


@router.put("/slides/{slide_id}/active-config", response_model=SlideOut)
def set_slide_active_config(
    payload: SlideActiveConfigRequest,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    """Point a slide at a different config version of its project.

    Patches and annotations stay attached to the version they were generated
    under (nothing is deleted or converted); every patch/annotation/export read
    follows the slide's active version, so switching just changes which set is
    shown. If the chosen version has no patches for this slide yet, the slide
    drops back to the stage where they can be generated."""
    forbid_for_image_project(slide.project, "Switching configuration versions")
    config = db.get(ProjectConfigVersion, payload.config_version_id)
    if config is None or config.project_id != slide.project_id:
        raise HTTPException(status_code=404, detail="Config version not found in this slide's project")

    slide.active_config_version_id = config.id
    has_patches = (
        db.query(Patch.id).filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id).first()
        is not None
    )
    if has_patches:
        slide.status = "patches_generated"
    elif slide.tissue_mask_path:
        slide.status = "tissue_detected"
    else:
        slide.status = "imported"
    db.commit()
    db.refresh(slide)
    return slide


@router.delete("/slides/{slide_id}", status_code=204, response_model=None)
def delete_slide(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)):
    reader_cache.invalidate(slide.id)
    purge_slide_tiles(slide.id)
    settings = get_settings()
    storage = settings.wsi_storage_dir.resolve()
    project_dir = (storage / str(slide.project_id)).resolve()
    if slide.file_path:
        primary = (storage / slide.file_path).resolve()
        slide_dir = primary.parent
        if slide_dir != project_dir and project_dir in slide_dir.parents:
            # Current layout: each slide owns a directory (a .mrxs slide keeps its data folder in it).
            shutil.rmtree(slide_dir, ignore_errors=True)
        elif project_dir in primary.parents and primary.exists():
            primary.unlink()  # older layout: one flat file per slide
    if slide.tissue_mask_path:
        mask_path = settings.wsi_storage_dir / slide.tissue_mask_path
        if mask_path.exists():
            mask_path.unlink()
    db.delete(slide)
    db.commit()


@router.get("/slides/{slide_id}/thumbnail")
def get_thumbnail(
    slide: Slide = Depends(get_slide_or_404),
    max_size: int = Query(512, ge=64, le=2048),
):
    if slide.status == "error":
        raise HTTPException(status_code=422, detail=slide.error_message or "Slide failed to import")
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        data = render_thumbnail_bytes(reader, slide.id, max_size)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg")


@router.get("/slides/{slide_id}/patch")
def get_dynamic_patch(
    slide: Slide = Depends(get_slide_or_404),
    x: int = Query(..., description="Level-0 origin X"),
    y: int = Query(..., description="Level-0 origin Y"),
    width: int = Query(..., gt=0, le=8192, description="Output width in pixels at `level`"),
    height: int = Query(..., gt=0, le=8192, description="Output height in pixels at `level`"),
    level: int = Query(0, ge=0),
):
    """Dynamically reads a region from the original WSI via OpenSlide.read_region.
    Nothing returned here is ever persisted as a file -- canonical representation
    is always (coordinates + original WSI)."""
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        meta = reader.get_metadata()
        if level >= meta.level_count:
            raise HTTPException(status_code=400, detail=f"Slide only has {meta.level_count} levels")
        region = reader.read_region(x, y, level, width, height)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Failed to read region: {exc}") from exc

    import io

    buf = io.BytesIO()
    if slide.project.project_type == "image":
        # Plain images are annotated pixel-for-pixel; don't re-compress them.
        region.save(buf, format="PNG", compress_level=3)
        return Response(content=buf.getvalue(), media_type="image/png")
    region.save(buf, format="JPEG", quality=92)
    return Response(content=buf.getvalue(), media_type="image/jpeg")


@router.get("/slides/{slide_id}/dzi.dzi")
def get_dzi_descriptor(slide: Slide = Depends(get_slide_or_404)):
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        adapter = DeepZoomAdapter(reader)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=adapter.get_dzi_xml(), media_type="application/xml")


@router.get("/slides/{slide_id}/dzi_files/{level}/{tile}")
def get_dzi_tile(
    tile: str,
    level: int,
    slide: Slide = Depends(get_slide_or_404),
):
    match = re.match(r"^(\d+)_(\d+)\.(jpeg|jpg|png)$", tile)
    if not match:
        raise HTTPException(status_code=400, detail="Malformed tile address")
    col, row = int(match.group(1)), int(match.group(2))
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        adapter = DeepZoomAdapter(reader)
        data = render_tile_bytes(adapter, slide.id, level, col, row)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg")
