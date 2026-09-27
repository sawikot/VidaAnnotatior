"""The updater: switches the app container to another released version, on request from the app.

Runs as its own container next to the app (docker-compose.yml) because a container cannot replace
itself. It is the only part with access to Docker, listens only on the compose network, and answers
only requests carrying the secret it shares with the app through a volume.

    GET  /status    what is running, what the updater is doing, the data backups
    GET  /releases  the versions published on GitHub
    POST /switch    {"version": "v1.2.0", "restore_backup": null | "<backup id>"}

A switch: pull the new image (the app keeps running meanwhile), stop the app, back up the database,
optionally restore an older backup, start the same container again on the new image, wait for it to
answer with the new version. If it does not, the old container and the database from before the
switch come back.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import docker
from docker.errors import APIError, NotFound

GITHUB_REPO = os.environ.get("GITHUB_REPO", "sawikot/VidaAnnotatior")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_USER = os.environ.get("GITHUB_USER", "").strip() or GITHUB_REPO.split("/")[0]
IMAGE = os.environ.get("IMAGE", "").strip() or f"ghcr.io/{GITHUB_REPO.lower()}"
APP_LABEL = os.environ.get("APP_LABEL", "com.vida.role=app")
APP_PORT = int(os.environ.get("APP_PORT", "8088"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SECRET_FILE = Path(os.environ.get("SECRET_FILE", "/run/vida/updater-secret"))
ENV_FILE = Path(os.environ.get("ENV_FILE", "/config/.env"))  # the install's .env, so compose keeps the choice
HEALTH_TIMEOUT = int(os.environ.get("HEALTH_TIMEOUT", "240"))
KEEP_BACKUPS = int(os.environ.get("KEEP_BACKUPS", "20"))

DB_DIR = DATA_DIR / "database"
DB_FILES = ("app.db", "app.db-wal", "app.db-shm")
BACKUP_DIR = DATA_DIR / "backups"

client = docker.from_env()
lock = threading.Lock()
state: dict = {"state": "idle", "target": None, "message": "", "log": [], "started_at": None, "finished_at": None}


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[updater] {message}", flush=True)
    state["log"] = (state["log"] + [f"{stamp}  {message}"])[-100:]
    state["message"] = message


# ------------------------------------------------------------------ the app container


def app_container():
    found = client.containers.list(all=True, filters={"label": APP_LABEL})
    running = [c for c in found if c.status == "running"]
    return (running or found or [None])[0]


def version_of(container) -> str | None:
    if container is None:
        return None
    env = container.attrs["Config"].get("Env") or []
    for item in env:
        if item.startswith("APP_VERSION="):
            return item.split("=", 1)[1]
    return None


def recreate(old, image: str, name: str):
    """Create (not start) a container like ``old`` on ``image``, called ``name``: same mounts, ports, networks,
    restart policy and settings. What ``old`` only had from its image (command, image env, image
    labels) is left for the new image to bring, the way watchtower does it."""
    attrs = client.api.inspect_container(old.id)
    config, host_config = attrs["Config"], attrs["HostConfig"]
    old_image = client.api.inspect_image(attrs["Image"])["Config"]

    def own(key):
        value = config.get(key)
        return None if value == old_image.get(key) else value

    image_env = set(old_image.get("Env") or [])
    image_labels = old_image.get("Labels") or {}
    env = [e for e in (config.get("Env") or []) if e not in image_env]
    labels = {k: v for k, v in (config.get("Labels") or {}).items() if image_labels.get(k) != v}

    networks = attrs["NetworkSettings"]["Networks"] or {}
    endpoints = {
        name: {"Aliases": [a for a in (net.get("Aliases") or []) if not old.id.startswith(a)]}
        for name, net in networks.items()
    }
    first = next(iter(endpoints), None)

    new_id = client.api.create_container(
        image=image,
        name=name,
        command=own("Cmd"),
        entrypoint=own("Entrypoint"),
        working_dir=own("WorkingDir"),
        user=own("User") or None,
        environment=env,
        labels=labels,
        host_config=host_config,
        networking_config={"EndpointsConfig": {first: endpoints[first]}} if first else None,
        stop_signal=config.get("StopSignal"),
    )["Id"]
    for net_name, endpoint in endpoints.items():
        if net_name != first:
            client.api.connect_container_to_network(new_id, net_name, aliases=endpoint["Aliases"])
    return client.containers.get(new_id)


def wait_healthy(container, version: str) -> None:
    """Until the app answers /api/health with the expected version, or HEALTH_TIMEOUT runs out."""
    deadline = time.time() + HEALTH_TIMEOUT
    last = "no answer yet"
    while time.time() < deadline:
        container.reload()
        if container.status in ("exited", "dead"):
            tail = container.logs(tail=15).decode(errors="replace")
            raise RuntimeError(f"The new version stopped while starting:\n{tail}")
        ip = next((n.get("IPAddress") for n in container.attrs["NetworkSettings"]["Networks"].values() if n.get("IPAddress")), None)
        if ip:
            try:
                with urllib.request.urlopen(f"http://{ip}:{APP_PORT}/api/health", timeout=3) as res:
                    running = json.load(res).get("version")
                    if running == version:
                        return
                    last = f"answers with version {running!r}"
            except (OSError, ValueError) as e:
                last = str(e)
        time.sleep(2)
    raise RuntimeError(f"The new version did not start within {HEALTH_TIMEOUT}s ({last}).")


# ------------------------------------------------------------------ database backups


def backups() -> list[dict]:
    if not BACKUP_DIR.is_dir():
        return []
    found = []
    for folder in sorted(BACKUP_DIR.iterdir(), reverse=True):
        meta = folder / "backup.json"
        if meta.is_file():
            try:
                found.append({"id": folder.name, **json.loads(meta.read_text())})
            except ValueError:
                pass
    return found


def backup_database(from_version: str | None, to_version: str) -> str | None:
    """Copy the database (the app is stopped) into data/backups/<time>_<version>/. Slides are not
    copied: no version changes them."""
    if not (DB_DIR / "app.db").is_file():
        log("No database file to back up.")
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = BACKUP_DIR / f"{stamp}_{from_version or 'unknown'}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in DB_FILES:
        if (DB_DIR / name).is_file():
            shutil.copy2(DB_DIR / name, folder / name)
    meta = {"from_version": from_version, "to_version": to_version, "created_at": datetime.now(timezone.utc).isoformat()}
    (folder / "backup.json").write_text(json.dumps(meta, indent=2))
    for old in backups()[KEEP_BACKUPS:]:
        shutil.rmtree(BACKUP_DIR / old["id"], ignore_errors=True)
    log(f"Database backed up ({folder.name}).")
    return folder.name


def restore_database(backup_id: str) -> None:
    folder = BACKUP_DIR / backup_id
    if not (folder / "app.db").is_file():
        raise RuntimeError(f"Backup {backup_id} has no database file.")
    for name in DB_FILES:
        (DB_DIR / name).unlink(missing_ok=True)
        if (folder / name).is_file():
            shutil.copy2(folder / name, DB_DIR / name)
    log(f"Database restored from backup {backup_id}.")


# ------------------------------------------------------------------ GitHub


def github(path: str):
    req = urllib.request.Request(f"https://api.github.com{path}", headers={"Accept": "application/vnd.github+json", "User-Agent": "vida-updater"})
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            hint = "Is GITHUB_TOKEN set in .env? The repository is private." if not GITHUB_TOKEN else "Check that GITHUB_TOKEN can read this repository."
            raise RuntimeError(f"GitHub did not find {GITHUB_REPO}. {hint}") from e
        if e.code == 401:
            raise RuntimeError("GitHub refused the GITHUB_TOKEN in .env (expired or wrong).") from e
        raise RuntimeError(f"GitHub answered {e.code}.") from e
    except OSError as e:
        raise RuntimeError(f"Could not reach GitHub: {e}") from e


def releases() -> list[dict]:
    items = github(f"/repos/{GITHUB_REPO}/releases?per_page=50")
    return [
        {
            "version": r["tag_name"],
            "name": r.get("name") or r["tag_name"],
            "notes": r.get("body") or "",
            "published_at": r.get("published_at"),
            "prerelease": bool(r.get("prerelease")),
            "url": r.get("html_url"),
        }
        for r in items
        if not r.get("draft")
    ]


# ------------------------------------------------------------------ switching


def remember_version(version: str) -> None:
    """APP_VERSION in the install's .env, so `docker compose up -d` keeps this version too. Written in
    place: the file is bind-mounted, and replacing it would detach the mount."""
    if not ENV_FILE.is_file():
        return
    lines = ENV_FILE.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("APP_VERSION="):
            lines[i] = f"APP_VERSION={version}"
            break
    else:
        lines.append(f"APP_VERSION={version}")
    with ENV_FILE.open("r+") as f:
        f.seek(0)
        f.write("\n".join(lines) + "\n")
        f.truncate()


def switch(version: str, restore_backup: str | None) -> None:
    image = f"{IMAGE}:{version}"
    old = app_container()
    if old is None:
        raise RuntimeError(f"No app container found (label {APP_LABEL}).")
    old_version = version_of(old)
    log(f"Downloading {image} (the app keeps running meanwhile)...")
    auth = {"username": GITHUB_USER, "password": GITHUB_TOKEN} if GITHUB_TOKEN else None
    try:
        client.images.pull(IMAGE, tag=version, auth_config=auth)
    except (APIError, NotFound) as e:
        raise RuntimeError(f"Could not download {image}: {e.explanation if hasattr(e, 'explanation') else e}") from e

    state["state"] = "switching"
    log(f"Stopping version {old_version or '?'}...")
    old.stop(timeout=30)
    safety = backup_database(old_version, version)
    name = old.name
    old.rename(f"{name}-previous-{int(time.time())}")
    new = None
    try:
        if restore_backup:
            restore_database(restore_backup)
        new = recreate(old, image, name)
        log(f"Starting version {version}...")
        new.start()
        wait_healthy(new, version)
    except Exception as e:
        log(f"Failed: {e}")
        log(f"Going back to version {old_version or '?'}...")
        if new is not None:
            new.remove(force=True)
        if safety:
            restore_database(safety)
        old.rename(name)
        old.start()
        raise RuntimeError(f"Version {version} did not start, so version {old_version or '?'} is running again. {e}") from e

    old.remove()
    remember_version(version)
    log(f"Version {version} is running.")


def run_switch(version: str, restore_backup: str | None) -> None:
    try:
        switch(version, restore_backup)
        state["state"] = "done"
    except Exception as e:  # noqa: BLE001 -- anything that goes wrong is reported to the page
        traceback.print_exc()
        state["state"] = "failed"
        state["message"] = str(e)
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        lock.release()


# ------------------------------------------------------------------ HTTP


def status() -> dict:
    app = app_container()
    return {
        **state,
        "current_version": version_of(app),
        "app_status": app.status if app else None,
        "image": IMAGE,
        "repo": GITHUB_REPO,
        "has_token": bool(GITHUB_TOKEN),
        "backups": backups(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        if secrets.compare_digest(self.headers.get("X-Updater-Secret", ""), SECRET):
            return True
        self._send(403, {"detail": "Wrong or missing updater secret."})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed():
            return
        try:
            if self.path == "/status":
                return self._send(200, status())
            if self.path == "/releases":
                return self._send(200, releases())
            self._send(404, {"detail": "Not found"})
        except Exception as e:  # noqa: BLE001
            self._send(502, {"detail": str(e)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._allowed():
            return
        if self.path != "/switch":
            return self._send(404, {"detail": "Not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"detail": "Body is not JSON."})
        version = str(body.get("version") or "").strip()
        restore = body.get("restore_backup") or None
        if not version or "/" in version or ":" in version:
            return self._send(400, {"detail": "Give a release version, e.g. v1.2.0."})
        if restore and restore not in {b["id"] for b in backups()}:
            return self._send(400, {"detail": f"There is no backup {restore}."})
        if not lock.acquire(blocking=False):
            return self._send(409, {"detail": "A version switch is already running."})
        state.update(state="pulling", target=version, log=[], message="", started_at=datetime.now(timezone.utc).isoformat(), finished_at=None)
        threading.Thread(target=run_switch, args=(version, restore), daemon=True).start()
        self._send(202, status())

    def log_message(self, format, *args) -> None:  # noqa: A002 -- quiet: the status polls would flood the log
        pass


def load_secret() -> str:
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.is_file() or not SECRET_FILE.read_text().strip():
        SECRET_FILE.write_text(secrets.token_urlsafe(32))
    return SECRET_FILE.read_text().strip()


def clean_leftovers() -> None:
    """A container renamed '-previous-' but never removed means the updater itself was stopped
    mid-switch. If the app is not running, bring that previous one back."""
    found = client.containers.list(all=True, filters={"label": APP_LABEL})
    for c in [c for c in found if "-previous-" in c.name]:
        other = next((o for o in found if o.id != c.id and "-previous-" not in o.name), None)
        if other is not None and other.status == "running":
            c.remove(force=True)  # the switch had finished, only the clean-up was missed
            continue
        if other is not None:
            other.remove(force=True)
        c.rename(c.name.split("-previous-")[0])
        if c.status != "running":
            c.start()
        print(f"[updater] Brought back {c.name} after an interrupted switch.", flush=True)


SECRET = load_secret()

if __name__ == "__main__":
    clean_leftovers()
    port = int(os.environ.get("UPDATER_PORT", "9000"))
    print(f"[updater] Listening on :{port} for {GITHUB_REPO} ({IMAGE}).", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
