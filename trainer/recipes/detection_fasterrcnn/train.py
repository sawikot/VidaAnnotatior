"""Faster R-CNN object detector (torchvision), trained on the dataset the app cut for this run.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Reads   <dataset>/dataset.json and each set's COCO file and images.
Prints  one "VP_METRIC {json}" line per epoch (the app draws its curves from these).
Writes  <output>/model.pt (the best epoch, by validation AP50) and <output>/result.json.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn, fasterrcnn_resnet50_fpn
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.transforms.functional import pil_to_tensor

SCORE_THRESHOLD = 0.5  # detections at least this sure count for precision and recall
IOU_MATCH = 0.5  # a detection overlapping an object this much has found it


class CocoBoxes(Dataset):
    """Images with their boxes; ``label_of`` maps the dataset's class ids to 1..N (0 is background)."""

    def __init__(self, root: Path, layout: dict, label_of: dict[int, int], augment: bool):
        doc = json.loads((root / layout["annotations"]).read_text(encoding="utf-8"))
        self.images_dir = root / layout["images"]
        self.images = doc["images"]
        self.label_of = label_of
        self.augment = augment
        self.boxes: dict[int, list] = {}
        for ann in doc["annotations"]:
            x, y, w, h = ann["bbox"]
            if w >= 1 and h >= 1 and ann["category_id"] in label_of:
                self.boxes.setdefault(ann["image_id"], []).append([x, y, x + w, y + h, label_of[ann["category_id"]]])

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


def build_model(size: str, classes: int, image_size: int):
    make = fasterrcnn_resnet50_fpn if size == "large" else fasterrcnn_mobilenet_v3_large_fpn
    sizes = {"min_size": image_size, "max_size": max(image_size, int(image_size * 1.5))}
    try:
        model = make(weights="DEFAULT", progress=False, **sizes)
    except Exception as exc:  # noqa: BLE001 - no internet: learn from scratch rather than not at all
        print(f"Could not download the pretrained network ({exc}); starting from an untrained one, which needs many more examples.")
        model = make(weights=None, weights_backbone=None, **sizes)
    head = model.roi_heads.box_predictor
    model.roi_heads.box_predictor = FastRCNNPredictor(head.cls_score.in_features, classes + 1)
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
        found_above = sum(1 for (c, _, _), ok in zip(detections, correct) if ok and c >= SCORE_THRESHOLD)
        missed += total - found_above
        # Area under the precision-recall curve, precision made non-increasing first.
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
        found += [{k: v.cpu() for k, v in out.items()} for out in outputs]
        truth += targets
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
    epochs, batch, image_size = int(settings["epochs"]), int(settings["batch_size"]), int(settings["image_size"])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
    print(f"Classes: {', '.join(cls['name'] for cls in classes)}")

    def loader(name: str, train: bool) -> DataLoader:
        data = CocoBoxes(args.dataset, dataset["sets"][name], label_of, augment=train)
        print(f"{name}: {len(data)} images, {sum(len(b) for b in data.boxes.values())} objects")
        return DataLoader(data, batch_size=batch if train else 1, shuffle=train, collate_fn=lambda rows: tuple(zip(*rows)), num_workers=0)

    train_loader, val_loader = loader("train", True), loader("val", False)
    model = build_model(settings["model_size"], len(classes), image_size).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(params, lr=float(settings["learning_rate"]), momentum=0.9, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")
    warmup = min(100, len(train_loader) * 2)  # ease in: the new head starts from random numbers

    checkpoint = {"recipe": "detection_fasterrcnn", "model_size": settings["model_size"], "image_size": image_size, "classes": classes, "label_of": label_of}
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

    # The kept model is the best epoch, not the last one: score that.
    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])

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
