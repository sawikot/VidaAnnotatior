"""Model recipes of your own: made from the template, a copy or a ZIP; edited with earlier versions
kept; and usable for training only once the trainer has checked them as they are now."""
import io
import zipfile

import pytest

from app.api.access import current_user
from app.main import app
from app.models.user import User
from app.services import recipes
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)
from tests.test_training import HW, carry_out, program, project, trainer  # noqa: F401  (fixtures)

BUILTIN = "detection_fasterrcnn"


@pytest.fixture()
def client(env):  # noqa: F811
    return env[0]


def new(client, **body):
    res = client.post("/api/recipes", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_built_in_recipes_are_listed_and_cannot_be_changed(client):
    listed = {r["id"]: r for r in client.get("/api/recipes").json()}
    assert listed[BUILTIN]["builtin"] and listed[BUILTIN]["usable"] and listed[BUILTIN]["check"] is None
    assert listed[BUILTIN]["files"][:3] == ["recipe.json", "train.py", "predict.py"]
    assert "fasterrcnn" in client.get(f"/api/recipes/{BUILTIN}/files/train.py").json()["content"]
    assert client.put(f"/api/recipes/{BUILTIN}/files/train.py", json={"content": "x"}).status_code == 422
    assert client.delete(f"/api/recipes/{BUILTIN}").status_code == 422
    assert client.post(f"/api/recipes/{BUILTIN}/check").status_code == 409
    assert client.get("/api/recipes/nothing_here").status_code == 404


def test_a_duplicate_and_a_blank_recipe_are_yours_to_edit(client):
    copy = new(client, name="My detector", source_id=BUILTIN)
    assert (copy["id"], copy["name"], copy["builtin"], copy["usable"]) == ("my_detector", "My detector", False, False)
    assert client.get("/api/recipes/my_detector/files/train.py").json()["content"] == client.get(f"/api/recipes/{BUILTIN}/files/train.py").json()["content"]
    assert new(client, name="My detector", source_id=BUILTIN)["id"] == "my_detector_2"  # never over another

    blank = new(client, name="From scratch", task="segmentation")
    assert (blank["task"], blank["trainable"], blank["has_predict"]) == ("segmentation", True, True)
    assert blank["check"] is None and blank["packages"] is None  # the template's requirements.txt lists nothing
    assert client.post("/api/recipes", json={"name": "x", "task": "poetry"}).status_code == 422

    # Not offered for training until it has passed its check.
    assert all(r["builtin"] for r in client.get("/api/training/recipes").json())


def test_saving_keeps_earlier_versions(client):
    rid = new(client, name="Edited")["id"]
    first = client.get(f"/api/recipes/{rid}/files/train.py").json()
    assert first["versions"] == []
    client.put(f"/api/recipes/{rid}/files/train.py", json={"content": "print('two')\r\n"})
    client.put(f"/api/recipes/{rid}/files/train.py", json={"content": "print('three')\n"})
    now = client.get(f"/api/recipes/{rid}/files/train.py").json()
    assert now["content"] == "print('three')\n" and len(now["versions"]) == 2
    older = [client.get(f"/api/recipes/{rid}/files/train.py/versions/{v['id']}").json()["content"] for v in now["versions"]]
    assert older == ["print('two')\n", first["content"]]  # newest first

    # New files, and only text files with simple names.
    assert client.put(f"/api/recipes/{rid}/files/helpers.py", json={"content": "X = 1\n"}).json()["files"][-1] == "helpers.py"
    for bad in ("model.pt", ".hidden.py", "a b.py"):
        assert client.put(f"/api/recipes/{rid}/files/{bad}", json={"content": ""}).status_code == 422
    assert client.delete(f"/api/recipes/{rid}/files/helpers.py").json()["files"] == ["recipe.json", "train.py", "predict.py", "requirements.txt"]
    assert client.delete(f"/api/recipes/{rid}/files/recipe.json").status_code == 422

    # A recipe.json that no longer makes sense is said so, not hidden.
    broken = client.put(f"/api/recipes/{rid}/files/recipe.json", json={"content": "{ not json"}).json()
    assert "not valid JSON" in broken["problem"] and not broken["usable"]
    assert client.post(f"/api/recipes/{rid}/check").status_code == 422

    wanted = client.put(f"/api/recipes/{rid}/files/requirements.txt", json={"content": "# a comment\ntimm==1.0.15\n"}).json()
    assert wanted["requirements"] == ["timm==1.0.15"]


def test_a_zip_becomes_a_recipe_and_back(client):
    rid = new(client, name="To share")["id"]
    packed = client.get(f"/api/recipes/{rid}/download")
    assert packed.status_code == 200 and sorted(zipfile.ZipFile(io.BytesIO(packed.content)).namelist())[0] == f"{rid}/predict.py"
    again = client.post("/api/recipes/upload", files={"file": ("r.zip", packed.content)}, data={"name": "Shared back"})
    assert again.status_code == 201 and again.json()["id"] == "shared_back" and again.json()["files"] == client.get(f"/api/recipes/{rid}").json()["files"]

    def zipped(files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            for name, content in files.items():
                z.writestr(name, content)
        return buffer.getvalue()

    for bad in (b"not a zip", zipped({"train.py": "x"}), zipped({"recipe.json": "{}", "weights.pt": "x"})):
        assert client.post("/api/recipes/upload", files={"file": ("r.zip", bad)}).status_code == 422
    # Only the files beside recipe.json are taken: nothing can be written outside the recipe's folder.
    sly = client.post("/api/recipes/upload", files={"file": ("r.zip", zipped({"recipe.json": "{}", "../escape.py": "x", "sub/deep.py": "x"}))}, data={"name": "Sly"})
    assert sly.status_code == 201 and sly.json()["files"] == ["recipe.json"] and sly.json()["problem"]
    assert not list(recipes.user_dir().parent.rglob("escape.py"))


def test_only_administrators_change_recipes(client):
    rid = new(client, name="Guarded")["id"]
    app.dependency_overrides[current_user] = lambda: User(id=99, email="m@example.org", name="A manager", role="manager")
    assert client.get(f"/api/recipes/{rid}/files/train.py").status_code == 200  # everyone signed in may read
    for res in (
        client.post("/api/recipes", json={"name": "Mine"}),
        client.put(f"/api/recipes/{rid}/files/train.py", json={"content": "x"}),
        client.post(f"/api/recipes/{rid}/check"),
        client.delete(f"/api/recipes/{rid}"),
    ):
        assert res.status_code == 403


def check(client, module, rid):
    """Ask for a check and have the trainer program carry it out."""
    assert client.post(f"/api/recipes/{rid}/check").json()["check"]["status"] == "queued"
    job = trainer(client, "/claim", {"hardware": HW}).json()["check"]
    assert job["id"] == rid and client.get(f"/api/recipes/{rid}").json()["check"]["status"] == "running"
    module.run_check(job, HW)
    return client.get(f"/api/recipes/{rid}").json()


def test_a_recipe_is_trained_with_only_after_its_check_passes(program):
    client, pid, module = program
    rid = new(client, name="Checked")["id"]
    start = lambda: client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": rid, "settings": {"epochs": 2}})  # noqa: E731
    assert start().status_code == 422 and "check" in start().json()["detail"]

    passed = check(client, module, rid)
    assert passed["check"]["status"] == "passed" and passed["usable"], passed["check"]
    assert [s["ok"] for s in passed["check"]["report"]["steps"]] == [True] * 6
    assert rid in [r["id"] for r in client.get("/api/training/recipes").json()]

    # Trained from the shared folder, with the run keeping its own copy of the code.
    run = carry_out(client, pid, module, {"epochs": 2}, recipe_id=rid)
    assert (run["status"], run["epoch"], run["has_model"]) == ("done", 2, True), run["error"]

    # Any change makes the check a check of something else.
    changed = client.put(f"/api/recipes/{rid}/files/train.py", json={"content": "raise SystemExit('boom')\n"}).json()
    assert changed["check"]["status"] == "stale" and not changed["usable"] and start().status_code == 422
    failed = check(client, module, rid)
    assert failed["check"]["status"] == "failed" and not failed["usable"]
    steps = failed["check"]["report"]["steps"]
    assert [s["ok"] for s in steps] == [False] and "boom" in failed["check"]["report"]["log"]

    # Deleting the recipe leaves the run and its model as they were.
    assert client.delete(f"/api/recipes/{rid}").status_code == 204
    assert client.get(f"/api/training/runs/{run['id']}").json()["has_model"]
