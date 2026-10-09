import { useEffect, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Card, Modal } from "../../components/primitives";
import { ApiError, deleteModel, importModel, listImporters, listProjectModels, updateModel } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { AnnotationClass, Recipe, TrainedModel } from "../../types/api";
import { SettingField, defaultSettings, type SettingValue } from "../training/SettingField";

type ClassMap = Record<string, number | null>;

const METRIC_NAMES: Record<string, string> = { ap50: "AP50", accuracy: "accuracy", miou: "mean IoU" };

/**
 * The project's models -- those trained here and those added from elsewhere -- which is what the
 * annotation workspace offers under "AI suggestions". A model added as a file has classes of its own;
 * each is matched to a class of the project (or left out) here.
 */
export function ModelsSection({
  projectId,
  classes,
  canManage,
  trainerOnline,
  refreshKey,
}: {
  projectId: number;
  classes: AnnotationClass[];
  canManage: boolean;
  trainerOnline: boolean;
  /** Changes when a training run may have produced or removed a model. */
  refreshKey: unknown;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [models, setModels] = useState<TrainedModel[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [mapping, setMapping] = useState<TrainedModel | null>(null);

  const reload = () => listProjectModels(projectId).then(setModels).catch(() => setModels((m) => m ?? []));
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, refreshKey]);

  async function remove(model: TrainedModel) {
    const what = model.source === "import" ? "The model file is deleted." : "Its training run and files stay.";
    if (!window.confirm(`Remove "${model.name}" from this project? ${what} Annotations made from its suggestions are kept.`)) return;
    try {
      await deleteModel(model.id);
      await reload();
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : "Could not remove the model", "error");
    }
  }

  return (
    <Card className="p-space-lg flex flex-col gap-space-md" data-testid="models-section">
      <div className="flex items-start justify-between gap-space-md flex-wrap">
        <p className="text-body-sm text-on-surface-variant max-w-2xl">
          These are offered in the annotation workspace under AI suggestions. A model only proposes shapes; nothing becomes an annotation until
          a person accepts it.
        </p>
        {canManage && (
          <Button icon="upload_file" onClick={() => setAdding(true)} disabled={!trainerOnline} title={trainerOnline ? undefined : "The trainer must be running to open a model file"}>
            Add a model trained elsewhere
          </Button>
        )}
      </div>

      {!models ? (
        <span className="text-body-sm text-on-surface-variant">Loading...</span>
      ) : models.length === 0 ? (
        <span className="text-body-sm text-on-surface-variant">No models yet. A finished training run becomes one, or add a model file trained elsewhere.</span>
      ) : (
        <div className="flex flex-col divide-y divide-outline-variant/40">
          {models.map((m) => {
            const used = m.class_map ? Object.values(m.class_map).filter((v) => v !== null).length : m.classes.length;
            const metric = m.score?.metric ? (METRIC_NAMES[m.score.metric] ?? m.score.metric) : null;
            return (
              <div key={m.id} className="py-space-sm flex items-center justify-between gap-space-md flex-wrap">
                <div className="min-w-0">
                  <div className="flex items-center gap-space-sm">
                    <span className="font-headline-sm text-label-lg truncate">{m.name}</span>
                    <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm whitespace-nowrap">
                      {m.source === "import" ? "Added from elsewhere" : "Trained here"}
                    </span>
                  </div>
                  <div className="text-label-sm text-on-surface-variant">
                    <span className="capitalize">{m.task}</span> · {used} of {m.classes.length} class{m.classes.length === 1 ? "" : "es"} used
                    {metric && typeof m.score?.val === "number" && ` · validation ${metric} ${(m.score.val * 100).toFixed(1)}%`}
                    {metric && typeof m.score?.test === "number" && ` · test ${(m.score.test * 100).toFixed(1)}%`}
                  </div>
                  {m.class_map && used === 0 && (
                    <div className="flex items-center gap-1 text-label-sm text-error">
                      <MaterialIcon name="error" className="!text-[16px]" />
                      None of its classes is matched to a class of this project, so it cannot suggest anything.
                    </div>
                  )}
                </div>
                {canManage && (
                  <div className="flex items-center gap-space-sm">
                    {m.class_map && (
                      <Button icon="swap_horiz" onClick={() => setMapping(m)}>
                        Classes
                      </Button>
                    )}
                    <Button variant="ghost" icon="delete" onClick={() => void remove(m)}>
                      Remove
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <AddModelModal
        open={adding}
        projectId={projectId}
        onClose={() => setAdding(false)}
        onAdded={(model) => {
          setAdding(false);
          void reload();
          setMapping(model); // straight on to saying which class is which
        }}
      />
      {mapping && (
        <ClassMapModal
          model={mapping}
          classes={classes}
          onClose={() => setMapping(null)}
          onSaved={() => {
            setMapping(null);
            void reload();
          }}
        />
      )}
    </Card>
  );
}

function AddModelModal({ open, projectId, onClose, onAdded }: { open: boolean; projectId: number; onClose: () => void; onAdded: (m: TrainedModel) => void }) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [importers, setImporters] = useState<Recipe[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [importerId, setImporterId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, SettingValue>>({});
  const [classNames, setClassNames] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) listImporters().then(setImporters).catch(() => setImporters([]));
  }, [open]);

  const extension = file ? (/\.[^.]+$/.exec(file.name)?.[0].toLowerCase() ?? "") : "";
  const fitting = importers.filter((i) => i.import?.extensions.includes(extension));
  const importer = fitting.find((i) => i.id === importerId) ?? fitting[0] ?? null;
  const accepted = [...new Set(importers.flatMap((i) => i.import?.extensions ?? []))];

  useEffect(() => {
    if (importer) setValues(defaultSettings(importer.settings));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [importer?.id]);

  function close() {
    if (busy) return;
    setFile(null);
    setName("");
    setClassNames("");
    setError(null);
    onClose();
  }

  async function submit() {
    if (!file || !importer) return;
    setBusy(true);
    setError(null);
    try {
      const model = await importModel(projectId, file, importer.id, { name, settings: values, classNames });
      pushToast(`Added "${model.name}"`, "success");
      setFile(null);
      setName("");
      setClassNames("");
      onAdded(model);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The model could not be added");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={close}>
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Add a Model Trained Elsewhere</h2>
          <button onClick={close} aria-label="Close">
            <MaterialIcon name="close" />
          </button>
        </div>
        <p className="text-body-md text-on-surface-variant">
          The file is opened once to check that it can be used and to read its classes. Supported: {importers.map((i) => i.name).join("; ") || "..."}.
        </p>

        <label className="flex flex-col gap-1">
          <span className="text-label-md text-on-surface-variant">Model file</span>
          <input
            type="file"
            accept={accepted.join(",")}
            disabled={busy}
            className="text-body-md"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />
        </label>
        {file && fitting.length === 0 && (
          <div className="rounded bg-error-container text-on-error-container p-space-sm text-body-md">
            &ldquo;{extension || file.name}&rdquo; files are not supported. Use one of: {accepted.join(", ")}.
          </div>
        )}

        {importer && (
          <>
            {fitting.length > 1 && (
              <label className="flex flex-col gap-1">
                <span className="text-label-md text-on-surface-variant">Kind of model</span>
                <select className="input" value={importer.id} onChange={(e) => setImporterId(e.target.value)}>
                  {fitting.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <p className="text-body-sm text-on-surface-variant">{importer.description}</p>
            <label className="flex flex-col gap-1">
              <span className="text-label-md text-on-surface-variant">Name</span>
              <input className="input" value={name} placeholder={file?.name.replace(/\.[^.]+$/, "")} onChange={(e) => setName(e.target.value)} maxLength={200} />
            </label>
            <div className="grid sm:grid-cols-2 gap-space-md">
              {importer.settings
                .filter((s) => advanced || !s.advanced)
                .map((s) => (
                  <SettingField key={s.key} spec={s} value={values[s.key]} disabled={busy} onChange={(v) => setValues((prev) => ({ ...prev, [s.key]: v }))} />
                ))}
            </div>
            {importer.settings.some((s) => s.advanced) && (
              <button className="self-start text-primary text-label-md hover:underline" onClick={() => setAdvanced((v) => !v)}>
                {advanced ? "Hide advanced settings" : "Show advanced settings"}
              </button>
            )}
            <label className="flex flex-col gap-1">
              <span className="text-label-md text-on-surface-variant">Class names (only if the file does not carry them)</span>
              <textarea
                className="input min-h-[5rem] font-mono"
                value={classNames}
                disabled={busy}
                placeholder={"One per line, in the model's own order"}
                onChange={(e) => setClassNames(e.target.value)}
              />
            </label>
          </>
        )}

        {error && (
          <div className="rounded bg-error-container text-on-error-container p-space-sm text-body-md whitespace-pre-wrap" role="alert">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-space-sm">
          <Button variant="ghost" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon="upload" onClick={() => void submit()} disabled={!file || !importer || busy}>
            {busy ? "Opening the model..." : "Add model"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ClassMapModal({ model, classes, onClose, onSaved }: { model: TrainedModel; classes: AnnotationClass[]; onClose: () => void; onSaved: () => void }) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [map, setMap] = useState<ClassMap>(model.class_map ?? {});
  const [busy, setBusy] = useState(false);
  const used = Object.values(map).filter((v) => v !== null).length;

  async function save() {
    setBusy(true);
    try {
      await updateModel(model.id, { class_map: map });
      onSaved();
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : "Could not save the classes", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose}>
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">Classes of &ldquo;{model.name}&rdquo;</h2>
          <button onClick={onClose} aria-label="Close">
            <MaterialIcon name="close" />
          </button>
        </div>
        <p className="text-body-md text-on-surface-variant">
          Say which class of this project each of the model&apos;s classes is. What it finds of a class that is left out is not suggested.
        </p>
        {classes.length === 0 && <div className="rounded bg-amber-500/10 p-space-sm text-body-sm">This project has no classes yet; add them in the project settings first.</div>}
        <div className="rounded border border-outline-variant divide-y divide-outline-variant">
          {model.classes.map((c) => {
            const place = String(c.index ?? 0);
            const target = map[place] ?? null;
            return (
              <div key={place} className="flex items-center gap-space-md px-space-md py-1.5">
                <span className="flex-1 truncate" title={c.name}>
                  {c.name}
                </span>
                <MaterialIcon name="arrow_forward" className="!text-[16px] text-on-surface-variant" />
                <select
                  className={`input !w-56 !py-0.5 ${target === null ? "text-on-surface-variant" : ""}`}
                  aria-label={`Project class for ${c.name}`}
                  value={target ?? ""}
                  onChange={(e) => setMap((m) => ({ ...m, [place]: e.target.value ? Number(e.target.value) : null }))}
                >
                  <option value="">Leave out</option>
                  {classes.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
        {used === 0 && <p className="text-body-sm text-error">With every class left out, the model cannot suggest anything.</p>}
        <div className="flex justify-end gap-space-sm">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={busy}>
            {busy ? "Saving..." : "Save"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
