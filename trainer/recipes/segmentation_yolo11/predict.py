"""Uses a trained Ultralytics model (YOLO, RT-DETR) on new images (see ../README.md, "predict.py").

What comes back follows the model: boxes from a detector, outlines from a "-seg" model, and from a
"-cls" model one class for the whole image (which the app offers as a suggested patch label).
The model's classes are read from model.json, written beside model.pt by train.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import RTDETR, YOLO


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    device = 0 if args.device != "cpu" and torch.cuda.is_available() else "cpu"
    about = json.loads((args.model.parent / "model.json").read_text(encoding="utf-8"))
    classes, size = about["classes"], int(about["image_size"])
    model = (RTDETR if about["architecture"].startswith("rtdetr") else YOLO)(str(args.model))
    print("VP_READY", flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        floor = float(request.get("min_score", 0.05))
        results = []
        for found in model.predict(request["images"], imgsz=size, conf=floor, device=device, verbose=False):
            here = []
            if found.probs is not None:  # a classifier: one class for the image
                here.append({"class_id": classes[int(found.probs.top1)]["id"], "score": round(float(found.probs.top1conf), 4)})
            elif found.boxes is not None:
                outlines = found.masks.xy if found.masks is not None else None
                for i, (box, label, score) in enumerate(zip(found.boxes.xyxy.cpu(), found.boxes.cls.cpu(), found.boxes.conf.cpu())):
                    item = {"class_id": classes[int(label)]["id"], "score": round(float(score), 4)}
                    if outlines is None:
                        item["box"] = [round(float(v), 2) for v in box]
                    else:
                        points = np.asarray(outlines[i], dtype=np.float32)
                        if len(points) < 3:
                            continue
                        # Drop the needless points along straight runs: hundreds make an outline hard to edit.
                        simple = cv2.approxPolyDP(points, max(0.5, 0.01 * cv2.arcLength(points, True)), True).reshape(-1, 2)
                        if len(simple) < 3:
                            continue
                        item["polygon"] = [[float(x), float(y)] for x, y in simple]
                    here.append(item)
            results.append(here)
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
