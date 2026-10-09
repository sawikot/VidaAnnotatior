"""Runs a model trained with Ultralytics elsewhere -- YOLO or RT-DETR (see ../README.md, "predict.py").

What comes back follows the model: boxes from a detector, outlines from a "-seg" model, and from a
"-cls" model one class for the whole image. Detections name their class by its place in the model's
own class list (``"class": 0`` is its first class), which the file carries; the app maps that to a
class of the project.

Ultralytics is licensed under AGPL-3.0; it is installed from requirements.txt the first time this runs.
An Ultralytics .pt file is a pickle and can carry code, unlike plain weights: add only files you trust.
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
    config_file = args.model.parent / "config.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
    size = int(config.get("image_size") or 1024)

    try:
        model = YOLO(str(args.model))
        if type(model.model).__name__.startswith("RTDETR"):
            model = RTDETR(str(args.model))
    except Exception as exc:  # noqa: BLE001 - whatever Ultralytics made of the file, say it plainly
        raise SystemExit(f"This is not a model Ultralytics can open: {exc}") from None
    if model.task not in ("detect", "segment", "classify"):
        raise SystemExit(f"This is a \"{model.task}\" model; only detectors, \"-seg\" and \"-cls\" models can make suggestions here.")
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    print("VP_READY " + json.dumps({"classes": [str(names[i]) for i in sorted(names)], "class_count": len(names), "task": model.task}), flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        floor = float(request.get("min_score", 0.05))
        results = []
        for found in model.predict(request["images"], imgsz=size, conf=floor, device=device, verbose=False):
            here = []
            if found.probs is not None:
                here.append({"class": int(found.probs.top1), "score": round(float(found.probs.top1conf), 4)})
            elif found.boxes is not None:
                outlines = found.masks.xy if found.masks is not None else None
                for i, (box, label, score) in enumerate(zip(found.boxes.xyxy.cpu(), found.boxes.cls.cpu(), found.boxes.conf.cpu())):
                    item = {"class": int(label), "score": round(float(score), 4)}
                    if outlines is None:
                        item["box"] = [round(float(v), 2) for v in box]
                    else:
                        points = np.asarray(outlines[i], dtype=np.float32)
                        if len(points) < 3:
                            continue
                        simple = cv2.approxPolyDP(points, max(0.5, 0.01 * cv2.arcLength(points, True)), True).reshape(-1, 2)
                        if len(simple) < 3:
                            continue
                        item["polygon"] = [[float(x), float(y)] for x, y in simple]
                    here.append(item)
            results.append(here)
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
