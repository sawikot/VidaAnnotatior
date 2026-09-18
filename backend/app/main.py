from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import annotations, configs, dev, export, patches, processing, projects, slides
from app.core.config import get_settings
from app.database.session import init_db

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


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
app.include_router(dev.router, prefix=settings.api_prefix)
