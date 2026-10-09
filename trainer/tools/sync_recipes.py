"""Writes the built-in recipes from the list below and the shared code in ../shared.

Every built-in recipe is a complete folder of its own -- recipe.json, train.py, predict.py -- so that
it can be read, duplicated and changed in the app without knowing about any other. Recipes for the
same kind of task run the same code (trainer/shared/<kind>), told which network to build by their
"architecture" setting; this script copies that code into each and writes its recipe.json.

    python trainer/tools/sync_recipes.py            write the recipes
    python trainer/tools/sync_recipes.py --check    say what is out of date, change nothing (exit 1 if anything)

To add a model: add it below and run this. To change the training code: change trainer/shared and run this.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TRAINER = Path(__file__).resolve().parents[1]
RECIPES = TRAINER / "recipes"
SHARED = TRAINER / "shared"

EPOCHS_HELP = "How many times the model sees every training image."
BATCH_HELP = "Images per step. Lower it if the run fails with 'out of memory'."
LR_HELP = "How big each learning step is. The default suits most projects."


def number(key, label, kind, default, low, high, help_text="", advanced=False):
    out = {"key": key, "label": label, "type": kind, "default": default, "min": low, "max": high}
    if help_text:
        out["help"] = help_text
    if advanced:
        out["advanced"] = True
    return out


def recipe(code, task, name, description, networks, *, epochs, image, batch, lr=None, image_help="", check_image=None, pick_help=""):
    """One recipe. ``networks``: [(torchvision name, what to call it)], the first being the default.
    ``image``: (default, min, max) of the image size, or None when the network fixes its own.
    ``lr``: the default learning rate, or None when the training code chooses it itself."""
    pick = {
        "key": "architecture", "label": "Network", "type": "choice", "default": networks[0][0],
        "choices": [{"value": value, "label": label} for value, label in networks],
    }
    if pick_help:
        pick["help"] = pick_help
    settings = [pick, number("epochs", "Epochs", "int", epochs, 1, 1000, EPOCHS_HELP)]
    check = {"epochs": 1, "batch_size": 2}
    if image:
        settings.append(number("image_size", "Image size (px)", "int", image[0], image[1], image[2], image_help))
        check["image_size"] = check_image or image[1]
    settings.append(number("batch_size", "Batch size", "int", batch, 1, 256, BATCH_HELP))
    if lr is not None:
        settings.append(number("learning_rate", "Learning rate", "float", lr, 0.000001, 0.1, LR_HELP, advanced=True))
    return {"code": code, "doc": {"name": name, "task": task, "description": description, "settings": settings, "check_settings": check}}


DETECT_SIZE = "Patches are resized so their shorter side is this long. Larger keeps small objects visible but needs more memory."
CLASSIFY_SIZE = "Patches are resized to a square of this size. Larger keeps fine detail but is slower."
REGION_SIZE = "Patches are resized to a square of this size. Larger follows outlines more closely but needs more memory."
PRETRAINED = "Starts from a network already trained on everyday photographs, so it learns from few examples."

YOLO_SIZES = {
    "n": "Nano - fastest, fits any graphics card", "t": "Tiny - fastest, fits any graphics card", "s": "Small",
    "m": "Medium", "b": "Balanced", "c": "Compact - needs 8 GB or more", "l": "Large - needs 8 GB or more",
    "e": "Extended - most accurate, needs a big graphics card", "x": "Extra large - most accurate, needs a big graphics card",
}
ULTRALYTICS = "Uses the Ultralytics package (AGPL-3.0 licence), installed the first time it is used; that needs internet."
YOLO_SIZE = "Patches are resized so their longer side is this long. Larger keeps small objects visible but needs more memory."


def yolo(task, name, description, pattern, sizes, *, image=(1024, 160, 2048), batch=4, epochs=100):
    """A family of Ultralytics models: ``pattern`` with {} where the size letter goes."""
    what = {"detection": "Finds objects and draws a box around each one.", "segmentation": "Finds each object and traces its outline, keeping touching objects apart.",
            "classification": "Gives each patch (or image) one class, learned from Patch Labels."}[task]
    return recipe(
        "ultralytics", task, name, f"{what} {description} {PRETRAINED} {ULTRALYTICS}",
        [(pattern.format(size), YOLO_SIZES[size]) for size in sizes],
        epochs=epochs, image=image, batch=batch, image_help=CLASSIFY_SIZE if task == "classification" else YOLO_SIZE, check_image=160,
        pick_help="Start with the smallest; move up when you have many examples and the scores stop improving.",
    )


BUILT_IN = {
    # ------------------------------------------------------------------ detection: a box round each object
    "detection_fasterrcnn": recipe(
        "detection", "detection", "Faster R-CNN detector",
        f"Finds objects and draws a box around each one (cells, parasites, mitoses). The dependable all-rounder; start here. {PRETRAINED}",
        [("fasterrcnn_mobilenet_v3_large_fpn", "Small (MobileNet) - fast, fits a 4 GB graphics card"),
         ("fasterrcnn_resnet50_fpn", "Large (ResNet-50) - more accurate, needs 8 GB or more"),
         ("fasterrcnn_resnet50_fpn_v2", "Large v2 (ResNet-50, improved) - most accurate, slowest")],
        epochs=30, image=(1024, 256, 2048), batch=2, lr=0.005, image_help=DETECT_SIZE, check_image=320,
        pick_help="Start small; move to large when you have many examples and the scores stop improving.",
    ),
    "detection_retinanet": recipe(
        "detection", "detection", "RetinaNet detector",
        f"Finds objects with one pass over the image. Built for scenes where the objects are few among a lot of background, as cells on a slide often are. {PRETRAINED}",
        [("retinanet_resnet50_fpn", "ResNet-50 - needs 8 GB or more"),
         ("retinanet_resnet50_fpn_v2", "ResNet-50 v2 (improved) - more accurate, slower")],
        epochs=30, image=(1024, 256, 2048), batch=2, lr=0.002, image_help=DETECT_SIZE, check_image=320,
    ),
    "detection_fcos": recipe(
        "detection", "detection", "FCOS detector",
        f"Finds objects without preset box shapes, so it suits objects of unusual or widely varying size. {PRETRAINED}",
        [("fcos_resnet50_fpn", "ResNet-50 - needs 8 GB or more")],
        epochs=30, image=(1024, 256, 2048), batch=2, lr=0.002, image_help=DETECT_SIZE, check_image=320,
    ),
    "detection_ssdlite": recipe(
        "detection", "detection", "SSDlite detector (lightweight)",
        f"A very small, very fast detector that works at a fixed 320 px, so it only suits objects that are large in the patch. Usable without a graphics card. {PRETRAINED}",
        [("ssdlite320_mobilenet_v3_large", "MobileNet - fits any graphics card, or none")],
        epochs=40, image=None, batch=8, lr=0.005,
    ),
    "detection_ssd": recipe(
        "detection", "detection", "SSD300 detector",
        f"A classic single-pass detector working at a fixed 300 px: fast, and only for objects that are large in the patch. {PRETRAINED}",
        [("ssd300_vgg16", "VGG-16 - needs 4 GB or more")],
        epochs=40, image=None, batch=4, lr=0.001,
    ),
    "detection_yolo26": yolo("detection", "YOLO26 detector", "The newest YOLO: fast, and made to do well on small objects.", "yolo26{}", "nsmlx"),
    "detection_yolo12": yolo("detection", "YOLO12 detector", "A YOLO built round attention, which helps it use the surroundings of an object.", "yolo12{}", "nsmlx"),
    "detection_yolo11": yolo("detection", "YOLO11 detector", "A widely used, well-rounded YOLO: a good first choice among them.", "yolo11{}", "nsmlx"),
    "detection_yolov10": yolo("detection", "YOLOv10 detector", "A YOLO that gives its final boxes directly, without a clean-up step, which makes it quick to answer.", "yolov10{}", "nsmblx"),
    "detection_yolov9": yolo("detection", "YOLOv9 detector", "A YOLO designed to lose less detail on the way through the network.", "yolov9{}", "tsmce"),
    "detection_yolov8": yolo("detection", "YOLOv8 detector", "The long-established YOLO that most published comparisons use.", "yolov8{}", "nsmlx"),
    "detection_yolov5": yolo("detection", "YOLOv5 detector", "The classic YOLO, in its updated form: light and dependable.", "yolov5{}u", "nsmlx"),
    "detection_yolov5_p6": yolo(
        "detection", "YOLOv5 detector for large images", "The classic YOLO in its variant for large images, which keeps small objects visible.",
        "yolov5{}6u", "nsmlx", image=(1280, 320, 2048), batch=2,
    ),
    "detection_yolov3": recipe(
        "ultralytics", "detection", "YOLOv3 detector",
        f"Finds objects and draws a box around each one. An early YOLO, kept for comparison with older work. {PRETRAINED} {ULTRALYTICS}",
        [("yolov3-tinyu", "Tiny - fastest"), ("yolov3u", "Standard - needs 8 GB or more"), ("yolov3-sppu", "SPP - most accurate of the three")],
        epochs=100, image=(1024, 160, 2048), batch=4, image_help=YOLO_SIZE, check_image=160,
    ),
    "detection_rtdetr": recipe(
        "ultralytics", "detection", "RT-DETR detector",
        f"Finds objects and draws a box around each one, with a transformer rather than a YOLO network: accurate, heavy, and in want of more examples. {PRETRAINED} {ULTRALYTICS}",
        [("rtdetr-l", "Large - needs 8 GB or more"), ("rtdetr-x", "Extra large - needs a big graphics card")],
        epochs=100, image=(640, 320, 1280), batch=2, image_help=YOLO_SIZE, check_image=320,
    ),
    # ------------------------------------------------------------------ classification: one class per patch
    "classification_yolo26": yolo("classification", "YOLO26 classifier", "The newest YOLO's classifier: small and fast.", "yolo26{}-cls", "nsmlx", image=(384, 64, 1280), batch=16, epochs=50),
    "classification_yolo11": yolo("classification", "YOLO11 classifier", "A small, fast classifier from the YOLO11 family.", "yolo11{}-cls", "nsmlx", image=(384, 64, 1280), batch=16, epochs=50),
    "classification_yolov8": yolo("classification", "YOLOv8 classifier", "The long-established YOLO's classifier.", "yolov8{}-cls", "nsmlx", image=(384, 64, 1280), batch=16, epochs=50),
    "classification_resnet": recipe(
        "classification", "classification", "ResNet classifier",
        f"Gives each patch (or image) one class, learned from Patch Labels. The dependable all-rounder; start here. {PRETRAINED}",
        [("resnet18", "ResNet-18 - fast"), ("resnet50", "ResNet-50 - more accurate, slower"), ("resnet101", "ResNet-101 - largest, slowest")],
        epochs=20, image=(384, 64, 2048), batch=16, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_resnext": recipe(
        "classification", "classification", "ResNeXt and Wide ResNet classifiers",
        f"One class per patch. Wider relatives of ResNet-50: usually a little more accurate for the same depth, and heavier. {PRETRAINED}",
        [("resnext50_32x4d", "ResNeXt-50"), ("wide_resnet50_2", "Wide ResNet-50 - largest")],
        epochs=20, image=(384, 64, 2048), batch=8, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_efficientnet": recipe(
        "classification", "classification", "EfficientNet classifier",
        f"One class per patch. Reaches good accuracy with a small network, which makes it a good choice on a modest graphics card. {PRETRAINED}",
        [("efficientnet_b0", "B0 - smallest, fastest"), ("efficientnet_b2", "B2 - balanced"), ("efficientnet_b4", "B4 - most accurate, slowest")],
        epochs=25, image=(384, 64, 2048), batch=16, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_efficientnetv2": recipe(
        "classification", "classification", "EfficientNetV2 classifier",
        f"One class per patch. The newer EfficientNet: trains faster and is more accurate, at a larger size. {PRETRAINED}",
        [("efficientnet_v2_s", "Small"), ("efficientnet_v2_m", "Medium - more accurate, needs 8 GB or more")],
        epochs=25, image=(384, 64, 2048), batch=8, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_convnext": recipe(
        "classification", "classification", "ConvNeXt classifier",
        f"One class per patch. A modern network that is among the most accurate here; worth trying when ResNet's scores level off. {PRETRAINED}",
        [("convnext_tiny", "Tiny"), ("convnext_small", "Small - more accurate"), ("convnext_base", "Base - most accurate, needs 8 GB or more")],
        epochs=25, image=(384, 64, 2048), batch=8, lr=0.0001, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_mobilenet": recipe(
        "classification", "classification", "MobileNet classifier (lightweight)",
        f"One class per patch, from a very small and fast network. For a machine without a strong graphics card, or for a quick first result. {PRETRAINED}",
        [("mobilenet_v3_large", "MobileNetV3 Large"), ("mobilenet_v3_small", "MobileNetV3 Small - smallest"), ("mobilenet_v2", "MobileNetV2")],
        epochs=25, image=(384, 64, 2048), batch=32, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_shufflenet": recipe(
        "classification", "classification", "ShuffleNet classifier (smallest)",
        f"One class per patch, from the smallest network offered. Fast even on a CPU; expect lower accuracy than the larger ones. {PRETRAINED}",
        [("shufflenet_v2_x1_0", "ShuffleNetV2 1.0x"), ("shufflenet_v2_x2_0", "ShuffleNetV2 2.0x - more accurate")],
        epochs=30, image=(384, 64, 2048), batch=32, lr=0.001, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_densenet": recipe(
        "classification", "classification", "DenseNet classifier",
        f"One class per patch. Long used in pathology research, so results are easy to compare with published work. {PRETRAINED}",
        [("densenet121", "DenseNet-121"), ("densenet169", "DenseNet-169 - larger")],
        epochs=20, image=(384, 64, 2048), batch=8, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_regnet": recipe(
        "classification", "classification", "RegNet classifier",
        f"One class per patch. A family of networks from very small to large with the same design, handy for finding the size your data needs. {PRETRAINED}",
        [("regnet_y_400mf", "RegNetY-400MF - small"), ("regnet_y_1_6gf", "RegNetY-1.6GF - medium"), ("regnet_y_8gf", "RegNetY-8GF - large")],
        epochs=20, image=(384, 64, 2048), batch=16, lr=0.0005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    "classification_vit": recipe(
        "classification", "classification", "Vision Transformer classifier",
        f"One class per patch, from a transformer that looks at the whole patch at once. It works at a fixed 224 px and wants more examples than the others. {PRETRAINED}",
        [("vit_b_16", "ViT-B/16 - needs 8 GB or more"), ("vit_b_32", "ViT-B/32 - coarser, faster")],
        epochs=25, image=(224, 224, 224), batch=16, lr=0.00005, image_help="This network only takes 224 px images.",
    ),
    "classification_swin": recipe(
        "classification", "classification", "Swin Transformer classifier",
        f"One class per patch, from a transformer that takes any image size. Among the most accurate here given enough examples. {PRETRAINED}",
        [("swin_t", "Swin-T"), ("swin_s", "Swin-S - more accurate, needs 8 GB or more")],
        epochs=25, image=(384, 64, 1024), batch=8, lr=0.00005, image_help=CLASSIFY_SIZE, check_image=96,
    ),
    # ------------------------------------------------------------------ segmentation: regions, or each object's outline
    "segmentation_deeplab": recipe(
        "segmentation", "segmentation", "DeepLabV3 region segmenter",
        f"Outlines regions of each class (tumour, stroma, necrosis), learned from polygons and brushed areas. Everything not drawn counts as background, so annotate the patches you train on completely. {PRETRAINED}",
        [("deeplabv3_mobilenet_v3_large", "Small (MobileNet) - fast, fits a 4 GB graphics card"),
         ("deeplabv3_resnet50", "Large (ResNet-50) - more accurate, needs 8 GB or more"),
         ("deeplabv3_resnet101", "Largest (ResNet-101) - most accurate, slowest")],
        epochs=40, image=(512, 128, 2048), batch=4, lr=0.0005, image_help=REGION_SIZE, check_image=160,
    ),
    "segmentation_lraspp": recipe(
        "segmentation", "segmentation", "LR-ASPP region segmenter (lightweight)",
        f"Outlines regions of each class with a very small, fast network. Everything not drawn counts as background. For a quick first result, or a modest graphics card. {PRETRAINED}",
        [("lraspp_mobilenet_v3_large", "MobileNet - fits any graphics card")],
        epochs=40, image=(512, 128, 2048), batch=4, lr=0.0005, image_help=REGION_SIZE, check_image=160,
    ),
    "segmentation_fcn": recipe(
        "segmentation", "segmentation", "FCN region segmenter",
        f"Outlines regions of each class with the classic fully convolutional network: simple and a common baseline. Everything not drawn counts as background. {PRETRAINED}",
        [("fcn_resnet50", "ResNet-50 - needs 8 GB or more"), ("fcn_resnet101", "ResNet-101 - more accurate, slower")],
        epochs=40, image=(512, 128, 2048), batch=4, lr=0.0005, image_help=REGION_SIZE, check_image=160,
    ),
    "segmentation_yolo26": yolo("segmentation", "YOLO26 object outliner", "The newest YOLO, for separate things such as cells, nuclei or parasites.", "yolo26{}-seg", "nsmlx"),
    "segmentation_yolo11": yolo("segmentation", "YOLO11 object outliner", "A well-rounded YOLO, for separate things such as cells, nuclei or parasites.", "yolo11{}-seg", "nsmlx"),
    "segmentation_yolov9": yolo("segmentation", "YOLOv9 object outliner", "A larger YOLO for outlining separate objects.", "yolov9{}-seg", "ce", batch=2),
    "segmentation_yolov8": yolo("segmentation", "YOLOv8 object outliner", "The long-established YOLO, for separate things such as cells, nuclei or parasites.", "yolov8{}-seg", "nsmlx"),
    "segmentation_maskrcnn": recipe(
        "instances", "segmentation", "Mask R-CNN (outlines each object)",
        f"Finds each object and traces its outline, keeping touching objects apart. For separate things -- cells, nuclei, glands, parasites -- rather than regions of tissue. {PRETRAINED}",
        [("maskrcnn_resnet50_fpn", "ResNet-50 - needs 8 GB or more"),
         ("maskrcnn_resnet50_fpn_v2", "ResNet-50 v2 (improved) - more accurate, slower")],
        epochs=30, image=(1024, 256, 2048), batch=1, lr=0.003, image_help=DETECT_SIZE, check_image=320,
    ),
}


def wanted() -> dict[Path, str]:
    """Every file a built-in recipe should hold, with its content."""
    files: dict[Path, str] = {}
    for recipe_id, spec in BUILT_IN.items():
        files[RECIPES / recipe_id / "recipe.json"] = json.dumps(spec["doc"], indent=2) + "\n"
        for source in sorted((SHARED / spec["code"]).iterdir()):  # train.py, predict.py, and requirements.txt if it needs packages
            if source.is_file():
                files[RECIPES / recipe_id / source.name] = source.read_text(encoding="utf-8")
    return files


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for path, content in wanted().items():
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            stale.append(path)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
    for path in stale:
        print(("out of date: " if check else "wrote ") + str(path.relative_to(TRAINER)))
    if not stale:
        print(f"{len(BUILT_IN)} recipes up to date")
    return 1 if check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
