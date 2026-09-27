"""The version page's routes (api/system.py): administrators only, and passed on to the updater."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.access import current_user
from app.core.config import get_settings
from app.main import app


@pytest.fixture()
def as_role():
    def sign_in(role):
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(is_admin=role == "admin", can_manage=role != "annotator")
        return TestClient(app)

    yield sign_in
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
def updater(tmp_path, monkeypatch):
    """A stand-in updater that records what it was asked and checks the shared secret."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            seen.append(("GET", self.path, self.headers.get("X-Updater-Secret")))
            if self.path == "/status":
                return self._reply(200, {"state": "idle", "current_version": "v1.0.0"})
            self._reply(200, [{"version": "v1.1.0"}, {"version": "v1.0.0"}])

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, body))
            if body["version"] == "busy":
                return self._reply(409, {"detail": "A version switch is already running."})
            self._reply(202, {"state": "pulling", "target": body["version"]})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    secret = tmp_path / "secret"
    secret.write_text("s3cret\n")
    settings = get_settings()
    monkeypatch.setattr(settings, "updater_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "updater_secret_file", secret)
    yield seen
    server.shutdown()


def test_only_administrators(as_role):
    for role in ("manager", "annotator"):
        client = as_role(role)
        assert client.get("/api/system/version").status_code == 403
        assert client.post("/api/system/update", json={"version": "v1.0.0"}).status_code == 403


def test_without_docker_there_is_nothing_to_switch(as_role, monkeypatch):
    monkeypatch.setattr(get_settings(), "updater_url", "")
    client = as_role("admin")
    res = client.get("/api/system/version").json()
    assert res["can_update"] is False and res["updater"] is None and res["version"] == get_settings().app_version
    assert client.get("/api/system/releases").status_code == 409
    assert client.get("/api/health").json()["version"] == get_settings().app_version


def test_requests_reach_the_updater_with_the_secret(as_role, updater):
    client = as_role("admin")
    res = client.get("/api/system/version").json()
    assert res["can_update"] is True and res["updater"]["current_version"] == "v1.0.0"
    assert client.get("/api/system/releases").json()[0]["version"] == "v1.1.0"
    assert all(secret == "s3cret" for method, _, secret in updater if method == "GET")

    res = client.post("/api/system/update", json={"version": "v1.1.0", "restore_backup": "20260101-000000_v1.1.0"})
    assert res.status_code == 202 and res.json()["target"] == "v1.1.0"
    assert updater[-1] == ("POST", "/switch", {"version": "v1.1.0", "restore_backup": "20260101-000000_v1.1.0"})

    res = client.post("/api/system/update", json={"version": "busy"})
    assert res.status_code == 409 and "already running" in res.json()["detail"]


def test_updater_down(as_role, updater, monkeypatch):
    monkeypatch.setattr(get_settings(), "updater_url", "http://127.0.0.1:1")
    res = as_role("admin").get("/api/system/version").json()
    assert res["updater"] is None and "not reachable" in res["updater_error"]
