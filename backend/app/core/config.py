"""Application configuration, loaded from environment variables / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    app_name: str = "VirtualPatch WSI Annotator"
    api_prefix: str = "/api"

    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'database' / 'app.db').as_posix()}"

    # Root directory where imported WSI files are stored, managed by the app.
    wsi_storage_dir: Path = PROJECT_ROOT / "data" / "uploads"

    # Optional directory the "register local path" import mode is allowed to read from.
    # Any path passed to the path-import endpoint must resolve inside this directory.
    wsi_watch_dir: Path = PROJECT_ROOT / "data" / "watch"

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Memory for the in-process cache of generated viewer tiles and thumbnails (MB).
    tile_cache_mb: int = 256

    # Ceilings for one upload request / one unpacked archive. Uploaded bytes and
    # unpacked bytes are each capped by max_upload_bytes.
    max_upload_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GB
    max_upload_files: int = 20_000  # files in one request, and entries in one zip

    # Image projects annotate ordinary images as-is, so each one is decoded whole into
    # memory. Larger images are refused (they belong in a WSI project).
    max_image_pixels: int = 25_000_000

    # Most patch images one export download may contain (they are cut from the slide on the fly).
    max_export_images: int = 50_000

    # Signing in: how long a session lasts, and whether its cookie is sent over HTTPS only. Set
    # COOKIE_SECURE=true when the server is reached through HTTPS (it should be, on a network).
    session_days: int = 14
    cookie_secure: bool = False

    # The released version this is (set in the Docker image; "dev" when run from the source).
    app_version: str = "dev"

    # The updater service that switches versions (docker-compose.yml). Empty: not running under
    # Docker, so versions are not switched from the app. The secret is written by the updater.
    updater_url: str = ""
    updater_secret_file: Path = Path("/run/vida/updater-secret")

    # The built frontend (npm run build). When it exists the backend serves it too, so the whole app
    # is one address -- which is also what lets the sign-in cookie reach every request.
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.wsi_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.wsi_watch_dir.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "database").mkdir(parents=True, exist_ok=True)
    return settings
