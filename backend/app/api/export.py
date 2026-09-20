from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404, get_slide_or_404
from app.database.session import get_db
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.exporter import get_exporter

router = APIRouter(tags=["export"])
log = logging.getLogger(__name__)


def _safe_stem(name: str, fallback: str) -> str:
    """User-supplied names reduced to safe characters for file and header use."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or fallback


@router.get("/slides/{slide_id}/export/{format_id}")
def export_slide(
    format_id: str,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    try:
        exporter = get_exporter(format_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        result = exporter.export(db, slide)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # The stored filename is user-supplied; keep the download name to safe characters
    # so it can't break out of the header or smuggle a path.
    stem = _safe_stem(slide.filename.rsplit(".", 1)[0], "slide")
    filename = f"{stem}_{format_id}.{exporter.file_extension}"
    body = exporter.render(result)
    return Response(
        content=body,
        media_type=exporter.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/export/{format_id}")
def export_project(
    format_id: str,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    """Every processed slide of the project, exported in one format and bundled
    into a single ZIP (one file per slide plus a ``manifest.json``). Slides that
    have no patch grid yet, or whose export fails, don't sink the download: they
    are listed under ``skipped`` in the manifest with the reason."""
    try:
        exporter = get_exporter(format_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    slides = sorted(project.slides, key=lambda s: s.id)
    files: list[dict] = []
    skipped: list[dict] = []
    used_names: set[str] = set()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for slide in slides:
            has_grid = (
                slide.active_config_version_id is not None
                and db.query(Patch.id)
                .filter(Patch.slide_id == slide.id, Patch.config_version_id == slide.active_config_version_id)
                .first()
                is not None
            )
            if not has_grid:
                skipped.append({"slide_id": slide.id, "slide": slide.filename, "reason": "No patch grid generated yet"})
                continue
            try:
                body = exporter.render(exporter.export(db, slide)).encode("utf-8")
            except Exception:  # one bad slide must not lose the rest of the batch
                log.exception("Bulk export of slide %s failed", slide.id)
                skipped.append({"slide_id": slide.id, "slide": slide.filename, "reason": "Export failed; see server log"})
                continue

            stem = _safe_stem(slide.filename.rsplit(".", 1)[0], "slide")
            name = f"{stem}_{format_id}.{exporter.file_extension}"
            if name in used_names:  # two slides can share a filename
                name = f"{stem}_{slide.id}_{format_id}.{exporter.file_extension}"
            used_names.add(name)
            archive.writestr(name, body)
            files.append({"slide_id": slide.id, "slide": slide.filename, "file": name, "bytes": len(body)})

        if not files:
            raise HTTPException(status_code=422, detail="No slide in this project has a patch grid yet; nothing to export.")

        manifest = {
            "project_id": project.slug,
            "format": format_id,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "slide_count": len(files),
            "files": files,
            "skipped": skipped,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    filename = f"{_safe_stem(project.slug, 'project')}_{format_id}_all_slides.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
