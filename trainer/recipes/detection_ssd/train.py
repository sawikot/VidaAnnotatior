"""Object detector (torchvision), trained on the dataset the app cut for this run.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Reads   <dataset>/dataset.json and each set's COCO file and images.
Prints  one "VP_METRIC {json}" line per epoch (the app draws its curves from these).
Writes  <output>/model.pt (the best epoch, by validation AP50) and <output>/result.json.

Which network is trained is the "architecture" setting: the name of one of torchvision's detection
models (fasterrcnn_resnet50_fpn, retinanet_resnet50_fpn, fcos_resnet50_fpn, ssdlite320_mobilenet_v3_large, ...).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import torch
import torchvision.models.detection as detectors
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision.models import get_model_weights
from torchvision.ops import box_iou
from torchvision.transforms.functional import pil_to_tensor

IOU_MATCH = 0.5  # a detection overlapping an object this much has found it
IOU_STEPS = [0.5 + 0.05 * i for i in range(10)]  # the overlaps AP50-95 is averaged over (the sixth is 0.75)
GRID = [i / 100 for i in range(101)]  # where the curves are read off
MAX_CURVES = 8  # at most this many classes get a line of their own
# Set by the recipe check's own tests: build the network without downloading pretrained weights.
PRETRAINED = os.environ.get("VP_NO_PRETRAINED") != "1"


class CocoBoxes(Dataset):
    """Images with their boxes; ``label_of`` maps the dataset's class ids to 1..N (0 is background)."""

    def __init__(self, root: Path, layout: dict, label_of: dict[int, int], augment: bool, need_boxes: bool = False):
        doc = json.loads((root / layout["annotations"]).read_text(encoding="utf-8"))
        self.images_dir = root / layout["images"]
        self.label_of = label_of
        self.augment = augment
        self.boxes: dict[int, list] = {}
        for ann in doc["annotations"]:
            x, y, w, h = ann["bbox"]
            if w >= 1 and h >= 1 and ann["category_id"] in label_of:
                self.boxes.setdefault(ann["image_id"], []).append([x, y, x + w, y + h, label_of[ann["category_id"]]])
        # Some networks (SSD) cannot learn from an image with nothing in it.
        self.images = [image for image in doc["images"] if not need_boxes or image["id"] in self.boxes]

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, index: int):
        info = self.images[index]
        image = pil_to_tensor(Image.open(self.images_dir / info["file_name"]).convert("RGB")).float() / 255
        rows = torch.tensor(self.boxes.get(info["id"], []), dtype=torch.float32).reshape(-1, 5)
        boxes, labels = rows[:, :4], rows[:, 4].long()
        if self.augment:
            _, height, width = image.shape
            if random.random() < 0.5:  # mirror left-right: tissue has no "right way round"
                image = image.flip(-1)
                boxes = torch.stack([width - boxes[:, 2], boxes[:, 1], width - boxes[:, 0], boxes[:, 3]], dim=1)
            if random.random() < 0.5:  # ...nor up-down
                image = image.flip(-2)
                boxes = torch.stack([boxes[:, 0], height - boxes[:, 3], boxes[:, 2], height - boxes[:, 1]], dim=1)
        return image, {"boxes": boxes, "labels": labels}


def build_model(architecture: str, classes: int, image_size: int | None):
    """The network with outputs for this project's classes. Everything that does not depend on the
    classes starts from torchvision's weights (learned on everyday photographs), so few examples go far."""
    make = getattr(detectors, architecture)
    sizes = {} if image_size is None else {"min_size": image_size, "max_size": max(image_size, int(image_size * 1.5))}
    try:
        model = make(weights=None, weights_backbone="DEFAULT" if PRETRAINED else None, num_classes=classes + 1, progress=False, **sizes)
        if PRETRAINED:
            learned = get_model_weights(architecture).DEFAULT.get_state_dict(progress=False)
            own = model.state_dict()
            model.load_state_dict({k: v for k, v in learned.items() if k in own and own[k].shape == v.shape}, strict=False)
    except Exception as exc:  # noqa: BLE001 - no internet: learn from scratch rather than not at all
        print(f"Could not download the pretrained network ({exc}); starting from an untrained one, which needs many more examples.")
        model = make(weights=None, weights_backbone=None, num_classes=classes + 1, **sizes)
    return model


def average_precision(flags: list[bool], total: int) -> float:
    """Area under the precision-recall curve, precision made non-increasing first."""
    precisions, recalls, right = [], [], 0
    for n, ok in enumerate(flags, start=1):
        right += ok
        precisions.append(right / n)
        recalls.append(right / total)
    for k in range(len(precisions) - 2, -1, -1):
        precisions[k] = max(precisions[k], precisions[k + 1])
    ap, last = 0.0, 0.0
    for p, r in zip(precisions, recalls):
        ap += p * (r - last)
        last = r
    return ap


def by_confidence(entries: list[tuple[float, bool]], total: int) -> list[tuple[float | None, float, float]]:
    """(precision, recall, F1) at each step of GRID, counting only the detections at least that sure.
    ``entries``: (confidence, found an object) from the surest down. No precision where nothing is left."""
    out, n, right = [], 0, 0
    for level in reversed(GRID):
        while n < len(entries) and entries[n][0] >= level:
            right += entries[n][1]
            n += 1
        p, r = (right / n if n else None), (right / total if total else 0.0)
        out.append((p, r, 2 * p * r / (p + r) if p and r else 0.0))
    return out[::-1]


def precision_at_recall(entries: list[tuple[float, bool]], total: int) -> list[float]:
    """The precision still to be had at each recall of GRID: the precision-recall curve."""
    points, right = [], 0
    for n, (_, hit) in enumerate(entries, start=1):
        right += hit
        points.append((right / max(total, 1), right / n))
    out, ceiling, k = [], 0.0, len(points) - 1
    for level in reversed(GRID):
        while k >= 0 and points[k][0] >= level:
            ceiling = max(ceiling, points[k][1])
            k -= 1
        out.append(ceiling)
    return out[::-1]


def confusion(found: list[dict], truth: list[dict], names: dict[int, str], level: float) -> list[list[int]]:
    """What each object was found as, counting detections at least ``level`` sure: a row per real class
    (the last row: nothing was there), a column per class it was found as (the last: not found at all)."""
    place = {label: i for i, label in enumerate(names)}
    n = len(place)
    grid = [[0] * (n + 1) for _ in range(n + 1)]
    for pred, target in zip(found, truth):
        order = [int(k) for k in pred["scores"].argsort(descending=True) if pred["scores"][k] >= level and int(pred["labels"][k]) in place]
        shared = box_iou(pred["boxes"][order], target["boxes"])
        taken = torch.zeros(len(target["boxes"]), dtype=torch.bool)
        for row, k in zip(shared, order):
            real = n
            if len(taken):
                row = row.clone()
                row[taken] = 0
                best = int(row.argmax())
                if row[best] >= IOU_MATCH:
                    taken[best] = True
                    real = place[int(target["labels"][best])]
            grid[real][place[int(pred["labels"][k])]] += 1
        for label in target["labels"][~taken].tolist():
            grid[place[label]][n] += 1
    return grid


def score(found: list[dict], truth: list[dict], names: dict[int, str], full: bool = False) -> dict:
    """How well the detections match the drawn objects. Always: AP50 per class and overall, and the
    precision, recall and F1 at the confidence where F1 is best. ``full`` (the final scoring) adds
    AP75 and AP50-95, a table per class, counts, and the figures the app draws."""
    steps = IOU_STEPS if full else [IOU_MATCH]
    pooled: list[tuple[float, bool]] = []
    per_class: dict[int, dict] = {}
    objects = 0
    for label in names:
        entries, overlaps, total = [], {}, 0  # (confidence, image, which of its detections)
        for i, (pred, target) in enumerate(zip(found, truth)):
            real = target["boxes"][target["labels"] == label]
            total += len(real)
            keep = pred["labels"] == label
            if keep.any():
                overlaps[i] = box_iou(pred["boxes"][keep], real)
                entries += [(float(s), i, k) for k, s in enumerate(pred["scores"][keep])]
        entries.sort(key=lambda e: -e[0])
        hits = []  # per overlap asked for: did each detection, from the surest down, find an object
        for step in steps:
            taken = {i: torch.zeros(m.shape[1], dtype=torch.bool) for i, m in overlaps.items()}
            flags = []
            for _, i, k in entries:
                hit = False
                if len(taken[i]):
                    row = overlaps[i][k].clone()
                    row[taken[i]] = 0
                    best = int(row.argmax())
                    if row[best] >= step:
                        taken[i][best] = hit = True
                flags.append(hit)
            hits.append(flags)
        mine = [(e[0], hit) for e, hit in zip(entries, hits[0])]
        pooled += mine
        objects += total
        if total:  # a class with no objects in this set has no score
            per_class[label] = {"entries": mine, "total": total, "aps": [average_precision(flags, total) for flags in hits]}
    pooled.sort(key=lambda e: -e[0])

    curve = by_confidence(pooled, objects)
    best = max(range(len(GRID)), key=lambda g: curve[g][2])
    level = GRID[best]
    mean = lambda values: sum(values) / len(values) if values else 0.0  # noqa: E731
    out = {
        "ap50": mean([c["aps"][0] for c in per_class.values()]),
        "precision": curve[best][0] or 0.0, "recall": curve[best][1], "f1": curve[best][2], "best_confidence": level,
        "per_class": {names[label]: c["aps"][0] for label, c in per_class.items()},
    }
    if not full:
        return out

    rows = []
    for label, c in per_class.items():
        p, r, f1 = by_confidence(c["entries"], c["total"])[best]
        rows.append({"name": names[label], "ap50": c["aps"][0], "ap75": c["aps"][5], "ap": mean(c["aps"]), "precision": p or 0.0, "recall": r, "f1": f1, "objects": c["total"]})
    shown = sum(1 for confidence, _ in pooled if confidence >= level)
    right = sum(hit for confidence, hit in pooled if confidence >= level)
    lines = [("All classes", pooled, objects)] if len(per_class) != 1 else []
    lines += [(names[label], c["entries"], c["total"]) for label, c in list(per_class.items())[:MAX_CURVES]]
    out.update(
        ap75=mean([row["ap75"] for row in rows]), ap=mean([row["ap"] for row in rows]), classes=rows,
        counts={"images": len(truth), "objects": objects, "found": right, "missed": objects - right, "false_alarms": shown - right},
        figures=[
            {
                "type": "curve", "title": "Precision against recall", "x": "Recall", "y": "Precision",
                "help": "Each point is one confidence the model could be cut off at: further right it finds more of the objects, lower down more of what it finds is wrong. A curve hugging the top right is a good detector; the area under it is AP50.",
                "series": [{"label": name, "points": [[x, y] for x, y in zip(GRID, precision_at_recall(entries, total))]} for name, entries, total in lines],
            },
            {
                "type": "curve", "title": "Precision, recall and F1 by confidence", "x": "Confidence", "y": "Score",
                "help": "What happens when only detections at least this sure are kept. The marked confidence is where F1 (the balance of precision and recall) is highest: a good place to set the slider for suggestions.",
                "mark": {"x": level, "label": "Best F1"},
                "series": [
                    {"label": label, "points": [[x, point[k]] for x, point in zip(GRID, curve) if point[k] is not None]}
                    for k, label in enumerate(("Precision", "Recall", "F1"))
                ],
            },
            {
                "type": "matrix", "title": "What was found as what", "rows": "Really", "columns": "Found as",
                "help": f"Counted at the best-F1 confidence ({level:.2f}). The diagonal is what was found rightly; the last column is objects not found, the last row detections where nothing was drawn.",
                "row_labels": [*names.values(), "Nothing there"], "column_labels": [*names.values(), "Not found"],
                "values": confusion(found, truth, names, level),
            },
        ],
    )
    return out


def rounded(value):
    """The same, with every fraction cut to four places: what goes into result.json."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(v) for v in value]
    return value


@torch.no_grad()
def evaluate(model, loader, device, names: dict[int, str], full: bool = False) -> dict:
    model.eval()
    found, truth = [], []
    for images, targets in loader:
        outputs = model([image.to(device) for image in images])
        found += [{k: v.float().cpu() if k != "labels" else v.cpu() for k, v in out.items() if k in ("boxes", "labels", "scores")} for out in outputs]
        truth += targets
    return score(found, truth, names, full)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    dataset = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))

    classes = dataset["classes"]
    label_of = {cls["id"]: i for i, cls in enumerate(classes, start=1)}
    name_of = {label_of[cls["id"]]: cls["name"] for cls in classes}
    architecture = settings["architecture"]
    epochs, batch = int(settings["epochs"]), int(settings["batch_size"])
    image_size = int(settings["image_size"]) if settings.get("image_size") else None  # SSD has a size of its own

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
    print(f"Network: {architecture}   Classes: {', '.join(cls['name'] for cls in classes)}")

    def loader(name: str, train: bool) -> DataLoader:
        data = CocoBoxes(args.dataset, dataset["sets"][name], label_of, augment=train, need_boxes=train and architecture.startswith("ssd"))
        print(f"{name}: {len(data)} images, {sum(len(b) for b in data.boxes.values())} objects")
        return DataLoader(data, batch_size=batch if train else 1, shuffle=train, collate_fn=lambda rows: tuple(zip(*rows)), num_workers=0)

    train_loader, val_loader = loader("train", True), loader("val", False)
    model = build_model(architecture, len(classes), image_size).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(params, lr=float(settings["learning_rate"]), momentum=0.9, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")
    warmup = min(100, len(train_loader) * 2)  # ease in: the new outputs start from random numbers

    checkpoint = {"kind": "detection", "architecture": architecture, "image_size": image_size, "classes": classes, "label_of": label_of}
    best = {"ap50": -1.0, "epoch": 0}
    step = 0
    for epoch in range(1, epochs + 1):
        model.train()
        started, total, seen = time.time(), 0.0, 0
        for images, targets in train_loader:
            images = [image.to(device) for image in images]
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]
            if step < warmup:
                for group in optimizer.param_groups:
                    group["lr"] = float(settings["learning_rate"]) * (step + 1) / warmup
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
                loss = sum(model(images, targets).values())
            if not torch.isfinite(loss):
                raise SystemExit("The loss became infinite. Lower the learning rate and try again.")
            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            total, seen, step = total + loss.item(), seen + 1, step + 1
        schedule.step()

        val = evaluate(model, val_loader, device, name_of)
        if val["ap50"] > best["ap50"]:
            best = {"ap50": val["ap50"], "epoch": epoch}
            torch.save({**checkpoint, "state_dict": model.state_dict(), "epoch": epoch}, args.output / "model.pt")
        print(f"epoch {epoch}/{epochs}  loss {total / max(seen, 1):.4f}  val AP50 {val['ap50']:.3f}  precision {val['precision']:.3f}  recall {val['recall']:.3f}  ({time.time() - started:.0f}s)")
        print("VP_METRIC " + json.dumps({
            "epoch": epoch, "epochs": epochs, "train_loss": round(total / max(seen, 1), 5),
            "val_ap50": round(val["ap50"], 4), "val_precision": round(val["precision"], 4), "val_recall": round(val["recall"], 4),
        }), flush=True)

    # The kept model is the best epoch, not the last one: score that.
    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])

    result = {"primary_metric": "ap50", "best_epoch": best["epoch"], "val": rounded(evaluate(model, val_loader, device, name_of, full=True))}
    if "test" in dataset["sets"]:
        result["test"] = rounded(evaluate(model, loader("test", False), device, name_of, full=True))
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Done. Best epoch {best['epoch']}, validation AP50 {result['val']['ap50']:.3f}" + (f", test AP50 {result['test']['ap50']:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
