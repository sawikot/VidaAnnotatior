"""The app's own version, and switching to another released one (administrators only).

The switching itself is done by the updater service (updater/updater.py), which runs next to the app
under Docker; these routes only pass the administrator's requests on to it.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.access import require_admin
from app.core.config import get_settings

router = APIRouter(tags=["system"], dependencies=[Depends(require_admin)])


class SwitchIn(BaseModel):
    version: str
    restore_backup: str | None = None


def _updater(method: str, path: str, body: dict | None = None):
    settings = get_settings()
    if not settings.updater_url:
        raise HTTPException(status_code=409, detail="Versions can only be switched when the app runs with Docker (docker-compose.yml).")
    try:
        secret = settings.updater_secret_file.read_text().strip()
    except OSError:
        raise HTTPException(status_code=503, detail="The updater has not started yet.") from None
    req = urllib.request.Request(
        settings.updater_url.rstrip("/") + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Updater-Secret": secret, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("detail", str(e))
        except ValueError:
            detail = str(e)
        raise HTTPException(status_code=e.code if e.code in (400, 409) else 502, detail=detail) from None
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"The updater is not reachable: {e}") from None


@router.get("/system/version")
def version() -> dict:
    """This app's version, and the updater's status when there is one."""
    settings = get_settings()
    updater, error = None, None
    if settings.updater_url:
        try:
            updater = _updater("GET", "/status")
        except HTTPException as e:
            error = e.detail
    return {"version": settings.app_version, "can_update": bool(settings.updater_url), "updater": updater, "updater_error": error}


@router.get("/system/releases")
def releases() -> list[dict]:
    return _updater("GET", "/releases")


@router.post("/system/update", status_code=202)
def update(payload: SwitchIn) -> dict:
    return _updater("POST", "/switch", payload.model_dump())
