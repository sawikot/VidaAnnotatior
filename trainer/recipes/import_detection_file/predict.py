"""Runs an object detector exported to ONNX or TorchScript (see ../README.md, "predict.py").

Two families of output are understood:

* ``yolo``  -- one table per image. Either raw (each row: centre x, centre y, width, height, then a
  score per class -- v8 and later -- or with an "is an object" score before them -- v5), in which case
  overlapping boxes are thinned here; or already final (each row: x0, y0, x1, y1, score, class).
  Images are fitted into the model's square input with grey borders, as YOLO was trained.
* ``boxes`` -- separate boxes, labels and scores (torchvision detectors), given the image as it is.

Detections name their class by its place in the model's class list; the app maps it to the project's.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision.ops import batched_nms

NMS_IOU = 0.5
MAX_DETECTIONS = 300


class OnnxModel:
    def __init__(self, path: Path, device: str):
        import onnxruntime as ort

        wanted = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(path), providers=[p for p in wanted if p in ort.get_available_providers()])
        self.input = self.session.get_inputs()[0]
        self.outputs = len(self.session.get_outputs())
        shape = self.input.shape
        self.batched = len(shape) == 4
        self.fixed = (shape[-2], shape[-1]) if all(isinstance(v, int) for v in shape[-2:]) else None
        self.half = "float16" in self.input.type
        self.names = _names(self.session.get_modelmeta().custom_metadata_map.get("names"))

    def run(self, image: np.ndarray) -> list[np.ndarray]:
        data = image[None] if self.batched else image
        return self.session.run(None, {self.input.name: data.astype(np.float16 if self.half else np.float32)})


class ScriptModel:
    def __init__(self, path: Path, device: str):
        extra = {"config.txt": ""}
        self.device = torch.device(device)
        self.model = torch.jit.load(str(path), map_location=self.device, _extra_files=extra).eval()
        self.fixed, self.batched, self.outputs = None, True, 1
        self.names: list[str] = []
        try:  # Ultralytics keeps the class names and input size here
            meta = json.loads(extra["config.txt"]) if extra["config.txt"] else {}
            self.names = _names(meta.get("names"))
            if meta.get("imgsz"):
                self.fixed = tuple(meta["imgsz"][-2:]) if isinstance(meta["imgsz"], list) else (meta["imgsz"], meta["imgsz"])
        except (ValueError, TypeError):
            pass

    @torch.no_grad()
    def run(self, image: np.ndarray, as_list: bool = False) -> list[np.ndarray]:
        tensor = torch.from_numpy(image).to(self.device)
        out = self.model([tensor]) if as_list else self.model(tensor[None])
        if as_list:  # torchvision: (losses, detections) when scripted, or just the detections
            found = out[1][0] if isinstance(out, tuple) else out[0]
            return [found["boxes"].cpu().numpy(), found["labels"].cpu().numpy(), found["scores"].cpu().numpy()]
        first = out[0] if isinstance(out, (tuple, list)) else out
        return [first.cpu().numpy()]


def _names(raw) -> list[str]:
    """Class names as exporters store them: {0: 'a', 1: 'b'} (possibly as text), or a list."""
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    if isinstance(raw, dict):
        return [str(raw[key]) for key in sorted(raw, key=lambda k: int(k))]
    return [str(name) for name in raw] if isinstance(raw, (list, tuple)) else []


def letterbox(image: Image.Image, height: int, width: int) -> tuple[np.ndarray, float, float, float]:
    """The image fitted into height x width, centred on grey; with the scale and offsets to undo it."""
    scale = min(width / image.width, height / image.height)
    new_w, new_h = max(1, round(image.width * scale)), max(1, round(image.height * scale))
    canvas = Image.new("RGB", (width, height), (114, 114, 114))
    left, top = (width - new_w) // 2, (height - new_h) // 2
    canvas.paste(image.resize((new_w, new_h), Image.BILINEAR), (left, top))
    return np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1) / 255, scale, left, top


def _is_final(table: np.ndarray, known_classes: int) -> bool:
    """Whether six-column rows are finished detections (corners, score, class number) rather than raw
    ones that merely have six numbers: corners are ordered, and the last column counts classes."""
    classes = table[:, 5]
    return bool(
        len(table) == 0
        or (np.all(table[:, 2] >= table[:, 0]) and np.all(table[:, 3] >= table[:, 1]) and np.allclose(classes, np.round(classes))
            and (not known_classes or classes.max() < known_classes))
    )


def decode_yolo(table: np.ndarray, known_classes: int, floor: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Boxes (x0, y0, x1, y1), scores and classes from one image's YOLO output, in the model's input pixels."""
    table = np.asarray(table, dtype=np.float32)
    table = table[0] if table.ndim == 3 else table
    if table.shape[1] == 6 and _is_final(table, known_classes):  # x0, y0, x1, y1, score, class
        keep = table[:, 4] >= floor
        return table[keep, :4], table[keep, 4], table[keep, 5].astype(int)
    # One row per box is wanted; exports from v8 on give one row per number instead.
    widths = {4 + known_classes, 5 + known_classes} if known_classes else set()
    if table.shape[1] not in widths and (table.shape[0] in widths or table.shape[1] < 5 or (not widths and table.shape[0] < table.shape[1])):
        table = table.T
    columns = table.shape[1]
    if known_classes and columns == 5 + known_classes:
        scores_all = table[:, 5:] * table[:, 4:5]  # v5: an "is an object" score first
    else:
        scores_all = table[:, 4:]
    classes = scores_all.argmax(axis=1)
    scores = scores_all[np.arange(len(table)), classes]
    keep = scores >= floor
    table, scores, classes = table[keep], scores[keep], classes[keep]
    cx, cy, w, h = table[:, 0], table[:, 1], table[:, 2], table[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    if len(boxes):
        chosen = batched_nms(torch.from_numpy(boxes), torch.from_numpy(scores), torch.from_numpy(classes), NMS_IOU)[:MAX_DETECTIONS].numpy()
        boxes, scores, classes = boxes[chosen], scores[chosen], classes[chosen]
    return boxes, scores, classes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    config_file = args.model.parent / "config.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
    device = args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu"

    onnx = args.model.suffix.lower() == ".onnx"
    model = OnnxModel(args.model, device) if onnx else ScriptModel(args.model, device)
    layout = config.get("layout", "auto")
    if layout == "auto":
        layout = "boxes" if model.outputs >= 3 else "yolo"
    first = config.get("first_label", "auto")
    first_label = int(first) if first in ("0", "1") else (1 if layout == "boxes" else 0)
    size = model.fixed or (int(config.get("image_size") or 640),) * 2
    print("VP_READY " + json.dumps({"classes": model.names, "class_count": len(model.names) or None, "layout": layout}), flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        floor = float(request.get("min_score", 0.05))
        results = []
        for file in request["images"]:
            image = Image.open(file).convert("RGB")
            if layout == "yolo":
                data, scale, left, top = letterbox(image, size[0], size[1])
                boxes, scores, classes = decode_yolo(model.run(data)[0], len(model.names), floor)
                boxes = (boxes - np.array([left, top, left, top], dtype=np.float32)) / scale
            else:
                data = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 255
                sx = sy = 1.0
                if model.fixed:  # the file accepts one size only: resize, and scale the boxes back
                    sx, sy = image.width / model.fixed[1], image.height / model.fixed[0]
                    data = np.asarray(image.resize((model.fixed[1], model.fixed[0]), Image.BILINEAR), dtype=np.float32).transpose(2, 0, 1) / 255
                outputs = model.run(data) if onnx else model.run(data, as_list=True)
                boxes = next(o for o in outputs if o.ndim >= 2 and o.shape[-1] == 4).reshape(-1, 4) * np.array([sx, sy, sx, sy], dtype=np.float32)
                flat = [o.reshape(-1) for o in outputs if not (o.ndim >= 2 and o.shape[-1] == 4)]
                classes = next(o for o in flat if np.issubdtype(o.dtype, np.integer)).astype(int)
                scores = next(o for o in flat if np.issubdtype(o.dtype, np.floating))
                keep = scores >= floor
                boxes, scores, classes = boxes[keep], scores[keep], classes[keep]
            results.append([
                {"box": [round(float(v), 2) for v in box], "class": int(cls) - first_label, "score": round(float(score), 4)}
                for box, score, cls in zip(boxes, scores, classes)
            ])
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
