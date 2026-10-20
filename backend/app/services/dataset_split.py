"""Train / validation / test split of a project's slides (or, in an image project, its images).

A whole slide belongs to one set: patches of the same tissue must never sit on both sides of a
split, or a model is tested on what it was trained on. The project keeps how the split is made
(``Project.split_config``); each slide keeps the set it is in (``Slide.split``).

* ``random`` -- slides are dealt out by chance to match the percentages. The deal is kept: slides
  added later join the set furthest below its share, and nothing already assigned moves until a
  reshuffle is asked for (or the percentages change).
* ``manual`` -- the person assigns each slide; nothing is assigned for them.

Split by patch (``"unit": "patch"``, random only) is for a project with too few slides to split --
often a single one: each slide's patches are dealt out by the shares instead (``deal_patches``),
so every slide is in every set. Patches of the same tissue then sit on both sides, which the
person chooses knowingly. Nothing is stored per patch: the deal is made afresh, the same way every
time, whenever a dataset is cut.
"""
from __future__ import annotations

import hashlib
import math
import random

from app.models.project import Project
from app.models.slide import Slide

SPLITS = ("train", "val", "test")
UNASSIGNED = "unassigned"
MODES = ("off", "random", "manual")
UNITS = ("slide", "patch")
DEFAULT_SHARES = {"train": 70, "val": 15, "test": 15}


def make_config(
    mode: str, train: int, val: int, test: int, seed: int | None = None, unit: str = "slide", image_project: bool = False
) -> dict | None:
    """The stored form of a split; None for "off". Raises ValueError on shares that do not add up."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    if unit not in UNITS:
        raise ValueError(f"unit must be one of: {', '.join(UNITS)}")
    if mode == "off":
        return None
    if unit == "patch" and image_project:
        raise ValueError("An image project is split image by image; it has no patches to split")
    if unit == "patch" and mode != "random":
        raise ValueError("Patches are split at random by the shares; only slides can be assigned by hand")
    shares = {"train": train, "val": val, "test": test}
    if any(v < 0 for v in shares.values()) or sum(shares.values()) != 100:
        raise ValueError("The train, validation and test shares must add up to 100%")
    if mode == "random" and train == 0:
        raise ValueError("The training set needs a share above 0%")
    return {"mode": mode, "unit": unit, **shares, "seed": seed if seed is not None else random.randrange(1, 2**31)}


def mode_of(project: Project) -> str:
    return (project.split_config or {}).get("mode", "off")


def unit_of(config: dict | None) -> str:
    """What a split deals out: whole "slide"s, or each slide's "patch"es. Splits from before the choice are by slide."""
    return (config or {}).get("unit", "slide") if (config or {}).get("mode") == "random" else "slide"


def deal_patches(slide_id: int, patch_ids: list[int], config: dict, pool: str = "") -> dict[int, str]:
    """Split by patch: the set of each of one slide's patches, matching the shares as closely as the
    count allows. The same patches always get the same sets for the same seed. Where the shares do
    not divide the count, the patches left over go to a set picked at random, weighted by what each
    set is short of -- so a slide of 3 patches still sends one to validation or test now and then.
    ``pool`` deals separate groups of a slide (annotated, empty) on their own, each by the shares."""
    seed = config["seed"]
    rank = lambda pid: hashlib.sha1(f"{seed}:{slide_id}:{pool}:{pid}".encode()).hexdigest()  # noqa: E731
    ordered = sorted(patch_ids, key=rank)
    n = len(ordered)
    exact = {name: config[name] / 100 * n for name in SPLITS}
    counts = {name: math.floor(exact[name]) for name in SPLITS}
    rng = random.Random(f"{seed}:{slide_id}:{pool}:{n}")
    short = {name: exact[name] - counts[name] for name in SPLITS if exact[name] > counts[name]}
    # Weighted picks without repeats (each set takes at most one of the patches left over).
    picked = sorted(short, key=lambda name: rng.random() ** (1 / short[name]), reverse=True)
    for name in picked[: n - sum(counts.values())]:
        counts[name] += 1
    sets: dict[int, str] = {}
    start = 0
    for name in SPLITS:
        for pid in ordered[start : start + counts[name]]:
            sets[pid] = name
        start += counts[name]
    return sets


def counts(slides: list[Slide]) -> dict[str, int]:
    out = {name: 0 for name in (*SPLITS, UNASSIGNED)}
    for slide in slides:
        out[slide.split if slide.split in SPLITS else UNASSIGNED] += 1
    return out


def _dealt(slide_ids: list[int], assigned: dict[str, int], config: dict, rng: random.Random) -> dict[int, str]:
    """The set of each waiting slide: each goes to the set furthest below its share, in a random order."""
    waiting = sorted(slide_ids)
    rng.shuffle(waiting)
    sets: dict[int, str] = {}
    for slide_id in waiting:
        total = sum(assigned.values()) + 1
        open_sets = [name for name in SPLITS if config[name] > 0]
        sets[slide_id] = max(open_sets, key=lambda name: config[name] / 100 * total - assigned[name])
        assigned[sets[slide_id]] += 1
    return sets


def _deal(waiting: list[Slide], assigned: dict[str, int], config: dict, rng: random.Random) -> None:
    sets = _dealt([s.id for s in waiting], assigned, config, rng)
    for slide in waiting:
        slide.split = sets[slide.id]


def deal(slide_ids: list[int], config: dict) -> dict[int, str]:
    """A random split of these slides by ``config``'s shares and seed, without touching any slide: a
    split used once (a training run's own), where the project's stays as it is."""
    return _dealt(slide_ids, {name: 0 for name in SPLITS}, config, random.Random(config["seed"]))


def reshuffle(project: Project, slides: list[Slide]) -> None:
    """Deal every slide out again, under a new seed."""
    config = {**project.split_config, "seed": random.randrange(1, 2**31)}
    project.split_config = config
    if unit_of(config) == "patch":  # the patches are dealt by the new seed when a dataset is cut
        return
    _deal(slides, {name: 0 for name in SPLITS}, config, random.Random(config["seed"]))


def fill(project: Project, slides: list[Slide]) -> bool:
    """Random mode: assign the slides that have no set yet (added since the last deal). True if any were."""
    config = project.split_config
    if not config or config.get("mode") != "random" or unit_of(config) == "patch":
        return False
    waiting = [s for s in slides if s.split not in SPLITS]
    if not waiting:
        return False
    assigned = {name: sum(1 for s in slides if s.split == name) for name in SPLITS}
    _deal(waiting, assigned, config, random.Random(f"{config['seed']}:{sum(assigned.values())}"))
    return True
