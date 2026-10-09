"""Runs detector weights that were trained elsewhere (see ../README.md, "predict.py").

Reads either a detector downloaded from VidaAnnotator -- any of its box detectors; the file says
which network it is and carries its classes and sizes -- or a plain torchvision Faster R-CNN state
dict, whose network (MobileNet or ResNet-50 backbone) and number of classes are read off the weights.
Detections name their class by its place in the model's own class list (``"class": 0`` is its first
class); the app maps that to a class of the project.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from PIL import Image
import torchvision.models.detection as detectors
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn, fasterrcnn_resnet50_fpn
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.transforms.functional import pil_to_tensor


def load(path: Path, device: torch.device):
    config_file = path.parent / "config.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
    saved = torch.load(path, map_location=device)  # weights only: a file cannot run code of its own
    if not isinstance(saved, dict):
        raise SystemExit("This file is a whole pickled model, not weights. Save model.state_dict() instead.")
    names: list[str] = []
    size = int(config.get("image_size") or 1024)
    if "state_dict" in saved:  # downloaded from VidaAnnotator
        if saved.get("kind", "detection") != "detection":
            raise SystemExit(f"This file is a {saved['kind']} model, not a box detector; it cannot be added here.")
        names = [cls["name"] for cls in saved.get("classes", [])]
        state = saved["state_dict"]
        if saved.get("architecture"):  # says which network it is
            fixed = saved.get("image_size")
            sizes = {} if not fixed else {"min_size": int(fixed), "max_size": max(int(fixed), int(int(fixed) * 1.5))}
            model = getattr(detectors, saved["architecture"])(weights=None, weights_backbone=None, num_classes=len(names) + 1, **sizes)
            model.load_state_dict(state)
            return model.to(device).eval(), names, len(names)
        size = int(saved.get("image_size") or size)
    else:
        state = saved.get("model", saved)  # torchvision's reference scripts save {"model": state_dict, ...}
    head = state.get("roi_heads.box_predictor.cls_score.weight")
    if head is None:
        raise SystemExit("These are not Faster R-CNN weights (no roi_heads.box_predictor in the file).")
    count = head.shape[0] - 1  # without the background
    make = fasterrcnn_resnet50_fpn if any(key.startswith("backbone.body.layer1.") for key in state) else fasterrcnn_mobilenet_v3_large_fpn
    model = make(weights=None, weights_backbone=None, min_size=size, max_size=max(size, int(size * 1.5)), box_score_thresh=0.01)
    model.roi_heads.box_predictor = FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features, count + 1)
    model.load_state_dict(state)
    return model.to(device).eval(), names, count


@torch.no_grad()
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    model, names, count = load(args.model, device)
    print("VP_READY " + json.dumps({"classes": names, "class_count": count}), flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        floor = float(request.get("min_score", 0.05))
        results = []
        for file in request["images"]:
            image = pil_to_tensor(Image.open(file).convert("RGB")).float().div(255).to(device)
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
                found = model([image])[0]
            results.append([
                {"box": [round(float(v), 2) for v in box], "class": int(label) - 1, "score": round(float(score), 4)}
                for box, label, score in zip(found["boxes"].float().cpu(), found["labels"].cpu(), found["scores"].float().cpu())
                if float(score) >= floor
            ])
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
