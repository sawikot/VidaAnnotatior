"""Uses the trained model on new images, for suggestions in the annotation workspace.

    python predict.py --model <model.pt> --device <cuda|cpu>

It is started once and kept running, so the model is loaded once: print "VP_READY" when it is loaded,
then answer each request line with one "VP_RESULT {json}" line. Fill in the two places marked TODO.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")  # "cpu" while a training run is using the graphics card
    args = parser.parse_args()

    # TODO 1: load what train.py saved.
    model = json.loads(args.model.read_text(encoding="utf-8"))
    classes = model["classes"]
    print("VP_READY", flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)  # {"id": 7, "images": ["<file>", ...], "min_score": 0.05}
        results = []
        for file in request["images"]:
            # TODO 2: run the model on this image. One entry per object found, boxes in the image's pixels,
            # "class_id" one of the ids in `classes`, "score" between 0 and 1, for example:
            #     {"box": [x0, y0, x1, y1], "class_id": classes[0]["id"], "score": 0.9}
            found: list[dict] = []
            results.append([d for d in found if d["score"] >= request.get("min_score", 0.05)])
        print("VP_RESULT " + json.dumps({"id": request["id"], "results": results}), flush=True)


if __name__ == "__main__":
    main()
