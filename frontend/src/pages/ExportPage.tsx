import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { EXPORT_FORMATS } from "../features/export/formats";
import { ExportOptionsPanel } from "../features/export/ExportOptionsPanel";
import {
  downloadExport,
  exportProjectUrl,
  exportSlideUrl,
  getExportSummary,
  getSlide,
  listGrids,
  listSlides,
  navigateDownload,
} from "../services/api";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";
import type { GridSpec, Slide } from "../types/api";
import { gridKey } from "../utils/gridKey";
import { formatBytes } from "../features/slides/uploadSelection";
import {
  DEFAULT_EXPORT_OPTIONS,
  exportProblem,
  normalizeOptions,
  projectDownloadKind,
  type ExportOptions,
  type ExportSummary,
} from "../utils/exportOptions";

// The preview is only a glance at the file; the download always carries everything.
const PREVIEW_LIMIT = 60_000;
// Above this, an image ZIP is streamed by the browser straight to disk instead of being held in memory first.
const IN_MEMORY_LIMIT = 300 * 1024 * 1024;

/** Mirrors the server's download-name rule (backend/app/api/export.py). */
function downloadName(filename: string, format: string, ext: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._]+|[._]+$/g, "") || "slide";
  return `${stem}_${format}${ext}`;
}

export function ExportPage() {
  const { slideId } = useParams();
  const sid = Number(slideId);
  const [searchParams] = useSearchParams();
  const pushToast = useUiStore((s) => s.pushToast);

  const [slide, setSlide] = useState<Slide | null>(null);
  const [format, setFormat] = useState("wsi_json");
  const [options, setOptionsState] = useState<ExportOptions>(DEFAULT_EXPORT_OPTIONS);
  const [preview, setPreview] = useState<string>("");
  const [loadingPreview, setLoadingPreview] = useState(false);
  const chosen = EXPORT_FORMATS.find((f) => f.id === format) ?? EXPORT_FORMATS[0];
  const projectType = useContextStore((s) => s.activeProject?.project_type);
  const isImage = projectType === "image";
  const noun = isImage ? "image" : "slide";
  const [scope, setScope] = useState<"slide" | "project">(searchParams.get("scope") === "project" ? "project" : "slide");
  const [projectSlides, setProjectSlides] = useState<Slide[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [summary, setSummary] = useState<ExportSummary | null>(null);
  const [defaultGrid, setDefaultGrid] = useState<GridSpec | null>(null);
  const gridParam = options.grid ? gridKey(options.grid) : ""; // re-fetch counts and preview when it changes

  const setOptions = (next: ExportOptions) => setOptionsState(normalizeOptions(next));
  const images = options.content === "images";
  const kind = projectDownloadKind(projectType, format, options);

  useEffect(() => {
    getSlide(sid).then((s) => {
      setSlide(s);
      listSlides(s.project_id).then(setProjectSlides).catch(() => setProjectSlides([s]));
    });
    listGrids(sid)
      .then((grids) => setDefaultGrid((grids.find((g) => g.active) ?? grids.find((g) => g.is_default))?.spec ?? null))
      .catch(() => setDefaultGrid(null));
  }, [sid]);

  // What the selection covers, refreshed whenever it changes.
  useEffect(() => {
    if (!slide) return;
    let stale = false;
    setSummary(null);
    getExportSummary(scope, scope === "slide" ? sid : slide.project_id, options)
      .then((s) => !stale && setSummary(s))
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [slide?.id, scope, options.patches, options.imageFormat, options.content, gridParam]);

  // The preview always shows the annotation file (images can't be shown as text).
  useEffect(() => {
    if (scope === "project") return; // the bulk view lists files instead of previewing one
    let stale = false; // a slower response for a previously selected format must not overwrite the current one
    setLoadingPreview(true);
    setPreview("");
    fetch(exportSlideUrl(sid, format, { ...options, content: "annotations", masks: false }))
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok) throw new Error(text);
        return text;
      })
      .then((text) => !stale && setPreview(text))
      .catch(() => !stale && setPreview("// Failed to load the preview."))
      .finally(() => !stale && setLoadingPreview(false));
    return () => {
      stale = true;
    };
  }, [sid, format, scope, options.patches, gridParam]);

  const problem = exportProblem(summary, options);

  async function deliver(url: string, fallbackName: string) {
    // An image ZIP can be several gigabytes: let the browser stream it to disk. The server builds
    // it before the first byte is sent, so say so instead of leaving the click looking dead.
    if (images && (summary?.approx_image_bytes ?? 0) > IN_MEMORY_LIMIT) {
      navigateDownload(url);
      pushToast("Building the ZIP on the server. The download starts when it is ready; large exports can take a few minutes.", "info");
      return;
    }
    setDownloading(true);
    try {
      const name = await downloadExport(url, fallbackName);
      pushToast(`Exported ${name}`, "success");
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Export failed", "error");
    } finally {
      setDownloading(false);
    }
  }

  async function handleExport() {
    if (!slide) return;
    if (scope === "slide") {
      if (images) await deliver(exportSlideUrl(sid, format, options), "slide_export.zip");
      else navigateDownload(exportSlideUrl(sid, format, options)); // small and instant
      return;
    }
    await deliver(exportProjectUrl(slide.project_id, format, options), `project_${format}_all_slides.zip`);
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(preview); // the full file, not the truncated view
      pushToast("Copied to clipboard", "success");
    } catch {
      pushToast("Could not access the clipboard", "error");
    }
  }

  const truncated = preview.length > PREVIEW_LIMIT;

  if (!slide) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

  const buttonLabel = downloading
    ? "Preparing download..."
    : scope === "project"
      ? images
        ? `Download all ${noun}s + images (.zip)`
        : `Download all ${noun}s (${kind === "file" ? chosen.ext : ".zip"})`
      : images
        ? `Download ${chosen.name} + images (.zip)`
        : `Generate & Download ${chosen.name}`;

  return (
    <div className="max-w-6xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg pb-28">
      <div className="bg-surface-container-low rounded-xl p-space-lg">
        <div className="text-label-sm text-on-surface-variant mb-1">Pipelines & Artifacts / Export Engine</div>
        <h1 className="font-headline-lg text-headline-lg mb-space-sm">Export Annotations & Coordinate Datasets</h1>
        <div className="flex items-center gap-space-sm flex-wrap text-label-md text-on-surface-variant mb-space-md">
          <Link to={`/projects/${slide.project_id}`} className="hover:underline text-primary">
            {slide.filename}
          </Link>
        </div>
        <Card className="p-space-md bg-primary-fixed/40 flex items-center justify-between flex-wrap gap-space-sm">
          <div className="flex items-center gap-space-sm">
            <MaterialIcon name="verified" className="text-primary" />
            <span className="font-headline-sm text-headline-sm">Level-0 Coordinate Guarantee Active</span>
          </div>
          <div className="flex items-center gap-space-lg font-mono text-label-md">
            <span>Pixel Scale (MPP): {slide.mpp_x?.toFixed(4) ?? "--"} µm/px</span>
          </div>
        </Card>
      </div>

      <div>
        <h2 className="font-headline-md text-headline-md mb-space-md">1. Select Target Specification</h2>
        <div className="grid md:grid-cols-5 gap-space-md">
          {EXPORT_FORMATS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFormat(f.id)}
              className={`relative text-left p-space-md rounded-xl bg-surface-container-lowest shadow-sm transition-shadow ${
                format === f.id ? "ring-2 ring-primary" : "hover:shadow-md"
              }`}
            >
              {f.id === "wsi_json" && (
                <span className="absolute top-2 right-2 px-space-sm py-0.5 rounded-full bg-primary text-on-primary text-label-sm">
                  Recommended
                </span>
              )}
              <MaterialIcon name={format === f.id ? "radio_button_checked" : "radio_button_unchecked"} className="text-primary" />
              <div className="font-headline-sm text-headline-sm mt-space-sm">{f.name}</div>
              <div className="text-body-sm text-on-surface-variant">{f.desc}</div>
              <div className="mt-space-sm flex items-center gap-1.5 flex-wrap">
                <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm font-mono">{f.ext}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div>
        <h2 className="font-headline-md text-headline-md mb-space-md">2. Export Options</h2>
        <ExportOptionsPanel
          options={options}
          onChange={setOptions}
          format={format}
          scope={scope}
          isImageProject={isImage}
          summary={summary}
          problem={problem}
          defaultGrid={defaultGrid}
        />
      </div>

      {scope === "project" ? (
        <div>
          <h2 className="font-headline-md text-headline-md mb-space-sm">3. All {isImage ? "Images" : "Slides"} in This Project</h2>
          <p className="text-body-sm text-on-surface-variant mb-space-md max-w-3xl">
            <span className="font-mono text-label-sm px-space-sm py-0.5 mr-space-sm rounded-full bg-surface-container-high">
              {kind === "file" ? chosen.ext : ".zip"}
            </span>
            {images ? (
              <>
                A ZIP with the {kind === "file" ? "combined" : "per-" + noun} <span className="font-mono">{format}</span> annotation file
                {kind === "file" ? "" : "s"}, {options.masks ? "the patch images and their label masks, " : "the patch images, "}
                and a <span className="font-mono">manifest.json</span>.
              </>
            ) : kind === "file" ? (
              <>
                One combined <span className="font-mono">{format}</span> file covering {isImage ? "all images" : "all slides"}, ready to feed a
                training pipeline.
              </>
            ) : (
              <>
                One <span className="font-mono">{format}</span> file per {noun}, bundled with a <span className="font-mono">manifest.json</span>.{" "}
                {isImage ? "Images" : "Slides"} that have no {isImage ? "annotation state" : "patch grid"} yet are left out and listed in the manifest with the reason.
              </>
            )}
          </p>
          {kind === "file" || images || (isImage && projectSlides.length > 50) ? null : (
            <Card className="p-0 overflow-hidden">
              <ul className="divide-y divide-outline-variant/40">
                {projectSlides.map((s) => {
                  const skipped = s.status === "imported" || s.status === "tissue_detected" || s.status === "error";
                  return (
                    <li key={s.id} className="flex items-center justify-between gap-space-md px-space-md py-space-sm text-body-sm">
                      <span className={`font-mono text-label-md truncate ${skipped ? "text-on-surface-variant line-through" : ""}`}>
                        {downloadName(s.filename, format, chosen.ext)}
                      </span>
                      <span className={`text-label-sm shrink-0 ${skipped ? "text-error" : "text-on-surface-variant"}`}>
                        {skipped ? "skipped: no patch grid yet" : s.status.replace(/_/g, " ")}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </div>
      ) : (
        <div>
          <h2 className="font-headline-md text-headline-md mb-space-sm">3. Live Manifest Preview</h2>
          <p className="text-body-sm text-on-surface-variant mb-space-md max-w-3xl">
            <span className="font-mono text-label-sm px-space-sm py-0.5 mr-space-sm rounded-full bg-surface-container-high">{chosen.space}</span>
            {chosen.note}
          </p>
          <div className="bg-[#0f172a] rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-space-md py-space-sm border-b border-slate-800">
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
                <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                <span className="ml-space-sm text-label-sm text-slate-400 font-mono">
                  {images ? "annotations/" : ""}
                  {downloadName(slide.filename, format, chosen.ext)}
                </span>
              </div>
              <div className="flex items-center gap-space-sm">
                <button onClick={handleCopy} disabled={!preview} className="text-label-sm text-slate-300 hover:text-white disabled:opacity-30">
                  Copy
                </button>
              </div>
            </div>
            <pre className="p-space-md text-label-sm font-mono text-slate-300 overflow-auto max-h-96">
              {loadingPreview ? "Loading preview..." : truncated ? preview.slice(0, PREVIEW_LIMIT) : preview}
            </pre>
            {truncated && (
              <div className="px-space-md py-space-sm border-t border-slate-800 text-label-sm text-slate-400">
                Preview shows the first {PREVIEW_LIMIT.toLocaleString()} of {preview.length.toLocaleString()} characters. The download contains the whole file.
              </div>
            )}
            {images && summary && (
              <div className="px-space-md py-space-sm border-t border-slate-800 text-label-sm text-slate-400">
                The ZIP also holds {summary.images.toLocaleString()} image file{summary.images === 1 ? "" : "s"} (&asymp; {formatBytes(summary.approx_image_bytes)}
                {options.masks ? ", plus masks" : ""}) that can't be shown here.
              </div>
            )}
          </div>
        </div>
      )}

      <div className="fixed bottom-4 left-14 right-4 z-30 flex justify-center pointer-events-none">
        <div className="pointer-events-auto bg-surface-container-highest rounded-2xl shadow-lg px-space-lg py-space-md flex items-center gap-space-md flex-wrap justify-center">
          <div className="flex items-center gap-space-sm">
            <span className="text-body-sm text-on-surface-variant">Scope</span>
            <div role="group" aria-label="Export scope" className="flex rounded-lg bg-surface-container-high p-0.5">
              {(
                [
                  ["slide", isImage ? "This image" : "This slide"],
                  ["project", `All ${noun}s (${projectSlides.length})`],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={scope === value}
                  onClick={() => setScope(value)}
                  className={`px-space-md py-1 rounded-md text-label-md transition-colors ${
                    scope === value ? "bg-surface-container-lowest shadow-sm text-primary" : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <Button variant="primary" icon="download" onClick={handleExport} disabled={downloading || !!problem} title={problem ?? undefined}>
            {buttonLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
