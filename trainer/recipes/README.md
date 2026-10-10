# Model recipes

A recipe is a folder here. The app lists it and builds its Start form from `recipe.json`; the trainer
(`../trainer.py`) runs its code. The app itself never runs recipe code.

| File | What it is for |
|---|---|
| `recipe.json` | Name, task (`detection`, `classification` or `segmentation`), description, and the settings a person may choose |
| `train.py` | Learns from the dataset and writes the trained model |
| `predict.py` | Uses the trained model on new images, for suggestions in the workspace |
| `requirements.txt` | Extra packages (optional) |

## recipe.json

```json
{
  "name": "Shown in the app",
  "task": "detection",
  "description": "One or two sentences.",
  "settings": [
    { "key": "epochs", "label": "Epochs", "type": "int", "default": 30, "min": 1, "max": 500, "help": "..." },
    { "key": "model_size", "label": "Model size", "type": "choice", "default": "small",
      "choices": [{ "value": "small", "label": "Small" }, { "value": "large", "label": "Large" }] }
  ]
}
```

Setting types: `int`, `float`, `choice`, `bool`. `"advanced": true` hides a setting until asked for.
A setting named `epochs` is also what the app shows as the run's length.

## What train.py is given

```
python train.py --dataset <folder> --output <folder> --settings <file.json>
```

- `--settings`: the chosen settings as JSON, already checked against `recipe.json`.
- `--dataset`: cut by the app from the project's annotations and its train / val / test split. For
  detection and segmentation recipes:

```
dataset.json                          {"task", "format": "coco", "classes": [{"id", "name"}], "sets": {...}}
train/annotations/dataset_coco.json   COCO; boxes and polygons in each image's own pixels
train/images/<name>.jpg
val/...    test/...                   test is missing when the project's test set is empty
```

`dataset.json`'s `sets` gives each set's `annotations` and `images` paths, relative to the folder.

For classification recipes each image has one class instead (a patch's label, else the drawn class
covering most of it):

```
dataset.json            {"task", "format": "folders", "classes": [{"id", "name"}], "sets": {...}}
train/labels.csv        one row per image: file (images/<class>/<name>.jpg, relative to train/), class, ...
train/images/<class>/<name>.jpg
val/...    test/...
```

Here `sets` gives each set's `labels` file and the folder (`images`) its `file` column is relative to.

## What train.py must do

- Print one line per finished epoch, starting with `VP_METRIC ` and followed by JSON with at least
  `epoch` and `epochs`; every other number in it (`train_loss`, `val_ap50`, ...) becomes a curve.
- Print anything else freely: it goes to the run's log.
- Write `<output>/model.pt`, the trained model.
- Write `<output>/result.json`: the final scores, e.g.
  `{"primary_metric": "ap50", "best_epoch": 12, "val": {"ap50": 0.81}, "test": {"ap50": 0.78}}`.
  See "The results page" below for everything a set's scores may hold.
- End with exit code 0. Anything else, or a missing file, marks the run as failed and shows the
  last lines of the log.

When someone presses Stop, the process is ended; whatever `model.pt` was written so far is kept.

## The results page

A finished run's page shows the test set's scores first (images the model never saw), the validation
set's beside them. Under `"val"` and `"test"` in `result.json`, the app shows whatever it finds:

| Key | Shown as |
|---|---|
| any number, e.g. `"ap50": 0.78` | A score. Numbers are shares from 0 to 1 and are shown as percentages. The one named by `primary_metric` is the headline. |
| `"counts": {"images": 40, "found": 310}` | Whole numbers, in a line under the scores |
| `"classes": [{"name": "Tumour", "ap50": 0.8, "objects": 120}]` | A table with a row per class and a column per key |
| `"per_class": {"Tumour": 0.8}` | The primary score per class; used when there is no `classes` |
| `"figures": [...]` | Figures, drawn by the app from the numbers given |

A figure is one of:

```json
{"type": "curve", "title": "Precision against recall", "x": "Recall", "y": "Precision", "help": "A sentence under it.",
 "series": [{"label": "All classes", "points": [[0, 1], [0.5, 0.9], [1, 0.2]]}],
 "mark": {"x": 0.35, "label": "Best F1"}, "diagonal": false}

{"type": "matrix", "title": "What was found as what", "rows": "Really", "columns": "Found as",
 "row_labels": ["Tumour", "Nothing there"], "column_labels": ["Tumour", "Not found"], "values": [[41, 9], [6, 0]]}

{"type": "bars", "title": "IoU per class", "labels": ["Tumour", "Stroma"], "series": [{"label": "IoU", "values": [0.7, 0.5]}]}
```

`mark`, `diagonal` and `help` are optional; a matrix with `"format": "percent"` holds shares of each
row instead of counts. Keep curves to about a hundred points: the result is stored with the run.

What the built-in recipes give:

- **Detectors and object outliners**: AP50, AP75, AP50-95; precision, recall and F1 at the confidence
  where F1 is best, and that confidence; objects found, missed and false alarms; the precision-recall
  curve, precision / recall / F1 by confidence, and what was found as what.
- **Classifiers**: accuracy, balanced accuracy, precision, recall and F1 averaged over the classes,
  AUC; what was recognised as what, the scores per class, and a ROC curve per class.
- **Region segmenters**: mean IoU, Dice, pixel accuracy, precision and recall; what each area was
  marked as, and the scores per class.

## predict.py

Without it the model trains but cannot make suggestions. The trainer starts it once per model and
keeps it running, so the model is loaded only once:

```
python predict.py --model <model.pt> --device <cuda|cpu>
```

- Print the line `VP_READY` once the model is loaded. It may be followed by JSON saying what the
  model knows about itself, e.g. `VP_READY {"classes": ["tumour", "stroma"]}`.
- Then read requests from standard input, one JSON object per line:
  `{"id": 7, "images": ["<file>", ...], "min_score": 0.05}`
- Answer each with one line: `VP_RESULT ` followed by
  `{"id": 7, "results": [[{"box": [x0, y0, x1, y1], "class_id": 3, "score": 0.91}], ...]}` --
  one list per image, in that image's pixels, `class_id` as in the dataset's `classes` (save what
  you need for that in `model.pt`). What a detection holds says what it is: a `box` is a rectangle;
  a `polygon` (`[[x, y], ...]`) is an outline; with neither, it is a class for the whole image,
  offered in the workspace as a suggested patch label (give one per image).

`--device` is `cpu` while a training run is using the graphics card.

## Importers: running models trained elsewhere

A recipe with a `predict.py`, no `train.py`, and `"import": {"extensions": [".onnx"]}` in its
`recipe.json` is offered under *Add a model trained elsewhere* for files with those extensions. The
uploaded file is saved as `model<extension>` next to a `config.json` holding the settings chosen on the
form, and `predict.py` is started with `--model` pointing at it. Such a model has classes of its own,
so its detections say `"class": <place in the model's class list, from 0>` instead of `class_id`, and
`VP_READY`'s `classes` (if the file carries the names) fills in the form where the person says which
class of the project each one is.
