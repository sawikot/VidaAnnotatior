import shutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import annotations, configs, export, images, patches, processing, projects, slides
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


app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(configs.router, prefix=settings.api_prefix)
app.include_router(slides.router, prefix=settings.api_prefix)
app.include_router(processing.router, prefix=settings.api_prefix)
app.include_router(patches.router, prefix=settings.api_prefix)
app.include_router(annotations.router, prefix=settings.api_prefix)
app.include_router(export.router, prefix=settings.api_prefix)
app.include_router(images.router, prefix=settings.api_prefix)
