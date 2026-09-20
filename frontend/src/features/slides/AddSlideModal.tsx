import { useEffect, useMemo, useRef, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Modal } from "../../components/primitives";
import {
  ApiError,
  UploadAborted,
  createDemoSlide,
  getWsiFormats,
  importSlideByPath,
  uploadSlides,
  type UploadItem,
} from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { SlideBatchImportResult, WsiFormats } from "../../types/api";
import { extensionOf, findProblems, formatBytes, mergeSelections, summarize, toUploadItem } from "./uploadSelection";

type Tab = "upload" | "path" | "demo";
type Phase = "idle" | "uploading" | "processing" | "done";

const MAX_LISTED = 40;

interface Props {
  open: boolean;
  onClose: () => void;
  projectId: number;
  configVersionId: number | null;
  /** Called whenever at least one slide was added, so the caller can refresh. */
  onImported: () => void;
}

export function AddSlideModal({ open, onClose, projectId, configVersionId, onImported }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [tab, setTab] = useState<Tab>("upload");
  const [phase, setPhase] = useState<Phase>("idle");
  const [items, setItems] = useState<UploadItem[]>([]);
  const [progress, setProgress] = useState({ sent: 0, total: 0 });
  const [result, setResult] = useState<SlideBatchImportResult | null>(null);
  const [path, setPath] = useState("");
  const [formats, setFormats] = useState<WsiFormats | null>(null);
  const abortRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (open && !formats) getWsiFormats().then(setFormats).catch(() => setFormats(null));
  }, [open, formats]);

  const busy = phase === "uploading" || phase === "processing";
  const summary = useMemo(() => summarize(items), [items]);
  const problems = useMemo(() => findProblems(items), [items]);
  const tooBig = formats ? summary.bytes > formats.max_upload_bytes : false;

  function reset() {
    setPhase("idle");
    setItems([]);
    setResult(null);
    setProgress({ sent: 0, total: 0 });
  }

  function requestClose() {
    if (phase === "uploading") return; // must be cancelled explicitly, not by a stray click
    reset();
    onClose();
  }

  function finish(res: SlideBatchImportResult) {
    setResult(res);
    setPhase("done");
    if (res.slides.length > 0) {
      pushToast(`Imported ${res.slides.length} slide${res.slides.length === 1 ? "" : "s"}`, "success");
      onImported();
    } else {
      pushToast("No slides were imported -- see the details below", "error");
    }
  }

  function fail(e: unknown) {
    setPhase("idle");
    if (e instanceof UploadAborted) return;
    pushToast(e instanceof Error ? e.message : "Import failed", "error");
  }

  function pick(fileList: FileList | null) {
    if (!fileList?.length) return;
    // Copy out of the FileList right now: the caller resets the <input> straight
    // after this (so the same folder can be picked again), which empties the
    // live FileList before a deferred state updater would get to read it.
    const picked = Array.from(fileList).map(toUploadItem);
    setItems((cur) => mergeSelections(cur, picked));
  }

  async function handleUpload() {
    setPhase("uploading");
    setProgress({ sent: 0, total: summary.bytes });
    const { promise, abort } = uploadSlides(projectId, items, {
      configVersionId: configVersionId ?? undefined,
      onProgress: (sent, total) => setProgress({ sent, total }),
      onSent: () => setPhase("processing"),
    });
    abortRef.current = abort;
    try {
      finish(await promise);
    } catch (e) {
      fail(e);
    } finally {
      abortRef.current = null;
    }
  }

  async function handlePathImport() {
    setPhase("processing");
    try {
      finish(await importSlideByPath(projectId, path.trim(), configVersionId ?? undefined));
    } catch (e) {
      setPhase("idle");
      pushToast(e instanceof ApiError ? String(e.message) : "Import failed", "error");
    }
  }

  async function handleDemo() {
    setPhase("processing");
    try {
      await createDemoSlide(projectId, undefined, configVersionId ?? undefined);
      pushToast("Demo slide added", "success");
      onImported();
      reset();
      onClose();
    } catch (e) {
      setPhase("idle");
      pushToast(e instanceof Error ? e.message : "Failed to add demo slide", "error");
    }
  }

  const showResult = phase === "done" && result;

  return (
    <Modal open={open} onClose={requestClose} widthClass="max-w-2xl">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Add WSI slides</h2>
          <button onClick={requestClose} disabled={phase === "uploading"} aria-label="Close" className="disabled:opacity-30">
            <MaterialIcon name="close" />
          </button>
        </div>

        {!showResult && (
          <div className="flex bg-surface-container-low rounded p-0.5">
            {(["upload", "path", "demo"] as const).map((m) => (
              <button
                key={m}
                disabled={busy}
                onClick={() => setTab(m)}
                className={`flex-1 py-1.5 rounded text-label-md disabled:opacity-50 ${tab === m ? "bg-surface-container-lowest shadow-sm" : ""}`}
              >
                {m === "upload" ? "Upload" : m === "path" ? "Server path" : "Demo"}
              </button>
            ))}
          </div>
        )}

        {showResult && <ResultPanel result={result} onMore={reset} onDone={requestClose} />}

        {!showResult && tab === "upload" && (
          <UploadTab
            formats={formats}
            items={items}
            summary={summary}
            problems={problems}
            tooBig={tooBig}
            phase={phase}
            progress={progress}
            onPick={pick}
            onRemove={(i) => setItems((cur) => cur.filter((_, idx) => idx !== i))}
            onClear={() => setItems([])}
            onUpload={handleUpload}
            onCancel={() => abortRef.current?.()}
          />
        )}

        {!showResult && tab === "path" && (
          <div className="flex flex-col gap-space-sm">
            <p className="text-body-md text-on-surface-variant">
              Import from the server's watch directory (<span className="font-mono text-label-md">WSI_WATCH_DIR</span>) without
              uploading anything. Give a slide file (a <span className="font-mono text-label-md">.mrxs</span>'s data folder is
              found automatically), a folder of slides, or a <span className="font-mono text-label-md">.zip</span>. Files are copied;
              the originals are never modified. Best for very large slides.
            </p>
            <input className="input font-mono" placeholder="C:/path/to/watch-dir/slides" value={path} onChange={(e) => setPath(e.target.value)} disabled={busy} />
            <Button variant="primary" disabled={busy || !path.trim()} onClick={handlePathImport}>
              {busy ? "Importing..." : "Import"}
            </Button>
          </div>
        )}

        {!showResult && tab === "demo" && (
          <div className="flex flex-col gap-space-sm">
            <p className="text-body-md text-on-surface-variant">
              Adds a synthetic, procedurally generated slide -- no real WSI file needed. Useful for exercising the whole
              pipeline (tissue detection, patches, annotation, export) without patient data.
            </p>
            <Button variant="primary" disabled={busy} onClick={handleDemo}>
              {busy ? "Generating..." : "Add demo slide"}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}

function UploadTab({
  formats,
  items,
  summary,
  problems,
  tooBig,
  phase,
  progress,
  onPick,
  onRemove,
  onClear,
  onUpload,
  onCancel,
}: {
  formats: WsiFormats | null;
  items: UploadItem[];
  summary: ReturnType<typeof summarize>;
  problems: string[];
  tooBig: boolean;
  phase: Phase;
  progress: { sent: number; total: number };
  onPick: (files: FileList | null) => void;
  onRemove: (index: number) => void;
  onClear: () => void;
  onUpload: () => void;
  onCancel: () => void;
}) {
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const busy = phase === "uploading" || phase === "processing";
  const pct = progress.total ? Math.min(100, Math.round((progress.sent / progress.total) * 100)) : 0;

  return (
    <div className="flex flex-col gap-space-md">
      <p className="text-body-md text-on-surface-variant">
        Pick slide files, a whole folder, or <strong>.zip</strong> archives -- any mix, as many as you like. A zip can hold
        several slides and they are all imported.
      </p>

      <div className="flex flex-wrap gap-space-sm">
        <Button variant="secondary" icon="upload_file" disabled={busy} onClick={() => filesInput.current?.click()}>
          Choose files
        </Button>
        <Button variant="secondary" icon="folder_open" disabled={busy} onClick={() => folderInput.current?.click()}>
          Choose folder
        </Button>
        <input
          ref={filesInput}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            onPick(e.target.files);
            e.target.value = "";
          }}
        />
        <input
          ref={folderInput}
          type="file"
          multiple
          hidden
          {...({ webkitdirectory: "" } as Record<string, string>)}
          onChange={(e) => {
            onPick(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {formats && (
        <div className="text-body-sm text-on-surface-variant flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1">
            <span>Supported:</span>
            {formats.formats.map((f) => (
              <span key={f.extension} title={f.description} className="px-space-sm py-0.5 rounded-full bg-surface-container-high font-mono text-label-sm">
                {f.extension}
              </span>
            ))}
            <span className="px-space-sm py-0.5 rounded-full bg-primary-fixed font-mono text-label-sm">.zip</span>
          </div>
          <span>
            <strong>.mrxs</strong> slides come with a folder of the same name, and <strong>.vms/.vmu</strong> with tile files --
            choose the folder that contains them, or zip them together. Each slide is checked by OpenSlide; anything that
            isn't a readable slide is reported, not imported.
          </span>
        </div>
      )}

      {items.length > 0 && (
        <div className="border border-outline-variant rounded">
          <div className="flex items-center justify-between px-space-md py-space-sm bg-surface-container-low text-label-md">
            <span>
              {summary.count.toLocaleString()} file{summary.count === 1 ? "" : "s"} &middot; {formatBytes(summary.bytes)}
              {summary.archives > 0 && ` \u00b7 ${summary.archives} zip${summary.archives === 1 ? "" : "s"}`}
            </span>
            <button className="text-primary hover:underline disabled:opacity-40" onClick={onClear} disabled={busy}>
              Clear
            </button>
          </div>
          <ul className="max-h-44 overflow-y-auto divide-y divide-outline-variant/50">
            {items.slice(0, MAX_LISTED).map((it, i) => (
              <li key={`${it.path}-${i}`} className="flex items-center gap-space-sm px-space-md py-1.5 text-body-sm">
                <MaterialIcon name={extensionOf(it.path) === ".zip" ? "folder_zip" : "draft"} className="!text-[16px] text-on-surface-variant" />
                <span className="flex-1 truncate font-mono" title={it.path}>
                  {it.path}
                </span>
                <span className="text-on-surface-variant font-mono">{formatBytes(it.file.size)}</span>
                <button aria-label={`Remove ${it.path}`} disabled={busy} onClick={() => onRemove(i)} className="text-error disabled:opacity-30">
                  <MaterialIcon name="close" className="!text-[16px]" />
                </button>
              </li>
            ))}
            {items.length > MAX_LISTED && (
              <li className="px-space-md py-1.5 text-body-sm text-on-surface-variant">and {(items.length - MAX_LISTED).toLocaleString()} more files</li>
            )}
          </ul>
        </div>
      )}

      {problems.length > 0 && (
        <div className="rounded bg-amber-50 text-amber-900 px-space-md py-space-sm text-body-sm flex flex-col gap-1" role="alert">
          {problems.map((p) => (
            <div key={p} className="flex gap-1.5">
              <MaterialIcon name="warning" className="!text-[16px] shrink-0" />
              {p}
            </div>
          ))}
        </div>
      )}
      {tooBig && formats && (
        <div className="rounded bg-error-container text-on-error-container px-space-md py-space-sm text-body-sm" role="alert">
          This selection ({formatBytes(summary.bytes)}) is over the {formatBytes(formats.max_upload_bytes)} upload limit.
        </div>
      )}

      {busy ? (
        <div className="flex flex-col gap-space-sm">
          <div className="h-2 rounded-full bg-surface-container-high overflow-hidden">
            {phase === "uploading" ? (
              <div className="h-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
            ) : (
              <div className="h-full w-1/3 bg-primary animate-pulse" />
            )}
          </div>
          <div className="flex items-center justify-between text-body-sm text-on-surface-variant">
            <span>
              {phase === "uploading"
                ? `Uploading ${pct}% (${formatBytes(progress.sent)} of ${formatBytes(progress.total)})`
                : "Unpacking and reading slides on the server -- large slides can take a minute..."}
            </span>
            {phase === "uploading" && (
              <Button variant="ghost" onClick={onCancel}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      ) : (
        <Button variant="primary" icon="cloud_upload" disabled={items.length === 0 || tooBig} onClick={onUpload}>
          {items.length === 0 ? "Upload" : `Upload ${summary.count.toLocaleString()} file${summary.count === 1 ? "" : "s"} (${formatBytes(summary.bytes)})`}
        </Button>
      )}
    </div>
  );
}

function ResultPanel({ result, onMore, onDone }: { result: SlideBatchImportResult; onMore: () => void; onDone: () => void }) {
  return (
    <div className="flex flex-col gap-space-md">
      <div className="flex items-center gap-space-sm">
        <MaterialIcon name={result.slides.length ? "check_circle" : "error"} className={result.slides.length ? "text-tertiary" : "text-error"} />
        <h3 className="font-headline-sm text-headline-sm">
          {result.slides.length
            ? `Imported ${result.slides.length} slide${result.slides.length === 1 ? "" : "s"}`
            : "Nothing was imported"}
          {result.skipped.length > 0 && <span className="text-on-surface-variant font-normal"> &middot; {result.skipped.length} skipped</span>}
        </h3>
      </div>

      {result.slides.length > 0 && (
        <ul className="border border-outline-variant rounded divide-y divide-outline-variant/50 max-h-40 overflow-y-auto">
          {result.slides.map((s) => (
            <li key={s.id} className="flex items-center justify-between px-space-md py-1.5 text-body-sm">
              <span className="font-mono truncate">{s.filename}</span>
              <span className="text-on-surface-variant font-mono shrink-0 ml-space-md">
                {s.width_l0?.toLocaleString()} x {s.height_l0?.toLocaleString()} px &middot; {s.level_count} levels
              </span>
            </li>
          ))}
        </ul>
      )}

      {result.skipped.length > 0 && (
        <div className="flex flex-col gap-1">
          <div className="text-label-md text-on-surface-variant">Skipped</div>
          <ul className="border border-error/30 rounded divide-y divide-outline-variant/50 max-h-40 overflow-y-auto">
            {result.skipped.map((s, i) => (
              <li key={`${s.name}-${i}`} className="px-space-md py-1.5 text-body-sm">
                <span className="font-mono">{s.name}</span>
                <span className="text-on-surface-variant"> -- {s.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(result.ignored_file_count > 0 || result.warnings.length > 0) && (
        <div className="text-body-sm text-on-surface-variant flex flex-col gap-0.5">
          {result.ignored_file_count > 0 && (
            <span>
              {result.ignored_file_count === 1
                ? "1 other file isn't a slide and was ignored."
                : `${result.ignored_file_count.toLocaleString()} other files aren't slides and were ignored.`}
            </span>
          )}
          {result.warnings.map((w) => (
            <span key={w}>{w}</span>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-space-sm">
        <Button variant="ghost" onClick={onMore}>
          Import more
        </Button>
        <Button variant="primary" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}
