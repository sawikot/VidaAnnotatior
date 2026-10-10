"""Training runs: the project must be ready, a run waits for the trainer, the trainer is handed the
dataset and reports back -- and only the trainer, by its secret, may do that."""
import json

import pytest

from app.api.training import run_dir, trainer_secret
from app.services import recipes
from tests.test_patch_grids import BIG, class_id, generate, patches, square  # noqa: F401
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)

RECIPE = "detection_fasterrcnn"


@pytest.fixture()
def project(env, monkeypatch):  # noqa: F811
    """Three slides with patches, each with one classified box: train, val and test."""
    client, pid, settings = env
    monkeypatch.setattr("app.api.processing.get_settings", lambda: settings)
    ids = [s["id"] for s in upload(client, pid, [("a.tif", SLIDE), ("b.tif", SLIDE), ("c.tif", SLIDE)]).json()["slides"]]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    cls = class_id(client, pid)
    for sid in ids:
        generate(client, sid, config_id, BIG)
        for patch in patches(client, sid)[:2]:
            res = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "rectangle", "class_id": cls, "coordinates_patch_local": square(10, 10, 50)})
            assert res.status_code in (200, 201), res.text
    return client, pid, ids


def split(client, pid, ids):
    sets = dict(zip(ids, ("train", "val", "test")))
    assert client.put(f"/api/projects/{pid}/split", json={"mode": "manual", "assignments": sets}).status_code == 200


def trainer(client, path, body=None, secret=None):
    return client.post(f"/api/trainer{path}", json=body or {}, headers={"X-Trainer-Secret": trainer_secret() if secret is None else secret})


def test_the_built_in_recipe_is_listed_with_its_settings(project):
    client, _, _ = project
    listed = {r["id"]: r for r in client.get("/api/training/recipes").json()}
    assert listed[RECIPE]["task"] == "detection"
    assert {s["key"] for s in listed[RECIPE]["settings"]} >= {"epochs", "batch_size", "image_size"}


def test_settings_are_filled_in_and_checked():
    recipe = recipes.get_recipe(RECIPE)
    assert recipes.resolve_settings(recipe, {"epochs": 5})["epochs"] == 5
    assert recipes.resolve_settings(recipe, {})["architecture"] == "fasterrcnn_mobilenet_v3_large_fpn"
    for bad in ({"epochs": 0}, {"epochs": 2.5}, {"architecture": "huge"}, {"nonsense": 1}):
        with pytest.raises(ValueError):
            recipes.resolve_settings(recipe, bad)


def test_a_project_is_ready_only_with_a_split_and_annotations_in_train_and_val(project):
    client, pid, ids = project
    before = client.get(f"/api/projects/{pid}/training/readiness").json()
    assert before["problems"] and "split" in before["problems"][0]
    assert client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE}).status_code == 422

    split(client, pid, ids)
    ready = client.get(f"/api/projects/{pid}/training/readiness").json()
    assert ready["problems"] == []
    assert {name: (s["slides"], s["images"], s["objects"]) for name, s in ready["sets"].items()} == {
        "train": (1, 2, 2), "val": (1, 2, 2), "test": (1, 2, 2),
    }
    assert len(ready["classes"]) == 1


def test_a_run_goes_from_waiting_to_done_through_the_trainer(project):
    client, pid, ids = project
    split(client, pid, ids)
    run = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE, "settings": {"epochs": 3}}).json()
    assert (run["status"], run["epochs"], run["settings"]["batch_size"]) == ("queued", 3, 2)

    # Only the trainer, by its secret.
    assert trainer(client, "/claim", secret="wrong").status_code == 403
    assert client.get("/api/training/status").json()["online"] is False

    job = trainer(client, "/claim", {"hardware": {"device": "cpu", "gpus": []}}).json()["run"]
    assert job["id"] == run["id"] and job["settings"]["epochs"] == 3
    assert client.get("/api/training/status").json() == {"online": True, "hardware": {"device": "cpu", "gpus": []}}

    # The dataset is in the run's folder: one COCO file and its images per set.
    folder = run_dir(run["id"]) / "dataset"
    layout = json.loads((folder / "dataset.json").read_text())
    assert set(layout["sets"]) == {"train", "val", "test"} and len(layout["classes"]) == 1
    train = json.loads((folder / layout["sets"]["train"]["annotations"]).read_text())
    assert len(train["images"]) == 2 and len(train["annotations"]) == 2
    assert all((folder / layout["sets"]["train"]["images"] / image["file_name"]).is_file() for image in train["images"])
    running = client.get(f"/api/training/runs/{run['id']}").json()
    assert running["status"] == "running" and running["dataset"]["sets"]["val"]["slides"] == ["b.tif"]
    assert trainer(client, "/claim").json()["run"] is None  # nothing else is waiting

    # Before the first epoch the trainer says what it is busy with; the first epoch's numbers end that.
    trainer(client, f"/runs/{run['id']}/progress", {"stage": "Installing this model's packages: example==1.0."})
    assert "Installing" in client.get(f"/api/training/runs/{run['id']}").json()["stage"]
    for epoch in (1, 2):
        answer = trainer(client, f"/runs/{run['id']}/progress", {"metrics": {"epoch": epoch, "epochs": 3, "train_loss": 1 / epoch}, "device": "CPU"})
        assert answer.json() == {"stop": False}
    assert trainer(client, f"/runs/{run['id']}/progress").json() == {"stop": False}  # a heartbeat adds nothing
    seen = client.get(f"/api/training/runs/{run['id']}").json()
    assert (seen["epoch"], len(seen["metrics"]), seen["device"], seen["stage"]) == (2, 2, "CPU", None)

    output = run_dir(run["id"]) / "output"
    output.mkdir()
    (output / "model.pt").write_bytes(b"weights")
    (output / "train.log").write_text("one\ntwo\nthree\n")
    trainer(client, f"/runs/{run['id']}/finish", {"status": "done", "result": {"val": {"ap50": 0.8}}})
    done = client.get(f"/api/training/runs/{run['id']}").json()
    assert (done["status"], done["result"]["val"]["ap50"], done["has_model"]) == ("done", 0.8, True)
    assert not folder.exists()  # the dataset copy is not kept
    assert client.get(f"/api/training/runs/{run['id']}/log", params={"tail": 2}).text == "two\nthree"
    assert client.get(f"/api/training/runs/{run['id']}/model").content == b"weights"

    assert [r["id"] for r in client.get(f"/api/projects/{pid}/training/runs").json()] == [run["id"]]
    assert client.delete(f"/api/training/runs/{run['id']}").status_code == 204
    assert not run_dir(run["id"]).exists()


def test_stopping_and_a_restarted_trainer(project):
    client, pid, ids = project
    split(client, pid, ids)
    start = lambda: client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE}).json()["id"]  # noqa: E731

    waiting = start()
    assert client.post(f"/api/training/runs/{waiting}/stop").json()["status"] == "stopped"  # never started

    running = start()
    assert trainer(client, "/claim").json()["run"]["id"] == running
    assert client.post(f"/api/training/runs/{running}/stop").json()["status"] == "running"
    assert trainer(client, f"/runs/{running}/progress").json() == {"stop": True}
    assert client.delete(f"/api/training/runs/{running}").status_code == 409
    trainer(client, f"/runs/{running}/finish", {"status": "stopped"})
    assert client.get(f"/api/training/runs/{running}").json()["status"] == "stopped"

    lost = start()
    trainer(client, "/claim")
    trainer(client, "/hello", {"fresh": True})  # the trainer came back up: that run is gone
    gone = client.get(f"/api/training/runs/{lost}").json()
    assert gone["status"] == "failed" and "restarted" in gone["error"]


def test_deleting_a_project_removes_its_runs(project):
    client, pid, ids = project
    split(client, pid, ids)
    run = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE}).json()
    trainer(client, "/claim")
    assert run_dir(run["id"]).exists()
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert not run_dir(run["id"]).exists()
    assert client.get(f"/api/training/runs/{run['id']}").status_code == 404


# ------------------------------------------------------------------- the trainer program itself

FAKE_TRAIN = '''
import argparse, json, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
for name in ("--dataset", "--output", "--settings"):
    p.add_argument(name, type=Path)
a = p.parse_args()
settings = json.loads(a.settings.read_text())
layout = json.loads((a.dataset / "dataset.json").read_text())
print("sets:", ",".join(sorted(layout["sets"])))
if settings["epochs"] == 99:
    sys.exit("out of memory")
for epoch in range(1, settings["epochs"] + 1):
    print("VP_METRIC " + json.dumps({"epoch": epoch, "epochs": settings["epochs"], "train_loss": 1 / epoch}), flush=True)
    time.sleep(settings.get("sleep", 0))
(a.output / "model.pt").write_text(json.dumps({"class_id": layout["classes"][0]["id"]}))
(a.output / "result.json").write_text(json.dumps({"val": {"ap50": 0.5}}))
'''


@pytest.fixture()
def program(project, tmp_path, monkeypatch):
    """trainer/trainer.py wired to the test app, with a recipe that needs no PyTorch."""
    import importlib.util
    from pathlib import Path

    from app.core.config import PROJECT_ROOT, get_settings

    client, pid, ids = project
    split(client, pid, ids)
    spec = importlib.util.spec_from_file_location("vida_trainer", PROJECT_ROOT / "trainer" / "trainer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake = tmp_path / "recipes" / RECIPE
    fake.mkdir(parents=True)
    (fake / "train.py").write_text(FAKE_TRAIN)
    (fake / "predict.py").write_text(FAKE_PREDICT)
    monkeypatch.setattr(module, "RECIPES_DIR", tmp_path / "recipes")
    monkeypatch.setattr(module, "TRAINING_DIR", Path(get_settings().training_dir))
    monkeypatch.setattr(module, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(module, "call", lambda path, payload, timeout=30: trainer(client, path, payload).json())
    # The recipe's settings are checked against recipe.json; let the fake one's extras through.
    monkeypatch.setattr("app.api.training.recipes.resolve_settings", lambda recipe, chosen: {"epochs": 2, **(chosen or {})})
    return client, pid, module


HW = {"device": "cpu", "gpus": []}


def carry_out(client, pid, module, settings, recipe_id=RECIPE):
    run = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": recipe_id, "settings": settings}).json()
    job = trainer(client, "/claim", {"hardware": HW}).json()["run"]
    module.run_recipe(job, HW)
    return client.get(f"/api/training/runs/{run['id']}").json()


def test_the_trainer_runs_a_recipe_and_reports_every_epoch(program):
    client, pid, module = program
    run = carry_out(client, pid, module, {"epochs": 3})
    assert (run["status"], run["epoch"], run["result"], run["has_model"]) == ("done", 3, {"val": {"ap50": 0.5}}, True)
    assert [m["epoch"] for m in run["metrics"]] == [1, 2, 3]
    assert "sets: test,train,val" in client.get(f"/api/training/runs/{run['id']}/log").text
    assert (run_dir(run["id"]) / "code" / "train.py").is_file()  # the code as it was run is kept


def test_a_recipe_that_crashes_fails_the_run_with_its_last_words(program):
    client, pid, module = program
    run = carry_out(client, pid, module, {"epochs": 99})
    assert run["status"] == "failed" and "out of memory" in run["error"]


def test_stop_ends_the_recipe(program):
    client, pid, module = program
    run = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE, "settings": {"epochs": 500, "sleep": 0.2}}).json()
    job = trainer(client, "/claim", {"hardware": HW}).json()["run"]
    client.post(f"/api/training/runs/{run['id']}/stop")
    module.run_recipe(job, HW)  # returns only because the stop reached it
    assert client.get(f"/api/training/runs/{run['id']}").json()["status"] == "stopped"


# ------------------------------------------------------------------- suggestions in the workspace

FAKE_PREDICT = '''
import argparse, json, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--model", type=Path)
p.add_argument("--device")
a = p.parse_args()
cls = json.loads(a.model.read_text())["class_id"]
print("loading...")
print("VP_READY", flush=True)
for line in sys.stdin:
    request = json.loads(line)
    assert all(Path(image).is_file() for image in request["images"])
    scripted = a.model.parent / "answers.json"
    if scripted.is_file():
        found = json.loads(scripted.read_text().replace('"CLS"', str(cls)))
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": [found for _ in request["images"]]}), flush=True)
        continue
    found = [
        {"box": [10, 10, 60, 60], "class_id": cls, "score": 0.95},      # what is already annotated there
        {"box": [200, 200, 260, 260], "class_id": cls, "score": 0.9},
        {"box": [202, 201, 259, 262], "class_id": cls, "score": 0.6},   # the same object again
        {"box": [300, 300, 340, 340], "class_id": cls, "score": 0.3},
        {"box": [400, 400, 440, 440], "class_id": 999999, "score": 0.8},  # a class the project does not have
    ]
    print("VP_RESULT " + json.dumps({"id": request["id"], "results": [found for _ in request["images"]]}), flush=True)
'''


def ask(client, module, patch_id, model_id):
    """POST .../suggest, with the trainer program answering it from another thread."""
    import threading

    def answer():
        job = trainer(client, "/predictions/claim").json()["job"]
        loaded = module.Predictor(job, "cpu")
        try:
            trainer(client, f"/predictions/{job['id']}/result", {"results": loaded.ask(job)})
        finally:
            loaded.close()

    trainer(client, "/hello", {"hardware": HW})
    worker = threading.Thread(target=answer)
    worker.start()
    res = client.post(f"/api/patches/{patch_id}/suggest", json={"model_id": model_id})
    worker.join(timeout=30)
    return res


def test_a_finished_run_becomes_a_model_that_suggests_and_people_decide(program):
    client, pid, module = program
    run = carry_out(client, pid, module, {"epochs": 1})
    models = client.get(f"/api/projects/{pid}/models").json()
    assert [(m["run_id"], m["task"], len(m["classes"])) for m in models] == [(run["id"], "detection", 1)]
    model_id = models[0]["id"]

    slide_id = client.get(f"/api/projects/{pid}/slides").json()[-1]["id"]
    patch = patches(client, slide_id)[0]  # has one box at (10, 10)-(60, 60)
    res = ask(client, module, patch["id"], model_id)
    assert res.status_code == 200, res.text
    found = res.json()
    # Not the box already annotated, not the duplicate, not the unknown class; the surest first.
    assert [(s["score"], s["coordinates_patch_local"][0]) for s in found] == [(0.9, [200.0, 200.0]), (0.3, [300.0, 300.0])]
    assert client.get(f"/api/patches/{patch['id']}/suggestions").json() == found

    # A suggestion is not an annotation: nothing sees it until it is accepted.
    count = lambda: len(client.get(f"/api/patches/{patch['id']}/annotations").json())  # noqa: E731
    assert count() == 1
    accepted = client.post(f"/api/suggestions/{found[0]['id']}/accept").json()
    assert accepted["type"] == "rectangle" and accepted["coordinates_patch_local"] == found[0]["coordinates_patch_local"]
    assert "suggested by" in accepted["created_by"] and count() == 2
    assert client.post(f"/api/suggestions/{found[0]['id']}/accept").status_code == 409  # decided once

    assert client.post(f"/api/suggestions/{found[1]['id']}/reject").status_code == 204
    assert client.get(f"/api/patches/{patch['id']}/suggestions").json() == []
    # Asked again, the model proposes neither the accepted object nor the rejected one.
    assert ask(client, module, patch["id"], model_id).json() == []

    # Another patch: accept everything sure enough at once.
    other = patches(client, slide_id)[1]
    assert len(ask(client, module, other["id"], model_id).json()) == 2
    made = client.post(f"/api/patches/{other['id']}/suggestions/accept", json={"min_score": 0.5}).json()
    assert len(made) == 1 and len(client.get(f"/api/patches/{other['id']}/suggestions").json()) == 1
    assert client.delete(f"/api/patches/{other['id']}/suggestions").status_code == 204
    assert client.get(f"/api/patches/{other['id']}/suggestions").json() == []

    # Deleting the run takes its model, and the model's suggestions, with it; annotations stay.
    assert client.delete(f"/api/training/runs/{run['id']}").status_code == 204
    assert client.get(f"/api/projects/{pid}/models").json() == []
    assert count() == 2


def test_suggesting_needs_the_trainer_and_a_model_of_the_project(program):
    client, pid, module = program
    import app.api.training as training_api

    training_api._trainer["seen"] = 0.0
    slide_id = client.get(f"/api/projects/{pid}/slides").json()[0]["id"]
    patch = patches(client, slide_id)[0]
    assert client.post(f"/api/patches/{patch['id']}/suggest", json={"model_id": 999}).status_code == 404
    run = carry_out(client, pid, module, {"epochs": 1})
    model_id = client.get(f"/api/projects/{pid}/models").json()[0]["id"]
    training_api._trainer["seen"] = 0.0
    assert client.post(f"/api/patches/{patch['id']}/suggest", json={"model_id": model_id}).status_code == 503
    assert run["status"] == "done"


# ------------------------------------------------------------------- models trained elsewhere

FAKE_IMPORTER = '''
import argparse, json, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--model", type=Path)
p.add_argument("--device")
a = p.parse_args()
about = json.loads(a.model.read_text())  # a file that is not JSON cannot be loaded: the import is refused
assert (a.model.parent / "config.json").is_file()
print("VP_READY " + json.dumps({"classes": about.get("classes", []), "class_count": about.get("count")}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    found = [{"box": [200, 200, 260, 260], "class": 0, "score": 0.9}, {"box": [300, 300, 340, 340], "class": 1, "score": 0.8}]
    print("VP_RESULT " + json.dumps({"id": request["id"], "results": [found for _ in request["images"]]}), flush=True)
'''


@pytest.fixture()
def importing(program, tmp_path, monkeypatch):
    """The app offering one importer, for ".bin" files, that needs no PyTorch."""
    client, pid, module = program
    folder = tmp_path / "app_recipes" / "fake_import"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text(json.dumps({"name": "Fake files", "task": "detection", "import": {"extensions": [".bin"]}, "settings": []}))
    (folder / "predict.py").write_text(FAKE_IMPORTER)
    monkeypatch.setattr(recipes, "RECIPES_DIR", tmp_path / "app_recipes")
    return client, pid, module


def with_trainer(client, module, request):
    """Make a request that needs the trainer, with the trainer program answering from another thread."""
    import threading

    def answer():
        job = trainer(client, "/predictions/claim").json()["job"]
        if job is None:
            return
        try:
            loaded = module.Predictor(job, "cpu")
        except Exception as exc:  # noqa: BLE001 - as trainer.py does: tell whoever is waiting
            trainer(client, f"/predictions/{job['id']}/result", {"error": str(exc)})
            return
        try:
            trainer(client, f"/predictions/{job['id']}/result", {"results": loaded.ask(job) if job["images"] else [], "info": loaded.info})
        finally:
            loaded.close()

    trainer(client, "/hello", {"hardware": HW})
    worker = threading.Thread(target=answer)
    worker.start()
    res = request()
    worker.join(timeout=30)
    return res


def upload_model(client, pid, module, content, filename="outside.bin", **form):
    post = lambda: client.post(  # noqa: E731
        f"/api/projects/{pid}/models/import", files={"file": (filename, content, "application/octet-stream")}, data={"importer_id": "fake_import", **form}
    )
    return with_trainer(client, module, post)


def test_a_model_trained_elsewhere_is_added_mapped_and_used(importing):
    client, pid, module = importing
    from app.api.training import training_dir

    assert [(r["id"], r["import"]) for r in client.get("/api/training/importers").json()] == [("fake_import", {"extensions": [".bin"]})]
    assert client.get("/api/training/recipes").json() == []  # an importer is not something to train

    cls = client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"][0]
    about = json.dumps({"classes": [cls["name"].upper(), "Something else"]}).encode()

    # Refused before anything is kept: the wrong kind of file, and a file the importer cannot load.
    assert client.post(f"/api/projects/{pid}/models/import", files={"file": ("m.onnx", about)}, data={"importer_id": "fake_import"}).status_code == 422
    broken = upload_model(client, pid, module, b"not a model")
    assert broken.status_code == 502 and "could not be used" in broken.json()["detail"]
    assert not list((training_dir() / "models").glob("*")) and client.get(f"/api/projects/{pid}/models").json() == []

    added = upload_model(client, pid, module, about, name="From the other lab")
    assert added.status_code == 201, added.text
    model = added.json()
    assert (model["name"], model["source"], model["run_id"]) == ("From the other lab", "import", None)
    assert model["classes"] == [{"index": 0, "name": cls["name"].upper()}, {"index": 1, "name": "Something else"}]
    assert model["class_map"] == {"0": cls["id"], "1": None}  # matched by name; the other is left out

    slide_id = client.get(f"/api/projects/{pid}/slides").json()[0]["id"]
    patch = patches(client, slide_id)[2]  # no annotations here
    suggest = lambda: with_trainer(client, module, lambda: client.post(f"/api/patches/{patch['id']}/suggest", json={"model_id": model["id"]}))  # noqa: E731
    assert [(s["score"], s["class_id"]) for s in suggest().json()] == [(0.9, cls["id"])]

    # Say that the model's second class is that class too: now both are suggested.
    assert client.put(f"/api/models/{model['id']}", json={"class_map": {"1": 999999}}).status_code == 422
    assert client.put(f"/api/models/{model['id']}", json={"class_map": {"7": cls["id"]}}).status_code == 422
    mapped = client.put(f"/api/models/{model['id']}", json={"name": "Renamed", "class_map": {"0": cls["id"], "1": cls["id"]}}).json()
    assert (mapped["name"], mapped["class_map"]) == ("Renamed", {"0": cls["id"], "1": cls["id"]})
    assert [s["score"] for s in suggest().json()] == [0.9, 0.8]

    folder = training_dir() / "models"
    assert len(list(folder.glob("*/model.bin"))) == 1
    assert client.delete(f"/api/models/{model['id']}").status_code == 204
    assert not list(folder.glob("*")) and client.get(f"/api/patches/{patch['id']}/suggestions").json() == []


def test_class_names_are_typed_when_the_file_has_none(importing):
    client, pid, module = importing
    nameless = json.dumps({"count": 2}).encode()
    assert upload_model(client, pid, module, nameless, class_names="only one").status_code == 422  # the model has two
    typed = upload_model(client, pid, module, nameless, class_names="first\nsecond\n").json()
    assert [c["name"] for c in typed["classes"]] == ["first", "second"] and typed["name"] == "outside"
    numbered = upload_model(client, pid, module, nameless).json()
    assert [c["name"] for c in numbered["classes"]] == ["Class 1", "Class 2"]
    assert upload_model(client, pid, module, b"{}").status_code == 422  # neither names nor a count

    import app.api.training as training_api

    training_api._trainer["seen"] = 0.0  # the trainer is off: nothing can open the file
    assert client.post(f"/api/projects/{pid}/models/import", files={"file": ("m.bin", nameless)}, data={"importer_id": "fake_import"}).status_code == 503


# ------------------------------------------------------------------- outlines, labels, whole slides


def trainer_at_work(client, module, until):
    """The trainer program answering prediction requests from a thread until ``until()`` is true."""
    import threading
    import time

    def serve():
        loaded = None
        deadline = time.time() + 60
        while not until() and time.time() < deadline:
            job = trainer(client, "/predictions/claim").json()["job"]
            if job is None:
                continue
            loaded = loaded or module.Predictor(job, "cpu")
            trainer(client, f"/predictions/{job['id']}/result", {"results": loaded.ask(job), "info": loaded.info})
        if loaded:
            loaded.close()

    trainer(client, "/hello", {"hardware": HW})
    worker = threading.Thread(target=serve)
    worker.start()
    return worker


def test_outlines_and_patch_labels_are_suggested_and_accepted(program):
    client, pid, module = program
    run = carry_out(client, pid, module, {"epochs": 1})
    model_id = client.get(f"/api/projects/{pid}/models").json()[0]["id"]
    slide_id = client.get(f"/api/projects/{pid}/slides").json()[0]["id"]
    patch = patches(client, slide_id)[3]  # nothing annotated here
    answers = run_dir(run["id"]) / "output" / "answers.json"

    # A segmenter answers with outlines.
    answers.write_text(json.dumps([
        {"polygon": [[100, 100], [220, 110], [180, 240]], "class_id": "CLS", "score": 0.8},
        {"polygon": [[5, 5], [9999, 5]], "class_id": "CLS", "score": 0.9},  # not an area
    ]))
    found = ask(client, module, patch["id"], model_id).json()
    assert [(s["type"], s["score"], len(s["coordinates_patch_local"])) for s in found] == [("polygon", 0.8, 3)]
    made = client.post(f"/api/suggestions/{found[0]['id']}/accept").json()
    assert made["type"] == "polygon" and made["coordinates_patch_local"] == [[100.0, 100.0], [220.0, 110.0], [180.0, 240.0]]

    # A classifier answers with a class for the whole patch: a suggested Patch Label.
    cls = client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"][0]
    answers.write_text(json.dumps([{"class_id": "CLS", "score": 0.92}, {"class_id": "CLS", "score": 0.4}]))
    found = ask(client, module, patch["id"], model_id).json()
    assert [(s["type"], s["class_id"], s["score"]) for s in found] == [("patch_label", cls["id"], 0.92)]
    assert client.get(f"/api/patches/{patch['id']}").json()["patch_label"] is None  # not until accepted
    fill = client.post(f"/api/suggestions/{found[0]['id']}/accept").json()
    assert fill["whole_patch"] and client.get(f"/api/patches/{patch['id']}").json()["patch_label"] == cls["name"]
    assert ask(client, module, patch["id"], model_id).json() == []  # it has that label now

    # Refusing a label keeps it from being proposed there again.
    other = patches(client, slide_id)[4]
    label = ask(client, module, other["id"], model_id).json()[0]
    assert client.post(f"/api/suggestions/{label['id']}/reject").status_code == 204
    assert ask(client, module, other["id"], model_id).json() == []


def test_a_model_works_through_a_whole_slide(program):
    import time

    client, pid, module = program
    carry_out(client, pid, module, {"epochs": 1})
    model_id = client.get(f"/api/projects/{pid}/models").json()[0]["id"]
    slide_id = client.get(f"/api/projects/{pid}/slides").json()[0]["id"]
    state = lambda: client.get(f"/api/slides/{slide_id}/suggest").json()  # noqa: E731
    assert state() == {"job": None, "pending": 0, "patches": 0}

    finished = lambda: (state()["job"] or {}).get("status") in ("done", "failed", "stopped")  # noqa: E731
    worker = trainer_at_work(client, module, finished)
    started = client.post(f"/api/slides/{slide_id}/suggest", json={"model_id": model_id})
    assert started.status_code == 200, started.text
    while not finished():
        time.sleep(0.1)
    worker.join(timeout=30)

    # Six patches, two already annotated: the other four were looked at, three suggestions kept in each.
    done = state()
    assert done["job"] == {"model_id": model_id, "status": "done", "done": 4, "total": 4, "found": 12, "error": None}
    assert (done["pending"], done["patches"]) == (12, 4)
    everything = client.get(f"/api/slides/{slide_id}/suggestions").json()
    assert len(everything) == 12 and all(len(s["coordinates_level0"]) == 4 for s in everything)

    # Reviewing patch by patch: the next patch with suggestions, wrapping round.
    indexes = {p["id"]: p["patch_index"] for p in patches(client, slide_id)}
    first = client.get(f"/api/slides/{slide_id}/suggestions/next").json()["patch_id"]
    second = client.get(f"/api/slides/{slide_id}/suggestions/next", params={"after": indexes[first]}).json()["patch_id"]
    assert indexes[second] > indexes[first]
    assert client.get(f"/api/slides/{slide_id}/suggestions/next", params={"after": 10**6}).json()["patch_id"] == first

    accepted = client.post(f"/api/slides/{slide_id}/suggestions/accept", json={"min_score": 0.5}).json()
    assert (accepted["accepted"], accepted["pending"]) == (8, 4)
    assert client.delete(f"/api/slides/{slide_id}/suggestions").json()["pending"] == 0
    assert client.get(f"/api/slides/{slide_id}/suggestions/next").json()["patch_id"] is None

    # Nothing left unannotated to look at now.
    assert client.post(f"/api/slides/{slide_id}/suggest", json={"model_id": model_id}).status_code == 422


# ------------------------------------------------------------------- classification


def test_a_classifier_learns_from_patch_labels(env, monkeypatch):  # noqa: F811
    client, pid, settings = env
    monkeypatch.setattr("app.api.processing.get_settings", lambda: settings)
    ids = [s["id"] for s in upload(client, pid, [("a.tif", SLIDE), ("b.tif", SLIDE), ("c.tif", SLIDE)]).json()["slides"]]
    config = client.get(f"/api/projects/{pid}/config").json()
    classes = [{"name": "Tumor", "color_hex": "#dc2626"}, {"name": "Stroma", "color_hex": "#16a34a"}]
    assert client.put(f"/api/configs/{config['id']}", json={"annotation_classes": classes}).status_code == 200
    for sid in ids:
        generate(client, sid, config["id"], BIG)
        for patch, label in zip(patches(client, sid), ("Tumor", "Stroma", "Tumor", "Artifact / Background")):
            assert client.put(f"/api/patches/{patch['id']}", json={"patch_label": label}).status_code == 200
    split(client, pid, ids)

    ready = client.get(f"/api/projects/{pid}/training/readiness", params={"task": "classification"}).json()
    assert ready["problems"] == [] and [c["name"] for c in ready["classes"]] == ["Tumor", "Stroma"]
    assert {name: (s["images"], sorted(s["per_class"].values())) for name, s in ready["sets"].items()} == {
        "train": (3, [1, 2]), "val": (3, [1, 2]), "test": (3, [1, 2]),  # the label that is not a class is left out
    }

    run = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": "classification_resnet", "settings": {"epochs": 1}}).json()
    assert run["task"] == "classification"
    job = trainer(client, "/claim", {"hardware": HW}).json()["run"]
    folder = run_dir(job["id"]) / "dataset"
    layout = json.loads((folder / "dataset.json").read_text())
    assert layout["format"] == "folders" and [c["name"] for c in layout["classes"]] == ["Tumor", "Stroma"]
    import csv

    rows = list(csv.DictReader((folder / layout["sets"]["train"]["labels"]).open()))
    assert sorted(r["class"] for r in rows) == ["Stroma", "Tumor", "Tumor"]
    assert all((folder / layout["sets"]["train"]["images"] / r["file"]).is_file() for r in rows)
    assert client.get(f"/api/training/runs/{run['id']}").json()["dataset"]["sets"]["train"]["images"] == 3


def test_what_is_annotated_on_the_whole_slide_is_not_suggested_again(program):
    client, pid, module = program
    carry_out(client, pid, module, {"epochs": 1})
    model_id = client.get(f"/api/projects/{pid}/models").json()[0]["id"]
    slide_id = client.get(f"/api/projects/{pid}/slides").json()[0]["id"]
    patch = patches(client, slide_id)[5]
    scale = patch["width_l0"] / patch["width"]
    # Drawn on the slide, exactly where the model finds its 0.9 box in this patch ((200, 200)-(260, 260)).
    x0, y0, x1, y1 = (patch["x"] + 200 * scale, patch["y"] + 200 * scale, patch["x"] + 260 * scale, patch["y"] + 260 * scale)
    cls = client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"][0]["id"]
    drawn = client.post(f"/api/slides/{slide_id}/annotations", json={"type": "rectangle", "class_id": cls, "coordinates_level0": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]})
    assert drawn.status_code == 201, drawn.text
    found = ask(client, module, patch["id"], model_id).json()
    assert [s["score"] for s in found] == [0.95, 0.3]  # not the 0.9 one


# ------------------------------------------------------------------- how the dataset is cut


def test_dataset_options_choose_the_patches_a_run_learns_from(project):
    client, pid, ids = project
    split(client, pid, ids)
    ready = lambda **params: client.get(f"/api/projects/{pid}/training/readiness", params=params)  # noqa: E731
    train = lambda **params: ready(**params).json()["sets"]["train"]  # noqa: E731

    # Six patches a slide, two of them annotated. Empty ones are added in proportion, as far as there are any.
    assert (train()["images"], train()["empty"]) == (2, 0)
    assert train(empty_percent=100)["empty"] == 2
    assert train(empty_percent=50)["empty"] == 1
    assert train(empty_percent=5000)["empty"] == 4

    # Only confirmed-empty patches: none is reviewed yet, then one is.
    none_yet = ready(empty_percent=100, empty_from="reviewed").json()
    assert none_yet["sets"]["train"]["empty"] == 0 and any("No empty patches" in w for w in none_yet["warnings"])
    bare = patches(client, ids[0])[4]
    assert client.put(f"/api/patches/{bare['id']}", json={"status": "reviewed"}).status_code == 200
    assert train(empty_percent=100, empty_from="reviewed")["empty"] == 1

    # Only reviewed annotated patches: none of those is, so there is nothing to learn from.
    assert ready(use="reviewed").json()["problems"]

    # Only some classes: one the project's shapes do not have leaves nothing.
    assert train(classes=str(ready().json()["classes"][0]["id"]))["objects"] == 2
    assert ready(classes="999999").json()["problems"]

    # Cut afresh at another size: each box lands in one smaller patch, and the whole slide offers more empty ones.
    small = train(patch_size=256, empty_percent=100000)
    assert small["images"] == 2 and small["empty"] > 4
    assert train(patch_size=256, stride=128)["images"] > 2  # overlapping patches see the same box more than once
    assert ready(patch_size=256, area="whole").json()["problems"] == []
    assert ready(patch_size=8).status_code == 422
    assert ready(patch_size=256, use="reviewed").status_code == 422  # a fresh cut has no Reviewed marks


def test_a_run_keeps_its_dataset_options_and_is_cut_with_them(project):
    client, pid, ids = project
    split(client, pid, ids)
    start = lambda dataset: client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE, "dataset": dataset})  # noqa: E731
    assert start({"patch_size": 256, "use": "reviewed"}).status_code == 422
    assert start({"nonsense": 1}).status_code == 422

    run = start({"empty_percent": 100}).json()
    assert run["dataset_options"]["empty_percent"] == 100 and run["dataset_options"]["patch_size"] is None
    job = trainer(client, "/claim").json()["run"]
    folder = run_dir(job["id"]) / "dataset"
    layout = json.loads((folder / "dataset.json").read_text())
    doc = json.loads((folder / layout["sets"]["train"]["annotations"]).read_text())
    assert len(doc["images"]) == 4 and len({a["image_id"] for a in doc["annotations"]}) == 2  # two with boxes, two empty
    assert all((folder / layout["sets"]["train"]["images"] / image["file_name"]).is_file() for image in doc["images"])
    kept = client.get(f"/api/training/runs/{run['id']}").json()["dataset"]
    assert kept["sets"]["train"]["empty"] == 2 and kept["options"]["empty_percent"] == 100
