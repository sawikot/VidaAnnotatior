"""Train / validation / test split of a project's slides (or, in an image project, its images).

A whole slide belongs to one set: patches of the same tissue must never sit on both sides of a
split, or a model is tested on what it was trained on. The project keeps how the split is made
(``Project.split_config``); each slide keeps the set it is in (``Slide.split``).

* ``random`` -- slides are dealt out by chance to match the percentages. The deal is kept: slides
  added later join the set furthest below its share, and nothing already assigned moves until a
  reshuffle is asked for (or the percentages change).
* ``manual`` -- the person assigns each slide; nothing is assigned for them.
"""
from __future__ import annotations

import random

from app.models.project import Project
from app.models.slide import Slide

SPLITS = ("train", "val", "test")
UNASSIGNED = "unassigned"
MODES = ("off", "random", "manual")
DEFAULT_SHARES = {"train": 70, "val": 15, "test": 15}


def make_config(mode: str, train: int, val: int, test: int, seed: int | None = None) -> dict | None:
    """The stored form of a split; None for "off". Raises ValueError on shares that do not add up."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    if mode == "off":
        return None
    shares = {"train": train, "val": val, "test": test}
    if any(v < 0 for v in shares.values()) or sum(shares.values()) != 100:
        raise ValueError("The train, validation and test shares must add up to 100%")
    if mode == "random" and train == 0:
        raise ValueError("The training set needs a share above 0%")
    return {"mode": mode, **shares, "seed": seed if seed is not None else random.randrange(1, 2**31)}


def mode_of(project: Project) -> str:
    return (project.split_config or {}).get("mode", "off")


def counts(slides: list[Slide]) -> dict[str, int]:
    out = {name: 0 for name in (*SPLITS, UNASSIGNED)}
    for slide in slides:
        out[slide.split if slide.split in SPLITS else UNASSIGNED] += 1
    return out


def _deal(waiting: list[Slide], assigned: dict[str, int], config: dict, rng: random.Random) -> None:
    """Give each waiting slide to the set furthest below its share, in a random order."""
    waiting = sorted(waiting, key=lambda s: s.id)
    rng.shuffle(waiting)
    for slide in waiting:
        total = sum(assigned.values()) + 1
        open_sets = [name for name in SPLITS if config[name] > 0]
        slide.split = max(open_sets, key=lambda name: config[name] / 100 * total - assigned[name])
        assigned[slide.split] += 1


def reshuffle(project: Project, slides: list[Slide]) -> None:
    """Deal every slide out again, under a new seed."""
    config = {**project.split_config, "seed": random.randrange(1, 2**31)}
    project.split_config = config
    _deal(slides, {name: 0 for name in SPLITS}, config, random.Random(config["seed"]))


def fill(project: Project, slides: list[Slide]) -> bool:
    """Random mode: assign the slides that have no set yet (added since the last deal). True if any were."""
    config = project.split_config
    if not config or config.get("mode") != "random":
        return False
    waiting = [s for s in slides if s.split not in SPLITS]
    if not waiting:
        return False
    assigned = {name: sum(1 for s in slides if s.split == name) for name in SPLITS}
    _deal(waiting, assigned, config, random.Random(f"{config['seed']}:{sum(assigned.values())}"))
    return True
