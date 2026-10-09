"""Uses a trained ResNet classifier on new images (see ../README.md, "predict.py").

A classifier finds no shapes: for each image it answers with the class it takes the whole image to be
and how sure it is -- no "box", so the app offers it as a suggested patch label.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from PIL import Image
from torchvision.models import resnet18, resnet50
from torchvision.transforms.functional import pil_to_tensor, resize

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


@torch.no_grad()
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    saved = torch.load(args.model, map_location=device)
    classes, size = saved["classes"], int(saved["image_size"])
    model = (resnet50 if saved["model_size"] == "large" else resnet18)(weights=None)
    model.fc = torch.nn.Linear(model.fc.in_features, len(classes))
    model.load_state_dict(saved["state_dict"])
    model.to(device).eval()
    print("VP_READY", flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        results = []
        for file in request["images"]:
            image = resize(pil_to_tensor(Image.open(file).convert("RGB")), [size, size], antialias=True).float() / 255
            sure = torch.softmax(model(((image - MEAN) / STD)[None].to(device)).float(), dim=1)[0].cpu()
            best = int(sure.argmax())
            results.append([{"class_id": classes[best]["id"], "score": round(float(sure[best]), 4)}])
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
