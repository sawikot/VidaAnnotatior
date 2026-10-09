"""Uses a trained Mask R-CNN on new images (see ../README.md, "predict.py").

Each object found comes back as an outline ({"polygon": [[x, y], ...]}, in the image's pixels) traced
round its mask, with its class and how sure the model is.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision.models.detection as detectors
from PIL import Image
from torchvision.transforms.functional import pil_to_tensor


def outline(mask: np.ndarray) -> list[list[float]] | None:
    """The outline of the largest piece of a mask, with needless points along straight runs dropped."""
    pieces, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not pieces:
        return None
    largest = max(pieces, key=cv2.contourArea)
    simple = cv2.approxPolyDP(largest, max(0.5, 0.01 * cv2.arcLength(largest, True)), True).reshape(-1, 2)
    return [[float(x), float(y)] for x, y in simple] if len(simple) >= 3 else None


@torch.no_grad()
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    saved = torch.load(args.model, map_location=device)
    size = int(saved["image_size"])
    model = getattr(detectors, saved["architecture"])(weights=None, weights_backbone=None, num_classes=len(saved["classes"]) + 1, min_size=size, max_size=max(size, int(size * 1.5)))
    model.load_state_dict(saved["state_dict"])
    model.to(device).eval()
    class_of = {label: int(class_id) for class_id, label in saved["label_of"].items()}  # 1..N back to the dataset's ids
    print("VP_READY", flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        floor = float(request.get("min_score", 0.05))
        results = []
        for file in request["images"]:
            image = pil_to_tensor(Image.open(file).convert("RGB")).float().div(255).to(device)
            found = model([image])[0]
            here = []
            for mask, label, score in zip(found["masks"], found["labels"].cpu(), found["scores"].float().cpu()):
                if float(score) < floor or int(label) not in class_of:
                    continue
                points = outline((mask[0] >= 0.5).cpu().numpy())
                if points:
                    here.append({"polygon": points, "class_id": class_of[int(label)], "score": round(float(score), 4)})
            results.append(here)
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
