"""Uses a trained detector on new images. Started once per model and kept running:

    python predict.py --model <model.pt> --device <cuda|cpu>

Prints "VP_READY" once the model is loaded. Then, for each line read from standard input --
{"id": 7, "images": ["<file>", ...], "min_score": 0.05} -- prints one line:

    VP_RESULT {"id": 7, "results": [[{"box": [x0, y0, x1, y1], "class_id": 3, "score": 0.91}, ...], ...]}

with one list per image, boxes in that image's pixels and class ids as they were in the training dataset.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
import torchvision.models.detection as detectors
from PIL import Image
from torchvision.transforms.functional import pil_to_tensor


def load(path: Path, device: torch.device):
    saved = torch.load(path, map_location=device)
    size = saved.get("image_size")
    sizes = {} if not size else {"min_size": int(size), "max_size": max(int(size), int(int(size) * 1.5))}
    model = getattr(detectors, saved["architecture"])(weights=None, weights_backbone=None, num_classes=len(saved["classes"]) + 1, **sizes)
    model.load_state_dict(saved["state_dict"])
    class_of = {label: int(class_id) for class_id, label in saved["label_of"].items()}  # 1..N back to the dataset's ids
    return model.to(device).eval(), class_of


@torch.no_grad()
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    model, class_of = load(args.model, device)
    print("VP_READY", flush=True)

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
                {"box": [round(float(v), 2) for v in box], "class_id": class_of[int(label)], "score": round(float(score), 4)}
                for box, label, score in zip(found["boxes"].float().cpu(), found["labels"].cpu(), found["scores"].float().cpu())
                if float(score) >= floor and int(label) in class_of
            ])
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
