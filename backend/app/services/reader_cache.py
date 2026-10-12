"""Process-local cache of open WSIReader instances, keyed by slide id.

Opening an OpenSlide file has real cost (parsing pyramid headers etc.), so we
keep a small number of readers open rather than re-opening per request. This
is intentionally simple (a dict, no eviction beyond a soft cap) -- adequate
for a single-process dev/research deployment.
"""
from __future__ import annotations

import threading
from pathlib import Path

from app.core.config import get_settings
from app.models.slide import Slide
from app.services.wsi_reader import ImageReader, OpenSlideReader, WSIReader

_lock = threading.Lock()
_cache: dict[int, WSIReader] = {}
_files: dict[int, str | None] = {}  # the file each open reader is of (Slide.file_path)
_MAX_OPEN = 8


def get_reader_for_slide(slide: Slide) -> WSIReader:
    with _lock:
        reader = _cache.get(slide.id)
        if reader is not None and _files.get(slide.id) == slide.file_path:
            return reader
        if reader is not None:
            # The same id, another file: a slide of a saved version read beside the work
            # (services/versioning.py), where ids deleted since may have been given out again.
            _cache.pop(slide.id).close()

        settings = get_settings()
        if not slide.file_path:
            raise FileNotFoundError(f"Slide {slide.id} has no file_path registered")
        full_path = (settings.wsi_storage_dir / slide.file_path).resolve()
        if not str(full_path).startswith(str(settings.wsi_storage_dir.resolve())):
            raise PermissionError("Resolved slide path escapes the managed storage directory")
        if not full_path.exists():
            raise FileNotFoundError(f"Slide file not found: {full_path}")
        reader = ImageReader(full_path) if slide.project.project_type == "image" else OpenSlideReader(full_path)

        if len(_cache) >= _MAX_OPEN:
            oldest_id = next(iter(_cache))
            _cache.pop(oldest_id).close()
        _cache[slide.id] = reader
        _files[slide.id] = slide.file_path
        return reader


def invalidate(slide_id: int) -> None:
    with _lock:
        _files.pop(slide_id, None)
        reader = _cache.pop(slide_id, None)
        if reader is not None:
            reader.close()
