"""Every network of every built-in recipe is put through the same check as a recipe of your own: one
epoch on made-up images, then loaded and asked about one. Run in the trainer's environment (it needs PyTorch):

    trainer/.venv/Scripts/python -m pytest trainer/tests

The networks are built without their pretrained weights here (VP_NO_PRETRAINED), so nothing is
downloaded: this shows that each one builds, trains, saves, loads and answers -- not how well it learns.
Recipes that need extra packages (the Ultralytics ones) have those installed once into
data/training/envs, which needs internet the first time; of each such family only the smallest
network is tried, the others differing in size alone.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trainer  # noqa: E402

TRAINER = Path(__file__).resolve().parents[1]
RECIPES = TRAINER / "recipes"


def networks():
    for file in sorted(RECIPES.glob("*/train.py")):
        doc = json.loads((file.parent / "recipe.json").read_text(encoding="utf-8"))
        pick = next((s for s in doc["settings"] if s["key"] == "architecture"), None)
        choices = pick["choices"] if pick else [{"value": None}]
        if (file.parent / "requirements.txt").is_file():
            choices = choices[:1]
        for choice in choices:
            yield pytest.param(file.parent.name, choice["value"], id=f"{file.parent.name}:{choice['value']}")


def test_the_built_in_recipes_are_what_the_generator_writes():
    done = subprocess.run([sys.executable, str(TRAINER / "tools" / "sync_recipes.py"), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, "Run trainer/tools/sync_recipes.py:\n" + done.stdout


@pytest.mark.parametrize("recipe_id, architecture", networks())
def test_a_built_in_network_passes_the_check(recipe_id, architecture, tmp_path, monkeypatch):
    monkeypatch.setattr(trainer, "TRAINING_DIR", tmp_path)
    monkeypatch.setenv("VP_NO_PRETRAINED", "1")
    monkeypatch.setenv("ENVS_DIR", str(TRAINER.parent / "data" / "training" / "envs"))  # installed once, not per test
    shutil.copytree(RECIPES / recipe_id, tmp_path / "recipes" / recipe_id)
    doc = json.loads((RECIPES / recipe_id / "recipe.json").read_text(encoding="utf-8"))
    settings = {s["key"]: s["default"] for s in doc["settings"]} | doc.get("check_settings", {})
    if architecture:
        settings["architecture"] = architecture
    sent = {}
    monkeypatch.setattr(trainer, "call", lambda path, payload, timeout=30: sent.update(payload) or {})

    job = {"id": recipe_id, "folder": f"recipes/{recipe_id}", "hash": "x", "trainable": True, "has_predict": True, "settings": settings, "task": doc["task"]}
    trainer.run_check(job, trainer.hardware())

    steps = sent["report"]["steps"]
    assert sent["ok"], "\n".join(f"{s['name']}: {s['detail']}" for s in steps if not s["ok"]) + "\n" + sent["report"]["log"]
    assert [s["name"].split()[0] for s in steps if not s["name"].startswith("Install")] == ["Run", "Report", "Write", "Write", "Load", "Suggest"]
    assert not (tmp_path / "checks" / recipe_id).exists()  # nothing of the check is left behind
