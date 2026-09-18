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
| `exporter.py` | Export format registry. `WSIJSONExporter` is fully implemented; others are stubs (see Scope below) |

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
2. **Add a WSI**: on the project dashboard, *Add WSI Slides* → upload a `.svs/.tif/.tiff/.ndpi` file, or
   register a path already under the server's `WSI_WATCH_DIR`, or add a synthetic demo slide.
3. **Process**: open *Slide Processing* → *Re-run Detection* (HSV+Otsu tissue mask, tunable via sliders)
   → *Generate Coords* (walks the grid, keeps patches meeting the tissue threshold — coordinates only).
4. **Annotate**: open the *Workspace* → draw Polygon/Rectangle/Point/Freehand shapes, assign a class,
   navigate with `A`/`D` or the on-screen buttons, `Space` jumps to the next unannotated patch. Every
   shape autosaves on completion; the save-state pill shows Saving/Saved/Error honestly (never a fake
   "Saved" if the API call failed).
5. **Review**: *Patch Gallery* (filterable, viewport/pagination-driven — patches are always fetched
   on-demand, never pre-rendered in bulk) and *Full WSI Overview* (all annotations stitched onto the
   whole slide in Level-0 space).
6. **Export**: *Export* screen → *Full WSI JSON* is fully implemented and downloadable; the schema
   matches the spec exactly, including `source_patch` provenance and Level-0 `coordinates`.

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

## Scope of this MVP (intentional, not accidental)

- **Export formats**: only `wsi_json` is implemented. `geojson`, `coco`, `patch_csv`, `stats_csv` are
  registered in the exporter registry (so the API/UI surface is stable) but return `501 Not Implemented`
  — the Export screen shows this honestly rather than faking output.
- **Annotation tools**: Select/Move, Polygon, Rectangle, Point, Freehand are fully functional. Brush
  mask, SAM-assisted, and Ruler/Caliper are visible in the toolbar per the design but disabled with a
  "planned" tooltip — the spec explicitly defers these past the core MVP phases.
- **No auth/multi-user system**: `created_by` / `reviewed_by` are free-text fields, not a relational
  `Annotator`/`Review` system. Not requested by the spec; would be scope creep.
- **No Alembic migrations** for the SQLite MVP — noted here as the natural next step for Postgres.
- **Undo/redo** is session-scoped to the currently open patch (resets on navigation), covering shape
  create/delete. It is not a full cross-session history log.

## Troubleshooting

- **"Failed to read WSI metadata"** on import: confirm the file is a real OpenSlide-supported format;
  check the backend log for the underlying OpenSlide error.
- **Tissue detection finds ~0% coverage**: lower the Otsu sensitivity slider, or check the slide isn't
  mostly background glass at the macro level.
- **"0 of N candidate patches met the minimum tissue fraction"**: run tissue detection first, or lower
  `min_tissue_fraction` in the project config (fork a new config version if patches already exist).
- **CORS errors in the browser console**: `VITE_API_BASE` (frontend `.env`) must match the backend's
  actual host:port, and that origin must be listed in `cors_origins` in `backend/app/core/config.py`.
