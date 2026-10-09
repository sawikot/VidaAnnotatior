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

# ---- Model training: pick the trainer for this machine's hardware (asked once; kept in .env).
if ! grep -q "^COMPOSE_PROFILES=." .env && ! grep -q "^TRAINING_ASKED=1" .env; then
  profile=""
  read -rp "Install model training on this machine? It is a large download (about 8 GB). [Y/n] " answer
  if [[ ! "$answer" =~ ^[Nn] ]]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
      echo "Found: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | paste -sd ';')"
      # The driver alone is not enough: Docker must be able to hand the card to a container.
      if $DOCKER run --rm --gpus all hello-world >/dev/null 2>&1; then
        profile="trainer-gpu"
        count="$(nvidia-smi -L | wc -l)"
        if [ "$count" -gt 1 ]; then
          read -rp "This machine has $count graphics cards. Which may training use? (\"all\", or indexes like 0 or 0,1) [0] " gpus
          set_env TRAINER_GPUS "${gpus:-0}"
        fi
      else
        echo "Docker cannot use the graphics card yet. Install the NVIDIA Container Toolkit, then run this script again:" >&2
        echo "    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html" >&2
        echo "Until then training is installed for the CPU, which is slow." >&2
        profile="trainer-cpu"
      fi
    else
      echo "No NVIDIA graphics card found: training is installed for the CPU, which is slow."
      profile="trainer-cpu"
    fi
  fi
  set_env COMPOSE_PROFILES "$profile"
  set_env TRAINING_ASKED 1
fi

$DOCKER compose pull
$DOCKER compose up -d

port="$(grep "^PORT=" .env | cut -d= -f2- || true)"
echo
echo "VidaAnnotator is running. Open one of these in a browser:"
for ip in $(hostname -I 2>/dev/null) localhost; do echo "    http://$ip:${port:-8088}"; done
echo "The first visit asks you to create the administrator account."
echo "Model training: $(grep "^COMPOSE_PROFILES=" .env | cut -d= -f2- | sed 's/^$/not installed/'). To change it, remove the COMPOSE_PROFILES and TRAINING_ASKED lines from .env and run this again."
