from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.slide import SlideCreateDemo, SlideImportPathRequest, SlideOut
from app.services import reader_cache
from app.services.deepzoom_service import DeepZoomAdapter, render_tile_bytes

router = APIRouter(tags=["slides"])


def _sanitize_filename(name: str) -> str:
    name = Path(name).name  # strip any directory components
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name or "slide"


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


@router.post("/projects/{project_id}/slides/upload", response_model=SlideOut, status_code=201)
def upload_slide(
    file: UploadFile,
    config_version_id: int | None = None,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    safe_name = _sanitize_filename(file.filename or "slide")
    ext = Path(safe_name).suffix.lower()
    if ext not in settings.supported_wsi_extensions:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported WSI format '{ext}'. Supported: {settings.supported_wsi_extensions}",
        )

    project_dir = settings.wsi_storage_dir / str(project.id)
    project_dir.mkdir(parents=True, exist_ok=True)
    dest = project_dir / safe_name
    n = 1
    while dest.exists():
        dest = project_dir / f"{Path(safe_name).stem}_{n}{ext}"
        n += 1

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    slide = Slide(
        project_id=project.id,
        filename=safe_name,
        file_path=str(dest.relative_to(settings.wsi_storage_dir).as_posix()),
        source_type="upload",
        format=ext,
        status="imported",
        active_config_version_id=config_version_id or project.active_config_version_id,
    )
    db.add(slide)
    db.commit()
    db.refresh(slide)

    _extract_and_store_metadata(db, slide)
    db.refresh(slide)
    return slide


@router.post("/projects/{project_id}/slides/import-path", response_model=SlideOut, status_code=201)
def import_slide_by_path(
    payload: SlideImportPathRequest,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
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
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {src}")

    ext = src.suffix.lower()
    if ext not in settings.supported_wsi_extensions:
        raise HTTPException(status_code=415, detail=f"Unsupported WSI format '{ext}'")

    safe_name = _sanitize_filename(src.name)
    project_dir = settings.wsi_storage_dir / str(project.id)
    project_dir.mkdir(parents=True, exist_ok=True)
    dest = project_dir / safe_name
    n = 1
    while dest.exists():
        dest = project_dir / f"{Path(safe_name).stem}_{n}{ext}"
        n += 1
    shutil.copy2(src, dest)

    slide = Slide(
        project_id=project.id,
        filename=safe_name,
        file_path=str(dest.relative_to(settings.wsi_storage_dir).as_posix()),
        source_type="path",
        format=ext,
        status="imported",
        active_config_version_id=payload.config_version_id or project.active_config_version_id,
    )
    db.add(slide)
    db.commit()
    db.refresh(slide)

    _extract_and_store_metadata(db, slide)
    db.refresh(slide)
    return slide


@router.post("/projects/{project_id}/slides/demo", response_model=SlideOut, status_code=201)
def create_demo_slide(
    payload: SlideCreateDemo,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
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


@router.delete("/slides/{slide_id}", status_code=204, response_model=None)
def delete_slide(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)):
    reader_cache.invalidate(slide.id)
    settings = get_settings()
    if slide.file_path:
        full_path = settings.wsi_storage_dir / slide.file_path
        if full_path.exists():
            full_path.unlink()
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
        thumb = reader.get_thumbnail(max_size)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    import io

    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=88)
    return Response(content=buf.getvalue(), media_type="image/jpeg")


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
