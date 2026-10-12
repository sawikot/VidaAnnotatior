import { useMemo, useRef, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Modal } from "../../components/primitives";
import {
  ApiError,
  UploadAborted,
  analyseDatasetImport,
  cancelDatasetImport,
  commitDatasetImport,
  sendDatasetImportFiles,
  startDatasetImport,
  type ImportLabelChoice,
  type UploadItem,
} from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { DatasetImportPreview, DatasetImportResult, ProjectType } from "../../types/api";
import { formatBytes, mergeSelections, toUploadItem } from "../slides/uploadSelection";

type Phase = "pick" | "sending" | "checking" | "review" | "importing" | "done";

// One request carries at most this much, so a big folder is many short uploads rather than one long one.
const BATCH_BYTES = 200 * 1024 * 1024;
const BATCH_FILES = 300;

const JUNK = new Set([".ds_store", "thumbs.db", "desktop.ini"]);
const isJunk = (path: string) => {
  const parts = path.toLowerCase().split("/");
  return parts.includes("__macosx") || JUNK.has(parts[parts.length - 1]) || parts[parts.length - 1].startsWith("._");
};

function batches(items: UploadItem[]): UploadItem[][] {
  const out: UploadItem[][] = [];
  let current: UploadItem[] = [];
  let bytes = 0;
  for (const item of items) {
    if (current.length && (bytes + item.file.size > BATCH_BYTES || current.length >= BATCH_FILES)) {
      out.push(current);
      current = [];
      bytes = 0;
    }
    current.push(item);
    bytes += item.file.size;
  }
  if (current.length) out.push(current);
  return out;
}

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/**
 * Imports a dataset in one go: images (or slides) and their annotations, picked together as files, a
 * folder or .zip archives, in whatever layout a labelling tool or a public dataset uses. The files
 * are sent first and only looked at; what was found is shown -- which annotations go with which
 * images, and the labels to match to the project's classes -- and nothing is imported until that is
 * confirmed. Annotation files on their own go to the images the project already has.
 */
export function DatasetImportModal({
  open,
  onClose,
  projectId,
  projectType,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  projectId: number;
  projectType: ProjectType;
  /** Called once something was imported, so the caller can refresh. */
  onImported: () => void;
}) {
  const isImage = projectType === "image";
  const noun = isImage ? "image" : "slide";
  const pushToast = useUiStore((s) => s.pushToast);
  const [phase, setPhase] = useState<Phase>("pick");
  const [items, setItems] = useState<UploadItem[]>([]);
  const [progress, setProgress] = useState({ sent: 0, total: 0 });
  const [importId, setImportId] = useState<string | null>(null);
  const [preview, setPreview] = useState<DatasetImportPreview | null>(null);
  const [choices, setChoices] = useState<Record<string, ImportLabelChoice>>({});
  const [folderLabels, setFolderLabels] = useState(true);
  const [newNames, setNewNames] = useState<Record<string, string>>({}); // label -> the name of the class made of it
  const [sendNotes, setSendNotes] = useState<string[]>([]);
  const [result, setResult] = useState<DatasetImportResult | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const totalBytes = useMemo(() => items.reduce((n, i) => n + i.file.size, 0), [items]);
  const busy = phase === "sending" || phase === "checking" || phase === "importing";

  function reset() {
    setPhase("pick");
    setItems([]);
    setProgress({ sent: 0, total: 0 });
    setImportId(null);
    setPreview(null);
    setChoices({});
    setFolderLabels(true);
    setNewNames({});
    setSendNotes([]);
    setResult(null);
  }

  /** Leaves the dialog; an import that was looked at but not confirmed is dropped on the server too. */
  function close() {
    if (busy) return;
    if (importId && phase === "review") cancelDatasetImport(projectId, importId).catch(() => undefined);
    reset();
    onClose();
  }

  function pick(fileList: FileList | null) {
    if (!fileList?.length) return;
    const picked = Array.from(fileList)
      .map(toUploadItem)
      .filter((i) => !isJunk(i.path));
    setItems((cur) => mergeSelections(cur, picked));
  }

  async function check() {
    setPhase("sending");
    setProgress({ sent: 0, total: totalBytes });
    let id: string | null = null;
    try {
      id = (await startDatasetImport(projectId)).id;
      setImportId(id);
      const notes: string[] = [];
      let done = 0;
      for (const batch of batches(items)) {
        const size = batch.reduce((n, i) => n + i.file.size, 0);
        const sending = sendDatasetImportFiles(projectId, id, batch, {
          onProgress: (sent) => setProgress({ sent: done + Math.min(sent, size), total: totalBytes }),
        });
        abortRef.current = sending.abort;
        const answer = await sending.promise;
        notes.push(...answer.skipped.map((s) => `${s.name}: ${s.reason}`), ...answer.warnings);
        done += size;
        setProgress({ sent: done, total: totalBytes });
      }
      abortRef.current = null;
      setSendNotes(notes);
      setPhase("checking");
      const found = await analyseDatasetImport(projectId, id);
      setPreview(found);
      setFolderLabels(found.folder_labels);
      // A label with a class of its name goes to it; any other becomes a new class unless changed.
      setChoices(Object.fromEntries(found.labels.map((l) => [l.label, l.class_id ?? (l.label ? "new" : "unlabeled")])));
      setPhase("review");
    } catch (e) {
      abortRef.current = null;
      if (id) cancelDatasetImport(projectId, id).catch(() => undefined);
      setImportId(null);
      setPhase("pick");
      if (!(e instanceof UploadAborted)) pushToast(e instanceof ApiError || e instanceof Error ? e.message : "The files could not be sent", "error");
    }
  }

  async function confirm() {
    if (!importId || !preview) return;
    setPhase("importing");
    try {
      const done = await commitDatasetImport(projectId, importId, choices, folderLabels, newNames);
      setResult(done);
      setImportId(null);
      setPhase("done");
      onImported();
    } catch (e) {
      setPhase("review");
      pushToast(e instanceof ApiError ? e.message : "The import failed", "error");
    }
  }

  const nothing = !!preview && preview.new_images === 0 && preview.shapes === 0 && preview.image_labels === 0;
  const pct = progress.total ? Math.min(100, Math.round((progress.sent / progress.total) * 100)) : 0;

  return (
    <Modal open={open} onClose={close} widthClass="max-w-3xl">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Import {isImage ? "images" : "slides"} and annotations</h2>
          <button onClick={close} disabled={busy} aria-label="Close" className="disabled:opacity-30">
            <MaterialIcon name="close" />
          </button>
        </div>

        {(phase === "pick" || phase === "sending" || phase === "checking") && (
          <>
            <p className="text-body-md text-on-surface-variant">
              Pick files, a whole folder, or <strong>.zip</strong> archives. It is worked out which file is which and which annotation
              belongs to which {noun}; you see the result before anything is imported. Any of these works:
            </p>
            <ul className="text-body-md flex flex-col gap-1">
              <li>
                <strong>{isImage ? "Images" : "Slides"} and annotations together</strong> -- each annotation goes to its {noun}.
              </li>
              <li>
                <strong>Only {noun}s</strong> -- they are added without annotations.
              </li>
              <li>
                <strong>Only annotations</strong> -- for {noun}s added earlier: they are matched by name to the {noun}s already in the
                project.
              </li>
            </ul>
            <p className="text-body-sm text-on-surface-variant">
              Annotations read:{" "}
              {isImage
                ? "YOLO .txt, Pascal VOC, LabelMe, COCO, CVAT, Label Studio, mask images, GeoJSON, CSV tables -- and folders named after classes, or a table of file and label, for whole-image labels."
                : "a file per slide named like it -- a mask stored like a slide (a pyramidal TIFF whose pixel values are the classes), GeoJSON (QuPath), ASAP or Aperio XML, COCO, Cytomine, CSV, this app's JSON."}
            </p>
            <div className="flex flex-wrap items-center gap-space-sm">
              <Button icon="upload_file" disabled={busy} onClick={() => filesInput.current?.click()}>
                Choose files
              </Button>
              <Button icon="folder_open" disabled={busy} onClick={() => folderInput.current?.click()}>
                Choose folder
              </Button>
              {items.length > 0 && !busy && (
                <Button variant="ghost" onClick={() => setItems([])}>
                  Clear
                </Button>
              )}
              <input ref={filesInput} type="file" multiple hidden onChange={(e) => (pick(e.target.files), (e.target.value = ""))} />
              <input
                ref={folderInput}
                type="file"
                multiple
                hidden
                {...({ webkitdirectory: "" } as Record<string, string>)}
                onChange={(e) => (pick(e.target.files), (e.target.value = ""))}
              />
            </div>
            {items.length > 0 && (
              <div className="text-body-md">
                {count(items.length, "file")} chosen · {formatBytes(totalBytes)}
              </div>
            )}
            {phase !== "pick" && (
              <div className="flex flex-col gap-1" role="status">
                <div className="h-2 rounded-full bg-surface-container-high overflow-hidden">
                  <div className="h-full bg-primary transition-[width]" style={{ width: `${phase === "checking" ? 100 : pct}%` }} />
                </div>
                <div className="text-body-sm text-on-surface-variant">
                  {phase === "checking"
                    ? "Working out what the files are..."
                    : `Sending ${formatBytes(progress.sent)} of ${formatBytes(progress.total)}. Nothing is imported yet.`}
                </div>
              </div>
            )}
            <div className="flex justify-end gap-space-sm">
              {phase === "sending" ? (
                <Button onClick={() => abortRef.current?.()}>Cancel</Button>
              ) : (
                <Button variant="ghost" onClick={close} disabled={busy}>
                  Cancel
                </Button>
              )}
              <Button variant="primary" icon="search" disabled={busy || items.length === 0} onClick={check}>
                {busy ? "Checking..." : "Check the files"}
              </Button>
            </div>
          </>
        )}

        {(phase === "review" || phase === "importing") && preview && (
          <Review
            preview={preview}
            noun={noun}
            choices={choices}
            setChoice={(label, choice) => setChoices((c) => ({ ...c, [label]: choice }))}
            newNames={newNames}
            setNewName={(label, name) => setNewNames((n) => ({ ...n, [label]: name }))}
            folderLabels={folderLabels}
            setFolderLabels={setFolderLabels}
            sendNotes={sendNotes}
            disabled={phase === "importing"}
          />
        )}
        {(phase === "review" || phase === "importing") && preview && (
          <div className="flex justify-end gap-space-sm">
            <Button variant="ghost" onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" icon="download_done" disabled={busy || nothing} onClick={confirm}>
              {phase === "importing" ? "Importing..." : nothing ? "Nothing to import" : "Import"}
            </Button>
          </div>
        )}

        {phase === "done" && result && (
          <>
            <div className="flex items-start gap-space-sm">
              <MaterialIcon name="check_circle" className="text-emerald-700" />
              <div className="text-body-md">
                Imported {count(result.images_imported, noun)}, {count(result.annotations_imported, "annotation")}
                {result.image_labels_set > 0 && ` and ${count(result.image_labels_set, `${noun} label`)}`}.
                {result.classes_created.length > 0 && ` New classes: ${result.classes_created.join(", ")}.`}
              </div>
            </div>
            {Object.keys(result.left_out).length > 0 && (
              <Notes title="Annotations left out" lines={Object.entries(result.left_out).map(([why, n]) => `${n.toLocaleString()} -- ${why}`)} />
            )}
            {result.images_failed.length > 0 && (
              <Notes title={`${count(result.images_failed.length, noun)} could not be imported`} lines={result.images_failed.map((f) => `${f.name}: ${f.reason}`)} />
            )}
            <div className="flex justify-end gap-space-sm">
              <Button onClick={reset}>Import more</Button>
              <Button variant="primary" onClick={close}>
                Done
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

function Notes({ title, lines, more = 0 }: { title: string; lines: string[]; more?: number }) {
  return (
    <div className="rounded border border-outline-variant/60 p-space-sm">
      <div className="text-label-md text-on-surface-variant mb-1">{title}</div>
      <ul className="text-body-sm max-h-32 overflow-auto flex flex-col gap-0.5">
        {lines.map((line) => (
          <li key={line} className="break-all">
            {line}
          </li>
        ))}
        {more > 0 && <li className="text-on-surface-variant">...and {more.toLocaleString()} more</li>}
      </ul>
    </div>
  );
}

function Review({
  preview,
  noun,
  choices,
  setChoice,
  newNames,
  setNewName,
  folderLabels,
  setFolderLabels,
  sendNotes,
  disabled,
}: {
  preview: DatasetImportPreview;
  noun: string;
  choices: Record<string, ImportLabelChoice>;
  setChoice: (label: string, choice: ImportLabelChoice) => void;
  newNames: Record<string, string>;
  setNewName: (label: string, name: string) => void;
  folderLabels: boolean;
  setFolderLabels: (on: boolean) => void;
  sendNotes: string[];
  disabled: boolean;
}) {
  const p = preview;
  const facts = [
    [p.new_images, `new ${noun}${p.new_images === 1 ? "" : "s"}`],
    [p.shapes, p.shapes === 1 ? "annotation" : "annotations"],
    ...(p.image_labels ? [[p.image_labels, `${noun} label${p.image_labels === 1 ? "" : "s"}`] as [number, string]] : []),
    [p.images_with_annotations, `${noun}${p.images_with_annotations === 1 ? "" : "s"} annotated`],
  ] as [number, string][];
  const unreadable = Object.entries(p.unreadable);

  return (
    <div className="flex flex-col gap-space-md" data-testid="import-review">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-space-sm">
        {facts.map(([n, what]) => (
          <div key={what} className="rounded bg-surface-container-low p-space-sm">
            <div className="font-headline-md text-headline-md">{n.toLocaleString()}</div>
            <div className="text-body-sm text-on-surface-variant">{what}</div>
          </div>
        ))}
      </div>
      <ul className="text-body-sm text-on-surface-variant flex flex-col gap-0.5">
        {p.annotation_files.length > 0 && <li>Found: {p.annotation_files.map((f) => `${f.name} (${f.count.toLocaleString()})`).join(", ")}.</li>}
        {p.class_list && <li>Class names taken from {p.class_list}.</li>}
        {p.already_in_project > 0 && <li>{count(p.already_in_project, noun)} already in the project: not added again.</li>}
        {p.existing_images_annotated > 0 && <li>{count(p.existing_images_annotated, noun)} already in the project get annotations from these files.</li>}
        {p.new_images_without_annotations > 0 && p.images_with_annotations > 0 && (
          <li>{count(p.new_images_without_annotations, `new ${noun}`)} have no annotations; they are imported as they are.</li>
        )}
        {p.files_not_understood > 0 && <li>{count(p.files_not_understood, "other file")} ignored.</li>}
      </ul>

      {p.folder_labels && (
        <label className="flex items-start gap-space-sm text-body-md cursor-pointer">
          <input type="checkbox" className="w-4 h-4 mt-1" checked={folderLabels} disabled={disabled} onChange={(e) => setFolderLabels(e.target.checked)} />
          <span>
            Use the folder names as each {noun}'s label
            <span className="block text-body-sm text-on-surface-variant">
              The {noun}s sit in folders named like classes and nothing else says what they are. Unticked, they are imported without labels.
            </span>
          </span>
        </label>
      )}

      {p.labels.length > 0 && (
        <div>
          <div className="text-label-md text-on-surface-variant mb-1">Match the files' labels to the project's classes</div>
          <div className="max-h-64 overflow-auto rounded border border-outline-variant/60">
            <table className="w-full text-body-sm">
              <thead className="sticky top-0 bg-surface-container-low text-label-sm text-on-surface-variant">
                <tr>
                  <th className="text-left px-space-md py-space-sm font-medium">Label in the files</th>
                  <th className="text-right px-space-sm py-space-sm font-medium">Used</th>
                  <th className="text-left px-space-md py-space-sm font-medium w-64">Becomes (a new class can be named)</th>
                </tr>
              </thead>
              <tbody>
                {p.labels.map((l) => {
                  const choice = choices[l.label] ?? "skip";
                  const off = disabled || (!!l.images && !l.shapes && p.folder_labels && !folderLabels);
                  return (
                    <tr key={l.label} className="border-t border-outline-variant/40">
                      <td className="px-space-md py-1.5 font-mono text-label-md break-all">{l.label || <span className="italic font-sans">no label</span>}</td>
                      <td className="px-space-sm py-1.5 text-right font-mono text-label-md whitespace-nowrap">
                        {[l.shapes ? count(l.shapes, "shape") : "", l.images ? count(l.images, noun) : ""].filter(Boolean).join(" · ")}
                      </td>
                      <td className="px-space-md py-1.5">
                        <select
                          className="input !py-0.5"
                          aria-label={`What "${l.label || "no label"}" becomes`}
                          disabled={off}
                          value={String(choice)}
                          onChange={(e) => setChoice(l.label, /^\d+$/.test(e.target.value) ? Number(e.target.value) : (e.target.value as ImportLabelChoice))}
                        >
                          {p.classes.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name}
                            </option>
                          ))}
                          {l.label && !p.classes.some((c) => c.name.trim().toLowerCase() === l.label.trim().toLowerCase()) && (
                            <option value="new">New class...</option>
                          )}
                          {l.shapes > 0 && <option value="unlabeled">Import without a class</option>}
                          <option value="skip">Leave out</option>
                        </select>
                        {choice === "new" && (
                          <input
                            className="input !py-0.5 mt-1"
                            aria-label={`Name of the class made of "${l.label}"`}
                            placeholder="Class name"
                            maxLength={100}
                            disabled={off}
                            value={newNames[l.label] ?? l.label}
                            onChange={(e) => setNewName(l.label, e.target.value)}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {p.unmatched_count > 0 && (
        <Notes
          title={`${count(p.unmatched_count, "annotation file")} could not be used`}
          lines={p.unmatched.map((u) => `${u.file}: ${u.reason}`)}
          more={p.unmatched_count - p.unmatched.length}
        />
      )}
      {p.skipped_image_count > 0 && (
        <Notes
          title={`${count(p.skipped_image_count, noun)} cannot be imported`}
          lines={p.skipped_images.map((s) => `${s.name}: ${s.reason}`)}
          more={p.skipped_image_count - p.skipped_images.length}
        />
      )}
      {(unreadable.length > 0 || p.warnings.length > 0 || sendNotes.length > 0) && (
        <Notes title="Notes" lines={[...unreadable.map(([why, n]) => `${n.toLocaleString()} left out: ${why}`), ...p.warnings, ...sendNotes]} />
      )}
    </div>
  );
}
