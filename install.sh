#!/usr/bin/env bash
# Installs VidaAnnotator with Docker on Linux (DGX, servers). Run once from the cloned folder:
#     bash install.sh
# Afterwards, versions are installed and switched from the app: menu -> Version & updates.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install it first: https://docs.docker.com/engine/install/" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is missing: sudo apt install docker-compose-plugin" >&2
  exit 1
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi

set_env() {  # set_env KEY VALUE -- replaces the line in .env, or adds it
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}

[ -f .env ] || cp .env.example .env
mkdir -p data/watch

if ! grep -q "^GITHUB_TOKEN=." .env; then
  echo "If the GitHub repository is private, paste a GitHub token (classic, scopes: repo + read:packages)."
  read -rsp "Token (press Enter if the repository is public): " token; echo
  set_env GITHUB_TOKEN "$token"
fi
token="$(grep "^GITHUB_TOKEN=" .env | cut -d= -f2-)"
repo="$(grep "^GITHUB_REPO=" .env | cut -d= -f2- || true)"
repo="${repo:-sawikot/VidaAnnotatior}"
if [ -n "$token" ]; then
  echo "$token" | $DOCKER login ghcr.io -u "${repo%%/*}" --password-stdin
fi

$DOCKER compose pull
$DOCKER compose up -d

port="$(grep "^PORT=" .env | cut -d= -f2- || true)"
echo
echo "VidaAnnotator is running. Open one of these in a browser:"
for ip in $(hostname -I 2>/dev/null) localhost; do echo "    http://$ip:${port:-8088}"; done
echo "The first visit asks you to create the administrator account."
