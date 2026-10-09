"""Semantic segmentation (torchvision LR-ASPP or DeepLabV3): every pixel gets a class or background.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Reads   <dataset>/dataset.json and each set's COCO file and images; the polygons are painted into a
        label image (0 = background, 1..N = the classes; where shapes overlap the later one wins).
Prints  one "VP_METRIC {json}" line per epoch.
Writes  <output>/model.pt (the best epoch, by validation mean IoU) and <output>/result.json.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from torch.utils.data import DataLoader, Dataset
from torchvision.models.segmentation import deeplabv3_resnet50, lraspp_mobilenet_v3_large
from torchvision.transforms.functional import pil_to_tensor

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


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


def build_model(size: str, outputs: int):
    make = deeplabv3_resnet50 if size == "large" else lraspp_mobilenet_v3_large
    try:
        return make(weights=None, weights_backbone="DEFAULT", num_classes=outputs, progress=False)
    except Exception as exc:  # noqa: BLE001 - no internet: learn from scratch rather than not at all
        print(f"Could not download the pretrained network ({exc}); starting from an untrained one, which needs many more examples.")
        return make(weights=None, weights_backbone=None, num_classes=outputs)


@torch.no_grad()
def evaluate(model, loader, device, outputs: int) -> dict:
    """Mean IoU over the classes (not the background) that this set or the model's answer has."""
    model.eval()
    overlap, union = torch.zeros(outputs), torch.zeros(outputs)
    right = total = 0
    loss, batches = 0.0, 0
    for images, targets in loader:
        scores = model(images.to(device))["out"].float().cpu()
        loss += float(torch.nn.functional.cross_entropy(scores, targets))
        batches += 1
        guess = scores.argmax(1)
        right += int((guess == targets).sum())
        total += targets.numel()
        for cls in range(1, outputs):
            mine, theirs = targets == cls, guess == cls
            overlap[cls] += int((mine & theirs).sum())
            union[cls] += int((mine | theirs).sum())
    per_class = {cls: float(overlap[cls] / union[cls]) for cls in range(1, outputs) if union[cls]}
    return {
        "loss": loss / max(batches, 1),
        "miou": sum(per_class.values()) / len(per_class) if per_class else 0.0,
        "pixel_accuracy": right / total if total else 0.0,
        "per_class": per_class,
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
    label_of = {cls["id"]: i for i, cls in enumerate(classes, start=1)}  # 0 is the background
    outputs = len(classes) + 1
    epochs, batch, size = int(settings["epochs"]), int(settings["batch_size"]), int(settings["image_size"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
    print(f"Classes: {', '.join(cls['name'] for cls in classes)}")

    def loader(name: str, train: bool) -> DataLoader:
        data = Painted(args.dataset, dataset["sets"][name], label_of, size, augment=train)
        print(f"{name}: {len(data)} images")
        # A batch of one cannot be normalised in training: drop a last odd image rather than fail on it.
        return DataLoader(data, batch_size=batch if train else 1, shuffle=train, num_workers=0, drop_last=train and len(data) > batch)

    train_loader, val_loader = loader("train", True), loader("val", False)
    model = build_model(settings["model_size"], outputs).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(settings["learning_rate"]), weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")

    saved = {"recipe": "segmentation_deeplab", "model_size": settings["model_size"], "image_size": size, "classes": classes}
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

        val = evaluate(model, val_loader, device, outputs)
        if val["miou"] > best["miou"]:
            best = {"miou": val["miou"], "epoch": epoch}
            torch.save({**saved, "state_dict": model.state_dict(), "epoch": epoch}, args.output / "model.pt")
        print(f"epoch {epoch}/{epochs}  loss {total / max(seen, 1):.4f}  val loss {val['loss']:.4f}  val mean IoU {val['miou']:.3f}  ({time.time() - started:.0f}s)")
        print("VP_METRIC " + json.dumps({
            "epoch": epoch, "epochs": epochs, "train_loss": round(total / max(seen, 1), 5), "val_loss": round(val["loss"], 5),
            "val_miou": round(val["miou"], 4), "val_pixel_accuracy": round(val["pixel_accuracy"], 4),
        }), flush=True)

    model.load_state_dict(torch.load(args.output / "model.pt", map_location=device)["state_dict"])  # score the kept (best) epoch

    def summary(scores: dict) -> dict:
        return {
            "miou": round(scores["miou"], 4), "pixel_accuracy": round(scores["pixel_accuracy"], 4),
            "per_class": {classes[i - 1]["name"]: round(v, 4) for i, v in scores["per_class"].items()},
        }

    result = {"primary_metric": "miou", "best_epoch": best["epoch"], "val": summary(evaluate(model, val_loader, device, outputs))}
    if "test" in dataset["sets"]:
        result["test"] = summary(evaluate(model, loader("test", False), device, outputs))
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Done. Best epoch {best['epoch']}, validation mean IoU {result['val']['miou']:.3f}" + (f", test mean IoU {result['test']['miou']:.3f}" if "test" in result else ""))


if __name__ == "__main__":
    main()
