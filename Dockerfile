# One image for the app and its updater (docker-compose.yml runs it twice with different commands).
# Built for linux/amd64 and linux/arm64 by .github/workflows/release.yml on every version tag.

# The frontend is plain JS once built, so it is built once on the build machine's own platform.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:////app/data/database/app.db \
    WSI_STORAGE_DIR=/app/data/uploads \
    WSI_WATCH_DIR=/app/data/watch \
    FRONTEND_DIST=/app/frontend/dist

RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt docker==7.1.0

COPY backend/app backend/app
COPY updater updater
# Only recipe.json is read by the app (to list the models); the code is run by the trainer image.
COPY trainer/recipes trainer/recipes
COPY trainer/templates trainer/templates
COPY --from=frontend /src/frontend/dist frontend/dist

LABEL org.opencontainers.image.source="https://github.com/sawikot/VidaAnnotatior" \
      org.opencontainers.image.title="VidaAnnotator" \
      org.opencontainers.image.version=$APP_VERSION

WORKDIR /app/backend
EXPOSE 8088
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
