import { useState, type ReactNode } from "react";
import { useAuthStore } from "../../stores/authStore";
import { Button, Modal } from "../../components/primitives";
import { Toggle } from "../../components/formControls";
import { MaterialIcon } from "../../components/MaterialIcon";
import {
  ApiError,
  importAnnotations,
  parseAnnotationFile,
  type ImportAnnotationsResult,
  type ImportLabelTarget,
  type ParsedAnnotations,
} from "../../services/api";
import { useUiStore } from "../../stores/uiStore";

interface Props {
  open: boolean;
  onClose: () => void;
  slideId: number;
  /** Called after a successful import (at least one shape saved); the page then moves on. */
  onImported: (result: ImportAnnotationsResult) => void;
}

const ACCEPT = ".json,.geojson,.xml,.csv,.tsv,.txt,application/json,application/geo+json,text/xml,text/csv";

/** Imports annotations made elsewhere. The server reads the file whatever its format -- this app's
 * WSI JSON, GeoJSON (QuPath), COCO, ASAP or Aperio XML, a CSV of points/boxes/WKT -- into Level-0
 * shapes; the person then maps the file's labels onto the project's classes and chooses whether
 * shapes go into the patches containing them, before anything is saved. Re-importing the same file
 * is safe: duplicates are detected and skipped. */
export function ImportAnnotationsModal({ open, onClose, slideId, onImported }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const annotatorName = useAuthStore((s) => s.user?.name ?? "");
  const [file, setFile] = useState<File | null>(null);
  // null: the scale is worked out from the file. A number is only typed for a file drawn on a
  // smaller copy of the slide that the file itself doesn't reveal.
  const [manualScale, setManualScale] = useState<number | null>(null);
  const [scaleDraft, setScaleDraft] = useState<string | null>(null); // the adjust box, while open
  const [parsed, setParsed] = useState<ParsedAnnotations | null>(null);
  const [labelMap, setLabelMap] = useState<Record<string, ImportLabelTarget>>({});
  const [assignToPatches, setAssignToPatches] = useState(true);
  const [busy, setBusy] = useState<"reading" | "importing" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportAnnotationsResult | null>(null);

  function reset() {
    setFile(null);
    setManualScale(null);
    setScaleDraft(null);
    setParsed(null);
    setLabelMap({});
    setError(null);
    setResult(null);
  }

  function close() {
    reset();
    onClose();
  }

  async function read(f: File, scale: number | null) {
    setBusy("reading");
    setManualScale(scale);
    setError(null);
    setResult(null);
    try {
      const p = await parseAnnotationFile(slideId, f, scale ?? undefined);
      setParsed(p);
      // A label matching a class by name goes to it; shapes without a label come in without a class;
      // anything else is left out until the person picks a class for it.
      setLabelMap(
        Object.fromEntries(
          p.labels.map((l) => [l.label, l.class_id ?? (l.label === "" ? "unlabeled" : "skip")] as [string, ImportLabelTarget]),
        ),
      );
    } catch (e) {
      setParsed(null);
      setError(e instanceof ApiError ? e.message : "The file could not be read");
    } finally {
      setBusy(null);
    }
  }

  async function runImport() {
    if (!parsed) return;
    setBusy("importing");
    try {
      const res = await importAnnotations(slideId, {
        annotations: parsed.annotations,
        created_by: `${annotatorName} (imported)`,
        label_map: labelMap,
        assign_to_patches: assignToPatches,
      });
      if (res.imported > 0) {
        const skipped = res.total - res.imported;
        pushToast(
          `Imported ${res.imported} annotation${res.imported === 1 ? "" : "s"}${skipped ? ` (${skipped} skipped)` : ""}`,
          "success",
        );
        reset();
        onImported(res);
      } else {
        setResult(res); // nothing saved: stay and show why
        pushToast("Nothing was imported -- see details below", "error");
      }
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : "Import failed", "error");
    } finally {
      setBusy(null);
    }
  }

  const toImport = parsed
    ? parsed.labels.filter((l) => labelMap[l.label] !== "skip").reduce((n, l) => n + l.count, 0)
    : 0;
  const unlinked = parsed ? parsed.annotations.length - parsed.linked_to_patches : 0;
  const unreadable = parsed ? Object.entries(parsed.unreadable) : [];

  function applyScale() {
    const factor = Number(scaleDraft);
    if (!(factor > 0) || !file) {
      setError("The scale must be a positive number.");
      return;
    }
    setScaleDraft(null);
    void read(file, factor);
  }

  return (
    <Modal open={open} onClose={close} widthClass="max-w-3xl">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Import Annotations</h2>
          <button onClick={close} aria-label="Close">
            <MaterialIcon name="close" />
          </button>
        </div>

        <p className="text-body-md text-on-surface-variant">
          Reads <strong>WSI JSON</strong> (this app), <strong>GeoJSON</strong> (QuPath and others),{" "}
          <strong>COCO</strong>, <strong>Cytomine</strong> (term IDs matched to each class's Class ID),{" "}
          <strong>ASAP</strong> or <strong>Aperio ImageScope XML</strong>, and <strong>CSV/TSV</strong>{" "}
          tables (x/y points, xmin/ymin/xmax/ymax boxes, or a WKT column). The format is detected from the content. Nothing
          is saved until you press Import.
        </p>

        <label className="flex flex-col gap-1">
          <span className="text-label-md text-on-surface-variant">File</span>
          <input
            type="file"
            accept={ACCEPT}
            disabled={!!busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              setFile(f);
              setScaleDraft(null);
              void read(f, null); // a new file: back to the automatic scale
            }}
            className="text-body-md"
          />
        </label>

        {busy === "reading" && <div className="text-body-md text-on-surface-variant">Reading {file?.name}...</div>}
        {error && (
          <div className="rounded bg-error-container text-on-error-container p-space-sm text-body-md">{error}</div>
        )}

        {parsed && !result && (
          <>
            <div className="rounded bg-surface-container-low p-space-md flex flex-col gap-1.5 text-body-md">
              <Row label="Format" value={parsed.format_name} />
              <Row
                label="Shapes"
                value={`${parsed.annotations.length} (${Object.entries(parsed.shape_counts)
                  .map(([t, n]) => `${n} ${t.replace("_", " ")}`)
                  .join(", ")})`}
              />
              {parsed.bounds && (
                <Row
                  label="Extent (Level-0 px)"
                  value={`x ${fmt(parsed.bounds[0])}–${fmt(parsed.bounds[2])}, y ${fmt(parsed.bounds[1])}–${fmt(parsed.bounds[3])}`}
                />
              )}
              {parsed.slide_size[0] && (
                <Row label="Slide size" value={`${fmt(parsed.slide_size[0])} × ${fmt(parsed.slide_size[1] ?? 0)}`} />
              )}
              <div className="flex items-center justify-between gap-space-md">
                <span className="text-on-surface-variant" title="Coordinates are multiplied by this to reach the slide's full-resolution pixels">
                  Scale
                </span>
                {scaleDraft === null ? (
                  <span className="flex items-center gap-space-sm">
                    <span className="font-mono">
                      {parsed.scale === 1 ? "×1 (full resolution)" : `×${parsed.scale}`}
                      <span className="font-sans text-on-surface-variant">
                        {manualScale === null ? " · automatic" : " · set by you"}
                      </span>
                    </span>
                    <button className="text-primary text-label-md hover:underline" onClick={() => setScaleDraft(String(parsed.scale))}>
                      Adjust
                    </button>
                    {manualScale !== null && file && (
                      <button className="text-primary text-label-md hover:underline" onClick={() => void read(file, null)}>
                        Automatic
                      </button>
                    )}
                  </span>
                ) : (
                  <span className="flex items-center gap-space-sm">
                    <span className="font-mono">×</span>
                    <input
                      className="input !w-24"
                      type="number"
                      min="0"
                      step="any"
                      autoFocus
                      value={scaleDraft}
                      onChange={(e) => setScaleDraft(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && applyScale()}
                      aria-label="Scale"
                    />
                    <Button variant="secondary" onClick={applyScale}>
                      Apply
                    </Button>
                    <Button variant="ghost" onClick={() => setScaleDraft(null)}>
                      Cancel
                    </Button>
                  </span>
                )}
              </div>
              {unreadable.map(([reason, n]) => (
                <Row key={reason} label={`Could not read -- ${reason}`} value={String(n)} tone="warn" />
              ))}
            </div>

            {parsed.outside_slide > 0 && (
              <Notice>
                {parsed.outside_slide} shape{parsed.outside_slide === 1 ? "" : "s"} reach outside the slide and will be
                skipped. If most do, the file was probably drawn on a smaller copy of the slide -- compare the extent
                with the slide size and use <strong>Adjust</strong> next to Scale (e.g. 4 for a 4× smaller image).
              </Notice>
            )}
            {parsed.scale_note && <p className="text-body-sm text-on-surface-variant">{parsed.scale_note}</p>}
            {parsed.warnings.map((w) => (
              <Notice key={w}>{w}</Notice>
            ))}

            {parsed.annotations.length > 0 && (
              <div className="flex flex-col gap-space-sm">
                <h3 className="font-headline-sm text-label-lg">Labels</h3>
                {parsed.classes.length === 0 && (
                  <Notice>This project has no classes yet; shapes can only be imported without a class.</Notice>
                )}
                <div className="rounded border border-outline-variant divide-y divide-outline-variant">
                  {parsed.labels.map((l) => {
                    const target = labelMap[l.label];
                    return (
                      <div key={l.label} className="flex items-center gap-space-md px-space-md py-1.5">
                        <span className={`flex-1 truncate ${l.label ? "" : "italic text-on-surface-variant"}`} title={l.label}>
                          {l.label || "(no label)"}
                        </span>
                        <span className="font-mono text-label-md text-on-surface-variant w-16 text-right">{l.count}</span>
                        <MaterialIcon name="arrow_forward" className="!text-[16px] text-on-surface-variant" />
                        <select
                          className={`input !w-56 !py-0.5 ${target === "skip" ? "text-error" : ""}`}
                          value={String(target)}
                          onChange={(e) => {
                            const v = e.target.value;
                            setLabelMap((m) => ({ ...m, [l.label]: v === "skip" || v === "unlabeled" ? v : Number(v) }));
                          }}
                        >
                          {parsed.classes.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name}
                              {c.code != null ? ` (ID ${c.code})` : ""}
                            </option>
                          ))}
                          <option value="unlabeled">Import without a class</option>
                          <option value="skip">Skip</option>
                        </select>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {unlinked > 0 && !parsed.image_project && (
              <div className="flex flex-col gap-1">
                <Toggle
                  label="Place each shape in the patch that contains it"
                  checked={assignToPatches}
                  onChange={setAssignToPatches}
                />
                <span className="text-body-sm text-on-surface-variant pl-6">
                  Shapes in a patch count toward its status and can be edited in the patch view. Shapes no patch fully
                  contains (or all of them, when this is off) are added to the whole slide.
                </span>
              </div>
            )}
            {parsed.linked_to_patches > 0 && (
              <p className="text-body-sm text-on-surface-variant">
                {parsed.linked_to_patches} shape{parsed.linked_to_patches === 1 ? "" : "s"} name the patch they were drawn
                in; they are matched to this slide's patches by position, so the same patch grid must be generated.
              </p>
            )}
          </>
        )}

        {result && (
          <div className="rounded bg-surface-container-low p-space-md flex flex-col gap-1.5 text-body-md">
            <Row label="Shapes sent" value={String(result.total)} />
            <Row label="Imported" value={String(result.imported)} tone="success" />
            {result.imported > 0 && (
              <Row
                label="  of which in patches / on the whole slide"
                value={`${result.imported_to_patches} / ${result.imported_on_slide}`}
              />
            )}
            <Row label="Skipped -- chosen to skip" value={String(result.skipped_by_choice)} />
            <Row label="Skipped -- already imported" value={String(result.skipped_duplicate)} />
            <SkipRow label="Skipped -- unknown class label" value={result.skipped_unknown_class} />
            <SkipRow label="Skipped -- no matching patch" value={result.skipped_no_matching_patch} />
            <SkipRow label="Skipped -- outside the slide" value={result.skipped_outside_slide} />
            <SkipRow label="Skipped -- malformed shape" value={result.skipped_invalid_shape} />
          </div>
        )}

        <div className="flex justify-end gap-space-sm">
          {result ? (
            <>
              <Button variant="ghost" onClick={reset}>
                Import another file
              </Button>
              <Button variant="primary" onClick={close}>
                Done
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={close}>
                Cancel
              </Button>
              <Button
                variant="primary"
                icon="upload"
                disabled={!parsed || toImport === 0 || !!busy}
                onClick={() => void runImport()}
              >
                {busy === "importing" ? "Importing..." : `Import ${toImport} shape${toImport === 1 ? "" : "s"}`}
              </Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}

function fmt(n: number) {
  return Math.round(n).toLocaleString();
}

function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-space-sm rounded bg-amber-500/10 text-on-surface p-space-sm text-body-sm">
      <MaterialIcon name="warning" className="!text-[18px] text-amber-600 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "success" | "warn" }) {
  const color = tone === "success" ? "text-tertiary" : tone === "warn" ? "text-error" : "text-on-surface";
  return (
    <div className="flex items-center justify-between gap-space-md">
      <span className="text-on-surface-variant whitespace-pre">{label}</span>
      <span className={`font-mono text-right ${color}`}>{value}</span>
    </div>
  );
}

function SkipRow({ label, value }: { label: string; value: number }) {
  return value ? <Row label={label} value={String(value)} tone="warn" /> : null;
}
