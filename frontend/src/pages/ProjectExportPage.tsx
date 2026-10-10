import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { EXPORT_FORMATS, type ExportFormat } from "../features/export/formats";
import { ExportOptionsPanel } from "../features/export/ExportOptionsPanel";
import { SplitPanel } from "../features/split/SplitPanel";
import { formatBytes } from "../features/slides/uploadSelection";
import {
  exportSelectionUrl,
  exportSlideUrl,
  getExportSummary,
  getProject,
  listProjectGrids,
  listSlides,
  startDownload,
} from "../services/api";
import { useUiStore } from "../stores/uiStore";
import type { GridSpec, ProjectDetail, Slide, SplitMode } from "../types/api";
import { gridKey } from "../utils/gridKey";
import {
  DEFAULT_EXPORT_OPTIONS,
  normalizeOptions,
  selectionDownloadKind,
  selectionProblem,
  type ExportOptions,
  type ExportSummary,
} from "../utils/exportOptions";

// The preview is only a glance at the file; the download always carries everything.
const PREVIEW_LIMIT = 60_000;
// Rows drawn at once in a very large image project; the search narrows it, the selection covers everything.
const ROW_LIMIT = 200;

const JSON_FORMATS = EXPORT_FORMATS.filter((f) => f.ext !== ".csv");
const CSV_FORMATS = EXPORT_FORMATS.filter((f) => f.ext === ".csv");

/** Whether a slide can be exported with these options, and if not, why. */
function readiness(slide: Slide, options: ExportOptions, isImage: boolean): string | null {
  if (slide.status === "error") return "Import failed";
  if (options.grid && !isImage) return slide.width_l0 ? null : "Not imported yet";
  if (!slide.active_config_version_id || !(slide.patch_count ?? 0)) return isImage ? "Not ready yet" : "No patches yet";
  return null;
}

/**
 * The project's export screen: choose the slides, the files (JSON, CSV, patch images) and the options,
 * and get them in one download.
 */
export function ProjectExportPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const [searchParams] = useSearchParams();
  const pushToast = useUiStore((s) => s.pushToast);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [slides, setSlides] = useState<Slide[] | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [formats, setFormats] = useState<string[]>(["wsi_json"]);
  const [options, setOptionsState] = useState<ExportOptions>(DEFAULT_EXPORT_OPTIONS);
  const [defaultGrid, setDefaultGrid] = useState<GridSpec | null>(null);
  const [summary, setSummary] = useState<ExportSummary | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [splitMode, setSplitMode] = useState<SplitMode | null>(null); // null: not loaded yet
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewFormat, setPreviewFormat] = useState<string | null>(null);
  const [previewSlide, setPreviewSlide] = useState<number | null>(null);
  const [preview, setPreview] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);

  const isImage = project?.project_type === "image";
  const noun = isImage ? "image" : "slide";
  const nouns = isImage ? "images" : "slides";
  const images = options.content === "images";
  const setOptions = (next: ExportOptions) => setOptionsState(normalizeOptions(next));

  useEffect(() => {
    getProject(pid).then(setProject).catch(() => undefined);
    listProjectGrids(pid)
      .then((grids) => setDefaultGrid((grids.find((g) => g.is_default) ?? grids[0])?.spec ?? null))
      .catch(() => setDefaultGrid(null));
    listSlides(pid)
      .then((list) => {
        const sorted = [...list].sort((a, b) => a.id - b.id);
        setSlides(sorted);
        // Straight from a slide's own Export button: just that slide. Otherwise every slide that is ready.
        const only = (searchParams.get("slides") ?? "").split(",").map(Number).filter(Boolean);
        const start = only.length ? sorted.filter((s) => only.includes(s.id)) : sorted;
        setChosen(new Set(start.map((s) => s.id)));
      })
      .catch(() => setSlides([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  const readyOf = useMemo(() => {
    const out = new Map<number, string | null>();
    for (const s of slides ?? []) out.set(s.id, readiness(s, options, isImage));
    return out;
  }, [slides, options, isImage]);
  const ready = (slides ?? []).filter((s) => readyOf.get(s.id) === null);
  const selected = ready.filter((s) => chosen.has(s.id)); // what the download covers
  const selectedIds = selected.map((s) => s.id);
  const selectedKey = selectedIds.join(",");
  const gridParam = options.grid ? gridKey(options.grid) : "";
  const classParam = `${options.minCoverage}|${options.unlabeled}|${options.otherLabels}`;

  // What the selection covers, refreshed whenever it changes.
  useEffect(() => {
    if (!slides) return;
    setSummary(null);
    if (!selectedIds.length) return;
    let stale = false;
    getExportSummary("project", pid, options, formats, selectedIds)
      .then((s) => !stale && setSummary(s))
      .catch(() => undefined);
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slides, selectedKey, formats.join(","), options.patches, options.imageFormat, options.content, gridParam, classParam]);

  // The preview: one chosen file for one chosen slide, fetched only while it is open.
  const shownFormat = previewFormat && formats.includes(previewFormat) ? previewFormat : formats[0];
  const shownSlide = previewSlide !== null && selectedIds.includes(previewSlide) ? previewSlide : selectedIds[0];
  useEffect(() => {
    if (!previewOpen || !shownFormat || shownSlide === undefined) return;
    let stale = false;
    setLoadingPreview(true);
    setPreview("");
    fetch(exportSlideUrl(shownSlide, shownFormat, { ...options, content: "annotations", masks: false }))
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewOpen, shownFormat, shownSlide, options.patches, gridParam, classParam]);

  const kind = selectionDownloadKind(project?.project_type, formats, options, selected.length);
  const problem = selectionProblem(summary, options, formats, selected.length, noun);

  /** A project with a split is exported by it unless the person unticks that; without one there is nothing to sort by. */
  function splitModeChanged(mode: SplitMode) {
    const turnedOn = mode !== "off" && (splitMode === null || splitMode === "off");
    setSplitMode(mode);
    setOptionsState((o) => ({ ...o, split: mode === "off" ? false : turnedOn ? true : o.split }));
  }

  function toggleFormat(id: string) {
    setFormats((prev) => (prev.includes(id) ? prev.filter((f) => f !== id) : [...prev, id]));
  }

  function toggleSlide(id: number) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleDownload() {
    if (problem || !slides) return;
    // Straight from the click: the browser downloads the file itself (see startDownload).
    startDownload(exportSelectionUrl(pid, options, formats, selectedIds, slides.length));
    pushToast(
      images || selected.length > 1 || formats.length > 1 || options.split
        ? "Building the download on the server. Your browser saves it as soon as it is ready; large exports can take a few minutes."
        : "Download started.",
      "info",
    );
    setDownloading(true); // a moment's pause, so a double click does not build it twice
    window.setTimeout(() => setDownloading(false), 3000);
  }

  if (!slides || !project) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

  const needle = query.trim().toLowerCase();
  const matching = needle ? slides.filter((s) => s.filename.toLowerCase().includes(needle)) : slides;
  const rows = matching.slice(0, ROW_LIMIT);
  const matchingReady = matching.filter((s) => readyOf.get(s.id) === null);
  const allMatchingChosen = matchingReady.length > 0 && matchingReady.every((s) => chosen.has(s.id));

  function setMatching(on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      for (const s of matchingReady) {
        if (on) next.add(s.id);
        else next.delete(s.id);
      }
      return next;
    });
  }

  const chosenNames = formats.map((id) => EXPORT_FORMATS.find((f) => f.id === id)?.name ?? id);
  const buttonLabel = downloading
    ? "Download started..."
    : `Download ${selected.length} ${selected.length === 1 ? noun : nouns} (${kind === "file" ? (EXPORT_FORMATS.find((f) => f.id === formats[0])?.ext ?? "file") : ".zip"})`;

  return (
    <div className="max-w-6xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg pb-32">
      <div className="bg-surface-container-low rounded-xl p-space-lg">
        <div className="text-label-sm text-on-surface-variant mb-1">
          <Link to={`/projects/${pid}`} className="hover:underline text-primary">
            {project.name}
          </Link>{" "}
          / Export
        </div>
        <h1 className="font-headline-lg text-headline-lg mb-space-sm">Export Annotations & Datasets</h1>
        <p className="text-body-md text-on-surface-variant max-w-3xl">
          Choose the {nouns}, the files you want -- JSON, CSV and the {isImage ? "images" : "patch images"} -- and get them in one download.
          Coordinates are always kept in Level-0 pixels of the original {noun}.
        </p>
      </div>

      {/* 1. Slides */}
      <section>
        <div className="flex items-end justify-between gap-space-md flex-wrap mb-space-md">
          <div>
            <h2 className="font-headline-md text-headline-md">1. {isImage ? "Images" : "Slides"}</h2>
            <p className="text-body-sm text-on-surface-variant">
              {selected.length} of {ready.length} ready {ready.length === 1 ? noun : nouns} chosen
              {slides.length > ready.length && ` · ${slides.length - ready.length} not ready to export`}
            </p>
          </div>
          <div className="flex items-center gap-space-sm flex-wrap">
            {slides.length > 8 && (
              <input className="input !w-56" placeholder={`Search ${nouns}...`} value={query} onChange={(e) => setQuery(e.target.value)} />
            )}
            <Button onClick={() => setMatching(!allMatchingChosen)} disabled={matchingReady.length === 0}>
              {allMatchingChosen ? "Clear" : needle ? "Choose matching" : "Choose all ready"}
            </Button>
          </div>
        </div>
        <Card className="p-0 overflow-hidden">
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-body-sm">
              <thead className="sticky top-0 bg-surface-container-low text-label-sm text-on-surface-variant">
                <tr>
                  <th className="w-10 px-space-md py-space-sm">
                    <input
                      type="checkbox"
                      className="w-4 h-4"
                      aria-label={`Choose all ready ${nouns}`}
                      checked={allMatchingChosen}
                      disabled={matchingReady.length === 0}
                      onChange={(e) => setMatching(e.target.checked)}
                    />
                  </th>
                  <th className="text-left px-space-sm py-space-sm font-medium">{isImage ? "Image" : "Slide"}</th>
                  {!isImage && <th className="text-right px-space-sm py-space-sm font-medium">Patches</th>}
                  <th className="text-right px-space-sm py-space-sm font-medium">Annotated</th>
                  <th className="text-right px-space-sm py-space-sm font-medium">Reviewed</th>
                  <th className="text-left px-space-md py-space-sm font-medium">Export</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const reason = readyOf.get(s.id);
                  const on = reason === null && chosen.has(s.id);
                  return (
                    <tr
                      key={s.id}
                      onClick={() => reason === null && toggleSlide(s.id)}
                      className={`border-t border-outline-variant/40 ${reason === null ? "cursor-pointer hover:bg-surface-container-low/60" : "opacity-60"}`}
                    >
                      <td className="px-space-md py-space-sm text-center">
                        <input
                          type="checkbox"
                          className="w-4 h-4"
                          aria-label={`Export ${s.filename}`}
                          checked={on}
                          disabled={reason !== null}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => toggleSlide(s.id)}
                        />
                      </td>
                      <td className="px-space-sm py-space-sm font-mono text-label-md truncate max-w-[22rem]" title={s.filename}>
                        {s.filename}
                      </td>
                      {!isImage && <td className="px-space-sm py-space-sm text-right font-mono">{(s.patch_count ?? 0).toLocaleString()}</td>}
                      <td className="px-space-sm py-space-sm text-right font-mono">{(s.annotated_patch_count ?? 0).toLocaleString()}</td>
                      <td className="px-space-sm py-space-sm text-right font-mono">{(s.reviewed_patch_count ?? 0).toLocaleString()}</td>
                      <td className="px-space-md py-space-sm">
                        {reason === null ? (
                          <span className="inline-flex items-center gap-1 text-label-sm text-emerald-700">
                            <MaterialIcon name="check_circle" className="!text-[16px]" />
                            Ready
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-label-sm text-on-surface-variant">
                            <MaterialIcon name="block" className="!text-[16px]" />
                            {reason}
                            {!isImage && s.status !== "error" && (
                              <Link
                                to={`/projects/${pid}/slides/${s.id}/processing`}
                                onClick={(e) => e.stopPropagation()}
                                className="ml-1 text-primary hover:underline"
                              >
                                generate
                              </Link>
                            )}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-space-md py-space-lg text-center text-on-surface-variant">
                      {slides.length === 0 ? `No ${nouns} in this project yet.` : `No ${noun} matches "${query}".`}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {matching.length > ROW_LIMIT && (
            <div className="px-space-md py-space-sm border-t border-outline-variant/40 text-label-sm text-on-surface-variant">
              Showing {ROW_LIMIT} of {matching.length.toLocaleString()} -- search to find others. The choice covers all of them.
            </div>
          )}
        </Card>
      </section>

      {/* 2. Split */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-sm">2. Train / Validation / Test Split</h2>
        <SplitPanel projectId={pid} isImageProject={isImage} onModeChange={splitModeChanged} />
        {splitMode !== null && splitMode !== "off" && (
          <label className="mt-space-md flex items-start gap-space-sm text-body-md cursor-pointer">
            <input type="checkbox" className="w-4 h-4 mt-1" checked={options.split} onChange={(e) => setOptions({ ...options, split: e.target.checked })} />
            <span>
              Sort this download into train / val / test folders
              <span className="block text-body-sm text-on-surface-variant">
                Each set gets its own annotation files{images ? " and images" : ""}, and a <span className="font-mono">splits.csv</span> lists
                which {noun} went where. Unchecked exports everything together.
              </span>
            </span>
          </label>
        )}
      </section>

      {/* 3. Files */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-sm">3. Files to Download</h2>
        <p className="text-body-sm text-on-surface-variant mb-space-md">Tick as many as you need; several files come together in one ZIP.</p>
        <div className="grid lg:grid-cols-3 gap-space-md">
          <FormatGroup title="JSON" icon="data_object" list={JSON_FORMATS} chosen={formats} onToggle={toggleFormat} />
          <FormatGroup title="CSV" icon="table_chart" list={CSV_FORMATS} chosen={formats} onToggle={toggleFormat} />
          <div className="flex flex-col gap-space-sm">
            <div className="flex items-center gap-1.5 text-label-md text-on-surface-variant">
              <MaterialIcon name="image" className="!text-[18px]" />
              Images
            </div>
            <CheckCard
              checked={images}
              onToggle={() => setOptions({ ...options, content: images ? "annotations" : "images" })}
              title={isImage ? "Images" : "Patch images"}
              body={isImage ? "Re-encoded from your originals" : "Cut from the original slide, one per patch"}
              tag={images ? `.${options.imageFormat}` : undefined}
            />
            {images && (
              <Card className="p-space-md flex flex-col gap-space-sm">
                <div role="group" aria-label="Image format" className="flex rounded-lg bg-surface-container-high p-0.5 self-start">
                  {(
                    [
                      ["png", "PNG · lossless"],
                      ["jpg", "JPEG · smaller"],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      aria-pressed={options.imageFormat === value}
                      onClick={() => setOptions({ ...options, imageFormat: value })}
                      className={`px-space-md py-1 rounded-md text-label-md ${
                        options.imageFormat === value ? "bg-surface-container-lowest shadow-sm text-primary" : "text-on-surface-variant"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <label className="flex items-center gap-space-sm text-body-sm cursor-pointer">
                  <input type="checkbox" className="w-4 h-4" checked={options.masks} onChange={(e) => setOptions({ ...options, masks: e.target.checked })} />
                  Also write label masks (PNG, one value per class)
                </label>
                <p className="text-label-sm text-on-surface-variant">
                  Made for this download only, nothing is stored. {isImage ? "Images" : "Patches"} flagged &ldquo;Exclude from training&rdquo; get none.
                </p>
              </Card>
            )}
          </div>
        </div>
      </section>

      {/* 4. Options */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-md">4. Options</h2>
        <ExportOptionsPanel
          options={options}
          onChange={setOptions}
          formats={formats}
          isImageProject={isImage}
          slideCount={selected.length}
          defaultGrid={defaultGrid}
        />
      </section>

      {/* 5. Preview */}
      <section>
        <button className="flex items-center gap-space-sm font-headline-md text-headline-md" onClick={() => setPreviewOpen((v) => !v)} aria-expanded={previewOpen}>
          <MaterialIcon name={previewOpen ? "expand_less" : "expand_more"} />
          5. Preview a File
        </button>
        {previewOpen && (
          <div className="mt-space-md">
            {!shownFormat || shownSlide === undefined ? (
              <p className="text-body-sm text-on-surface-variant">Choose a {noun} and a file to preview.</p>
            ) : (
              <>
                <div className="flex items-center gap-space-sm flex-wrap mb-space-sm">
                  <select className="input !w-auto" value={shownFormat} onChange={(e) => setPreviewFormat(e.target.value)} aria-label="File to preview">
                    {formats.map((id) => (
                      <option key={id} value={id}>
                        {EXPORT_FORMATS.find((f) => f.id === id)?.name ?? id}
                      </option>
                    ))}
                  </select>
                  <select className="input !w-auto" value={shownSlide} onChange={(e) => setPreviewSlide(Number(e.target.value))} aria-label={`${noun} to preview`}>
                    {selected.slice(0, 500).map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.filename}
                      </option>
                    ))}
                  </select>
                  <span className="text-body-sm text-on-surface-variant">{EXPORT_FORMATS.find((f) => f.id === shownFormat)?.note}</span>
                </div>
                <div className="bg-[#0f172a] rounded-xl overflow-hidden">
                  <div className="flex items-center justify-end px-space-md py-space-sm border-b border-slate-800">
                    <button
                      onClick={() => navigator.clipboard.writeText(preview).then(() => pushToast("Copied to clipboard", "success"), () => pushToast("Could not access the clipboard", "error"))}
                      disabled={!preview}
                      className="text-label-sm text-slate-300 hover:text-white disabled:opacity-30"
                    >
                      Copy
                    </button>
                  </div>
                  <pre className="p-space-md text-label-sm font-mono text-slate-300 overflow-auto max-h-96">
                    {loadingPreview ? "Loading preview..." : preview.slice(0, PREVIEW_LIMIT)}
                  </pre>
                  {preview.length > PREVIEW_LIMIT && (
                    <div className="px-space-md py-space-sm border-t border-slate-800 text-label-sm text-slate-400">
                      Showing the first {PREVIEW_LIMIT.toLocaleString()} of {preview.length.toLocaleString()} characters. The download has the whole file.
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </section>

      {/* Download bar */}
      <div className="fixed bottom-4 left-14 right-4 z-30 flex justify-center pointer-events-none">
        <div className="pointer-events-auto bg-surface-container-highest rounded-2xl shadow-lg px-space-lg py-space-md flex items-center gap-space-lg flex-wrap justify-center max-w-4xl">
          <div className="text-body-sm text-on-surface-variant min-w-0">
            {problem ? (
              <span className="flex items-center gap-1 text-error" role="alert">
                <MaterialIcon name="error" className="!text-[18px] shrink-0" />
                {problem}
              </span>
            ) : summary ? (
              <span>
                <span className="text-on-surface">{chosenNames.join(" + ")}</span>
                {images && " + images"}
                {options.split && " · split into train / val / test"} · {summary.patches.toLocaleString()} {isImage ? "images" : "patches"} ·{" "}
                {summary.annotations.toLocaleString()} annotations
                {images && ` · ${summary.images.toLocaleString()} image files ≈ ${formatBytes(summary.approx_image_bytes)}`}
              </span>
            ) : (
              <span>Counting...</span>
            )}
          </div>
          <Button variant="primary" icon="download" onClick={handleDownload} disabled={downloading || !!problem || !summary} title={problem ?? undefined}>
            {buttonLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

function FormatGroup({
  title,
  icon,
  list,
  chosen,
  onToggle,
}: {
  title: string;
  icon: string;
  list: ExportFormat[];
  chosen: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-space-sm">
      <div className="flex items-center gap-1.5 text-label-md text-on-surface-variant">
        <MaterialIcon name={icon} className="!text-[18px]" />
        {title}
      </div>
      {list.map((f) => (
        <CheckCard key={f.id} checked={chosen.includes(f.id)} onToggle={() => onToggle(f.id)} title={f.name} body={f.desc} tag={f.ext} />
      ))}
    </div>
  );
}

function CheckCard({ checked, onToggle, title, body, tag }: { checked: boolean; onToggle: () => void; title: string; body: string; tag?: string }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      onClick={onToggle}
      className={`text-left p-space-md rounded-xl flex gap-space-sm items-start bg-surface-container-lowest shadow-sm transition-shadow ${
        checked ? "ring-2 ring-primary" : "hover:shadow-md"
      }`}
    >
      <MaterialIcon name={checked ? "check_box" : "check_box_outline_blank"} className="text-primary mt-0.5 shrink-0" />
      <span className="flex-1 min-w-0">
        <span className="flex items-center justify-between gap-space-sm">
          <span className="font-headline-sm text-headline-sm">{title}</span>
          {tag && <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm font-mono shrink-0">{tag}</span>}
        </span>
        <span className="block text-body-sm text-on-surface-variant">{body}</span>
      </span>
    </button>
  );
}
