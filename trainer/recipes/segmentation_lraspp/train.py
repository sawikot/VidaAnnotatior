"""Semantic segmentation (torchvision): every pixel gets a class or background.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Reads   <dataset>/dataset.json and each set's COCO file and images; the polygons are painted into a
        label image (0 = background, 1..N = the classes; where shapes overlap the later one wins).
Prints  one "VP_METRIC {json}" line per epoch.
Writes  <output>/model.pt (the best epoch, by validation mean IoU) and <output>/result.json.

Which network is trained is the "architecture" setting: the name of one of torchvision's
segmentation models (lraspp_mobilenet_v3_large, deeplabv3_resnet50, fcn_resnet50, ...).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from torch.utils.data import DataLoader, Dataset
import torchvision.models.segmentation as segmenters
from torchvision.transforms.functional import pil_to_tensor

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
# Set by the recipe check's own tests: build the network without downloading pretrained weights.
PRETRAINED = os.environ.get("VP_NO_PRETRAINED") != "1"


class Painted(Dataset):
    """Images with their label image, both resized to size x size."""

    def __init__(self, root: Path, layout: dict, label_of: dict[int, int], size: int, augment: bool):
        doc = json.loads((root / layout["annotations"]).read_text(encoding="utf-8"))
        self.images_dir = root / layout["images"]
        self.images = doc["images"]
        self.shapes: dict[int, list] = {}
        for ann in sorted(doc["annotations"], key=lambda a: a["id"]):  # creation order: later shapes paint over earlier
            if ann["category_id"] in label_of:
                self.shapes.setdefault(ann["image_id"], []).append((label_of[ann["category_id"]], ann.get("segmentation") or []))
        self.size, self.augment = size, augment

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, i: int):
        info = self.images[i]
        picture = Image.open(self.images_dir / info["file_name"]).convert("RGB")
        labels = Image.new("L", picture.size, 0)
        draw = ImageDraw.Draw(labels)
        for label, rings in self.shapes.get(info["id"], []):
            for ring in rings:
                if len(ring) >= 6:
                    draw.polygon(ring, fill=label)
        image = pil_to_tensor(picture.resize((self.size, self.size), Image.BILINEAR)).float() / 255
        target = pil_to_tensor(labels.resize((self.size, self.size), Image.NEAREST))[0].long()
        if self.augment:
            if random.random() < 0.5:
                image, target = image.flip(-1), target.flip(-1)
            if random.random() < 0.5:
                image, target = image.flip(-2), target.flip(-2)
            turns = random.randrange(4)
            image, target = torch.rot90(image, turns, dims=(-2, -1)), torch.rot90(target, turns, dims=(-2, -1))
        return (image - MEAN) / STD, target


def region_loss(scores: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """Cross-entropy plus a Dice term. Drawn regions are often a small part of the image; counting
    pixels alone, calling everything background is nearly right. Dice scores each class by how well
    its area is matched, however small it is, so the classes are learned rather than ignored."""
    scores = scores.float()
    sure = torch.softmax(scores, dim=1)[:, 1:]  # the classes, without the background
    drawn = torch.nn.functional.one_hot(targets, scores.shape[1]).permute(0, 3, 1, 2)[:, 1:].float()
    shared = (sure * drawn).sum(dim=(0, 2, 3))
    dice = (2 * shared + 1) / (sure.sum(dim=(0, 2, 3)) + drawn.sum(dim=(0, 2, 3)) + 1)
    return torch.nn.functional.cross_entropy(scores, targets) + (1 - dice.mean())


def build_model(architecture: str, outputs: int):
    make = getattr(segmenters, architecture)
    try:
        return make(weights=None, weights_backbone="DEFAULT" if PRETRAINED else None, num_classes=outputs, progress=False)
    except Exception as exc:  # noqa: BLE001 - no internet: learn from scratch rather than not at all
        print(f"Could not download the pretrained network ({exc}); starting from an untrained one, which needs many more examples.")
        return make(weights=None, weights_backbone=None, num_classes=outputs)


@torch.no_grad()
def evaluate(model, loader, device, names: list[str], full: bool = False) -> dict:
    """How well the marked areas match the drawn ones, pixel by pixel. The background is counted in
    the pixel accuracy only; every other number is a mean over the classes that this set or the
    model's answer has. ``full`` (the final scoring) adds a table per class and the figures."""
    model.eval()
    outputs = len(names) + 1
    grid = torch.zeros(outputs, outputs, dtype=torch.long)  # grid[really][marked as], in pixels; 0 is the background
    loss, batches = 0.0, 0
    for images, targets in loader:
        scores = model(images.to(device))["out"].float().cpu()
        loss += float(torch.nn.functional.cross_entropy(scores, targets))
        batches += 1
        grid += torch.bincount((targets * outputs + scores.argmax(1)).flatten(), minlength=outputs * outputs).reshape(outputs, outputs)
    drawn, marked, shared = grid.sum(1), grid.sum(0), grid.diag()
    total = int(grid.sum())
    rows = []
    for cls in range(1, outputs):
        either = int(drawn[cls] + marked[cls] - shared[cls])
        if not either:
            continue
        rows.append({
            "name": names[cls - 1], "iou": int(shared[cls]) / either, "dice": 2 * int(shared[cls]) / int(drawn[cls] + marked[cls]),
            "precision": float(shared[cls] / marked[cls]) if marked[cls] else 0.0, "recall": float(shared[cls] / drawn[cls]) if drawn[cls] else 0.0,
            "area": int(drawn[cls]) / total,
        })
    mean = lambda key: sum(row[key] for row in rows) / len(rows) if rows else 0.0  # noqa: E731
    out = {
        "loss": loss / max(batches, 1),
        "miou": mean("iou"), "dice": mean("dice"), "pixel_accuracy": int(shared.sum()) / total if total else 0.0,
        "precision": mean("precision"), "recall": mean("recall"),
        "per_class": {row["name"]: row["iou"] for row in rows},
    }
    if not full:
        return out
    out.pop("loss")
    labels = ["Background", *names]
    out.update(
        classes=rows, counts={"images": len(loader.dataset)},
        figures=[
            {
                "type": "matrix", "title": "What each area was marked as", "rows": "Really", "columns": "Marked as", "format": "percent",
                "help": "Each row is the pixels drawn as one class (the first: not drawn at all), as shares of where the model put them. The diagonal is right; the first column is area the model left out, the first row area it marked where nothing was drawn.",
                "row_labels": labels, "column_labels": labels,
                "values": [[int(v) / max(int(drawn[r]), 1) for v in grid[r]] for r in range(outputs)],
            },
            {
                "type": "bars", "title": "IoU, Dice, precision and recall per class",
                "help": "IoU and Dice both compare the marked area with the drawn one (Dice is the more forgiving). Precision: how much of what the model marked was drawn. Recall: how much of what was drawn it marked.",
                "labels": [row["name"] for row in rows],
                "series": [{"label": label, "values": [row[key] for row in rows]} for key, label in (("iou", "IoU"), ("dice", "Dice"), ("precision", "Precision"), ("recall", "Recall"))],
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    dataset = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))

    classes = dataset["classes"]
    label_of = {cls["id"]: i for i, cls in enumerate(classes, start=1)}  # 0 is the background
    outputs = len(classes) + 1
    names = [cls["name"] for cls in classes]
    epochs, batch, size = int(settings["epochs"]), int(settings["batch_size"]), int(settings["image_size"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
    print(f"Network: {settings['architecture']}   Classes: {', '.join(cls['name'] for cls in classes)}")

    def loader(name: str, train: bool) -> DataLoader:
        data = Painted(args.dataset, dataset["sets"][name], label_of, size, augment=train)
        print(f"{name}: {len(data)} images")
        # A batch of one cannot be normalised in training: drop a last odd image rather than fail on it.
        return DataLoader(data, batch_size=batch if train else 1, shuffle=train, num_workers=0, drop_last=train and len(data) > batch)

    train_loader, val_loader = loader("train", True), loader("val", False)
    model = build_model(settings["architecture"], outputs).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(settings["learning_rate"]), weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")

    saved = {"kind": "segmentation", "architecture": settings["architecture"], "image_size": size, "classes": classes}
    best = {"miou": -1.0, "epoch": 0}
    for epoch in range(1, epochs + 1):
        model.train()
        started, total, seen = time.time(), 0.0, 0
        for images, targets in train_loader:
            if len(images) < 2:
                continue  # see the loader: one image alone cannot be normalised
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
                loss = region_loss(model(images.to(device))["out"], targets.to(device))
            if not torch.isfinite(loss):
                raise SystemExit("The loss became infinite. Lower the learning rate and try again.")
            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            total, seen = total + loss.item(), seen + 1
        schedule.step()

        val = evaluate(model, val_loader, device, names)
        if val["miou"] > best["miou"]:
            best = {"miou": val["miou"], "epoch": epoch}
            torch.save({**saved, "state_dict": model.state_dict(), "epoch": epoch}, args.output / "model.pt")
        print(f"epoch {epoch}/{epochs}  loss {total / max(seen, 1):.4f}  val loss {val['loss']:.4f}  val mean IoU {val['miou']:.3f}  ({time.time() - started:.0f}s)")
        print("VP_METRIC " + json.dumps({
            "epoch": epoch, "epochs": epochs, "train_loss": round(total / max(seen, 1), 5), "val_loss": round(val["loss"], 5),
            "val_miou": round(val["miou"], 4), "val_pixel_accuracy": round(val["pixel_accuracy"], 4),
        }), flush=True)

    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])  # score the kept (best) epoch

    result = {"primary_metric": "miou", "best_epoch": best["epoch"], "val": rounded(evaluate(model, val_loader, device, names, full=True))}
    if "test" in dataset["sets"]:
        result["test"] = rounded(evaluate(model, loader("test", False), device, names, full=True))
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Done. Best epoch {best['epoch']}, validation mean IoU {result['val']['miou']:.3f}" + (f", test mean IoU {result['test']['miou']:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
