"""A recipe to start from: it has the whole shape of a training script and learns nothing yet.

    python train.py --dataset <folder> --output <folder> --settings <file.json>

Fill in the three places marked TODO. Everything else is what the app needs from any recipe:
one "VP_METRIC {json}" line per epoch, <output>/model.pt and <output>/result.json.
The full description is in the app's Model recipes page ("How recipes work").
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_set(dataset: Path, layout: dict) -> tuple[list[dict], dict[int, list[dict]]]:
    """One set (train, val or test): its images, and each image's annotations by image id.

    An image is {"id", "file_name", "width", "height"}; open it at dataset / layout["images"] / file_name.
    An annotation is COCO: {"category_id", "bbox": [x, y, width, height], "segmentation": [[x0, y0, x1, y1, ...]]},
    in that image's own pixels.
    """
    doc = json.loads((dataset / layout["annotations"]).read_text(encoding="utf-8"))
    by_image: dict[int, list[dict]] = {}
    for annotation in doc["annotations"]:
        by_image.setdefault(annotation["image_id"], []).append(annotation)
    return doc["images"], by_image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()

    settings = json.loads(args.settings.read_text(encoding="utf-8"))  # the values chosen on the Start form
    dataset = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))
    classes = dataset["classes"]  # [{"id": 3, "name": "Tumor"}, ...] -- the project's classes
    train_images, train_annotations = load_set(args.dataset, dataset["sets"]["train"])
    val_images, val_annotations = load_set(args.dataset, dataset["sets"]["val"])
    print(f"Classes: {', '.join(c['name'] for c in classes)}")
    print(f"train: {len(train_images)} images   val: {len(val_images)} images")

    # TODO 1: build your model here (import torch, torchvision, timm, ... at the top of the file).
    model = {"classes": classes, "note": "replace me with real weights"}

    epochs = int(settings["epochs"])
    best = {"score": -1.0, "epoch": 0}
    for epoch in range(1, epochs + 1):
        # TODO 2: train for one epoch on train_images / train_annotations, then score on the validation set.
        train_loss = 1.0 / epoch
        val_score = 0.0

        if val_score > best["score"]:
            best = {"score": val_score, "epoch": epoch}
            # TODO 3: save whatever predict.py needs to load the model again (e.g. torch.save(...)).
            (args.output / "model.pt").write_text(json.dumps(model), encoding="utf-8")

        # Anything printed goes to the run's log...
        print(f"epoch {epoch}/{epochs}  loss {train_loss:.4f}  val score {val_score:.3f}")
        # ...and this line becomes a point on the run's curves. "epoch" and "epochs" are needed; every
        # other number gets a curve of its own (names ending in "loss" share the loss chart).
        print("VP_METRIC " + json.dumps({"epoch": epoch, "epochs": epochs, "train_loss": train_loss, "val_ap50": val_score}), flush=True)

    # The final scores, shown on the run's page. "ap50" is what detection recipes report.
    result = {"primary_metric": "ap50", "best_epoch": best["epoch"], "val": {"ap50": best["score"]}}
    if "test" in dataset["sets"]:  # missing when the project's test set is empty
        result["test"] = {"ap50": 0.0}
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
