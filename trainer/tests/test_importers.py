"""The importers that run models trained elsewhere, each started the way the trainer starts it.

These need PyTorch and onnxruntime, so they run in the trainer's environment, not the backend's:

    trainer/.venv/Scripts/python -m pytest trainer/tests        (Linux/macOS: trainer/.venv/bin/python)
"""
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import pytest
import torch
from onnx import TensorProto, helper, numpy_helper
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trainer  # noqa: E402

RECIPES = Path(__file__).resolve().parents[1] / "recipes"


def constant_model(path: Path, input_shape, outputs: dict[str, np.ndarray], names: dict | None = None) -> None:
    """An ONNX file that answers the same thing whatever it is shown."""
    nodes = [helper.make_node("Constant", [], [name], value=numpy_helper.from_array(value)) for name, value in outputs.items()]
    graph = helper.make_graph(
        nodes, "constant",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, input_shape)],
        [helper.make_tensor_value_info(name, TensorProto.INT64 if value.dtype == np.int64 else TensorProto.FLOAT, list(value.shape)) for name, value in outputs.items()],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9
    if names:
        entry = model.metadata_props.add()
        entry.key, entry.value = "names", str(names)
    onnx.save(model, path)


@pytest.fixture()
def ask(tmp_path, monkeypatch):
    """Load a model file with an importer and ask it about one image; gives (what it said about itself, detections)."""
    monkeypatch.setattr(trainer, "TRAINING_DIR", tmp_path)
    image = tmp_path / "patch.jpg"
    Image.new("RGB", (128, 64), (200, 180, 200)).save(image)

    def run(importer: str, weights: Path, config: dict | None = None):
        (weights.parent / "config.json").write_text(json.dumps(config or {}))
        job = {"id": 1, "model_id": 1, "code": str(RECIPES / importer), "weights": str(weights), "images": [str(image)], "min_score": 0.25}
        loaded = trainer.Predictor(job, "cpu")
        try:
            return loaded.info, loaded.ask(job)[0]
        finally:
            loaded.close()

    return run


def test_raw_yolo_output_is_thinned_and_put_back_into_the_image(ask, tmp_path):
    # Rows: centre x, centre y, width, height, then a score per class -- as (4 + classes, boxes).
    table = np.array([
        [32, 32, 20, 20, 0.9, 0.1],   # a cat
        [33, 32, 20, 20, 0.8, 0.1],   # the same cat again
        [10, 24, 8, 8, 0.05, 0.7],    # a dog
        [50, 50, 6, 6, 0.1, 0.1],     # nothing sure enough
    ], dtype=np.float32).T[None]
    constant_model(tmp_path / "model.onnx", [1, 3, 64, 64], {"output0": table}, names={0: "cat", 1: "dog"})
    info, found = ask("import_detection_file", tmp_path / "model.onnx")
    assert info["classes"] == ["cat", "dog"] and info["layout"] == "yolo"
    # The 128 x 64 image fills the 64 x 64 input at half size, 16 px down: boxes come back in image pixels.
    assert [(d["class"], d["score"], d["box"]) for d in found] == [(0, 0.9, [44.0, 12.0, 84.0, 52.0]), (1, 0.7, [12.0, 8.0, 28.0, 24.0])]


def test_yolo_with_an_objectness_score_and_final_yolo_tables(ask, tmp_path):
    v5 = np.array([[[32, 32, 20, 20, 0.5, 0.9, 0.1]]], dtype=np.float32)  # is-an-object 0.5 x cat 0.9
    constant_model(tmp_path / "model.onnx", [1, 3, 64, 64], {"output0": v5}, names={0: "cat", 1: "dog"})
    _, found = ask("import_detection_file", tmp_path / "model.onnx")
    assert [(d["class"], d["score"]) for d in found] == [(0, 0.45)]

    final = np.array([[[22, 22, 42, 42, 0.8, 1], [0, 0, 5, 5, 0.1, 0]]], dtype=np.float32)  # x0, y0, x1, y1, score, class
    constant_model(tmp_path / "model.onnx", [1, 3, 64, 64], {"output0": final})
    info, found = ask("import_detection_file", tmp_path / "model.onnx")
    assert info["classes"] == [] and [(d["class"], d["box"]) for d in found] == [(1, [44.0, 12.0, 84.0, 52.0])]


def test_boxes_labels_and_scores_as_torchvision_exports_them(ask, tmp_path):
    outputs = {
        "boxes": np.array([[10, 20, 30, 40], [1, 1, 2, 2]], dtype=np.float32),
        "labels": np.array([2, 1], dtype=np.int64),  # 0 is the background, so these are the second and first class
        "scores": np.array([0.75, 0.1], dtype=np.float32),
    }
    constant_model(tmp_path / "model.onnx", [3, "height", "width"], outputs)
    info, found = ask("import_detection_file", tmp_path / "model.onnx")
    assert info["layout"] == "boxes" and found == [{"box": [10.0, 20.0, 30.0, 40.0], "class": 1, "score": 0.75}]


def test_torchscript_yolo(ask, tmp_path):
    class Fixed(torch.nn.Module):
        def forward(self, images: torch.Tensor) -> torch.Tensor:
            row = torch.tensor([[32.0], [32.0], [20.0], [20.0], [0.9], [0.1]])
            return row.unsqueeze(0) + images.sum() * 0

    torch.jit.script(Fixed()).save(str(tmp_path / "model.torchscript"))
    _, found = ask("import_detection_file", tmp_path / "model.torchscript", {"image_size": 64})
    assert [(d["class"], d["box"]) for d in found] == [(0, [44.0, 12.0, 84.0, 52.0])]


def test_plain_faster_rcnn_weights_and_what_is_not_one(ask, tmp_path):
    from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn

    net = fasterrcnn_mobilenet_v3_large_fpn(weights=None, weights_backbone=None, num_classes=4)
    torch.save(net.state_dict(), tmp_path / "model.pt")
    info, found = ask("import_fasterrcnn", tmp_path / "model.pt", {"image_size": 256})
    assert info == {"classes": [], "class_count": 3}  # read off the weights: four outputs, one is background
    assert all(0 <= d["class"] < 3 for d in found)

    torch.save({"layer.weight": torch.zeros(2)}, tmp_path / "model.pt")
    with pytest.raises(RuntimeError, match="not Faster R-CNN weights"):
        ask("import_fasterrcnn", tmp_path / "model.pt")
