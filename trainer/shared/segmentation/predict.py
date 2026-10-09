"""Uses a trained region segmenter on new images (see ../README.md, "predict.py").

The model gives every pixel a class; each connected region of one class comes back as an outline
({"polygon": [[x, y], ...]}, in the image's pixels) with the model's average sureness inside it.
Regions smaller than a thousandth of the image are dropped as specks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
import torchvision.models.segmentation as segmenters
from torchvision.transforms.functional import pil_to_tensor

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
MIN_AREA_SHARE = 0.001


def regions(labels: np.ndarray, sure: np.ndarray, classes: list[dict], floor: float) -> list[dict]:
    """Outlines of each class's regions in a label image, with how sure the model is of each."""
    found = []
    height, width = labels.shape
    for index, cls in enumerate(classes, start=1):
        mask = (labels == index).astype(np.uint8)
        if not mask.any():
            continue
        outlines, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for outline in outlines:
            if cv2.contourArea(outline) < MIN_AREA_SHARE * width * height:
                continue
            simple = cv2.approxPolyDP(outline, max(1.0, 0.002 * (width + height)), True).reshape(-1, 2)
            if len(simple) < 3:
                continue
            inside = np.zeros_like(mask)
            cv2.drawContours(inside, [outline], -1, 1, thickness=cv2.FILLED)
            score = float(sure[inside.astype(bool)].mean())
            if score >= floor:
                found.append({"polygon": [[float(x), float(y)] for x, y in simple], "class_id": cls["id"], "score": round(score, 4)})
    return found


@torch.no_grad()
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    saved = torch.load(args.model, map_location=device)
    classes, size = saved["classes"], int(saved["image_size"])
    model = getattr(segmenters, saved["architecture"])(weights=None, weights_backbone=None, num_classes=len(classes) + 1)
    model.load_state_dict(saved["state_dict"])
    model.to(device).eval()
    print("VP_READY", flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        floor = float(request.get("min_score", 0.05))
        results = []
        for file in request["images"]:
            picture = Image.open(file).convert("RGB")
            image = pil_to_tensor(picture.resize((size, size), Image.BILINEAR)).float() / 255
            scores = model(((image - MEAN) / STD)[None].to(device))["out"].float()
            # Back at the image's own size, so the outlines are in its pixels.
            scores = torch.nn.functional.interpolate(scores, size=(picture.height, picture.width), mode="bilinear", align_corners=False)
            sure, labels = torch.softmax(scores, dim=1)[0].max(0)
            results.append(regions(labels.cpu().numpy().astype(np.uint8), sure.cpu().numpy(), classes, floor))
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
