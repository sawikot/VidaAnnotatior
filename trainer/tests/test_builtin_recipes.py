"""Every built-in recipe that trains is put through the same check as a recipe of your own: one epoch
on made-up images, then loaded and asked about one. Run in the trainer's environment (it needs PyTorch):

    trainer/.venv/Scripts/python -m pytest trainer/tests
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trainer  # noqa: E402

RECIPES = Path(__file__).resolve().parents[1] / "recipes"
TRAINABLE = sorted(p.parent.name for p in RECIPES.glob("*/train.py"))


@pytest.mark.parametrize("recipe_id", TRAINABLE)
def test_a_built_in_recipe_passes_its_own_check(recipe_id, tmp_path, monkeypatch):
    monkeypatch.setattr(trainer, "TRAINING_DIR", tmp_path)
    shutil.copytree(RECIPES / recipe_id, tmp_path / "recipes" / recipe_id)
    doc = json.loads((RECIPES / recipe_id / "recipe.json").read_text(encoding="utf-8"))
    settings = {s["key"]: s["default"] for s in doc["settings"]} | doc.get("check_settings", {})
    sent = {}
    monkeypatch.setattr(trainer, "call", lambda path, payload, timeout=30: sent.update(payload) or {})

    hardware = trainer.hardware()
    trainer.run_check({"id": recipe_id, "folder": f"recipes/{recipe_id}", "hash": "x", "trainable": True, "has_predict": True, "settings": settings, "task": doc["task"]}, hardware)

    steps = sent["report"]["steps"]
    assert sent["ok"], "\n".join(f"{s['name']}: {s['detail']}" for s in steps if not s["ok"]) + "\n" + sent["report"]["log"]
    assert [s["name"].split()[0] for s in steps] == ["Run", "Report", "Write", "Write", "Load", "Suggest"]
    assert not (tmp_path / "checks" / recipe_id).exists()  # nothing of the check is left behind
