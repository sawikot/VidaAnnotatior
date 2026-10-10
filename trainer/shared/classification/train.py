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
GRID = [i / 100 for i in range(101)]  # where the curves are read off
MAX_CURVES = 8  # at most this many classes get a line of their own


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
def evaluate(model, loader, device, names: list[str], full: bool = False) -> dict:
    """How well the images are given their class. Always: the loss, accuracy, and the precision,
    recall and F1 averaged over the classes (so that a rare class weighs as much as a common one).
    ``full`` (the final scoring) adds a table per class, the area under each ROC curve, and figures."""
    model.eval()
    count = len(names)
    sure, real = [torch.zeros(0, count)], [torch.zeros(0, dtype=torch.long)]
    loss, batches = 0.0, 0
    for images, labels in loader:
        scores = model(images.to(device)).float().cpu()
        loss += float(torch.nn.functional.cross_entropy(scores, labels))
        batches += 1
        sure.append(torch.softmax(scores, dim=1))
        real.append(labels)
    sure, real = torch.cat(sure), torch.cat(real)
    # grid[really][recognised as]: how many images of each class were taken for each class.
    grid = torch.bincount(real * count + sure.argmax(1), minlength=count * count).reshape(count, count)
    seen, said, right = grid.sum(1), grid.sum(0), grid.diag()
    rows = []
    for cls in range(count):
        if not seen[cls]:
            continue  # a class this set has no image of has no score
        recall = float(right[cls] / seen[cls])
        precision = float(right[cls] / said[cls]) if said[cls] else 0.0
        rows.append({
            "name": names[cls], "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0, "images": int(seen[cls]),
        })
    mean = lambda key: sum(row[key] for row in rows) / len(rows) if rows else 0.0  # noqa: E731
    out = {
        "loss": loss / max(batches, 1),
        "accuracy": float(right.sum() / len(real)) if len(real) else 0.0,
        "balanced_accuracy": mean("recall"), "precision": mean("precision"), "recall": mean("recall"), "f1": mean("f1"),
        # Of the images of each class, the share recognised as it.
        "per_class": {row["name"]: row["recall"] for row in rows},
    }
    if not full:
        return out

    curves = []
    for row in rows:  # each class against all the others: is the model surer of it where it is there?
        cls = names.index(row["name"])
        order = sure[:, cls].argsort(descending=True)
        there = (real[order] == cls).float()
        if there.sum() == len(there):
            continue  # nothing else to tell it from
        found, alarms = there.cumsum(0) / there.sum(), (1 - there).cumsum(0) / (1 - there).sum()
        zero = torch.zeros(1)
        row["auc"] = float(torch.trapezoid(torch.cat([zero, found]), torch.cat([zero, alarms])))
        if len(curves) < MAX_CURVES:
            curves.append({"label": row["name"], "points": [[level, float(found[alarms <= level].max()) if (alarms <= level).any() else 0.0] for level in GRID]})
    scored = [row["auc"] for row in rows if "auc" in row]
    out.pop("loss")
    if scored:
        out["auc"] = sum(scored) / len(scored)
    out.update(
        classes=rows, counts={"images": len(real), "right": int(right.sum()), "wrong": len(real) - int(right.sum())},
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
    if curves:
        out["figures"].append({
            "type": "curve", "title": "ROC: each class against the rest", "x": "False alarms (share of the other images)", "y": "Recall",
            "help": "For each class: how many of its images are caught as the model is allowed more false alarms. A curve in the top left corner tells the class apart well; the diagonal would be guessing. The area under it is the AUC.",
            "diagonal": True, "series": curves,
        })
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
    index_of = {cls["name"]: i for i, cls in enumerate(classes)}
    names = [cls["name"] for cls in classes]
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

        val = evaluate(model, val_loader, device, names)
        if val["accuracy"] > best["accuracy"]:
            best = {"accuracy": val["accuracy"], "epoch": epoch}
            torch.save({**saved, "state_dict": model.state_dict(), "epoch": epoch}, args.output / "model.pt")
        print(f"epoch {epoch}/{epochs}  loss {total / max(seen, 1):.4f}  val loss {val['loss']:.4f}  val accuracy {val['accuracy']:.3f}  ({time.time() - started:.0f}s)")
        print("VP_METRIC " + json.dumps({
            "epoch": epoch, "epochs": epochs, "train_loss": round(total / max(seen, 1), 5), "val_loss": round(val["loss"], 5), "val_accuracy": round(val["accuracy"], 4),
        }), flush=True)

    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])  # score the kept (best) epoch

    result = {"primary_metric": "accuracy", "best_epoch": best["epoch"], "val": rounded(evaluate(model, val_loader, device, names, full=True))}
    if "test" in dataset["sets"]:
        result["test"] = rounded(evaluate(model, loader("test", False), device, names, full=True))
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Done. Best epoch {best['epoch']}, validation accuracy {result['val']['accuracy']:.3f}" + (f", test accuracy {result['test']['accuracy']:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
