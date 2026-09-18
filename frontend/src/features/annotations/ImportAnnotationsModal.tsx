import { useState } from "react";
import { Button, Modal } from "../../components/primitives";
import { MaterialIcon } from "../../components/MaterialIcon";
import { ApiError, importAnnotations, type ImportAnnotationsResult } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";

interface Props {
  open: boolean;
  onClose: () => void;
  slideId: number;
  onImported: () => void;
}

/** Re-imports annotations from a previously exported WSI JSON file (see
 * ExportPage / the "wsi_json" format). This is the inverse of export: it lets
 * a project pick up annotations generated elsewhere -- a backup, another
 * VirtualPatch instance, or a model's predictions formatted the same way --
 * as long as patches have already been generated for this slide with a
 * matching grid (patches are matched by exact Level-0 origin, never
 * fabricated from unverified import data). */
export function ImportAnnotationsModal({ open, onClose, slideId, onImported }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const annotatorName = useUiStore((s) => s.annotatorName);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportAnnotationsResult | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  function reset() {
    setResult(null);
    setFileName(null);
  }

  async function handleFile(file: File) {
    setFileName(file.name);
    setResult(null);
    setBusy(true);
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        pushToast("That file isn't valid JSON", "error");
        return;
      }

      const annotations = Array.isArray(parsed)
        ? parsed
        : (parsed as { annotations?: unknown[] })?.annotations;

      if (!Array.isArray(annotations)) {
        pushToast('Expected a WSI JSON export ({ "annotations": [...] }) or a bare annotations array', "error");
        return;
      }
      if (annotations.length === 0) {
        pushToast("That file has no annotations to import", "error");
        return;
      }

      const res = await importAnnotations(slideId, { annotations, created_by: `${annotatorName} (imported)` });
      setResult(res);
      if (res.imported > 0) {
        pushToast(`Imported ${res.imported} annotation${res.imported === 1 ? "" : "s"}`, "success");
        onImported();
      } else {
        pushToast("Nothing was imported -- see details below", "error");
      }
    } catch (e) {
      pushToast(e instanceof ApiError ? String(e.detail) : "Import failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
    >
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Import Annotations</h2>
          <button onClick={onClose}>
            <MaterialIcon name="close" />
          </button>
        </div>

        <p className="text-body-md text-on-surface-variant">
          Import a previously exported <span className="font-mono text-label-md">wsi_json</span> file (or a bare{" "}
          <span className="font-mono text-label-md">annotations</span> array). Annotations are matched to existing
          patches by their exact Level-0 origin, so{" "}
          <strong>tissue detection and "Generate Coords" must already be run</strong> with the same grid before
          importing. Diagnostic class labels are matched by name -- unrecognized labels are skipped, never
          invented. Re-importing the same file is safe: duplicates are detected and skipped.
        </p>

        <input
          type="file"
          accept="application/json,.json"
          disabled={busy}
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          className="text-body-md"
        />

        {busy && <div className="text-body-md text-on-surface-variant">Importing {fileName}...</div>}

        {result && (
          <div className="rounded bg-surface-container-low p-space-md flex flex-col gap-1.5 text-body-md">
            <ResultRow label="Total in file" value={result.total} />
            <ResultRow label="Imported" value={result.imported} tone="success" />
            <ResultRow label="Skipped -- no matching patch" value={result.skipped_no_matching_patch} tone={result.skipped_no_matching_patch ? "warn" : undefined} />
            <ResultRow label="Skipped -- unknown class label" value={result.skipped_unknown_class} tone={result.skipped_unknown_class ? "warn" : undefined} />
            <ResultRow label="Skipped -- already imported" value={result.skipped_duplicate} />
          </div>
        )}

        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ResultRow({ label, value, tone }: { label: string; value: number; tone?: "success" | "warn" }) {
  const color = tone === "success" ? "text-tertiary" : tone === "warn" ? "text-error" : "text-on-surface";
  return (
    <div className="flex items-center justify-between">
      <span className="text-on-surface-variant">{label}</span>
      <span className={`font-mono ${color}`}>{value}</span>
    </div>
  );
}
