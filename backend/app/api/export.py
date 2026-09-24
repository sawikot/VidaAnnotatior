from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.patch_grid import active_grid_filter
from app.services.exporter import get_exporter
from app.services.exporter.bundle import Bundle, ExportTooLarge, build_bundle, summarize
from app.services.exporter.options import ExportOptions, parse_options

router = APIRouter(tags=["export"])
log = logging.getLogger(__name__)


def _safe_stem(name: str, fallback: str) -> str:
    """User-supplied names reduced to safe characters for file and header use."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or fallback


def _options(
    patches: str, content: str, image_format: str, masks: bool, combine: bool | None, grid: str | None = None, classify: dict | None = None
) -> ExportOptions:
    try:
        return parse_options(patches, content, image_format, masks, combine, grid, **(classify or {}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def classification_params(
    min_coverage: float = Query(0.9, description="patch_classification: share of a patch a drawn class must cover (0.5-1)"),
    unlabeled: str = Query("skip", description="patch_classification: skip | folder (patches with no clear class)"),
    other_labels: bool = Query(True, description="patch_classification: folders for labels that are not a class (Mixed, Artifact)"),
) -> dict:
    return {"min_coverage": min_coverage, "unlabeled": unlabeled, "other_labels": other_labels}


def _suffix(options: ExportOptions) -> str:
    """Download names say what they hold, so files from different runs stay apart."""
    scope = "" if options.patch_scope == "annotated" else f"_{options.patch_scope}"
    grid = f"_grid{options.grid.patch_width}s{options.grid.stride_x}" if options.grid else ""
    return scope + grid


def _ready(db: Session, slide: Slide, options: ExportOptions) -> bool:
    """Whether the slide can be exported: it has a grid -- or, with a custom grid, can be cut into one."""
    if options.grid is not None:
        return bool(slide.width_l0) and slide.status != "error" and slide.project.project_type != "image"
    return _has_grid(db, slide)


def _has_grid(db: Session, slide: Slide) -> bool:
    return (
        slide.active_config_version_id is not None
        and active_grid_filter(db.query(Patch.id).filter(Patch.slide_id == slide.id), slide).first() is not None
    )


def _stream(bundle: Bundle, filename: str) -> StreamingResponse:
    """Send the temporary ZIP in chunks and delete it afterwards."""

    def chunks() -> Iterator[bytes]:
        try:
            while chunk := bundle.file.read(1 << 20):
                yield chunk
        finally:
            bundle.file.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Length": str(bundle.size)},
    )


def _bundle(
    db: Session, slides: list[Slide], exporter, options: ExportOptions, *, combine: bool, label: str, skipped: list[dict] | None = None
) -> Bundle:
    try:
        return build_bundle(
            db, slides, exporter, options, combine=combine, label=label,
            max_images=get_settings().max_export_images, skipped_slides=skipped,
        )
    except ExportTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


# --------------------------------------------------------------------------- one slide


@router.get("/slides/{slide_id}/export/{format_id}")
def export_slide(
    format_id: str,
    patches: str = Query("annotated", description="annotated | all | empty | reviewed"),
    content: str = Query("annotations", description="annotations | images (adds the patch images, as a ZIP)"),
    image_format: str = Query("jpg", description="jpg | png"),
    masks: bool = Query(False, description="with content=images: also write label masks"),
    grid: str | None = Query(None, description="export in another patch grid, e.g. 512x512_s512x512_m20_t0.5 (see /slides/{id}/grids)"),
    classify: dict = Depends(classification_params),
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    try:
        exporter = get_exporter(format_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    options = _options(patches, content, image_format, masks, None, grid, classify)
    if not _ready(db, slide, options):
        raise HTTPException(status_code=422, detail="This slide has no patch grid yet; generate one, or choose a custom grid.")

    # The stored filename is user-supplied; keep the download name to safe characters
    # so it can't break out of the header or smuggle a path.
    stem = _safe_stem(slide.filename.rsplit(".", 1)[0], "slide")

    if options.with_images:
        bundle = _bundle(db, [slide], exporter, options, combine=False, label=stem)
        return _stream(bundle, f"{stem}_{format_id}{_suffix(options)}_with_images.zip")

    try:
        result = exporter.export(db, slide, options)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:  # e.g. a custom grid far too fine for the slide
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=exporter.render(result),
        media_type=exporter.content_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}_{format_id}{_suffix(options)}.{exporter.file_extension}"'},
    )


# ------------------------------------------------------------------------ whole project


@router.get("/projects/{project_id}/export/{format_id}")
def export_project(
    format_id: str,
    patches: str = Query("annotated", description="annotated | all | empty | reviewed"),
    content: str = Query("annotations", description="annotations | images"),
    image_format: str = Query("jpg", description="jpg | png"),
    masks: bool = Query(False),
    combine: bool | None = Query(None, description="one combined file for dataset-level formats (COCO, CSV)"),
    grid: str | None = Query(None, description="export in another patch grid, e.g. 512x512_s512x512_m20_t0.5 (see /slides/{id}/grids)"),
    classify: dict = Depends(classification_params),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    """Every processed slide of the project, exported in one format.

    Normally a ZIP (one file per slide plus a ``manifest.json``): slides that have no patch grid
    yet, or whose export fails, don't sink the download but are listed as skipped with the reason.

    Dataset-level formats (COCO, both CSVs) can be *combined* into a single file for the whole
    project, which is what a training pipeline wants. That is the default in an image project and
    whenever patch images are included; otherwise ask for it with ``combine=true``.

    With ``content=images`` the ZIP also holds the patch images (cut from the slides on the fly).
    """
    try:
        exporter = get_exporter(format_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    options = _options(patches, content, image_format, masks, combine, grid, classify)

    slides = sorted(project.slides, key=lambda s: s.id)
    ready = [s for s in slides if _ready(db, s, options)]
    skipped = [
        {"slide_id": s.id, "slide": s.filename, "reason": "No patch grid generated yet"} for s in slides if s not in ready
    ]
    if not ready:
        raise HTTPException(status_code=422, detail="Nothing to export yet: no slide in this project is ready.")

    slug = _safe_stem(project.slug, "project")
    wants_combined = options.combine if options.combine is not None else (project.project_type == "image" or options.with_images)
    merged = wants_combined and exporter.mergeable

    if options.with_images:
        bundle = _bundle(db, ready, exporter, options, combine=bool(wants_combined), label=slug, skipped=skipped)
        return _stream(bundle, f"{slug}_{format_id}{_suffix(options)}_with_images.zip")

    results: list[tuple[Slide, object]] = []
    for slide in ready:
        try:
            results.append((slide, exporter.export(db, slide, options)))
        except Exception:  # one bad slide must not lose the rest of a ZIP
            if merged:
                raise  # ...but a dataset file must never be silently incomplete
            log.exception("Bulk export of slide %s failed", slide.id)
            skipped.append({"slide_id": slide.id, "slide": slide.filename, "reason": "Export failed; see server log"})

    if not results:
        raise HTTPException(status_code=422, detail="Nothing could be exported.")

    if merged:
        return Response(
            content=exporter.render(exporter.merge([r for _, r in results])),
            media_type=exporter.content_type,
            headers={"Content-Disposition": f'attachment; filename="{slug}_{format_id}{_suffix(options)}.{exporter.file_extension}"'},
        )

    files: list[dict] = []
    used_names: set[str] = set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for slide, result in results:
            body = exporter.render(result).encode("utf-8")
            stem = _safe_stem(slide.filename.rsplit(".", 1)[0], "slide")
            name = f"{stem}_{format_id}.{exporter.file_extension}"
            if name in used_names:  # two slides can share a filename
                name = f"{stem}_{slide.id}_{format_id}.{exporter.file_extension}"
            used_names.add(name)
            archive.writestr(name, body)
            files.append({"slide_id": slide.id, "slide": slide.filename, "file": name, "bytes": len(body)})

        manifest = {
            "project_id": project.slug,
            "format": format_id,
            "patches": options.patch_scope,
            "grid": options.grid.key if options.grid else "as annotated",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "slide_count": len(files),
            "files": files,
            "skipped": skipped,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}_{format_id}{_suffix(options)}_all_slides.zip"'},
    )


# ------------------------------------------------------- chosen slides, one or several formats


def _ids(text: str | None, what: str) -> list[int] | None:
    if text is None or not text.strip():
        return None
    try:
        return [int(t) for t in text.split(",") if t.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{what} must be comma-separated ids") from exc


def _exporters(formats: str) -> list:
    ids = list(dict.fromkeys(f.strip() for f in formats.split(",") if f.strip()))
    if not ids:
        raise HTTPException(status_code=422, detail="Choose at least one format")
    try:
        return [get_exporter(f) for f in ids]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _chosen_slides(project: Project, slide_ids: list[int] | None) -> list[Slide]:
    slides = sorted(project.slides, key=lambda s: s.id)
    if slide_ids is None:
        return slides
    by_id = {s.id: s for s in slides}
    unknown = [i for i in slide_ids if i not in by_id]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Not slides of this project: {', '.join(map(str, unknown))}")
    return [by_id[i] for i in dict.fromkeys(slide_ids)]


@router.get("/projects/{project_id}/export")
def export_selection(
    formats: str = Query(..., description="one or more formats, comma-separated, e.g. coco,patch_csv"),
    slides: str | None = Query(None, description="the slides to export, comma-separated ids (default: every slide)"),
    patches: str = Query("annotated", description="annotated | all | empty | reviewed"),
    content: str = Query("annotations", description="annotations | images (adds the patch images)"),
    image_format: str = Query("jpg", description="jpg | png"),
    masks: bool = Query(False),
    combine: bool | None = Query(None, description="one combined file per dataset-level format (COCO, CSV) instead of one per slide"),
    grid: str | None = Query(None, description="export in another patch grid, e.g. 512x512_s512x512_m20_t0.5"),
    classify: dict = Depends(classification_params),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    """The export screen's download: the chosen slides, in one or several formats, optionally with images.

    One format for one slide (or combined into one file) without images arrives as that file;
    anything else is a ZIP (annotation files under ``annotations/``, images under ``images/``, and a
    ``manifest.json`` listing slides that were skipped and why)."""
    exporters = _exporters(formats)
    options = _options(patches, content, image_format, masks, combine, grid, classify)
    chosen = _chosen_slides(project, _ids(slides, "slides"))
    ready = [s for s in chosen if _ready(db, s, options)]
    skipped = [{"slide_id": s.id, "slide": s.filename, "reason": "No patch grid generated yet"} for s in chosen if s not in ready]
    if not ready:
        raise HTTPException(status_code=422, detail="Nothing to export: none of the chosen slides has a patch grid yet.")

    slug = _safe_stem(project.slug, "project")
    wants_combined = options.combine if options.combine is not None else (project.project_type == "image" or options.with_images)

    if len(exporters) == 1 and not options.with_images:
        exporter = exporters[0]
        single = len(ready) == 1 and not skipped
        if single or (wants_combined and exporter.mergeable):
            try:
                results = [exporter.export(db, s, options) for s in ready]
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            body = exporter.render(results[0] if single else exporter.merge(results))
            stem = _safe_stem(ready[0].filename.rsplit(".", 1)[0], "slide") if single else slug
            return Response(
                content=body,
                media_type=exporter.content_type,
                headers={"Content-Disposition": f'attachment; filename="{stem}_{exporter.format_id}{_suffix(options)}.{exporter.file_extension}"'},
            )

    what = exporters[0].format_id if len(exporters) == 1 else "export"
    bundle = _bundle(db, ready, exporters, options, combine=bool(wants_combined), label=slug, skipped=skipped)
    tail = "_with_images" if options.with_images else ""
    return _stream(bundle, f"{slug}_{what}{_suffix(options)}{tail}.zip")


# --------------------------------------------------------------------------- preview counts


class ExportSummaryOut(BaseModel):
    slides: int
    patches: int
    annotations: int
    images: int
    approx_image_bytes: int
    max_images: int  # most images one download may hold


# Rough bytes per pixel of a tissue patch: JPEG (quality 95) and PNG. Only used to give an
# order of magnitude before a large download, never for anything that has to be exact.
_BYTES_PER_PIXEL = {"jpg": 0.3, "png": 1.8}


def _summary_out(db: Session, slides: list[Slide], options: ExportOptions, image_format: str, format_id: str | None = None) -> ExportSummaryOut:
    exporters = _exporters(format_id) if format_id else []  # formats decide which patches get images (classification)
    try:
        total = summarize(db, [s for s in slides if _ready(db, s, options)], options, exporters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExportSummaryOut(
        slides=total.slides,
        patches=total.patches,
        annotations=total.annotations,
        images=total.images,
        approx_image_bytes=int(total.image_pixels * _BYTES_PER_PIXEL.get(image_format, 1.0)),
        max_images=get_settings().max_export_images,
    )


@router.get("/slides/{slide_id}/export-summary", response_model=ExportSummaryOut)
def export_slide_summary(
    patches: str = Query("annotated"),
    image_format: str = Query("jpg"),
    grid: str | None = Query(None),
    format: str | None = Query(None, description="the export format, when it decides which patches get images"),
    classify: dict = Depends(classification_params),
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    """What an export with these options would cover -- counts only, nothing is rendered."""
    return _summary_out(db, [slide], _options(patches, "annotations", image_format, False, None, grid, classify), image_format, format)


@router.get("/projects/{project_id}/export-summary", response_model=ExportSummaryOut)
def export_project_summary(
    patches: str = Query("annotated"),
    image_format: str = Query("jpg"),
    grid: str | None = Query(None),
    format: str | None = Query(None, description="the export format(s), comma-separated"),
    slides: str | None = Query(None, description="only these slides, comma-separated ids"),
    classify: dict = Depends(classification_params),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    return _summary_out(
        db, _chosen_slides(project, _ids(slides, "slides")),
        _options(patches, "annotations", image_format, False, None, grid, classify), image_format, format,
    )
