"""Model recipes: what the trainer can train and run.

A recipe is a folder::

    recipe.json        its name, task, and the settings a person may choose
    train.py           learns from a dataset and writes the trained model
    predict.py         uses a trained model on new images
                       (a recipe with only this one runs models trained elsewhere: an "importer",
                       whose recipe.json says which files it takes: "import": {"extensions": [...]})
    requirements.txt   extra packages, if any

There are two kinds:

* **built-in** recipes ship with the app (``trainer/recipes``) and are never changed, so an update can
  improve them and there is always a version known to work;
* **your own** recipes live in the training folder (``<training dir>/recipes``), which the app shares
  with the trainer: copies of built-in ones, ones started from the template, ones uploaded. Their code
  is edited in the app. One can be trained with only once the trainer has *checked* it -- run it for
  one epoch on a few made-up images -- in exactly the state it is in now.

The app reads and writes these files but never runs them: only the trainer does.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings

RECIPES_DIR = PROJECT_ROOT / "trainer" / "recipes"
TEMPLATE_DIR = PROJECT_ROOT / "trainer" / "templates" / "blank"
TASKS = ("detection", "classification", "segmentation")

# What a recipe may hold: a flat folder of text files.
EDITABLE = (".py", ".json", ".txt", ".md", ".yaml", ".yml", ".cfg", ".toml", ".ini")
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 60
MAX_VERSIONS = 30  # earlier versions kept of each file
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$")


class RecipeError(ValueError):
    """Something a person asked of a recipe that cannot be done; the message is for them."""


def user_dir() -> Path:
    path = get_settings().training_dir / "recipes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recipe_path(recipe_id: str) -> Path | None:
    """The folder of a recipe, built-in or your own; None if there is none of that id."""
    if not _ID.match(recipe_id or ""):
        return None
    for root in (RECIPES_DIR, user_dir()):
        if (root / recipe_id / "recipe.json").is_file():
            return root / recipe_id
    return None


def is_builtin(path: Path) -> bool:
    return path.parent == RECIPES_DIR


def file_names(path: Path) -> list[str]:
    """The recipe's files, the ones every recipe has first."""
    first = ["recipe.json", "train.py", "predict.py", "requirements.txt"]
    names = [p.name for p in path.iterdir() if p.is_file() and _FILE.match(p.name) and p.suffix.lower() in EDITABLE]
    return sorted(names, key=lambda n: (first.index(n) if n in first else len(first), n.lower()))


def code_hash(path: Path) -> str:
    """Changes whenever any file of the recipe does: what a check is a check *of*."""
    digest = hashlib.sha256()
    for name in sorted(file_names(path)):
        digest.update(name.encode() + b"\0" + (path / name).read_bytes() + b"\0")
    return digest.hexdigest()[:20]


def requirements(path: Path) -> list[str]:
    file = path / "requirements.txt"
    if not file.is_file():
        return []
    return [line.strip() for line in file.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.strip().startswith("#")]


def packages_key(wanted: list[str]) -> str:
    """Names the environment holding these packages; trainer/trainer.py works it out the same way."""
    return hashlib.sha256("\n".join(sorted(wanted)).encode()).hexdigest()[:16]


def check_of(path: Path) -> dict | None:
    """The recipe's last check: {"status": queued | running | passed | failed | stale, "report", "at"}.
    "stale": it was checked, but the code has changed since. None: never checked (or built-in)."""
    file = path / ".check.json"
    if is_builtin(path) or not file.is_file():
        return None
    try:
        state = json.loads(file.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if state.get("hash") != code_hash(path):
        return {"status": "stale", "at": state.get("at")}
    return state


def set_check(path: Path, status: str, report: dict | None = None) -> dict:
    state = {"status": status, "hash": code_hash(path), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "report": report}
    (path / ".check.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _describe(path: Path) -> dict | None:
    try:
        doc = json.loads((path / "recipe.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    builtin = is_builtin(path)
    trainable = (path / "train.py").is_file()
    problem = None
    if not isinstance(doc, dict):
        problem, doc = "recipe.json is not valid JSON.", {}
    elif doc.get("task") not in TASKS:
        problem = f"recipe.json's \"task\" must be one of: {', '.join(TASKS)}."
    elif not isinstance(doc.get("settings", []), list):
        problem = "recipe.json's \"settings\" must be a list."
    importer = doc.get("import") if (path / "predict.py").is_file() and isinstance(doc.get("import"), dict) else None
    if problem is None and not (trainable or importer):
        problem = "It has neither train.py nor an importer's predict.py."
    if problem and builtin:
        return None  # a broken built-in recipe must not hide the others
    check = check_of(path)
    wanted = requirements(path)
    return {
        "id": path.name,
        "name": str(doc.get("name") or path.name),
        "task": doc.get("task") if doc.get("task") in TASKS else "detection",
        "description": str(doc.get("description", "")),
        "builtin": builtin,
        "trainable": trainable and problem is None,
        "import": importer if problem is None else None,
        "settings": doc.get("settings", []) if problem is None else [],
        # Settings a check runs with instead of the defaults (small and quick).
        "check_settings": doc.get("check_settings") if isinstance(doc.get("check_settings"), dict) else {},
        "problem": problem,  # why it cannot be used at all, in words
        "check": check,
        # Built-in recipes are known to work; your own must have passed the check as they are now.
        "usable": problem is None and (builtin or (check or {}).get("status") == "passed"),
        "files": file_names(path),
        "has_predict": (path / "predict.py").is_file(),
        "requirements": wanted,
        "packages_key": packages_key(wanted) if wanted else None,
        # Where the trainer finds it in the folder it shares with the app; None: among its own.
        "folder": None if builtin else f"recipes/{path.name}",
    }


def list_recipes(root: Path | None = None) -> list[dict]:
    roots = [root] if root else [RECIPES_DIR, user_dir()]
    found = [_describe(p.parent) for r in roots for p in sorted(r.glob("*/recipe.json")) if _ID.match(p.parent.name)]
    return [r for r in found if r is not None]


def get_recipe(recipe_id: str, root: Path | None = None) -> dict | None:
    if root is not None:
        return next((r for r in list_recipes(root) if r["id"] == recipe_id), None)
    path = recipe_path(recipe_id)
    return _describe(path) if path else None


# ---------------------------------------------------------------------------- your own recipes


def _editable(recipe_id: str) -> Path:
    path = recipe_path(recipe_id)
    if path is None:
        raise RecipeError(f"There is no model recipe \"{recipe_id}\".")
    if is_builtin(path):
        raise RecipeError("Built-in recipes cannot be changed. Duplicate it and change the copy.")
    return path


def _new_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:50] or "recipe"
    candidate, n = base, 2
    while (RECIPES_DIR / candidate).exists() or (user_dir() / candidate).exists():
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def _rename(path: Path, name: str, task: str | None = None) -> None:
    file = path / "recipe.json"
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # left as it is; the recipe says what is wrong with it
    doc["name"] = name
    if task:
        doc["task"] = task
    file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def create(name: str, task: str | None = None, source_id: str | None = None) -> str:
    """A new recipe of your own: a copy of ``source_id``, or the blank template."""
    name = name.strip()
    if not name:
        raise RecipeError("Give the recipe a name.")
    if task is not None and task not in TASKS:
        raise RecipeError(f"The task must be one of: {', '.join(TASKS)}.")
    source = recipe_path(source_id) if source_id else TEMPLATE_DIR
    if source is None or not source.is_dir():
        raise RecipeError(f"There is no model recipe \"{source_id}\".")
    target = user_dir() / _new_id(name)
    target.mkdir()
    for file_name in file_names(source):
        shutil.copyfile(source / file_name, target / file_name)
    _rename(target, name, task)
    return target.name


def create_from_zip(data: bytes, name: str) -> str:
    """A new recipe from an uploaded ZIP: its files, taken from the folder that holds recipe.json."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RecipeError("That is not a ZIP file.") from None
    entries = [e for e in archive.infolist() if not e.is_dir() and "__pycache__" not in e.filename and "/." not in f"/{e.filename}"]
    roots = sorted({e.filename.rpartition("/")[0] for e in entries if e.filename.rpartition("/")[2] == "recipe.json"}, key=len)
    if not roots:
        raise RecipeError("The ZIP has no recipe.json. See trainer/recipes/README.md for what a recipe holds.")
    prefix = f"{roots[0]}/" if roots[0] else ""
    wanted = [e for e in entries if e.filename.startswith(prefix) and "/" not in e.filename[len(prefix):]]
    if len(wanted) > MAX_FILES:
        raise RecipeError(f"A recipe holds at most {MAX_FILES} files.")
    files: dict[str, bytes] = {}
    for entry in wanted:
        file_name = entry.filename[len(prefix):]
        if not _FILE.match(file_name) or Path(file_name).suffix.lower() not in EDITABLE:
            raise RecipeError(f"\"{file_name}\" cannot be part of a recipe: only text files ({', '.join(EDITABLE)}) with simple names.")
        if entry.file_size > MAX_FILE_BYTES:
            raise RecipeError(f"\"{file_name}\" is larger than 1 MB.")
        files[file_name] = archive.read(entry)
    try:
        doc = json.loads(files["recipe.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RecipeError("The ZIP's recipe.json is not valid JSON.") from None
    name = name.strip() or str(doc.get("name") if isinstance(doc, dict) else "") or "Uploaded recipe"
    target = user_dir() / _new_id(name)
    target.mkdir()
    for file_name, content in files.items():
        (target / file_name).write_bytes(content)
    _rename(target, name)
    return target.name


def as_zip(recipe_id: str) -> bytes:
    path = recipe_path(recipe_id)
    if path is None:
        raise RecipeError(f"There is no model recipe \"{recipe_id}\".")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_name in file_names(path):
            archive.write(path / file_name, f"{recipe_id}/{file_name}")
    return buffer.getvalue()


def delete(recipe_id: str) -> None:
    shutil.rmtree(_editable(recipe_id))


def _file(path: Path, file_name: str) -> Path:
    if not _FILE.match(file_name or "") or Path(file_name).suffix.lower() not in EDITABLE:
        raise RecipeError(f"\"{file_name}\" cannot be a recipe file: use a simple name ending in one of {', '.join(EDITABLE)}.")
    return path / file_name


def read_file(recipe_id: str, file_name: str) -> str:
    path = recipe_path(recipe_id)
    if path is None:
        raise RecipeError(f"There is no model recipe \"{recipe_id}\".")
    file = _file(path, file_name)
    if not file.is_file():
        raise RecipeError(f"The recipe has no file \"{file_name}\".")
    return file.read_text(encoding="utf-8", errors="replace")


def write_file(recipe_id: str, file_name: str, content: str) -> None:
    """Save a file of one of your own recipes, keeping what it held before as an earlier version."""
    path = _editable(recipe_id)
    file = _file(path, file_name)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise RecipeError("A recipe file can be at most 1 MB.")
    if not file.is_file() and len(file_names(path)) >= MAX_FILES:
        raise RecipeError(f"A recipe holds at most {MAX_FILES} files.")
    content = content.replace("\r\n", "\n")
    if file.is_file():
        before = file.read_text(encoding="utf-8", errors="replace")
        if before == content:
            return
        kept = path / ".history" / file_name
        kept.mkdir(parents=True, exist_ok=True)
        (kept / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.txt").write_text(before, encoding="utf-8", newline="\n")
        for old in sorted(kept.glob("*.txt"))[:-MAX_VERSIONS]:
            old.unlink()
    file.write_text(content, encoding="utf-8", newline="\n")


def delete_file(recipe_id: str, file_name: str) -> None:
    path = _editable(recipe_id)
    if file_name == "recipe.json":
        raise RecipeError("A recipe cannot be without its recipe.json.")
    file = _file(path, file_name)
    if not file.is_file():
        raise RecipeError(f"The recipe has no file \"{file_name}\".")
    write_file(recipe_id, file_name, "")  # so that what it held can still be brought back
    file.unlink()


def versions(recipe_id: str, file_name: str) -> list[dict]:
    """Earlier versions of a file, newest first: [{"id", "saved_at"}]."""
    path = recipe_path(recipe_id)
    if path is None or is_builtin(path):
        return []
    _file(path, file_name)
    out = []
    for kept in sorted((path / ".history" / file_name).glob("*.txt"), reverse=True):
        try:
            saved = datetime.strptime(kept.stem, "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append({"id": kept.stem, "saved_at": saved.isoformat(timespec="seconds")})
    return out


def read_version(recipe_id: str, file_name: str, version_id: str) -> str:
    path = recipe_path(recipe_id)
    if path is None or not re.fullmatch(r"[0-9T]{10,30}", version_id or ""):
        raise RecipeError("There is no such earlier version.")
    _file(path, file_name)
    kept = path / ".history" / file_name / f"{version_id}.txt"
    if not kept.is_file():
        raise RecipeError("There is no such earlier version.")
    return kept.read_text(encoding="utf-8", errors="replace")


def resolve_settings(recipe: dict, chosen: dict | None) -> dict:
    """The recipe's settings with the chosen values filled in, checked against each setting's type and
    limits. Raises ValueError naming the setting that is wrong; unknown keys are refused."""
    chosen = dict(chosen or {})
    out: dict = {}
    for spec in recipe["settings"]:
        key, kind = spec["key"], spec.get("type", "int")
        label = spec.get("label", key)
        value = chosen.pop(key, spec.get("default"))
        if kind == "choice":
            allowed = [c["value"] if isinstance(c, dict) else c for c in spec.get("choices", [])]
            if value not in allowed:
                raise ValueError(f"{label} must be one of: {', '.join(map(str, allowed))}")
        elif kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{label} must be on or off")
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be a number") from None
            if kind == "int":
                if not value.is_integer():
                    raise ValueError(f"{label} must be a whole number")
                value = int(value)
            low, high = spec.get("min"), spec.get("max")
            if (low is not None and value < low) or (high is not None and value > high):
                raise ValueError(f"{label} must be between {low} and {high}")
        out[key] = value
    if chosen:
        raise ValueError(f"Unknown settings: {', '.join(sorted(chosen))}")
    return out


def check_settings(recipe: dict) -> dict:
    """What a check trains with: the defaults, one epoch, and whatever the recipe asks for to keep it
    quick. A recipe whose own check settings are wrong is checked with the plain defaults."""
    try:
        settings = resolve_settings(recipe, recipe.get("check_settings") or {})
    except ValueError:
        settings = resolve_settings(recipe, {})
    if "epochs" in settings and "epochs" not in (recipe.get("check_settings") or {}):
        settings["epochs"] = 1
    return settings
