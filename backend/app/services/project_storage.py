"""A project's files on disk.

Everything the app stores for a project -- its slide/image files and cached
tissue masks -- lives under ``<wsi_storage_dir>/<project_id>/``, so removing
that one directory removes all of it. Callers pass the storage root in (rather
than this module reading settings) so tests can point it at a temp directory.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
import time
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

_ATTEMPTS = 5


def project_dir(storage_root: Path, project_id: int) -> Path:
    return storage_root / str(project_id)


def _clear_readonly_and_retry(func, path, _exc) -> None:  # noqa: ANN001
    # Windows refuses to delete read-only files (common for files copied off a
    # scanner share); clear the flag and try that one file again.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> bool:
    """Delete a directory and everything in it. Returns True once it is gone.

    Retries briefly because on Windows a file that was open a moment ago (a
    slide reader just closed, an antivirus scan) can stay locked for a short
    while. Never raises: a leftover is logged and removed by the next
    :func:`remove_orphaned_project_dirs` sweep.
    """
    kwargs = {"onexc": _clear_readonly_and_retry} if sys.version_info >= (3, 12) else {"onerror": _clear_readonly_and_retry}
    for attempt in range(_ATTEMPTS):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, **kwargs)
        except OSError as exc:
            if attempt == _ATTEMPTS - 1:
                log.warning("Could not delete %s: %s (it will be retried at the next server start)", path, exc)
                return False
            time.sleep(0.2 * (attempt + 1))
    return not path.exists()


def remove_orphaned_project_dirs(storage_root: Path, existing_project_ids: Iterable[int]) -> list[Path]:
    """Delete project directories whose project no longer exists (a delete whose
    files were locked at the time). Returns the directories removed."""
    keep = {str(pid) for pid in existing_project_ids}
    removed = []
    if not storage_root.is_dir():
        return removed
    for child in storage_root.iterdir():
        if child.is_dir() and child.name.isdigit() and child.name not in keep and remove_tree(child):
            removed.append(child)
    return removed
