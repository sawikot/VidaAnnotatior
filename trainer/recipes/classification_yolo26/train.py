"""Ultralytics models (YOLO, RT-DETR), trained on the dataset the app cut for this run.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

The "architecture" setting names the model (yolo11n, yolov8s-seg, yolo26n-cls, rtdetr-l, ...); its
ending says what it does: "-seg" outlines each object, "-cls" gives the image one class, anything
else draws boxes. The dataset is rewritten the way Ultralytics reads it (a text file of labels beside
each image, or one folder of images per class), and Ultralytics' own training loop does the rest.

Prints  one "VP_METRIC {json}" line per epoch.
Writes  <output>/model.pt (the best epoch), <output>/model.json (its classes, for predict.py) and
        <output>/result.json.

Ultralytics is licensed under AGPL-3.0; it is installed from requirements.txt the first time this runs.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from pathlib import Path

import torch
from ultralytics import RTDETR, YOLO

# Set by the recipe check's own tests: build the network without downloading pretrained weights.
PRETRAINED = os.environ.get("VP_NO_PRETRAINED") != "1"
# Downloaded pretrained weights are kept here, beside PyTorch's own, so each is fetched once.
WEIGHTS = Path(os.environ.get("TORCH_HOME") or Path.home() / ".cache" / "torch") / "ultralytics"
MAX_CURVES = 8  # at most this many classes get a line of their own


def open_model(architecture: str):
    kind = RTDETR if architecture.startswith("rtdetr") else YOLO
    if PRETRAINED:
        WEIGHTS.mkdir(parents=True, exist_ok=True)
        return kind(str(WEIGHTS / f"{architecture}.pt"))  # fetched there if it is not yet
    return kind(re.sub(r"u$", "", architecture) + ".yaml")  # the bare network, nothing learned


def write_shapes(dataset: Path, layout: dict, classes: list[dict], outlines: bool) -> Path:
    """Boxes or outlines the way Ultralytics reads them: beside each image folder a labels folder with one
    text file per image, a line per object -- its class (0..N-1) and its box (centre, size) or its
    outline's points, all as shares of the image's width and height. Returns the data file naming it all."""
    index_of = {cls["id"]: i for i, cls in enumerate(classes)}
    for name, where in layout.items():
        doc = json.loads((dataset / where["annotations"]).read_text(encoding="utf-8"))
        labels = (dataset / where["images"]).parent / "labels"
        labels.mkdir(exist_ok=True)
        lines: dict[int, list[str]] = {image["id"]: [] for image in doc["images"]}
        size = {image["id"]: (image["width"], image["height"]) for image in doc["images"]}
        for ann in doc["annotations"]:
            if ann["category_id"] not in index_of or ann["image_id"] not in size:
                continue
            width, height = size[ann["image_id"]]
            if outlines:
                ring = max((r for r in ann.get("segmentation") or [] if len(r) >= 6), key=len, default=None)
                if ring is None:
                    continue
                numbers = [min(1.0, max(0.0, v / (width if i % 2 == 0 else height))) for i, v in enumerate(ring)]
            else:
                x, y, w, h = ann["bbox"]
                if w < 1 or h < 1:
                    continue
                numbers = [(x + w / 2) / width, (y + h / 2) / height, w / width, h / height]
            lines[ann["image_id"]].append(" ".join([str(index_of[ann["category_id"]]), *(f"{v:.6f}" for v in numbers)]))
        for image in doc["images"]:  # an image with nothing in it gets an empty file: it is background
            (labels / (Path(image["file_name"]).stem + ".txt")).write_text("\n".join(lines[image["id"]]), encoding="utf-8")
    data = dataset / "data.yaml"
    rows = [f"path: {json.dumps(dataset.resolve().as_posix())}"]
    rows += [f"{name}: {layout[name]['images']}" for name in ("train", "val", "test") if name in layout]
    rows += ["names:"] + [f"  {i}: {json.dumps(cls['name'])}" for i, cls in enumerate(classes)]
    data.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return data


def write_folders(dataset: Path, layout: dict, classes: list[dict]) -> Path:
    """Labelled images the way Ultralytics reads them: train/<class>/..., val/<class>/... -- the class
    folders named c000, c001, ... so that their order is the order of ``classes``."""
    index_of = {cls["name"]: i for i, cls in enumerate(classes)}
    root = dataset / "by_class"
    for name, where in layout.items():
        for i in range(len(classes)):  # every class has a folder in every set, even an empty one
            (root / name / f"c{i:03d}").mkdir(parents=True, exist_ok=True)
        with (dataset / where["labels"]).open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                if row["class"] not in index_of:
                    continue
                source = dataset / where["images"] / row["file"]
                target = root / name / f"c{index_of[row['class']]:03d}" / source.name
                try:
                    os.link(source, target)  # no second copy on disk
                except OSError:
                    shutil.copyfile(source, target)
    return root


def total(loss) -> float:
    """The training loss as one number, however this version of Ultralytics holds it: a tensor of
    its parts, a dict of them, or already a number."""
    if isinstance(loss, dict):
        return float(sum(total(v) for v in loss.values()))
    if hasattr(loss, "sum"):
        return float(loss.sum())
    return float(loss or 0)


def rounded(value):
    """The same as plain numbers, every fraction cut to four places: what goes into result.json."""
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(v) for v in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    return round(float(value), 4)


def labels_scores(found, names: list[str]) -> dict:
    """A classifier's scores, from Ultralytics' count of what was recognised as what."""
    grid = found.confusion_matrix.matrix.T.astype(int)  # theirs is [recognised as][really]
    seen, said, right = grid.sum(1), grid.sum(0), grid.diagonal()
    rows = []
    for i, name in enumerate(names):
        if not seen[i]:
            continue  # a class this set has no image of has no score
        recall, precision = right[i] / seen[i], (right[i] / said[i] if said[i] else 0.0)
        rows.append({"name": name, "precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0, "images": int(seen[i])})
    mean = lambda key: sum(row[key] for row in rows) / len(rows) if rows else 0.0  # noqa: E731
    out = {"accuracy": found.top1}
    if len(names) > 5:
        out["top5_accuracy"] = found.top5
    out.update(
        balanced_accuracy=mean("recall"), precision=mean("precision"), recall=mean("recall"), f1=mean("f1"),
        per_class={row["name"]: row["recall"] for row in rows}, classes=rows,
        counts={"images": int(grid.sum()), "right": int(right.sum()), "wrong": int(grid.sum() - right.sum())},
        figures=[
            {
                "type": "matrix", "title": "What was recognised as what", "rows": "Really", "columns": "Recognised as",
                "help": "Each row is the images of one class, spread over the classes the model took them for. The diagonal is right; anything else shows which classes it mixes up.",
                "row_labels": names, "column_labels": names, "values": grid.tolist(),
            },
            {
                "type": "bars", "title": "Precision, recall and F1 per class",
                "help": "Recall: of the images of a class, the share recognised as it. Precision: of the images the model called that class, the share that are. F1 balances the two.",
                "labels": [row["name"] for row in rows],
                "series": [{"label": label, "values": [row[key] for row in rows]} for key, label in (("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"))],
            },
        ],
    )
    return out


def shapes_scores(found, scores, names: list[str], matrix) -> dict:
    """A detector's or outliner's scores, from Ultralytics' own: ``scores`` is its box or its mask part.
    ``matrix(confidence)`` counts what was found as what, keeping detections at least that sure."""
    order = [int(c) for c in scores.ap_class_index]  # the classes this set has objects of
    rows = [
        {"name": names[c], "ap50": scores.ap50[i], "ap75": scores.all_ap[i, 5], "ap": scores.ap[i], "precision": scores.p[i], "recall": scores.r[i], "f1": scores.f1[i], "objects": int(found.nt_per_class[c])}
        for i, c in enumerate(order)
    ]
    out = {
        "ap50": scores.map50, "ap75": scores.map75, "ap": scores.map, "precision": scores.mp, "recall": scores.mr,
        "f1": sum(row["f1"] for row in rows) / len(rows) if rows else 0.0,
        "per_class": {row["name"]: row["ap50"] for row in rows}, "classes": rows, "figures": [],
    }
    if not rows:
        return out
    # Ultralytics reads its curves off at a thousand confidences; a hundred and one are plenty to draw.
    steps = list(range(0, len(scores.px), 10)) + [len(scores.px) - 1]
    along = [float(scores.px[k]) for k in steps]
    best = int(scores.f1_curve.mean(0).argmax())
    level = float(scores.px[best])
    out["best_confidence"] = level
    lines = ([("All classes", scores.prec_values.mean(0))] if len(rows) > 1 else []) + [(row["name"], scores.prec_values[i]) for i, row in enumerate(rows[:MAX_CURVES])]
    out["figures"] = [
        {
            "type": "curve", "title": "Precision against recall", "x": "Recall", "y": "Precision",
            "help": "Each point is one confidence the model could be cut off at: further right it finds more of the objects, lower down more of what it finds is wrong. A curve hugging the top right is a good detector; the area under it is AP50.",
            "series": [{"label": name, "points": [[x, line[k]] for x, k in zip(along, steps)]} for name, line in lines],
        },
        {
            "type": "curve", "title": "Precision, recall and F1 by confidence", "x": "Confidence", "y": "Score",
            "help": "What happens when only detections at least this sure are kept. The marked confidence is where F1 (the balance of precision and recall) is highest: a good place to set the slider for suggestions.",
            "mark": {"x": level, "label": "Best F1"},
            "series": [{"label": label, "points": [[x, line.mean(0)[k]] for x, k in zip(along, steps)]} for label, line in (("Precision", scores.p_curve), ("Recall", scores.r_curve), ("F1", scores.f1_curve))],
        },
    ]
    try:
        grid = matrix(max(level, 0.001)).T.astype(int)  # theirs is [found as][really], the background last
    except Exception as exc:  # noqa: BLE001 - a figure less, not a failed run
        print(f"The table of what was found as what could not be made: {exc}")
        return out
    count = len(names)
    out["counts"] = {
        "objects": int(found.nt_per_class.sum()), "found": int(grid[:count, :count].trace()), "missed": int(grid[:count].sum() - grid[:count, :count].trace()), "false_alarms": int(grid[count, :count].sum()),
    }
    out["figures"].append({
        "type": "matrix", "title": "What was found as what", "rows": "Really", "columns": "Found as",
        "help": f"Counted at the best-F1 confidence ({level:.2f}). The diagonal is what was found rightly; the last column is objects not found, the last row detections where nothing was drawn.",
        "row_labels": [*names, "Nothing there"], "column_labels": [*names, "Not found"], "values": grid.tolist(),
    })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    dataset = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))

    classes = dataset["classes"]
    architecture = settings["architecture"]
    labels_only, outlines = architecture.endswith("-cls"), architecture.endswith("-seg")
    epochs, batch, size = int(settings["epochs"]), int(settings["batch_size"]), int(settings["image_size"])
    cuda = torch.cuda.is_available()
    print(f"Device: {torch.cuda.get_device_name(0) if cuda else 'CPU (slow)'}")
    print(f"Network: {architecture}   Classes: {', '.join(cls['name'] for cls in classes)}")

    data = write_folders(args.dataset, dataset["sets"], classes) if labels_only else write_shapes(args.dataset, dataset["sets"], classes, outlines)
    common = {"imgsz": size, "batch": batch, "device": 0 if cuda else "cpu", "workers": 0, "plots": False, "verbose": False, "project": str(args.output / "ultralytics"), "exist_ok": True}
    part = "M" if outlines else "B"  # Ultralytics scores masks (M) and boxes (B) apart

    best = {"score": -1.0, "epoch": 0}
    told = {"epoch": 0}

    def report(trainer) -> None:
        """After each epoch's validation: pass its numbers on. (Ultralytics calls this once more at the end.)"""
        epoch = trainer.epoch + 1
        if epoch <= told["epoch"] or epoch > trainer.epochs:
            return
        told["epoch"] = epoch
        found = trainer.metrics or {}
        out = {"epoch": epoch, "epochs": trainer.epochs, "train_loss": round(total(trainer.tloss), 5)}
        if labels_only:
            out["val_accuracy"] = round(float(found.get("metrics/accuracy_top1", 0)), 4)
            score = out["val_accuracy"]
        else:
            out["val_ap50"] = round(float(found.get(f"metrics/mAP50({part})", 0)), 4)
            out["val_precision"] = round(float(found.get(f"metrics/precision({part})", 0)), 4)
            out["val_recall"] = round(float(found.get(f"metrics/recall({part})", 0)), 4)
            score = out["val_ap50"]
        if score > best["score"]:
            best.update(score=score, epoch=epoch)
        print("VP_METRIC " + json.dumps(out), flush=True)

    model = open_model(architecture)
    model.add_callback("on_fit_epoch_end", report)
    model.train(
        data=str(data), epochs=epochs, name="train", patience=epochs, amp=cuda and PRETRAINED,
        fliplr=0.5, flipud=0.5,  # tissue has no "right way round", nor a "right way up"
        **common,
    )
    kept = Path(model.trainer.best) if Path(model.trainer.best).is_file() else Path(model.trainer.last)
    shutil.copyfile(kept, args.output / "model.pt")
    (args.output / "model.json").write_text(json.dumps({"kind": "ultralytics", "architecture": architecture, "image_size": size, "classes": classes}, indent=2), encoding="utf-8")

    final = (RTDETR if architecture.startswith("rtdetr") else YOLO)(str(args.output / "model.pt"))  # score the kept (best) epoch

    names = [cls["name"] for cls in classes]

    def summary(split: str) -> dict:
        found = final.val(data=str(data), split=split, name=f"score_{split}", **common)
        if labels_only:
            return rounded(labels_scores(found, names))

        def matrix(confidence: float):
            # Ultralytics counts this only while drawing its own plots, and at the confidence it is given.
            again = final.val(data=str(data), split=split, name=f"matrix_{split}", conf=confidence, **{**common, "plots": True})
            return again.confusion_matrix.matrix

        return rounded(shapes_scores(found, found.seg if outlines else found.box, names, matrix))

    metric = "accuracy" if labels_only else "ap50"
    result = {"primary_metric": metric, "best_epoch": best["epoch"], "val": summary("val")}
    if "test" in dataset["sets"]:
        result["test"] = summary("test")
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    shutil.rmtree(args.output / "ultralytics", ignore_errors=True)  # Ultralytics' own copies of the weights and its tables
    print(f"Done. Best epoch {best['epoch']}, validation {metric} {result['val'][metric]:.3f}" + (f", test {result['test'][metric]:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
