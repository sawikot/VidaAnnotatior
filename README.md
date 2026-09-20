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
4. **Annotate**: open the *Workspace* → draw Polygon/Rectangle/Point/Freehand shapes, assign a class,
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

## Export formats

`GET /api/slides/{id}/export/{format}` — every format covers the slide's **active config version** only
and leaves out annotations on patches flagged *Exclude from training* (the patch CSV still lists those
patches, marked `excluded = true`, so it stays a complete registry).

| Format | File | Coordinate space | Notes |
|---|---|---|---|
| `wsi_json` | `.json` | Level-0 | The native schema; also what *Import Annotations* reads back. |
| `geojson` | `.geojson` | Level-0 | RFC 7946 FeatureCollection in **image pixels** (y down), not lon/lat. Polygons and points; QuPath-style `objectType` / `classification` properties, so it opens in QuPath directly. Self-crossing polygons are exported as drawn and flagged `valid_geometry: false`; polygons with no area are skipped and counted in `virtualpatch.skipped_degenerate_geometry`. |
| `coco` | `.json` | **Patch pixels** (+ Level-0) | Each annotated patch is a COCO `image` (`width`/`height` = the patch at its read level); `segmentation`, `bbox` and `area` are in that image's own pixels, which is what Detectron2/MMDetection-style code expects. `coco_url` is the API path that renders that exact patch from the original slide (patches are never stored). Each annotation also carries `vp_level0_segmentation`, and each image its `vp_origin_level0` / `vp_downsample`, so nothing about position on the slide is lost. COCO has no point type and requires a category, so points and unclassified shapes are omitted and counted in `info.vp_skipped`. |
| `patch_csv` | `.csv` | Level-0 | One row per patch: `level0_x/y`, `width_level0/height_level0`, `read_level`, `width_px/height_px`, `tissue_fraction`, `status`, review flags, `n_annotations`, `dominant_class` (largest summed area on that patch). |
| `stats_csv` | `.csv` | px² / mm² | Long ("tidy") layout, one row per class — including classes with zero annotations and an `(unclassified)` row — with counts, summed/mean area in px² and mm², share of annotated and of tissue area, plus slide-level patch counts. mm² columns are blank when the slide has no resolution (mpp) metadata. Areas are summed **per annotation**, so overlapping shapes count twice. |

**All slides at once:** `GET /api/projects/{id}/export/{format}` returns one ZIP with a file per processed
slide (same content as the single-slide download) plus a `manifest.json`. Slides with no patch grid yet, or
whose export fails, don't block the download; they're listed under `skipped` with the reason. On the Export
screen use the *Scope* switch (*This slide* / *All slides*).

CSV cells that begin with `=`, `+`, `-`, `@` are prefixed with `'` so a note like `=HYPERLINK(...)`
can't execute when the file is opened in Excel; numbers are never altered. Download names are reduced
to `[A-Za-z0-9._-]`. Adding a format means one `Exporter` subclass registered in
`backend/app/services/exporter/__init__.py`.

## Scope of this MVP (intentional, not accidental)

- **Annotation tools**: Select/Move, Polygon, Rectangle, Point, Freehand are fully functional. Brush
  mask, SAM-assisted, and Ruler/Caliper are visible in the toolbar per the design but disabled with a
  "planned" tooltip — the spec explicitly defers these past the core MVP phases.
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
