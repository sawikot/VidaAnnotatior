"""Image classifier (torchvision): one class per patch, learned from the labelled patches.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Reads   <dataset>/dataset.json and each set's labels.csv and images.
Prints  one "VP_METRIC {json}" line per epoch.
Writes  <output>/model.pt (the best epoch, by validation accuracy) and <output>/result.json.

Which network is trained is the "architecture" setting: the name of one of torchvision's
classification models (resnet50, efficientnet_b0, convnext_tiny, vit_b_16, swin_t, ...).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time
from pathlib import Path

import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset
import torchvision.models as networks
from torchvision.transforms.functional import pil_to_tensor, resize

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)  # what the pretrained network expects
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
# Set by the recipe check's own tests: build the network without downloading pretrained weights.
PRETRAINED = os.environ.get("VP_NO_PRETRAINED") != "1"


class Labelled(Dataset):
    """The images of one set with their class, as an index into the dataset's class list."""

    def __init__(self, root: Path, layout: dict, index_of: dict[str, int], size: int, augment: bool):
        with (root / layout["labels"]).open(encoding="utf-8", newline="") as file:
            rows = [row for row in csv.DictReader(file) if row["class"] in index_of]
        self.items = [(root / layout["images"] / row["file"], index_of[row["class"]]) for row in rows]
        self.size, self.augment = size, augment

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int):
        file, label = self.items[i]
        image = resize(pil_to_tensor(Image.open(file).convert("RGB")), [self.size, self.size], antialias=True).float() / 255
        if self.augment:  # tissue has no "right way round": mirror and turn it freely
            if random.random() < 0.5:
                image = image.flip(-1)
            if random.random() < 0.5:
                image = image.flip(-2)
            image = torch.rot90(image, random.randrange(4), dims=(-2, -1))
        return (image - MEAN) / STD, label


def new_head(model: torch.nn.Module, classes: int) -> torch.nn.Module:
    """Replace the network's last layer -- the one that names the class -- with one for this project's
    classes. Every torchvision classifier ends in such a layer, wherever it keeps it."""
    name, last = [(n, m) for n, m in model.named_modules() if isinstance(m, torch.nn.Linear)][-1]
    parent = model
    *path, leaf = name.split(".")
    for part in path:
        parent = getattr(parent, part)
    setattr(parent, leaf, torch.nn.Linear(last.in_features, classes))
    return model


def build_model(architecture: str, classes: int):
    make = getattr(networks, architecture)
    try:
        model = make(weights="DEFAULT" if PRETRAINED else None, progress=False)
    except Exception as exc:  # noqa: BLE001 - no internet: learn from scratch rather than not at all
        print(f"Could not download the pretrained network ({exc}); starting from an untrained one, which needs many more examples.")
        model = make(weights=None)
    return new_head(model, classes)


@torch.no_grad()
def evaluate(model, loader, device, classes: int) -> dict:
    model.eval()
    right = torch.zeros(classes)
    seen = torch.zeros(classes)
    loss, batches = 0.0, 0
    for images, labels in loader:
        scores = model(images.to(device)).float().cpu()
        loss += float(torch.nn.functional.cross_entropy(scores, labels))
        batches += 1
        guess = scores.argmax(1)
        for cls in range(classes):
            mine = labels == cls
            seen[cls] += int(mine.sum())
            right[cls] += int((guess[mine] == cls).sum())
    total = float(seen.sum())
    return {
        "loss": loss / max(batches, 1),
        "accuracy": float(right.sum()) / total if total else 0.0,
        # Of the images of each class, the share recognised as it (only classes this set has).
        "per_class": {cls: float(right[cls] / seen[cls]) for cls in range(classes) if seen[cls]},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    dataset = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))

    classes = dataset["classes"]
    index_of = {cls["name"]: i for i, cls in enumerate(classes)}
    epochs, batch, size = int(settings["epochs"]), int(settings["batch_size"]), int(settings["image_size"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
    print(f"Network: {settings['architecture']}   Classes: {', '.join(cls['name'] for cls in classes)}")

    def loader(name: str, train: bool) -> DataLoader:
        data = Labelled(args.dataset, dataset["sets"][name], index_of, size, augment=train)
        print(f"{name}: {len(data)} images")
        return DataLoader(data, batch_size=batch, shuffle=train, num_workers=0, drop_last=train and len(data) > batch)

    train_loader, val_loader = loader("train", True), loader("val", False)
    model = build_model(settings["architecture"], len(classes)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(settings["learning_rate"]), weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")

    saved = {"kind": "classification", "architecture": settings["architecture"], "image_size": size, "classes": classes}
    best = {"accuracy": -1.0, "epoch": 0}
    for epoch in range(1, epochs + 1):
        model.train()
        started, total, seen = time.time(), 0.0, 0
        for images, labels in train_loader:
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
                loss = torch.nn.functional.cross_entropy(model(images.to(device)), labels.to(device))
            if not torch.isfinite(loss):
                raise SystemExit("The loss became infinite. Lower the learning rate and try again.")
            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            total, seen = total + loss.item(), seen + 1
        schedule.step()

        val = evaluate(model, val_loader, device, len(classes))
        if val["accuracy"] > best["accuracy"]:
            best = {"accuracy": val["accuracy"], "epoch": epoch}
            torch.save({**saved, "state_dict": model.state_dict(), "epoch": epoch}, args.output / "model.pt")
        print(f"epoch {epoch}/{epochs}  loss {total / max(seen, 1):.4f}  val loss {val['loss']:.4f}  val accuracy {val['accuracy']:.3f}  ({time.time() - started:.0f}s)")
        print("VP_METRIC " + json.dumps({
            "epoch": epoch, "epochs": epochs, "train_loss": round(total / max(seen, 1), 5), "val_loss": round(val["loss"], 5), "val_accuracy": round(val["accuracy"], 4),
        }), flush=True)

    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])  # score the kept (best) epoch

    def summary(scores: dict) -> dict:
        return {"accuracy": round(scores["accuracy"], 4), "per_class": {classes[i]["name"]: round(v, 4) for i, v in scores["per_class"].items()}}

    result = {"primary_metric": "accuracy", "best_epoch": best["epoch"], "val": summary(evaluate(model, val_loader, device, len(classes)))}
    if "test" in dataset["sets"]:
        result["test"] = summary(evaluate(model, loader("test", False), device, len(classes)))
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Done. Best epoch {best['epoch']}, validation accuracy {result['val']['accuracy']:.3f}" + (f", test accuracy {result['test']['accuracy']:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
