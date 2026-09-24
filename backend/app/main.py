import shutil

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import annotations, auth, configs, export, images, patches, processing, projects, slides
from app.api.access import authorize
from app.core.config import get_settings
from app.database.session import SessionLocal, init_db
from app.models.project import Project
from app.services import project_storage

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # so a download started from the page keeps the server's file name
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    # Half-finished uploads (e.g. the server was stopped mid-upload) leave their
    # temporary files here; nothing can be using them at startup.
    shutil.rmtree(settings.wsi_storage_dir / "_staging", ignore_errors=True)
    # Files of deleted projects that were locked at the time they were deleted.
    with SessionLocal() as db:
        project_ids = [pid for (pid,) in db.query(Project.id)]
    project_storage.remove_orphaned_project_dirs(settings.wsi_storage_dir, project_ids)


@app.get(f"{settings.api_prefix}/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


# Signing in, users and members check access themselves; every other route needs a signed-in user
# with access to what it touches (api/access.py).
app.include_router(auth.router, prefix=settings.api_prefix)
for module in (projects, configs, slides, processing, patches, annotations, export, images):
    app.include_router(module.router, prefix=settings.api_prefix, dependencies=[Depends(authorize)])


# The built frontend, when there is one (npm run build): the whole app from this one address.
_dist = settings.frontend_dist.resolve()
if (_dist / "index.html").exists():

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path.startswith(settings.api_prefix.strip("/") + "/"):
            raise HTTPException(status_code=404, detail="Not found")
        target = (_dist / path).resolve()
        if path and target.is_file() and target.is_relative_to(_dist):
            return FileResponse(target)
        return FileResponse(_dist / "index.html")  # the app's own pages (/projects/3/...) all load it
