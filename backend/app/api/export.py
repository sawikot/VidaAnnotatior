from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_slide_or_404
from app.database.session import get_db
from app.models.slide import Slide
from app.services.exporter import get_exporter

router = APIRouter(tags=["export"])


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

    body = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result
    filename = f"{slide.filename.rsplit('.', 1)[0]}_{format_id}.{exporter.file_extension}"
    return Response(
        content=body,
        media_type=exporter.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
