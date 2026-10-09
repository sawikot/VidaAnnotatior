"""Mask R-CNN (torchvision): finds each object and outlines it, learned from polygons and brushed shapes.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Reads   <dataset>/dataset.json and each set's COCO file and images; every shape is painted into a
        mask of its own, so touching objects stay separate (unlike a region segmenter).
Prints  one "VP_METRIC {json}" line per epoch.
Writes  <output>/model.pt (the best epoch, by validation AP50 of the objects' boxes) and <output>/result.json.
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
from PIL import Image, ImageDraw
from torch.utils.data import DataLoader, Dataset
from torchvision.models import get_model_weights
from torchvision.transforms.functional import pil_to_tensor

SCORE_THRESHOLD = 0.5  # detections at least this sure count for precision and recall
IOU_MATCH = 0.5  # a detection overlapping an object this much has found it
# Set by the recipe check's own tests: build the network without downloading pretrained weights.
PRETRAINED = os.environ.get("VP_NO_PRETRAINED") != "1"


class CocoMasks(Dataset):
    """Images with, for each object, its box, class (1..N; 0 is background) and mask."""

    def __init__(self, root: Path, layout: dict, label_of: dict[int, int], augment: bool):
        doc = json.loads((root / layout["annotations"]).read_text(encoding="utf-8"))
        self.images_dir = root / layout["images"]
        self.images = doc["images"]
        self.augment = augment
        self.objects: dict[int, list] = {}
        for ann in doc["annotations"]:
            x, y, w, h = ann["bbox"]
            rings = [ring for ring in ann.get("segmentation") or [] if len(ring) >= 6]
            if w >= 1 and h >= 1 and rings and ann["category_id"] in label_of:
                self.objects.setdefault(ann["image_id"], []).append(([x, y, x + w, y + h], label_of[ann["category_id"]], rings))

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, index: int):
        info = self.images[index]
        picture = Image.open(self.images_dir / info["file_name"]).convert("RGB")
        image = pil_to_tensor(picture).float() / 255
        found = self.objects.get(info["id"], [])
        masks = []
        for _, _, rings in found:
            mask = Image.new("L", picture.size, 0)
            draw = ImageDraw.Draw(mask)
            for ring in rings:
                draw.polygon(ring, fill=1)
            masks.append(pil_to_tensor(mask)[0])
        boxes = torch.tensor([box for box, _, _ in found], dtype=torch.float32).reshape(-1, 4)
        labels = torch.tensor([label for _, label, _ in found], dtype=torch.long)
        masks = torch.stack(masks) if masks else torch.zeros((0, picture.height, picture.width), dtype=torch.uint8)
        if self.augment:
            _, height, width = image.shape
            if random.random() < 0.5:  # mirror left-right: tissue has no "right way round"
                image, masks = image.flip(-1), masks.flip(-1)
                boxes = torch.stack([width - boxes[:, 2], boxes[:, 1], width - boxes[:, 0], boxes[:, 3]], dim=1)
            if random.random() < 0.5:  # ...nor up-down
                image, masks = image.flip(-2), masks.flip(-2)
                boxes = torch.stack([boxes[:, 0], height - boxes[:, 3], boxes[:, 2], height - boxes[:, 1]], dim=1)
        return image, {"boxes": boxes, "labels": labels, "masks": masks}


def build_model(architecture: str, classes: int, image_size: int):
    """The network with outputs for this project's classes. Everything that does not depend on the
    classes starts from torchvision's weights (learned on everyday photographs), so few examples go far."""
    make = getattr(detectors, architecture)
    sizes = {"min_size": image_size, "max_size": max(image_size, int(image_size * 1.5))}
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


def iou(box: torch.Tensor, others: torch.Tensor) -> torch.Tensor:
    left, top = torch.maximum(box[0], others[:, 0]), torch.maximum(box[1], others[:, 1])
    right, bottom = torch.minimum(box[2], others[:, 2]), torch.minimum(box[3], others[:, 3])
    overlap = (right - left).clamp(min=0) * (bottom - top).clamp(min=0)
    area = (box[2] - box[0]) * (box[3] - box[1]) + (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1]) - overlap
    return overlap / area.clamp(min=1e-6)


def score(found: list[dict], truth: list[dict], labels: list[int]) -> dict:
    """AP50 per class and overall, and precision / recall of the detections above SCORE_THRESHOLD."""
    per_class: dict[int, float] = {}
    hits = wrong = missed = 0
    for label in labels:
        detections = []  # (score, image index, box)
        objects: dict[int, torch.Tensor] = {}
        for i, (pred, target) in enumerate(zip(found, truth)):
            objects[i] = target["boxes"][target["labels"] == label]
            keep = pred["labels"] == label
            detections += [(float(s), i, b) for s, b in zip(pred["scores"][keep], pred["boxes"][keep])]
        total = sum(len(b) for b in objects.values())
        detections.sort(key=lambda d: -d[0])
        taken = {i: torch.zeros(len(b), dtype=torch.bool) for i, b in objects.items()}
        correct = []
        for confidence, i, box in detections:
            hit = False
            if len(objects[i]):
                overlaps = iou(box, objects[i])
                overlaps[taken[i]] = 0
                best = int(overlaps.argmax())
                if overlaps[best] >= IOU_MATCH:
                    taken[i][best] = hit = True
            correct.append(hit)
            if confidence >= SCORE_THRESHOLD:
                hits += hit
                wrong += not hit
        if total == 0:
            continue  # a class with no objects in this set has no score
        missed += total - sum(1 for (c, _, _), ok in zip(detections, correct) if ok and c >= SCORE_THRESHOLD)
        precisions, recalls, right = [], [], 0
        for n, ok in enumerate(correct, start=1):
            right += ok
            precisions.append(right / n)
            recalls.append(right / total)
        for k in range(len(precisions) - 2, -1, -1):
            precisions[k] = max(precisions[k], precisions[k + 1])
        ap, last = 0.0, 0.0
        for p, r in zip(precisions, recalls):
            ap += p * (r - last)
            last = r
        per_class[label] = ap
    return {
        "ap50": sum(per_class.values()) / len(per_class) if per_class else 0.0,
        "precision": hits / (hits + wrong) if hits + wrong else 0.0,
        "recall": hits / (hits + missed) if hits + missed else 0.0,
        "per_class": per_class,
    }


@torch.no_grad()
def evaluate(model, loader, device, labels: list[int]) -> dict:
    model.eval()
    found, truth = [], []
    for images, targets in loader:
        outputs = model([image.to(device) for image in images])
        found += [{"boxes": out["boxes"].float().cpu(), "labels": out["labels"].cpu(), "scores": out["scores"].float().cpu()} for out in outputs]
        truth += [{"boxes": t["boxes"], "labels": t["labels"]} for t in targets]
    return score(found, truth, labels)


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
    labels = list(label_of.values())
    name_of = {label_of[cls["id"]]: cls["name"] for cls in classes}
    architecture = settings["architecture"]
    epochs, batch, image_size = int(settings["epochs"]), int(settings["batch_size"]), int(settings["image_size"])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
    print(f"Network: {architecture}   Classes: {', '.join(cls['name'] for cls in classes)}")

    def loader(name: str, train: bool) -> DataLoader:
        data = CocoMasks(args.dataset, dataset["sets"][name], label_of, augment=train)
        print(f"{name}: {len(data)} images, {sum(len(o) for o in data.objects.values())} objects")
        return DataLoader(data, batch_size=batch if train else 1, shuffle=train, collate_fn=lambda rows: tuple(zip(*rows)), num_workers=0)

    train_loader, val_loader = loader("train", True), loader("val", False)
    model = build_model(architecture, len(classes), image_size).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(params, lr=float(settings["learning_rate"]), momentum=0.9, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")
    warmup = min(100, len(train_loader) * 2)  # ease in: the new outputs start from random numbers

    checkpoint = {"kind": "instances", "architecture": architecture, "image_size": image_size, "classes": classes, "label_of": label_of}
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

        val = evaluate(model, val_loader, device, labels)
        if val["ap50"] > best["ap50"]:
            best = {"ap50": val["ap50"], "epoch": epoch}
            torch.save({**checkpoint, "state_dict": model.state_dict(), "epoch": epoch}, args.output / "model.pt")
        print(f"epoch {epoch}/{epochs}  loss {total / max(seen, 1):.4f}  val AP50 {val['ap50']:.3f}  precision {val['precision']:.3f}  recall {val['recall']:.3f}  ({time.time() - started:.0f}s)")
        print("VP_METRIC " + json.dumps({
            "epoch": epoch, "epochs": epochs, "train_loss": round(total / max(seen, 1), 5),
            "val_ap50": round(val["ap50"], 4), "val_precision": round(val["precision"], 4), "val_recall": round(val["recall"], 4),
        }), flush=True)

    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])  # score the kept (best) epoch

    def summary(scores: dict) -> dict:
        return {
            "ap50": round(scores["ap50"], 4), "precision": round(scores["precision"], 4), "recall": round(scores["recall"], 4),
            "per_class": {name_of[label]: round(ap, 4) for label, ap in scores["per_class"].items()},
        }

    result = {"primary_metric": "ap50", "best_epoch": best["epoch"], "val": summary(evaluate(model, val_loader, device, labels))}
    if "test" in dataset["sets"]:
        result["test"] = summary(evaluate(model, loader("test", False), device, labels))
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Done. Best epoch {best['epoch']}, validation AP50 {result['val']['ap50']:.3f}" + (f", test AP50 {result['test']['ap50']:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
