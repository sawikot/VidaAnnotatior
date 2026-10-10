# VirtualPatch WSI Annotator

A digital pathology Whole Slide Image (WSI) annotation platform built around one non-negotiable rule:

> **Annotate locally. Store globally.**

The original WSI is always the single source of pixel data. Virtual patches are pure coordinates
(`x, y, width, height, level`) generated dynamically from a tissue mask — **no patch image files are
ever extracted to disk.** Annotators draw inside a patch, but everything persisted to the database and
every JSON export is transformed into **WSI Level-0 absolute pixel coordinates**.

```
Original WSI → Tissue Detection → Virtual Patch Coordinates → Patch-wise Annotation
             → Local→Global Coordinate Transform → Full WSI JSON Export
```

## Architecture

```
frontend/   React 18 + TypeScript + Vite + Tailwind + Zustand + OpenSeadragon
backend/    FastAPI + SQLAlchemy 2 + Pydantic 2 + OpenSlide
data/       uploads/ (managed WSI files + tissue mask cache), database/ (SQLite), watch/ (path-import source)
```

Backend service modules (`backend/app/services/`):

| Module | Responsibility |
|---|---|
| `wsi_reader.py` | `WSIReader` interface. `OpenSlideReader` (real .svs/.tif/.tiff/.ndpi) and `ImageReader` (plain images for image projects) |
| `deepzoom_service.py` | Generates DZI tiles on the fly for the OpenSeadragon viewer — a *display* concern, unrelated to annotation patches |
| `tissue_detector.py` | `TissueDetector` interface. `HSVOtsuDetector`: HSV→Otsu threshold→morphology→small-component removal |
| `patch_generator.py` | Walks the Level-0 grid at the configured patch/stride size, keeps patches meeting the tissue threshold |
| `coordinate_transform.py` | The **single** source of truth for Level-0 ⇄ patch-local math (mirrored in `frontend/src/utils/coordinates.ts`) |
| `config_versioning.py` | Editing rules for the project's one configuration (tissue method fixed once patches exist) and diagnostic-class sync |
| `exporter/` | Export format registry (`get_exporter`). One small class per format — `wsi_json`, `geojson`, `coco`, `patch_csv`, `stats_csv`, `patch_classification` — all reading the same `ExportData` snapshot (see *Export formats* below) |
| `geometry.py` | Shape kinds, validation and Shapely-backed area/length helpers shared by the API and the exporters |
| `projection.py` | Shows whole-slide annotations inside patches: `local = (level0 − origin) / downsample`, clipped to each patch |
| `image_import.py` | Finds and validates plain images for *image projects* (see below); `ImageReader` in `wsi_reader.py` presents one as a single-level slide |

## Annotation tools

| Tool | Key | How | Stored as |
|---|---|---|---|
| Select / Move | `V` | Click a shape to select it, drag to move it (stays inside the patch; undoable), `Delete` removes it | — |
| Point | `N` | Click | `point`: 1 point |
| Line | `L` | Drag from start to end | `line`: 2 points |
| Freehand line | `G` | Drag along a path | `freehand_line`: an open path, ≥ 2 points |
| Rectangle | `R` | Drag a corner to the opposite corner | `rectangle`: 4 corners |
| Circle | `C` | Drag from the centre outwards | `circle`: the centre and one point on the edge |
| Polygon | `P` | Click the vertices, then click the first point, double-click or press `Enter`; `Backspace` takes back the last point. Panning mid-shape (Space) keeps it | `polygon`: ≥ 3 points |
| Freehand polygon | `F` | Drag around the outline | `freehand`: a closed outline, ≥ 3 points |
| Brush | `B` | Drag to paint. *New*: each stroke is a shape of its own. *Add*: the stroke grows every shape of the active class it touches, and the shape it starts on whatever its class; shapes it connects become one, and touching none it paints a new shape. *Erase* (or hold `Shift` in the other modes): the stroke is cut out of the shapes it crosses. `[` / `]` change the size | `freehand`: the outline of what was painted |

**Modifying an annotation** (Select tool, or click an object in the workspace's object list): the selected
shape shows handles. Drag the shape to move it; drag a handle to reshape it — a polygon, freehand or line
vertex; a rectangle's corners (the opposite corner stays fixed) or side midpoints; a circle's edge handle
to resize it and its centre handle to move it. Double-click an outline to add a point, a point to remove
it (a polygon keeps at least 3, a freehand line 2). The *Edit* panel under the object list changes its
class, unsure/flag marks and a note, or deletes it. Every one of these changes is saved at once and can be
undone with `Ctrl+Z` (redo with `Ctrl+Shift+Z`); a shape can only be given a class of its own project
configuration (the server answers 422 otherwise).

**The brush** works on area shapes (polygon, freehand, rectangle, circle) and has its own bar of options under
the toolbar: the mode, the size (in screen pixels, so it covers the same part of the screen at any zoom) and,
for the eraser, whether it cuts into only the shapes of the active class (the default) or every shape. A rectangle or circle the
brush reworks becomes a `freehand` outline. A shape is one outline without holes, so erasing right through a
shape leaves several shapes (each keeps the class, marks and note), erasing all of it deletes it, and rubbing
in the middle of a shape does nothing until the stroke reaches its edge. One stroke is one undo step, however
many shapes it changed. The fill of a patch label is left alone. The same brush draws tissue regions on the
slide processing page, where "the active class" is the kind picked there (add tissue or remove tissue). The brush is offered wherever the freehand
polygon tool is enabled. Outlines are worked out with [clipper-lib](https://www.npmjs.com/package/clipper-lib)
(`frontend/src/utils/brush.ts`).

**New, Add or Erase for the shape tools.** Rectangle, Circle, Polygon and Freehand polygon show the same *New* /
*Add* / *Erase* choice under the toolbar (not for points and lines, which have no area to join). *New* is how they have always
worked. With *Add*, a shape drawn onto shapes of the active class is joined to them -- several become one --
and the result is a `polygon` (a `freehand` outline if either was one); drawn touching none, it is a new shape
as usual. With *Erase*, the drawn shape is not kept: it is cut out of the shapes it crosses, by the brush's
rules (pieces, no holes, every shape or the active class only). The choice is kept apart from the brush's mode, and applies to tissue regions too.

`Esc` abandons a shape in progress. Which of these a project offers is set in its configuration
(*Config → Tools*); Select is always available, and new projects get all of them. Projects created
before the line/freehand-line/circle tools existed were upgraded automatically, unless their tool list
had been customised.

A circle is stored as centre + edge point, not as a polygon, so it stays exact: the local→Level-0
transform is a uniform scale plus a shift, so the centre and the radius convert exactly. The server checks
each shape (right number of points, finite numbers, a line with length, a circle with a radius) and
answers 422 otherwise. The type name `freehand` has always meant the closed freehand polygon and is kept
so existing data and exports stay valid.

How each export treats them: **WSI JSON** keeps the native shape and adds `radius` (circle) or `length`
(lines); **GeoJSON** writes lines as `LineString` (with `length_px`) and circles as 64-sided polygons
(with the exact `area_px2` and `radius_px`); **COCO** has no lines or points, so those are counted in
`info.vp_skipped` (`point_annotations`, `line_annotations`) and circles go in as their 64-sided outline;
**masks** paint circles as discs and leave lines and points out; **statistics CSV** counts circles as
polygons (exact πr² area) and reports lines in `n_lines`, `summed_length_px` and `summed_length_um`
(blank without resolution metadata).

## Annotating in a patch or on the whole slide

The workspace has a **Patch | WSI** switch (WSI projects; an image is its own patch). The last choice is
remembered per browser, and `?mode=wsi` / `?mode=patch` in the URL selects one directly.

- **Patch mode** — annotate one patch at a time, at full detail, in the patch's own pixels. Stored per patch
  (patch-local *and* Level-0 coordinates), as before.
- **WSI mode** — draw directly on the whole slide (zoom and pan freely) with the same tools, in **Level-0
  pixels**. Such an annotation belongs to *no patch*: it may cross hundreds of them, or lie where no patch was
  generated, so only its Level-0 coordinates are stored (`patch_id` is `NULL`, `coordinates_patch_local` is
  empty). It survives regenerating the patch grid. The Pan tool (`H`) moves around; in any other tool, hold
  `Space` to pan and scroll to zoom. Handles, lines and minimum sizes are constant on screen at any zoom.

Each kind is **edited in the view that created it** and **shown in the other**:

- Patch view: whole-slide annotations appear faintly (dashed), projected into the patch through its origin and
  downsample and clipped by its border, and are listed under *From the whole slide*. Pressing one jumps to it on
  the slide.
- WSI view: patch-drawn annotations appear faintly (toggle: *Patch annotations*); pressing one offers *Open in
  patch view*, with it selected. A *Patch grid* toggle shows where the patches are.

Exports treat the two kinds like this:

| Format | Slide-level annotations |
|---|---|
| WSI JSON | Included in Level-0, with `source_patch: null` (re-importable); each patch's `slide_annotation_count` says how many reach it. |
| GeoJSON | Included in Level-0 (`drawn_in: "slide"`, patch properties null). |
| Statistics CSV | Counted **once, in full** (a shape crossing 50 patches is one polygon); `n_patches` is the patches it reaches. |
| COCO, masks, patch images | Through the patches: the piece of the shape inside each patch, in that patch's pixels (a shape wholly inside a patch is kept as drawn, a circle stays a circle; otherwise it becomes polygon pieces). Each piece has a unique `id` (`annotation id × 10⁹ + patch id`), `vp_source_annotation_id`, `vp_scope: "slide"` and `vp_clipped`. Only the part inside generated patches is included. |
| Patch CSV | `n_annotations` counts own + reaching slide-level shapes; `n_slide_annotations` the latter; `dominant_class` uses the area *inside the patch*. |

A patch reached by a slide-level annotation counts as **annotated** for the patch scope, so `annotated` /
`empty` mean what they say whichever view the shapes were drawn in. The slide-level annotations always belong
to the slide's own exports (WSI JSON, GeoJSON, statistics) whatever patch scope is chosen; the scope selects
patches. API: `POST /slides/{id}/annotations` (Level-0 coordinates, inside the slide, class of the slide's
configuration), `GET /slides/{id}/annotations?scope=all|patch|slide`; `PUT /annotations/{id}` takes
`coordinates_level0` for a slide-level annotation and `coordinates_patch_local` for a patch-drawn one.

Existing databases are upgraded at startup: `geometry_annotations.patch_id` used to be `NOT NULL`, and SQLite
cannot drop that in place, so the table is rebuilt in one transaction (rows copied, foreign keys re-checked,
rolled back on any problem).

## Coordinate model (read this before touching geometry code)

Four coordinate spaces exist. Only two of them are ever significant to you:

1. **Browser screen** — never stored, converted immediately via `getScreenCTM()`.
2. **Patch-display** — pixels of the image actually rendered (`width × height` returned by the dynamic
   patch endpoint). This is what the frontend draws in and what `coordinates_patch_local` stores.
3. **Patch-source origin** — the patch's `(x, y)` anchor in Level-0 pixels, plus the `downsample` of the
   pyramid level it was read at.
4. **WSI Level-0** — the master, absolute coordinate system. `coordinates_level0` and every JSON export
   use this space exclusively.

```
global = origin + local * downsample
```

Every `Patch` row stores both its display size (`width`, `height`, at pyramid `level`) and its Level-0
footprint (`width_l0`, `height_l0`), so `downsample = width_l0 / width` — no reader access is needed to
transform a coordinate, which is why `coordinate_transform.py` has zero dependency on OpenSlide.

Tests: `backend/tests/test_coordinate_transform.py` and `frontend/src/utils/coordinates.test.ts` share the
same fixture values (including the spec's own worked example: origin `(20000, 15000)` + local `(100, 80)`
at 1:1 resolution → `(20100, 15080)`), so the two implementations can't silently drift apart.

## Installing with Docker (DGX, servers, Windows)

The usual way to install the app: one Docker image, built for Intel/AMD and ARM (e.g. DGX Spark).
After the one-time install, administrators install new versions, or go back to older ones, from the
app itself (**menu → Version & updates**). Nobody touches Docker again.

**Install once**, on a machine with Docker (Linux: Docker Engine + compose plugin; Windows: Docker Desktop):

```bash
git clone https://github.com/sawikot/VidaAnnotatior.git
cd VidaAnnotatior
bash install.sh                                        # Windows: powershell -ExecutionPolicy Bypass -File install.ps1
```

The script creates `.env`, asks for a GitHub token while the repository is private (a *classic* token
with the `repo` and `read:packages` scopes; press Enter once the repository is public), downloads the
latest version and starts it on `http://<machine>:8088`. Slides and the database live in `data/` next
to it and survive every version change. `SLIDES_DIR` in `.env` makes an existing slide folder available
to *import from a path*.

**Releasing a version** (developers): push a tag.

```bash
git tag v1.2.0 && git push origin v1.2.0
```

`.github/workflows/release.yml` builds the image, publishes it to `ghcr.io/sawikot/vidaannotatior`, and
then creates the GitHub release. Its notes are what administrators read on the Version page. A tag
with a hyphen (`v1.3.0-beta.1`) is a pre-release and does not become `latest`. When the repository
becomes public, also make the package public (GitHub → Packages → vidaannotatior → Package settings),
so installs without a token can download it.

**How switching works:** `docker-compose.yml` runs the image twice: the app, and a small updater
(`updater/updater.py`) that alone may control Docker, has no open port, and answers only the app
(through a secret the two share on a volume). On *Install*, the updater downloads the version while
the app keeps running, stops the app, copies the database to `data/backups/`, starts the same
container on the new version and waits for it to answer. If it does not start, the previous version
and database come back by themselves. When going back to an older version, the administrator can also
restore the data as it was when that version was last used. The choice is written to `APP_VERSION` in
`.env`, so a restart keeps it.

## Model training

A project's annotations can train a model from the app (**Training** in the project menu). The work
is done by a second program, the **trainer** (`trainer/trainer.py`), so that hours of GPU work never
slow the app down and a crashing model can never take the app with it.

- **The app** keeps the list of training runs, cuts each run's dataset (the same COCO export with
  patch images, sorted by the project's train / validation / test split) and shows progress.
- **The trainer** asks the app for the next waiting run, runs that run's *model recipe* as a process of
  its own, and reports each epoch back. It talks to the app only over HTTP, with a secret kept in the
  one folder the two share (`data/training`), and never opens the database or the slides.
- **A model recipe** is a folder under `trainer/recipes` with a `recipe.json` (name, task, and the
  settings shown on the Start form) and a `train.py`. What a recipe is given and must write is
  described in `trainer/recipes/README.md`. Thirty-seven ship with the app, each offering its
  networks in several sizes. Built on torchvision, with nothing more to install:
  - **detectors** (a box round each object; learn from boxes, circles and outlines): Faster R-CNN,
    RetinaNet, FCOS, SSDlite, SSD300;
  - **classifiers** (one class per patch; learn from Patch Labels): ResNet, ResNeXt / Wide ResNet,
    EfficientNet, EfficientNetV2, ConvNeXt, MobileNet, ShuffleNet, DenseNet, RegNet, Vision
    Transformer, Swin Transformer;
  - **segmenters** (learn from polygons and brushed areas): DeepLabV3, LR-ASPP and FCN mark regions,
    everything not drawn being background; Mask R-CNN outlines each object separately.

  Built on **Ultralytics** (YOLO and RT-DETR):
  - **detectors**: YOLO26, YOLO12, YOLO11, YOLOv10, YOLOv9, YOLOv8, YOLOv5 (and its large-image
    variant), YOLOv3, RT-DETR;
  - **classifiers**: YOLO26, YOLO11, YOLOv8;
  - **object outliners**: YOLO26, YOLO11, YOLOv9, YOLOv8.

  The Ultralytics package is **AGPL-3.0** licensed. It is not part of the app or of the trainer
  image: each of these recipes lists it in its `requirements.txt`, so it is downloaded from PyPI
  into an environment of its own the first time one of them is checked or trained (that needs
  internet, about 400 MB). The recipes' own scripts import it, so they fall under its terms; if
  the AGPL is not acceptable where you install the app, delete the `*_yolo*` and `detection_rtdetr`
  folders from `trainer/recipes` (and their lines from `trainer/tools/sync_recipes.py`), or buy an
  Ultralytics licence.

  Recipes of one kind share their code: it lives once in `trainer/shared/<kind>`, and
  `python trainer/tools/sync_recipes.py` writes each recipe's folder from it and from the list of
  models in that script -- so every recipe stays a complete folder that can be read, duplicated and
  changed on its own. To add a torchvision model, add a line to that list and run the script.

**Results.** A finished run shows its **test results** first -- scored once, after training, on the
test set the model never saw -- with the validation results a click away. What is shown suits the kind
of model: a detector or object outliner gets AP50, AP75 and AP50-95, precision, recall and F1 at the
confidence where F1 is best (and that confidence, a good setting for the workspace's slider), what
was found, missed and falsely raised, the precision-recall curve, precision / recall / F1 by
confidence, and a table of what was found as what; a classifier gets accuracy, balanced accuracy,
precision, recall, F1 and AUC, the table of what was recognised as what, and a ROC curve per class;
a region segmenter gets mean IoU, Dice, pixel accuracy, precision and recall, and what each area was
marked as. Each also has a table per class. A recipe of your own gives its numbers and figures in
`result.json` (see `trainer/recipes/README.md`, "The results page"). A project whose test set is
empty has only validation results, and the page says so.

A project is ready to train once it has a train / validation / test split and, in the training and
validation sets, what the chosen kind of model learns from: classified shapes, or -- for a classifier
-- patches with a class (at least two classes). Finished runs can be ticked on the Training page to
**compare** them: their settings (those that differ stand out), scores, and validation score by epoch. Each run keeps its settings, what it was trained on, a copy of the code
as it ran, its log and the trained model in `data/training/runs/<id>`; the dataset copy is deleted when
the run ends.

**Suggestions in the workspace.** A finished run becomes a *trained model* of the project. In the
annotation workspace (patch view), **AI suggestions** lists the project's models: *Suggest* sends the
patch image to the trainer, which keeps the model loaded and answers in a second or two (on the CPU
while a training run has the graphics card). What the model finds is drawn dashed, with how sure it
is, and is **not an annotation**: no export or count sees it until a person accepts it. Accepting
makes an ordinary annotation recorded as that person's, "suggested by" the model; rejecting keeps the
model from proposing the same object there again. Objects already annotated -- in the patch, or on
the whole slide where it reaches the patch -- are never suggested. What a suggestion is follows the
model: a detector proposes boxes, a segmenter outlines, a classifier a **Patch Label** (accepting it
labels the patch). In a slide project the model can also be sent through **the whole slide** in the
background (the patches not annotated yet, or all of them): the whole-slide view then shows every
waiting suggestion on the slide and accepts or clears them in bulk by confidence, and *Next patch
with suggestions* steps through them patch by patch.

**Models trained elsewhere.** *Training -> Models -> Add a model trained elsewhere* takes a model file
and makes it available in the workspace like one trained here. Two kinds of file are read, each by an
*importer* (a recipe with prediction code only): Faster R-CNN weights (`.pt`: a model downloaded from
another VidaAnnotator, or a torchvision state dict), and detectors exported to ONNX or TorchScript
(YOLO v5 / v8 / v11-style outputs, or torchvision's boxes-labels-scores). The trainer opens the file
once -- a file it cannot run is refused with the reason -- and reads its class names where the file
carries them; otherwise they are typed in. Each of the model's classes is then matched to a class of
the project (by name to begin with) or left out. Those weights are loaded as data only, never as
code. A third importer takes models trained with **Ultralytics** elsewhere (`.pt`: YOLO or RT-DETR
detectors, `-seg` and `-cls` models); such a file is a pickle and *can* carry code, so add only
files you trust.
The importers have tests of their own, run in the trainer's environment:
`trainer/.venv/Scripts/python -m pytest trainer/tests` (they need `pytest` and `onnx` installed there).

**Your own recipes.** The **Model recipes** page (the code icon at the bottom of the left rail) lists
every recipe with its code. Built-in ones are read-only. Administrators -- only they, since recipe
code runs on the training machine -- can *duplicate* one, start a *new* one from a template that
has the whole shape of a recipe with the places for their code marked, or *upload* a ZIP; edit the
files in the browser (every save keeps the earlier version); and *download* a recipe to work on it
elsewhere. Your own recipes live in `data/training/recipes`. One can be trained with only after its
**check** has passed as it is now: the trainer installs its packages, trains one epoch on a few
made-up images, loads the result in `predict.py` and asks it about an image, and reports each step
with what the code printed. Any later change makes the check stale. Packages listed in a recipe's
`requirements.txt` are installed once into an environment of their own on top of the trainer's
(`data/training/envs`, shared by recipes wanting the same packages, removable from the page) and
need internet the first time. A run or model keeps its own copy of the code, so editing or deleting
a recipe never changes what an earlier run used.

**With Docker**, `install.sh` / `install.ps1` ask once whether to install training, look for an NVIDIA
graphics card (`nvidia-smi`), check that Docker can hand it to a container, and write the result to
`COMPOSE_PROFILES` in `.env`: `trainer-gpu`, `trainer-cpu` (no card: works, slowly) or empty. The GPU
image carries its own CUDA libraries, so the machine needs only the NVIDIA driver and, on Linux, the
NVIDIA Container Toolkit (DGX systems ship with both; Docker Desktop on Windows needs the WSL 2
engine). On a machine with several cards, `TRAINER_GPUS` chooses which the trainer may use. The
trainer is not switched by the app's Version page: after installing a new app version, run
`docker compose pull && docker compose up -d` (or the install script again) to bring it along.

**From the source code**, give the trainer an environment of its own with PyTorch, then run it beside
the backend:

```bash
python -m venv trainer/.venv
trainer/.venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128   # Linux/macOS: trainer/.venv/bin/python; no NVIDIA card: .../whl/cpu
trainer/.venv/Scripts/python -m pip install -r trainer/requirements.txt
trainer/.venv/Scripts/python trainer/trainer.py          # APP_URL defaults to http://127.0.0.1:8088
```

## Setup (from the source code, for development)

### Prerequisites

- Python 3.11+ (tested on 3.14) with **OpenSlide** — `pip install openslide-bin openslide-python` gets you
  the OpenSlide binaries too, no manual DLL/PATH setup needed on Windows.
- Node 18+ / npm.

### Backend

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env        # adjust paths if needed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
```

The SQLite database and `data/` subfolders are created automatically on first run. No Alembic migrations
for this MVP — tables are created via `Base.metadata.create_all`; add Alembic when moving to Postgres.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env               # nothing to set: the dev server passes /api to the backend
npm run dev
```

Open `http://localhost:5173`. The first visit asks you to create the **administrator** account.

### Running it on a server (others sign in from their own devices)

```bash
cd frontend && npm install && npm run build      # the backend serves frontend/dist itself
cd ../backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8088
```

Everyone opens `http://<server>:8088` — the page and the API on one address, which the sign-in cookie
needs. On a network, put it behind HTTPS (e.g. nginx or Caddy as a reverse proxy) and set
`COOKIE_SECURE=true` in `.env` so the session cookie is only ever sent encrypted. `SESSION_DAYS`
(default 14) is how long a sign-in lasts.

## Users and access

Everyone signs in with an email and password. There are three roles:

| Role | Can |
|---|---|
| **Administrator** | Manage users (Users page); see and manage every project |
| **Project manager** | Create projects; in their projects change settings, add slides, run processing, manage members, delete |
| **Annotator** | In their projects: annotate, label, review (Validate) and export |

- People see only the projects they are **members** of (Project settings → Members); administrators
  see all. Whoever creates a project becomes a member of it.
- The administrator adds people on the Users page, either with a password or — better — with a
  one-time **password link** (valid 7 days) they open to choose their own. The same link resets a
  forgotten password. Disabling an account signs it out everywhere at once.
- Who drew an annotation (`created_by`) and who reviewed a patch (`reviewed_by`) are filled in by the
  server from the signed-in account; the names from before accounts existed are kept as they were.
- The rules are enforced by the server on every request (`backend/app/api/access.py`), not only by
  hiding buttons. Passwords are stored as salted scrypt hashes; sessions and password links only as
  SHA-256 digests. Repeated failed sign-ins are slowed down.

### Windows notes

- `openslide-bin` bundles the OpenSlide native libraries — you do **not** need to separately download
  OpenSlide binaries and add them to `PATH` (the old requirement for openslide-python on Windows).
- All paths in `.env` are relative to the project root and created automatically; no machine-specific
  paths are hard-coded anywhere in the code.

## Using the app

**Where am I, and what next?** The project dashboard shows a *Project progress* checklist — add slides →
find tissue → generate patches → annotate → review → export — with how far each step is and a button for
the current one (it opens the first slide that needs it). Each row of the slide table has a *Next step*
button with a one-line status (e.g. *Continue · 12 of 80 patches annotated, 3 reviewed*).

**Shortcuts:** press **?** anywhere (or the header's help button) for every keyboard and mouse shortcut,
the ones for the current page first.

1. **Create a project** (`/projects` → *New Project*). The wizard sets patch size, stride, target magnification, minimum tissue fraction,
   tissue-detection parameters, diagnostic classes (name/color/hotkey), and QC settings — all versioned
   as a `ProjectConfigVersion`.
2. **Add WSIs**: on the project dashboard, *Add WSI Slides*.
   - **Upload** any mix of slide files, a whole folder (*Choose folder*), and `.zip` archives, in one go.
     A zip may hold many slides; every slide found is imported. Each slide is stored in its own directory.
   - **Server path** imports without uploading: a slide file, a folder of slides, or a `.zip` inside
     `WSI_WATCH_DIR`. Files are copied, the originals are never touched. Best for very large slides.
   - Supported: `.svs .tif .tiff .ndpi .scn .bif .svslide .vms .vmu .mrxs` (whatever OpenSlide reads; the
     list is served by `GET /api/wsi-formats`). **Multi-file formats need their companions**: a `.mrxs` comes
     with a same-named folder (`Slide.mrxs` + `Slide/`), and `.vms`/`.vmu` with their tile files. Choose the
     folder that contains them or zip them together; a lone `.mrxs` is refused with that advice.
   - Every candidate is checked by OpenSlide itself, so plain TIFFs, masks or corrupt files are *reported
     with a reason*, not imported as broken slides. The import report lists what was imported, skipped and ignored.
   - Uploads are unpacked defensively (zip-slip, symlinks, encrypted zips and decompression bombs are
     refused). Limits: 500 GB and 20,000 files per request/zip (`max_upload_bytes`, `max_upload_files`).
   - Not supported: DICOM WSI, and archive formats other than `.zip` (`.7z`, `.tar.gz`); nested zips are
     reported, not unpacked.
3. **Process**: open *Slide Processing* → *Re-run Detection* (HSV+Otsu tissue mask, tunable via sliders)
   → *Generate Coords* (walks the grid, keeps patches meeting the tissue threshold — coordinates only).
   The mask can also be drawn by hand (*Manual regions*, with the rectangle, polygon, freehand and circle
   tools): **Add tissue** areas count as tissue, **Remove tissue** areas never do (remove wins where they
   overlap). *Mask starts from* picks the base: **Automatic detection** (detected tissue + added − removed)
   or **Manual only** (just the added areas − removed; detection ignored). Every change rebuilds the mask at
   once (shown in *Tissue Mask* view) and can be undone; *Generate Coords* then applies the usual minimum
   tissue fraction to it. Regions are stored per slide in Level-0 pixels (`slides.tissue_regions`), are not
   annotations and are not exported. The detector's own result is kept in
   `data/uploads/<project>/_masks/<slide>_tissue_auto.png`, so regions can be edited without re-detecting.
4. **Annotate**: open the *Workspace* → draw shapes with the tools below, assign a class,
   navigate with `A`/`D` or the on-screen buttons, `Space` jumps to the next unannotated patch. Every
   shape autosaves on completion; the save-state pill shows Saving/Saved/Error honestly (never a fake
   "Saved" if the API call failed).
5. **Review**: *Patch Gallery* (filterable, viewport/pagination-driven — patches are always fetched
   on-demand, never pre-rendered in bulk) and *Full WSI Overview* (all annotations stitched onto the
   whole slide in Level-0 space).
6. **Export**: *Export* screen → pick one of five formats, preview it, download it (details in
   *Export formats* below). *Full WSI JSON* matches the spec schema exactly, including `source_patch`
   provenance and Level-0 `coordinates`.
7. **Import annotations**: on *Slide Processing*, *Import Annotations* reads annotation files from this
   app or other tools; the format is detected from the content (`services/annotation_import.py`):

   | Format | Read as |
   |---|---|
   | WSI JSON (this app, or a bare `annotations` array) | As exported, with `source_patch` provenance |
   | GeoJSON (QuPath, GIS tools, this app) | Points, lines, polygons in Level-0 px; multi-geometries split, holes dropped; label from `classification.name`, `label`, `class` or `name` |
   | COCO | This app's export exactly (`vp_*` fields, circles restored); other COCO placed by the image's `vp_origin_level0` or a `_x<left>_y<top>` tile name; RLE masks as their boxes |
   | Cytomine JSON (a list, or an API page's `collection`) | WKT `location` with y flipped (Cytomine counts y from the image's bottom); each `term` id is matched to a class's **Class ID** (set per class in *Settings*, any size) — with several matching terms the class listed first wins; shapes with no matching term are labelled `Term <ids>`/`No term` and skipped unless mapped |
   | ASAP XML / Aperio ImageScope XML | Polygons, rectangles, dots/pins, rulers; Aperio ellipses as circles or 64-gons; negative regions left out |
   | CSV / TSV | One row per shape: `x`,`y` points (also QuPath's `Centroid X µm`, converted with the slide's mpp), `xmin/ymin/xmax/ymax` or `x/y/width/height` boxes, or a WKT column; label from `label`/`class`/… |

   The file is read first (`POST /slides/{id}/import-annotations/parse`, nothing saved) and the dialog shows
   what it found: the shapes, their extent against the slide, and every label, which you map to a class,
   *Import without a class* or *Skip* (labels matching a class name are pre-selected). A *scale* multiplies
   every coordinate, for files drawn on a downsampled image. Shapes are then saved with
   `POST /slides/{id}/import-annotations`: one naming its `source_patch` is matched to an *existing* patch by
   exact Level-0 origin (never fabricated from unverified import data); any other shape goes into the
   patch that wholly contains it (*Place each shape in the patch that contains it*; always in image
   projects) or onto the whole slide. Shapes outside the slide are skipped, and re-importing the same file
   is a safe no-op (near-identical geometry on the same patch is detected and skipped).
8. **Delete a project**: from the project card's `⋮` menu, *Delete Project* requires typing the project's
   exact slug to confirm (the same pattern GitHub uses for deleting a repo) before it becomes clickable.
   Deletion removes every DB row under the project *and* all of its files on disk (`data/uploads/<project_id>/`:
   slide/image files and tissue masks). Files are removed after the rows are committed; if one is locked
   (e.g. still open in another program on Windows) the leftover folder is removed at the next server start.
   Originals in `WSI_WATCH_DIR` that were imported by *Server path* are never touched — only the app's copies.

9. **Project settings**: *Settings* (sidebar, dashboard, or the project card's `⋮` menu) shows and edits
   everything in one place — project details (name, cancer type, team, description) and the project's
   **one configuration**: tissue-detection defaults, diagnostic classes, tools and QC settings. Changes save
   in place. Patch sizes have their own section, *Patch sizes* (see 10): **+ Add patch size** opens the same
   dialog as on Slide Processing and cuts every slide whose tissue has been found into that size (slides
   without tissue are listed as skipped), optionally making it the default for *Generate Coords*.
   - Renaming or recoloring a class keeps every annotation attached to it; removing a class that annotations
     still use is refused.
   - Changing the default **patch size** never moves existing patches: every patch records the grid it was
     cut with, so slides keep the grid they have. Tissue method and coordinate system are fixed once patches
     exist.
   - There are no configuration versions: a project has exactly one configuration. Projects from before
     were merged into the configuration they used, at startup, without losing anything (classes matched by
     name, patches and annotations moved over). The old `/versions` address redirects to Settings.

10. **Several patch sizes on one slide**: a slide can hold any number of **patch grids** under the same
    configuration (so the same classes). In the workspace — or the *Patch grid* card on Slide Processing —
    the patch-size selector switches between them, and *+ New patch size…* cuts another grid from the tissue
    mask (size presets, overlap none/50 %, magnification, minimum tissue) and switches to it. **Annotations
    are shared across grids**: every annotation is also stored in Level-0 pixels, so whatever size you work
    in shows everything drawn so far (shapes from other grids appear under *From overlapping patches* and can
    be edited; each stays owned by the patch it was drawn in). Switching opens the patch at the same spot.
    Regenerating a grid updates it in place: patches at the same place keep their ids, status and
    annotations, and annotated patches that no longer meet the threshold are kept.
    *Settings → Patch sizes* lists every size used in the project (slides, patches, annotated) with **Use as
    default** (what *Generate Coords* cuts) and **Remove**, which deletes that size's patches from every slide —
    the annotations drawn in them are kept as whole-slide annotations at the same place, so nothing drawn is
    lost. The patch-size menu on a slide can remove a size from just that slide the same way.

11. **Export in any patch grid**: the Export screen's *Patch grid* option is *As annotated* (each slide's
    active grid; annotations drawn in other grids or on the whole slide are cut into it) or *Custom grid*
    (patch size, stride, magnification, minimum tissue, edge patches). A custom grid is cut from each slide's
    tissue mask **at download time** and every annotation is cut into it — nothing is stored. It applies to
    every format, patch images and masks included (`?grid=<key>` on the export endpoints, e.g.
    `512x512_s256x256_m20_t0.5`; see `GET /api/slides/{id}/grids`).

## Database

SQLite file at `data/database/app.db` (WAL mode + a busy-timeout pragma, so light concurrent access from
FastAPI's threadpool doesn't hit `database is locked`). `DATABASE_URL` in `.env` is a standard SQLAlchemy
URL — pointing it at Postgres is a drop-in change once Alembic migrations are added.

## Testing

```bash
cd backend && python -m pytest tests/ -q
cd frontend && npx vitest run && npx tsc -b
```

Backend tests cover coordinate transforms (including a downsampled-level case), tissue-fraction
sampling, patch-grid generation/bounds, configuration editing rules, and JSON export shape —
all against synthetic fixtures, no OpenSlide/file dependency, so they run anywhere.

Tests never touch your real data: `tests/conftest.py` points the storage and watch directories at
throw-away temp folders for every test (an earlier version of the project-deletion test removed the real
`data/uploads/1`, because a project has id 1 in the in-memory test database). `test_storage_isolation.py`
guards against that regression.

## Project types: WSI or images

Choose the type in step 1 of the *New Project* wizard.

- **Whole-slide images (WSI)** — the workflow described above: tissue detection, virtual patch grid, annotate
  patch by patch.
- **Images / patches** — for datasets that are already ordinary images or pre-cut patches (PNG, JPEG, TIFF, BMP,
  WebP, GIF). Each image is annotated as it is: no magnification, patch grid or tissue detection, so the wizard
  skips those steps. Upload images, a folder, or a `.zip` (or import from the watch directory); sub-folders are
  kept in each image's name (`tumor/001.png`).

How an image project maps onto the same model (so annotation, storage and export need no special cases): each
image is one slide whose Level-0 grid **is the image**, holding a single virtual patch that covers all of it.
Origin is (0, 0) and the downsample is 1, so local and global coordinates are the same image pixels. The original
file is never modified and is served losslessly (PNG). Photos are turned upright by their EXIF rotation and
transparent / 16-bit images are converted to RGB the same way on every read, so coordinates always refer to the
pixels the annotator saw.

Differences worth knowing:

- Images up to 8192 px per side and 25 megapixels (`MAX_IMAGE_PIXELS`) are accepted; anything bigger belongs in a
  WSI project. Unreadable or oversized images are reported, not imported.
- An image project has one configuration. Classes, tools and QC settings are edited in place; tissue
  detection and patch generation are refused (they would strand or delete the whole-image patch).
- The workspace lists the project's images on the left; *Next / Prev / Next Unannotated / Next Flagged* (and
  A / D / Space) move across images, and *Validate* / *Skip* advance to the next one. The gallery (*Images*) is the
  whole project as a filterable thumbnail grid.
- Exports: COCO and both CSVs are dataset-level formats, so *Export All* returns **one merged file** for the whole
  project (COCO lists every annotated or reviewed image, including confirmed negatives; `file_name` is the
  image's own relative path). GeoJSON and WSI JSON are per-image coordinate spaces and stay one file per image in a ZIP.

Existing databases are upgraded automatically at startup (a `project_type` column is added, defaulting every
existing project to WSI).

## Export formats

`GET /api/slides/{id}/export/{format}` — every format covers the slide's **active patch grid** (or a custom grid, below)
and leaves out annotations on patches flagged *Exclude from training* (the patch CSV still lists those
patches, marked `excluded = true`, so it stays a complete registry).

| Format | File | Coordinate space | Notes |
|---|---|---|---|
| `wsi_json` | `.json` | Level-0 | The native schema; also what *Import Annotations* reads back. |
| `geojson` | `.geojson` | Level-0 | RFC 7946 FeatureCollection in **image pixels** (y down), not lon/lat. Polygons and points; QuPath-style `objectType` / `classification` properties, so it opens in QuPath directly. Self-crossing polygons are exported as drawn and flagged `valid_geometry: false`; polygons with no area are skipped and counted in `virtualpatch.skipped_degenerate_geometry`. |
| `coco` | `.json` | **Patch pixels** (+ Level-0) | Each annotated patch is a COCO `image` (`width`/`height` = the patch at its read level); `segmentation`, `bbox` and `area` are in that image's own pixels, which is what Detectron2/MMDetection-style code expects. `coco_url` is the API path that renders that exact patch from the original slide (patches are never stored). Each annotation also carries `vp_level0_segmentation`, and each image its `vp_origin_level0` / `vp_downsample`, so nothing about position on the slide is lost. COCO has no point or line type and requires a category, so points, lines and unclassified shapes are omitted and counted in `info.vp_skipped`; circles are written as their 64-sided outline. |
| `patch_csv` | `.csv` | Level-0 | One row per patch: `level0_x/y`, `width_level0/height_level0`, `read_level`, `width_px/height_px`, `tissue_fraction`, `status`, review flags, `n_annotations`, `dominant_class` (largest summed area on that patch). |
| `stats_csv` | `.csv` | px² / mm² | Long ("tidy") layout, one row per class — including classes with zero annotations and an `(unclassified)` row — with counts, summed/mean area in px² and mm², share of annotated and of tissue area, plus slide-level patch counts. mm² columns are blank when the slide has no resolution (mpp) metadata. Areas are summed **per annotation**, so overlapping shapes count twice. |
| `patch_classification` | `.csv` | class folders | One class per patch for classification training: `labels.csv` (file, class, class source, coverage, slide, patch position/size) and, with images, `images/<class>/<name>`. The class is the patch's **Patch Label**; otherwise the drawn class covering at least `min_coverage` of the patch (default 0.9). `other_labels=false` leaves out Mixed / Artifact labels; `unlabeled=folder` keeps unclear patches in `unlabeled/` instead of skipping them. Works with custom export grids, where labels arrive through their whole-patch shapes. |

**Patch labels.** Choosing one of the project's classes as a patch's label also adds a rectangle of that class covering the whole patch (`whole_patch`), so the label reaches masks, COCO and other patch sizes. Label and rectangle stay linked: changing either changes the other, unsetting the label removes the rectangle, deleting the rectangle clears the label, and reshaping it turns it into an ordinary shape. Mixed and Artifact / Background are labels only. Labels are stored by class, so renaming a class renames them, and a class used by a label cannot be removed.

**The export screen** (`/projects/{id}/export`, Export in the sidebar) belongs to the project: tick the slides (those without a patch grid are shown but can't be chosen), tick any number of files — JSON, CSV, patch images (+ masks) — and get them in one download via `GET /api/projects/{id}/export?formats=coco,patch_csv&slides=3,7&content=images`. One format for one slide (or combined) without images arrives as that file; anything else is a ZIP with `annotations/`, `images/` and a `manifest.json`. `export-summary` takes the same `slides` and `format` parameters.

**All slides at once:** `GET /api/projects/{id}/export/{format}` returns one ZIP with a file per processed
slide (same content as the single-slide download) plus a `manifest.json`. Slides with no patch grid yet, or
whose export fails, don't block the download; they're listed under `skipped` with the reason. On the Export
screen use the *Scope* switch (*This slide* / *All slides*).

### Export options

Every export (one slide or the whole project) takes these options; the Export screen shows them with a live
count of what the selection covers.

| Option | Values | Effect |
|---|---|---|
| `patches` | `annotated` (default), `all`, `empty`, `reviewed` | Which patches are covered. `annotated`: at least one annotation. `all`: every patch, empty ones included (negatives). `empty`: no annotation at all. `reviewed`: marked Reviewed/QA, confirmed negatives included. Patches flagged *Exclude from training* never contribute annotations or images; they are only listed in the registries (WSI JSON `patches`, patch CSV) under `all`. |
| `content` | `annotations` (default), `images` | `images` returns a ZIP: `annotations/` (the chosen format), `images/` (one file per patch, cut from the original slide), optional `masks/`, and a `manifest.json`. |
| `image_format` | `png` (default), `jpg` | PNG is lossless; JPEG is smaller. |
| `masks` | `true` / `false` | With images: a single-channel PNG per patch, pixel value 0 = background and 1..N = the class (by class order; `mask_classes.json` lists them). Where shapes overlap the later one wins; points are not painted. |
| `combine` | `true` / `false` (project export) | One file for the whole project for COCO and the CSVs, instead of one per slide. On by default in image projects and whenever images are included. |

How the scope applies to each format: **COCO** lists the patches in scope as images (empty ones as images
without annotations); **WSI JSON** adds a `patches` array listing them (with `annotation_count`);
**patch CSV** has one row per patch in scope; **GeoJSON** holds shapes only, so scope simply selects which
patches' shapes are written; **statistics CSV** counts the shapes on the selected patches while its
slide-level columns still describe the whole grid. Download names carry the scope (`..._all.json`,
`..._with_images.zip`).

With images, the COCO `file_name` of each image is exactly its name under `images/`, so the ZIP is a
ready-to-train dataset. The images are generated on the fly for that one download (streamed from a temporary
file that is deleted afterwards) and **nothing is stored on the server**, so the "no permanent patch extraction"
rule still holds. One download holds at most `MAX_EXPORT_IMAGES` (50,000) images; `GET
/api/{slides|projects}/{id}/export-summary?patches=...` returns the counts and a rough size beforehand.

CSV cells that begin with `=`, `+`, `-`, `@` are prefixed with `'` so a note like `=HYPERLINK(...)`
can't execute when the file is opened in Excel; numbers are never altered. Download names are reduced
to `[A-Za-z0-9._-]`. Adding a format means one `Exporter` subclass registered in
`backend/app/services/exporter/__init__.py`.

## Scope of this MVP (intentional, not accidental)

- **Annotation tools**: Select/Move, Point, Line, Freehand line, Rectangle, Circle, Polygon, Freehand
  polygon and Brush are fully functional (see *Annotation tools*). SAM-assisted and Ruler/Caliper
  are shown in the project wizard per the design but disabled with a "planned" tooltip — the spec
  explicitly defers these past the core MVP phases. Existing shapes can be moved, reshaped
  and reclassified (see *Annotation tools*); rotating a shape is not supported.
- **No auth/multi-user system**: `created_by` / `reviewed_by` are free-text fields, not a relational
  `Annotator`/`Review` system. Not requested by the spec; would be scope creep.
- **No Alembic migrations** for the SQLite MVP — noted here as the natural next step for Postgres.
- **Undo/redo** is session-scoped to the currently open patch (resets on navigation), covering shape
  create/delete. It is not a full cross-session history log.

## Troubleshooting

- **Large uploads** pass through the web server's temporary folder (your system temp directory) before being
  stored, so it needs free space of about the upload size. For multi-gigabyte slides prefer *Server path*.
- **A `.mrxs` was skipped as "data folder not found"**: MIRAX keeps its pixels in a folder next to the
  `.mrxs` file. Upload with *Choose folder*, or zip the `.mrxs` and its folder together.
- **"Failed to read WSI metadata"** on import: confirm the file is a real OpenSlide-supported format;
  check the backend log for the underlying OpenSlide error.
- **Tissue detection finds ~0% coverage**: lower the Otsu sensitivity slider, or check the slide isn't
  mostly background glass at the macro level.
- **"0 of N candidate patches met the minimum tissue fraction"**: run tissue detection first, or lower
  `min_tissue_fraction` in Settings (existing patches keep their grid; generate again to apply it).
- **CORS errors in the browser console**: `VITE_API_BASE` (frontend `.env`) must match the backend's
  actual host:port, and that origin must be listed in `cors_origins` in `backend/app/core/config.py`.
