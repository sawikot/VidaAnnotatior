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
| `wsi_reader.py` | `WSIReader` interface. `OpenSlideReader` (real .svs/.tif/.tiff/.ndpi) and `DemoWSIReader` (synthetic, no file needed) |
| `deepzoom_service.py` | Generates DZI tiles on the fly for the OpenSeadragon viewer — a *display* concern, unrelated to annotation patches |
| `tissue_detector.py` | `TissueDetector` interface. `HSVOtsuDetector`: HSV→Otsu threshold→morphology→small-component removal |
| `patch_generator.py` | Walks the Level-0 grid at the configured patch/stride size, keeps patches meeting the tissue threshold |
| `coordinate_transform.py` | The **single** source of truth for Level-0 ⇄ patch-local math (mirrored in `frontend/src/utils/coordinates.ts`) |
| `config_versioning.py` | Locks a config version's critical fields once patches exist; forks a new version instead of mutating |
| `exporter/` | Export format registry (`get_exporter`). One small class per format — `wsi_json`, `geojson`, `coco`, `patch_csv`, `stats_csv` — all reading the same `ExportData` snapshot (see *Export formats* below) |
| `geometry.py` | Shapely-backed polygon area/validity helpers shared by the exporters |
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
| Polygon | `P` | Click the vertices, then double-click or `Enter` | `polygon`: ≥ 3 points |
| Freehand polygon | `F` | Drag around the outline | `freehand`: a closed outline, ≥ 3 points |

**Modifying an annotation** (Select tool, or click an object in the workspace's object list): the selected
shape shows handles. Drag the shape to move it; drag a handle to reshape it — a polygon, freehand or line
vertex; a rectangle's corners (the opposite corner stays fixed) or side midpoints; a circle's edge handle
to resize it and its centre handle to move it. Double-click an outline to add a point, a point to remove
it (a polygon keeps at least 3, a freehand line 2). The *Edit* panel under the object list changes its
class, unsure/flag marks and a note, or deletes it. Every one of these changes is saved at once and can be
undone with `Ctrl+Z` (redo with `Ctrl+Shift+Z`); a shape can only be given a class of its own project
configuration (the server answers 422 otherwise).

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

## Setup

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
cp .env.example .env               # VITE_API_BASE must match the backend's host:port
npm run dev
```

Open `http://localhost:5173`.

### Windows notes

- `openslide-bin` bundles the OpenSlide native libraries — you do **not** need to separately download
  OpenSlide binaries and add them to `PATH` (the old requirement for openslide-python on Windows).
- All paths in `.env` are relative to the project root and created automatically; no machine-specific
  paths are hard-coded anywhere in the code.

## Using the app

1. **Create a project** (`/projects` → *New Project*, or *Seed Demo* for a synthetic project needing no
   real WSI file). The wizard sets patch size, stride, target magnification, minimum tissue fraction,
   tissue-detection parameters, diagnostic classes (name/color/hotkey), and QC settings — all versioned
   as a `ProjectConfigVersion`.
2. **Add WSIs**: on the project dashboard, *Add WSI Slides*.
   - **Upload** any mix of slide files, a whole folder (*Choose folder*), and `.zip` archives, in one go.
     A zip may hold many slides; every slide found is imported. Each slide is stored in its own directory.
   - **Server path** imports without uploading: a slide file, a folder of slides, or a `.zip` inside
     `WSI_WATCH_DIR`. Files are copied, the originals are never touched. Best for very large slides.
   - **Demo** adds a synthetic slide with no real file.
   - Supported: `.svs .tif .tiff .ndpi .scn .bif .svslide .vms .vmu .mrxs` (whatever OpenSlide reads; the
     list is served by `GET /api/wsi-formats`). **Multi-file formats need their companions**: a `.mrxs` comes
     with a same-named folder (`Slide.mrxs` + `Slide/`), and `.vms`/`.vmu` with their tile files. Choose the
     folder that contains them or zip them together; a lone `.mrxs` is refused with that advice.
   - Every candidate is checked by OpenSlide itself, so plain TIFFs, masks or corrupt files are *reported
     with a reason*, not imported as broken slides. The import report lists what was imported, skipped and ignored.
   - Uploads are unpacked defensively (zip-slip, symlinks, encrypted zips and decompression bombs are
     refused). Limits: 20 GB and 20,000 files per request/zip (`max_upload_bytes`, `max_upload_files`).
   - Not supported: DICOM WSI, and archive formats other than `.zip` (`.7z`, `.tar.gz`); nested zips are
     reported, not unpacked.
3. **Process**: open *Slide Processing* → *Re-run Detection* (HSV+Otsu tissue mask, tunable via sliders)
   → *Generate Coords* (walks the grid, keeps patches meeting the tissue threshold — coordinates only).
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
7. **Import annotations** (the inverse of export): on *Slide Processing*, *Import Annotations* accepts a
   previously exported `wsi_json` file (or a bare `annotations` array) and re-creates those annotations
   against this slide. Requires tissue detection + *Generate Coords* to have already been run with a
   matching grid — annotations are matched to *existing* patches by exact Level-0 origin, never fabricated
   from unverified import data. Class labels are matched by name (unrecognized ones are skipped, not
   invented), and re-importing the same file is a safe no-op (near-identical geometry on the same patch is
   detected and skipped).
8. **Delete a project**: from the project card's `⋮` menu, *Delete Project* requires typing the project's
   exact slug to confirm (the same pattern GitHub uses for deleting a repo) before it becomes clickable.
   Deletion removes every DB row under the project *and* its files on disk (`data/uploads/<project_id>/`).

9. **Change a project's configuration after creating it**: *Config Versions* → **Edit** on any version
   (patch grid, tissue-detection defaults, diagnostic classes, tools, QC settings), or **Edit Details** on
   the dashboard for the project's name/organ/team/description (the project ID is fixed).
   - Classes, tools, QC settings and tissue defaults are always editable in place. Renaming or recoloring a
     class keeps every annotation attached to it; removing a class that annotations still use is refused.
   - Patch size, stride, magnification and minimum tissue define where every patch and annotation sits, so
     they are editable in place **only** while a version has no generated patches and isn't locked. Otherwise
     the editor tells you which fields you changed and saves everything as a **new version** instead; the
     original version and its annotations are left exactly as they were.
   - To run an existing slide on another version, choose it in **Config version** on *Slide Processing* and
     press *Generate Coords*. Each slide shows only the patches/annotations of its active version, exports
     contain only that version, and switching back restores the earlier set untouched. *Use for new slides*
     sets which version newly added slides start on.

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
sampling, patch-grid generation/bounds, config-version lock/fork behavior, and JSON export shape —
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
- An image project has one configuration. Classes, tools and QC settings are edited in place; forking versions,
  tissue detection and patch generation are refused (they would strand or delete the whole-image patch).
- The workspace lists the project's images on the left; *Next / Prev / Next Unannotated / Next Flagged* (and
  A / D / Space) move across images, and *Validate* / *Skip* advance to the next one. The gallery (*Images*) is the
  whole project as a filterable thumbnail grid.
- Exports: COCO and both CSVs are dataset-level formats, so *Export All* returns **one merged file** for the whole
  project (COCO lists every annotated or reviewed image, including confirmed negatives; `file_name` is the
  image's own relative path). GeoJSON and WSI JSON are per-image coordinate spaces and stay one file per image in a ZIP.

Existing databases are upgraded automatically at startup (a `project_type` column is added, defaulting every
existing project to WSI).

## Export formats

`GET /api/slides/{id}/export/{format}` — every format covers the slide's **active config version** only
and leaves out annotations on patches flagged *Exclude from training* (the patch CSV still lists those
patches, marked `excluded = true`, so it stays a complete registry).

| Format | File | Coordinate space | Notes |
|---|---|---|---|
| `wsi_json` | `.json` | Level-0 | The native schema; also what *Import Annotations* reads back. |
| `geojson` | `.geojson` | Level-0 | RFC 7946 FeatureCollection in **image pixels** (y down), not lon/lat. Polygons and points; QuPath-style `objectType` / `classification` properties, so it opens in QuPath directly. Self-crossing polygons are exported as drawn and flagged `valid_geometry: false`; polygons with no area are skipped and counted in `virtualpatch.skipped_degenerate_geometry`. |
| `coco` | `.json` | **Patch pixels** (+ Level-0) | Each annotated patch is a COCO `image` (`width`/`height` = the patch at its read level); `segmentation`, `bbox` and `area` are in that image's own pixels, which is what Detectron2/MMDetection-style code expects. `coco_url` is the API path that renders that exact patch from the original slide (patches are never stored). Each annotation also carries `vp_level0_segmentation`, and each image its `vp_origin_level0` / `vp_downsample`, so nothing about position on the slide is lost. COCO has no point or line type and requires a category, so points, lines and unclassified shapes are omitted and counted in `info.vp_skipped`; circles are written as their 64-sided outline. |
| `patch_csv` | `.csv` | Level-0 | One row per patch: `level0_x/y`, `width_level0/height_level0`, `read_level`, `width_px/height_px`, `tissue_fraction`, `status`, review flags, `n_annotations`, `dominant_class` (largest summed area on that patch). |
| `stats_csv` | `.csv` | px² / mm² | Long ("tidy") layout, one row per class — including classes with zero annotations and an `(unclassified)` row — with counts, summed/mean area in px² and mm², share of annotated and of tissue area, plus slide-level patch counts. mm² columns are blank when the slide has no resolution (mpp) metadata. Areas are summed **per annotation**, so overlapping shapes count twice. |

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
| `image_format` | `jpg` (default), `png` | JPEG is smaller; PNG is lossless. |
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

- **Annotation tools**: Select/Move, Point, Line, Freehand line, Rectangle, Circle, Polygon and Freehand
  polygon are fully functional (see *Annotation tools*). Brush mask, SAM-assisted, and Ruler/Caliper
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
  `min_tissue_fraction` in the project config (fork a new config version if patches already exist).
- **CORS errors in the browser console**: `VITE_API_BASE` (frontend `.env`) must match the backend's
  actual host:port, and that origin must be listed in `cors_origins` in `backend/app/core/config.py`.
