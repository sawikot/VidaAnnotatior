"""Signing in, the first administrator, password links, users and project members -- and the access
rules every data route follows (api/access.py). These tests sign in for real (no test override)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.access import current_user
from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.services.auth import hash_password, login_throttle, verify_password
from tests.test_slide_import_api import SLIDE

PASSWORD = "correct horse"


@pytest.fixture()
def api():
    """A fresh database and a client with no one signed in."""
    app.dependency_overrides.pop(current_user, None)  # real sign-in
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):  # noqa: ANN001
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    login_throttle._failures.clear()
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def client_for(api, email, password=PASSWORD):
    c = TestClient(app)
    res = c.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return c


def setup_admin(api):
    res = api.post("/api/auth/setup", json={"name": "Ada Admin", "email": "Ada@Lab.org", "password": PASSWORD})
    assert res.status_code == 201, res.text
    return api


def add_user(admin, name, email, role, password=PASSWORD):
    res = admin.post("/api/users", json={"name": name, "email": email, "role": role, "password": password})
    assert res.status_code == 201, res.text
    return res.json()["user"]


def test_passwords_are_hashed_and_checked():
    stored = hash_password("s3cret-pass")
    assert stored.startswith("scrypt$") and "s3cret-pass" not in stored
    assert verify_password("s3cret-pass", stored) and not verify_password("wrong", stored)
    assert hash_password("s3cret-pass") != stored  # salted


def test_first_run_sets_up_an_administrator_once(api):
    assert api.get("/api/auth/status").json() == {"needs_setup": True, "user": None}
    assert api.get("/api/projects").status_code == 401  # nothing without signing in
    assert api.post("/api/auth/setup", json={"name": "A", "email": "a@lab.org", "password": "short"}).status_code == 422

    setup_admin(api)  # signs in straight away
    status = api.get("/api/auth/status").json()
    assert status["needs_setup"] is False and status["user"]["email"] == "ada@lab.org" and status["user"]["role"] == "admin"
    assert api.post("/api/auth/setup", json={"name": "B", "email": "b@lab.org", "password": PASSWORD}).status_code == 409


def test_sign_in_out_and_failed_attempts_are_limited(api):
    setup_admin(api)
    api.post("/api/auth/logout")
    assert api.get("/api/auth/me").status_code == 401

    assert api.post("/api/auth/login", json={"email": "ada@lab.org", "password": "nope"}).status_code == 401
    assert api.post("/api/auth/login", json={"email": "ADA@lab.org ", "password": PASSWORD}).status_code == 200
    assert api.get("/api/auth/me").json()["name"] == "Ada Admin"

    for _ in range(8):
        api.post("/api/auth/login", json={"email": "ada@lab.org", "password": "nope"})
    assert api.post("/api/auth/login", json={"email": "ada@lab.org", "password": PASSWORD}).status_code == 429


def test_password_links_and_changing_a_password(api):
    admin = setup_admin(api)
    created = admin.post("/api/users", json={"name": "Nia", "email": "nia@lab.org", "role": "annotator"}).json()
    token = created["password_link_token"]
    assert created["user"]["has_password"] is False and token

    guest = TestClient(app)
    assert guest.get(f"/api/auth/password-link/{token}").json()["name"] == "Nia"
    assert guest.post("/api/auth/password-link", json={"token": token, "password": PASSWORD}).status_code == 200
    assert guest.get("/api/auth/me").json()["email"] == "nia@lab.org"  # signed in by setting it
    assert guest.post("/api/auth/password-link", json={"token": token, "password": PASSWORD}).status_code == 404  # used

    assert guest.post("/api/auth/password", json={"current_password": "wrong", "new_password": "another pass"}).status_code == 422
    assert guest.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "another pass"}).status_code == 204
    client_for(api, "nia@lab.org", "another pass")


def test_only_administrators_manage_users_and_the_last_admin_stays(api):
    admin = setup_admin(api)
    add_user(admin, "Max", "max@lab.org", "manager")
    manager = client_for(api, "max@lab.org")
    assert manager.get("/api/users").status_code == 200  # to add members
    assert manager.post("/api/users", json={"name": "X", "email": "x@lab.org"}).status_code == 403

    me = admin.get("/api/auth/me").json()
    assert admin.put(f"/api/users/{me['id']}", json={"role": "annotator"}).status_code == 409
    assert admin.post("/api/users", json={"name": "Dup", "email": "MAX@lab.org"}).status_code == 409


def test_a_disabled_account_is_signed_out_everywhere(api):
    admin = setup_admin(api)
    ann = add_user(admin, "Ann", "ann@lab.org", "annotator")
    session = client_for(api, "ann@lab.org")
    assert admin.put(f"/api/users/{ann['id']}", json={"is_active": False}).status_code == 200
    assert session.get("/api/auth/me").status_code == 401
    assert api.post("/api/auth/login", json={"email": "ann@lab.org", "password": PASSWORD}).status_code == 401


def test_projects_are_seen_by_their_members_and_changed_by_managers(api):
    admin = setup_admin(api)
    add_user(admin, "Max", "max@lab.org", "manager")
    ann = add_user(admin, "Ann", "ann@lab.org", "annotator")
    manager, annotator = client_for(api, "max@lab.org"), client_for(api, "ann@lab.org")

    assert annotator.post("/api/projects", json={"name": "Nope"}).status_code == 403
    pid = manager.post("/api/projects", json={"name": "Bladder"}).json()["id"]  # the creator is a member
    other = admin.post("/api/projects", json={"name": "Private"}).json()["id"]

    assert [p["id"] for p in manager.get("/api/projects").json()] == [pid]
    assert annotator.get("/api/projects").json() == []
    assert annotator.get(f"/api/projects/{pid}").status_code == 403
    assert {p["id"] for p in admin.get("/api/projects").json()} == {pid, other}  # admins see everything

    assert annotator.post(f"/api/projects/{pid}/members", json={"user_id": ann["id"]}).status_code == 403
    members = manager.post(f"/api/projects/{pid}/members", json={"user_id": ann["id"]}).json()
    assert {m["email"] for m in members} == {"max@lab.org", "ann@lab.org"}
    assert annotator.get(f"/api/projects/{pid}").status_code == 200
    assert manager.get(f"/api/projects/{other}").status_code == 403  # managers too: only their projects

    # annotators read, but do not change the project
    assert annotator.put(f"/api/projects/{pid}", json={"name": "Renamed"}).status_code == 403
    assert annotator.delete(f"/api/projects/{pid}").status_code == 403
    upload = [("files", ("s.tif", SLIDE, "application/octet-stream"))]
    assert annotator.post(f"/api/projects/{pid}/slides/upload", files=upload).status_code == 403

    manager.delete(f"/api/projects/{pid}/members/{ann['id']}")
    assert annotator.get(f"/api/projects/{pid}").status_code == 403


def test_annotators_annotate_and_review_under_their_own_name(api, tmp_path, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr("app.api.processing.get_settings", lambda: settings)
    admin = setup_admin(api)
    ann = add_user(admin, "Ann", "ann@lab.org", "annotator")
    annotator = client_for(api, "ann@lab.org")
    pid = admin.post("/api/projects", json={"name": "Work"}).json()["id"]
    admin.post(f"/api/projects/{pid}/members", json={"user_id": ann["id"]})
    sid = admin.post(f"/api/projects/{pid}/slides/upload", files=[("files", ("s.tif", SLIDE, "application/octet-stream"))]).json()["slides"][0]["id"]
    config_id = admin.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    grid = {"patch_width": 512, "patch_height": 512, "stride_x": 512, "stride_y": 512, "target_magnification": 40, "min_tissue_fraction": 0}

    assert annotator.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id, "grid": grid}).status_code == 403
    assert admin.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id, "grid": grid}).status_code == 200
    patch = annotator.get(f"/api/slides/{sid}/patches", params={"limit": 1}).json()["items"][0]

    square = [[1, 1], [50, 1], [50, 50], [1, 50]]
    res = annotator.post(f"/api/patches/{patch['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square, "created_by": "Someone Else"})
    assert res.status_code == 201 and res.json()["created_by"] == "Ann"  # the server decides who drew it
    assert annotator.put(f"/api/annotations/{res.json()['id']}", json={"notes": "checked"}).status_code == 200

    reviewed = annotator.put(f"/api/patches/{patch['id']}", json={"status": "reviewed", "reviewed_by": "Dr. Nobody"}).json()
    assert reviewed["status"] == "reviewed" and reviewed["reviewed_by"] == "Ann"
    assert annotator.get(f"/api/projects/{pid}/export/patch_csv").status_code == 200  # members export
    assert annotator.delete(f"/api/annotations/{res.json()['id']}").status_code == 204
