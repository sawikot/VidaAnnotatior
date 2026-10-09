import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { ModelsSection } from "../features/models/ModelsSection";
import { CompareRuns, MAX_COMPARED, METRIC_NAMES } from "../features/training/CompareRuns";
import { MetricChart, SERIES_COLORS, type Series } from "../features/training/MetricChart";
import { SettingField, defaultSettings, type SettingValue } from "../features/training/SettingField";
import {
  ApiError,
  deleteTrainingRun,
  getProject,
  getTrainerStatus,
  getTrainingLog,
  getTrainingReadiness,
  getTrainingRun,
  listRecipes,
  listTrainingRuns,
  startDownload,
  startTrainingRun,
  stopTrainingRun,
  trainingModelUrl,
} from "../services/api";
import { useCan } from "../stores/authStore";
import { useUiStore } from "../stores/uiStore";
import type { ProjectDetail, Recipe, TrainerStatus, TrainingReadiness, TrainingRun } from "../types/api";

const ACTIVE = ["queued", "preparing", "running"];
const STATUS: Record<string, { label: string; tone: string }> = {
  queued: { label: "Waiting for the trainer", tone: "bg-slate-100 text-slate-700" },
  preparing: { label: "Cutting the dataset", tone: "bg-sky-100 text-sky-900" },
  running: { label: "Training", tone: "bg-sky-100 text-sky-900" },
  done: { label: "Done", tone: "bg-emerald-100 text-emerald-900" },
  failed: { label: "Failed", tone: "bg-red-100 text-red-900" },
  stopped: { label: "Stopped", tone: "bg-amber-100 text-amber-900" },
};
// What each kind of model does, in the order offered; and the recipe to start with in each.
const TASKS: { id: Recipe["task"]; title: string; body: string }[] = [
  { id: "detection", title: "Find objects", body: "A box around each one: cells, parasites, mitoses" },
  { id: "classification", title: "Classify patches", body: "One class for the whole patch, from Patch Labels" },
  { id: "segmentation", title: "Outline areas", body: "Regions of tissue, or each object's own outline" },
];
const START_WITH = ["detection_fasterrcnn", "classification_resnet", "segmentation_deeplab"];
const startFirst = (list: Recipe[]) => [...list].sort((a, b) => Number(START_WITH.includes(b.id)) - Number(START_WITH.includes(a.id)));

const SET_LABELS: Record<string, string> = { train: "Train", val: "Validation", test: "Test" };
// Names of the numbers a recipe reports, as shown on the curves.
const METRIC_LABELS: Record<string, string> = {
  train_loss: "Training loss",
  val_loss: "Validation loss",
  val_ap50: "AP50",
  val_precision: "Precision",
  val_recall: "Recall",
  val_accuracy: "Accuracy",
  val_miou: "Mean IoU",
  val_pixel_accuracy: "Pixel accuracy",
};
const METRIC_HELP: Record<string, string> = {
  ap50: "AP50 is the share of objects found with a box overlapping the real one by at least half, averaged over how sure the model is. Precision: how many of its detections are right. Recall: how many of the real objects it finds.",
  accuracy: "Accuracy is the share of images given their right class. Per class: of the images of that class, the share recognised as it.",
  miou: "IoU compares the area the model marks as a class with the area drawn as it: what they share, divided by what either covers. Mean IoU averages that over the classes; the background is left out.",
};

const percent = (v: number | undefined) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "--");
const when = (iso: string | null) => (iso ? new Date(`${iso}Z`).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

/** The project's model training: is the data ready, start a run, and watch the runs. */
export function ProjectTrainingPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const canManage = useCan().manage;
  const pushToast = useUiStore((s) => s.pushToast);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [trainer, setTrainer] = useState<TrainerStatus | null>(null);
  const [readiness, setReadiness] = useState<TrainingReadiness | null>(null);
  const [recipes, setRecipes] = useState<Recipe[] | null>(null);
  const [recipeId, setRecipeId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, SettingValue>>({});
  const [advanced, setAdvanced] = useState(false);
  const [starting, setStarting] = useState(false);
  const [runs, setRuns] = useState<TrainingRun[] | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [open, setOpen] = useState<TrainingRun | null>(null);
  const [log, setLog] = useState("");
  const [compared, setCompared] = useState<number[]>([]); // finished runs ticked to compare

  const isImage = project?.project_type === "image";
  const recipe = recipes?.find((r) => r.id === recipeId) ?? null;
  const openRef = useRef(openId);
  openRef.current = openId;

  useEffect(() => {
    getProject(pid).then(setProject).catch(() => undefined);
    listRecipes()
      .then((list) => {
        setRecipes(list);
        setRecipeId((list.find((r) => r.id === START_WITH[0]) ?? list[0])?.id ?? null);
      })
      .catch(() => setRecipes([]));
  }, [pid]);

  // What there is to learn from depends on the kind of model: drawn shapes, or one class per patch.
  const task = recipe?.task ?? null;
  useEffect(() => {
    if (!task) return;
    let stale = false;
    setReadiness(null);
    getTrainingReadiness(pid, task).then((r) => !stale && setReadiness(r)).catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [pid, task]);

  // A newly picked recipe starts from its own defaults.
  useEffect(() => {
    if (recipe) setValues(defaultSettings(recipe.settings));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipeId]);

  // Runs change by themselves (the trainer works on them), so the page keeps asking.
  useEffect(() => {
    let stale = false;
    const refresh = () => {
      getTrainerStatus().then((s) => !stale && setTrainer(s)).catch(() => undefined);
      listTrainingRuns(pid)
        .then((list) => {
          if (stale) return;
          setRuns(list);
          if (openRef.current === null && list.length) setOpenId(list[0].id);
        })
        .catch(() => !stale && setRuns((r) => r ?? []));
      const id = openRef.current;
      if (id !== null) {
        getTrainingRun(id).then((r) => !stale && openRef.current === id && setOpen(r)).catch(() => undefined);
        getTrainingLog(id).then((t) => !stale && openRef.current === id && setLog(t)).catch(() => undefined);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => {
      stale = true;
      window.clearInterval(timer);
    };
  }, [pid, openId]);

  async function start() {
    if (!recipe) return;
    setStarting(true);
    try {
      const run = await startTrainingRun(pid, recipe.id, values);
      setOpen(null);
      setLog("");
      setOpenId(run.id);
      pushToast(trainer?.online ? "Training queued; the trainer picks it up in a few seconds." : "Training queued. It starts when the trainer is running.", "success");
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : "Could not start the training", "error");
    } finally {
      setStarting(false);
    }
  }

  async function act(action: () => Promise<unknown>, failed: string) {
    try {
      await action();
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : failed, "error");
    }
  }

  const charts = useMemo(() => chartsOf(open?.metrics ?? []), [open?.metrics]);

  if (!project) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  const problems = readiness?.problems ?? [];
  const gpu = trainer?.hardware?.gpus?.[0];

  return (
    <div className="max-w-6xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div className="bg-surface-container-low rounded-xl p-space-lg">
        <div className="text-label-sm text-on-surface-variant mb-1">
          <Link to={`/projects/${pid}`} className="hover:underline text-primary">
            {project.name}
          </Link>{" "}
          / Training
        </div>
        <h1 className="font-headline-lg text-headline-lg mb-space-sm">Train a Model</h1>
        <p className="text-body-md text-on-surface-variant max-w-3xl">
          Teach a model from this project&apos;s annotations. It learns from the training set, is checked on the validation set while it
          learns, and gets its final score on the test set.
        </p>
        <div className="mt-space-md flex items-center gap-space-sm text-body-sm" role="status">
          {trainer === null ? (
            <span className="text-on-surface-variant">Looking for the trainer...</span>
          ) : trainer.online ? (
            <>
              <MaterialIcon name="check_circle" className="!text-[18px] text-emerald-700" />
              <span>
                Trainer running ·{" "}
                {gpu ? `${gpu.name}, ${(gpu.memory_mb / 1024).toFixed(0)} GB` : "no graphics card found, so training runs on the CPU and will be slow"}
              </span>
            </>
          ) : (
            <>
              <MaterialIcon name="error" className="!text-[18px] text-amber-700" />
              <span>The trainer is not running. Runs you start wait until it is; ask your administrator to start it.</span>
            </>
          )}
        </div>
      </div>

      {/* 1. Data */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-md">1. Data</h2>
        <Card className="p-space-lg flex flex-col gap-space-md">
          {!readiness ? (
            <span className="text-body-sm text-on-surface-variant">Counting annotations...</span>
          ) : (
            <>
              <div className="overflow-auto">
                <table className="w-full text-body-sm">
                  <thead className="text-label-sm text-on-surface-variant">
                    <tr>
                      <th className="text-left py-1 font-medium">Set</th>
                      <th className="text-right py-1 pl-space-md font-medium whitespace-nowrap">{isImage ? "Images" : "Slides"}</th>
                      {!isImage && <th className="text-right py-1 pl-space-md font-medium whitespace-nowrap">Annotated patches</th>}
                      <th className="text-right py-1 pl-space-md font-medium whitespace-nowrap">{readiness.task === "classification" ? "With a class" : "Objects"}</th>
                      {readiness.classes.map((c) => (
                        <th key={c.id} className="text-right py-1 pl-space-md font-medium whitespace-nowrap" title={c.name}>
                          {c.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(readiness.sets).map(([name, set]) => (
                      <tr key={name} className="border-t border-outline-variant/40">
                        <td className="py-1.5">{SET_LABELS[name] ?? name}</td>
                        <td className="py-1.5 pl-space-md text-right font-mono">{set.slides}</td>
                        {!isImage && <td className="py-1.5 pl-space-md text-right font-mono">{set.images.toLocaleString()}</td>}
                        <td className="py-1.5 pl-space-md text-right font-mono">{set.objects.toLocaleString()}</td>
                        {readiness.classes.map((c) => (
                          <td key={c.id} className="py-1.5 pl-space-md text-right font-mono">
                            {(set.per_class[c.id] ?? 0).toLocaleString()}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {problems.map((p) => (
                <p key={p} className="flex items-start gap-1.5 text-body-sm text-error" role="alert">
                  <MaterialIcon name="error" className="!text-[18px] shrink-0" />
                  <span>
                    {p}{" "}
                    {readiness.split_mode === "off" && (
                      <Link to={`/projects/${pid}/settings`} className="text-primary hover:underline">
                        Open the project settings
                      </Link>
                    )}
                  </span>
                </p>
              ))}
              {readiness.warnings.map((w) => (
                <p key={w} className="flex items-start gap-1.5 text-body-sm text-on-surface-variant">
                  <MaterialIcon name="warning" className="!text-[18px] text-amber-600 shrink-0" />
                  {w}
                </p>
              ))}
              <p className="text-label-sm text-on-surface-variant">
                {readiness.task === "classification"
                  ? `A ${isImage ? "image" : "patch"} counts with its label, or else the drawn class covering most of it; "Mixed" and "Artifact" are left out.`
                  : readiness.task === "segmentation"
                    ? "Only shapes with a class count, and everything not drawn is taken as background: annotate what you train on completely."
                    : "Only shapes with a class count; points and lines are left out of detection."}{" "}
                {isImage ? "Images" : "Patches"} flagged
                &ldquo;Exclude from training&rdquo; are never used.
              </p>
            </>
          )}
        </Card>
      </section>

      {/* 2. Model */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-md">2. Model</h2>
        {recipes && recipes.length === 0 ? (
          <Card className="p-space-lg text-body-sm text-on-surface-variant">No model recipes are installed.</Card>
        ) : (
          <Card className="p-space-lg flex flex-col gap-space-md">
            <div role="tablist" aria-label="What the model should do" className="grid sm:grid-cols-3 gap-space-sm">
              {TASKS.map((t) => {
                const mine = (recipes ?? []).filter((r) => r.task === t.id);
                const on = task === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={on}
                    disabled={mine.length === 0}
                    onClick={() => !on && setRecipeId(startFirst(mine)[0].id)}
                    className={`text-left px-space-md py-space-sm rounded-xl disabled:opacity-40 ${on ? "bg-primary text-on-primary" : "bg-surface-container-high hover:bg-surface-container-highest"}`}
                  >
                    <span className="flex items-center justify-between gap-space-sm font-headline-sm text-headline-sm">
                      {t.title}
                      <span className={`text-label-sm font-mono ${on ? "" : "text-on-surface-variant"}`}>{mine.length}</span>
                    </span>
                    <span className={`block text-body-sm ${on ? "opacity-90" : "text-on-surface-variant"}`}>{t.body}</span>
                  </button>
                );
              })}
            </div>
            <div role="radiogroup" aria-label="Model" className="grid sm:grid-cols-2 gap-space-sm max-h-[26rem] overflow-y-auto p-1 -m-1">
              {startFirst((recipes ?? []).filter((r) => r.task === task)).map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="radio"
                  aria-checked={r.id === recipeId}
                  onClick={() => setRecipeId(r.id)}
                  className={`text-left p-space-md rounded-xl bg-surface-container-lowest shadow-sm ${r.id === recipeId ? "ring-2 ring-primary" : "hover:shadow-md"}`}
                >
                  <span className="flex items-center justify-between gap-space-sm">
                    <span className="font-headline-sm text-headline-sm">{r.name}</span>
                    {START_WITH.includes(r.id) ? (
                      <span className="px-space-sm py-0.5 rounded-full bg-emerald-100 text-emerald-900 text-label-sm shrink-0">Start here</span>
                    ) : (
                      !r.builtin && <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm shrink-0">Yours</span>
                    )}
                  </span>
                  <span className="block text-body-sm text-on-surface-variant">{r.description}</span>
                </button>
              ))}
            </div>
            {recipe && (
              <>
                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-space-md">
                  {recipe.settings
                    .filter((s) => advanced || !s.advanced)
                    .map((s) => (
                      <SettingField key={s.key} spec={s} value={values[s.key]} disabled={!canManage} onChange={(v) => setValues((prev) => ({ ...prev, [s.key]: v }))} />
                    ))}
                </div>
                {recipe.settings.some((s) => s.advanced) && (
                  <button className="self-start text-primary text-label-md hover:underline" onClick={() => setAdvanced((v) => !v)}>
                    {advanced ? "Hide advanced settings" : "Show advanced settings"}
                  </button>
                )}
                <div className="flex items-center gap-space-md flex-wrap">
                  <Button
                    variant="primary"
                    icon="model_training"
                    onClick={() => void start()}
                    disabled={!canManage || starting || !readiness || problems.length > 0}
                    title={!canManage ? "Only project managers and administrators can start training" : (problems[0] ?? undefined)}
                  >
                    {starting ? "Starting..." : "Start training"}
                  </Button>
                  {!canManage && <span className="text-body-sm text-on-surface-variant">Only project managers and administrators can start training.</span>}
                  {canManage && (
                    <Link to="/recipes" className="text-primary text-label-md hover:underline ml-auto">
                      Model recipes: see or change the code
                    </Link>
                  )}
                </div>
              </>
            )}
          </Card>
        )}
      </section>

      {/* 3. Runs */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-md">3. Training Runs</h2>
        {!runs ? (
          <Card className="p-space-lg text-body-sm text-on-surface-variant">Loading...</Card>
        ) : runs.length === 0 ? (
          <Card className="p-space-lg text-body-sm text-on-surface-variant">No training runs yet.</Card>
        ) : (
          <div className="grid lg:grid-cols-[18rem_1fr] gap-space-md items-start">
            <Card className="p-0 overflow-hidden">
              {runs.map((r) => (
                <div key={r.id} className="relative border-b border-outline-variant/40 last:border-b-0">
                {r.status === "done" && (
                  <input
                    type="checkbox"
                    className="absolute right-2 bottom-2 w-4 h-4"
                    title="Compare this run with others"
                    aria-label={`Compare run #${r.id}`}
                    checked={compared.includes(r.id)}
                    disabled={!compared.includes(r.id) && compared.length >= MAX_COMPARED}
                    onChange={(e) => setCompared((c) => (e.target.checked ? [...c, r.id].sort((x, y) => x - y) : c.filter((id) => id !== r.id)))}
                  />
                )}
                <button
                  onClick={() => {
                    if (r.id === openId) return;
                    setOpen(null);
                    setLog("");
                    setOpenId(r.id);
                  }}
                  aria-current={r.id === openId}
                  className={`w-full text-left px-space-md py-space-sm ${
                    r.id === openId ? "bg-primary-container/40" : "hover:bg-surface-container-low"
                  }`}
                >
                  <span className="flex items-center justify-between gap-space-sm">
                    <span className="font-headline-sm text-label-lg">Run #{r.id}</span>
                    <StatusBadge status={r.status} />
                  </span>
                  <span className="block text-label-sm text-on-surface-variant truncate">
                    {r.recipe_name} · {when(r.created_at)}
                  </span>
                  {r.status === "running" && r.epochs > 0 && <Progress done={r.epoch} total={r.epochs} />}
                </button>
                </div>
              ))}
            </Card>

            {open && open.id === openId ? (
              <Card className="p-space-lg flex flex-col gap-space-md min-w-0">
                <div className="flex items-start justify-between gap-space-md flex-wrap">
                  <div>
                    <div className="flex items-center gap-space-sm">
                      <h3 className="font-headline-md text-headline-md">Run #{open.id}</h3>
                      <StatusBadge status={open.status} />
                    </div>
                    <div className="text-body-sm text-on-surface-variant">
                      {open.recipe_name} · started by {open.created_by ?? "someone"} · {when(open.created_at)}
                      {open.device && ` · ${open.device}`}
                    </div>
                  </div>
                  <div className="flex items-center gap-space-sm">
                    {ACTIVE.includes(open.status) && canManage && (
                      <Button icon="stop_circle" disabled={open.stop_requested} onClick={() => void act(() => stopTrainingRun(open.id).then(setOpen), "Could not stop the run")}>
                        {open.stop_requested ? "Stopping..." : "Stop"}
                      </Button>
                    )}
                    {open.has_model && (
                      <Button icon="download" onClick={() => startDownload(trainingModelUrl(open.id))}>
                        Download model
                      </Button>
                    )}
                    {!["preparing", "running"].includes(open.status) && canManage && (
                      <Button
                        variant="ghost"
                        icon="delete"
                        onClick={() => {
                          if (!window.confirm(`Delete run #${open.id}, its log and its trained model?`)) return;
                          void act(async () => {
                            await deleteTrainingRun(open.id);
                            setOpen(null);
                            setOpenId(null);
                          }, "Could not delete the run");
                        }}
                      >
                        Delete
                      </Button>
                    )}
                  </div>
                </div>

                {open.status === "queued" && (
                  <p className="text-body-sm text-on-surface-variant">
                    {trainer?.online ? "Waiting for the trainer to take it." : "Waiting: the trainer is not running."}
                  </p>
                )}
                {open.status === "preparing" && (
                  <p className="text-body-sm text-on-surface-variant">Cutting the patch images for the dataset. Large projects take a few minutes.</p>
                )}
                {open.status === "running" && open.epochs > 0 && (
                  <div>
                    <div className="text-body-sm text-on-surface-variant mb-1">
                      Epoch {open.epoch} of {open.epochs}
                    </div>
                    <Progress done={open.epoch} total={open.epochs} />
                  </div>
                )}
                {open.error && (
                  <pre className="rounded bg-error-container text-on-error-container p-space-sm text-label-sm font-mono whitespace-pre-wrap overflow-auto max-h-56" role="alert">
                    {open.error}
                  </pre>
                )}

                {open.result && <Scores result={open.result} />}

                <div className="grid md:grid-cols-2 gap-space-md">
                  {charts.map((c) => (
                    <MetricChart key={c.title} title={c.title} rows={open.metrics ?? []} series={c.series} yMax={c.yMax} format={c.format} />
                  ))}
                </div>

                <div className="grid sm:grid-cols-2 gap-space-md text-body-sm">
                  <div>
                    <div className="text-label-md text-on-surface-variant mb-1">Settings</div>
                    {Object.entries(open.settings).map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-space-md">
                        <span className="text-on-surface-variant">{k.replace(/_/g, " ")}</span>
                        <span className="font-mono">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                  {open.dataset && (
                    <div>
                      <div className="text-label-md text-on-surface-variant mb-1">Trained on</div>
                      {Object.entries(open.dataset.sets).map(([name, set]) => (
                        <div key={name} className="flex justify-between gap-space-md">
                          <span className="text-on-surface-variant truncate" title={set.slides.join(", ")}>
                            {SET_LABELS[name] ?? name} · {set.slides.length} {isImage ? "images" : "slides"}
                          </span>
                          <span className="font-mono whitespace-nowrap">
                            {set.images.toLocaleString()} {isImage ? "" : "patches · "}
                            {set.objects.toLocaleString()} objects
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <details open={ACTIVE.includes(open.status) || open.status === "failed"}>
                  <summary className="cursor-pointer text-label-md text-on-surface-variant">Log</summary>
                  <pre className="mt-space-sm bg-[#0f172a] text-slate-300 rounded p-space-md text-label-sm font-mono overflow-auto max-h-72 whitespace-pre-wrap">
                    {log || "Nothing printed yet."}
                  </pre>
                </details>
              </Card>
            ) : (
              <Card className="p-space-lg text-body-sm text-on-surface-variant">{openId === null ? "Choose a run." : "Loading the run..."}</Card>
            )}
          </div>
        )}
      </section>

      {/* Comparing runs */}
      {runs && runs.filter((r) => r.status === "done").length >= 2 && (
        <section>
          <h2 className="font-headline-md text-headline-md mb-space-md">Compare Runs</h2>
          <Card className="p-space-lg">
            {compared.filter((id) => runs.some((r) => r.id === id && r.status === "done")).length >= 2 ? (
              <CompareRuns runs={runs.filter((r) => compared.includes(r.id) && r.status === "done").sort((x, y) => x.id - y.id)} />
            ) : (
              <span className="text-body-sm text-on-surface-variant">Tick the box on two to {MAX_COMPARED} finished runs in the list above to see them side by side.</span>
            )}
          </Card>
        </section>
      )}

      {/* 4. Models */}
      <section>
        <h2 className="font-headline-md text-headline-md mb-space-md">4. Models</h2>
        <ModelsSection
          projectId={pid}
          classes={project.active_config?.annotation_classes ?? []}
          canManage={canManage}
          trainerOnline={!!trainer?.online}
          refreshKey={(runs ?? []).map((r) => `${r.id}:${r.status}`).join(",")}
        />
      </section>
    </div>
  );
}

/** Losses on one chart, scores (0-1) on another: two measures never share an axis. */
function chartsOf(metrics: Record<string, number>[]) {
  const keys = [...new Set(metrics.flatMap((m) => Object.keys(m)))].filter((k) => k !== "epoch" && k !== "epochs");
  const series = (list: string[]): Series[] =>
    list.map((key, i) => ({ key, label: METRIC_LABELS[key] ?? key.replace(/_/g, " "), color: SERIES_COLORS[i % SERIES_COLORS.length] }));
  const losses = keys.filter((k) => k.endsWith("loss")).slice(0, 3);
  const scores = keys.filter((k) => !k.endsWith("loss")).slice(0, 3);
  return [
    { title: "Loss (lower is better)", series: series(losses.length ? losses : ["train_loss"]), yMax: undefined, format: (v: number) => v.toFixed(3) },
    { title: "Validation scores (higher is better)", series: series(scores.length ? scores : ["val_ap50"]), yMax: 1, format: (v: number) => `${Math.round(v * 100)}%` },
  ];
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "bg-slate-100 text-slate-700" };
  return <span className={`px-space-sm py-0.5 rounded-full text-label-sm whitespace-nowrap ${s.tone}`}>{s.label}</span>;
}

function Progress({ done, total }: { done: number; total: number }) {
  return (
    <div className="h-1.5 rounded-full bg-surface-container-high overflow-hidden mt-1" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
      <div className="h-full bg-primary" style={{ width: `${Math.min(100, (done / total) * 100)}%` }} />
    </div>
  );
}

function Scores({ result }: { result: NonNullable<TrainingRun["result"]> }) {
  const metric = (result.primary_metric ?? "ap50") as "ap50" | "accuracy" | "miou";
  const name = METRIC_NAMES[metric] ?? metric;
  const sets = (["val", "test"] as const).filter((set) => result[set]);
  const classes = [...new Set(sets.flatMap((set) => Object.keys(result[set]?.per_class ?? {})))];
  const also = (set: "val" | "test") =>
    metric === "ap50"
      ? `precision ${percent(result[set]?.precision)} · recall ${percent(result[set]?.recall)}`
      : metric === "miou"
        ? `pixel accuracy ${percent(result[set]?.pixel_accuracy)}`
        : null;
  return (
    <div className="rounded-xl bg-surface-container-low p-space-md flex flex-col gap-space-sm">
      <div className="flex items-baseline gap-space-lg flex-wrap">
        {sets.map((set) => (
          <div key={set}>
            <div className="text-label-sm text-on-surface-variant">{set === "test" ? `Test ${name} (final score)` : `Validation ${name}`}</div>
            <div className="font-headline-lg text-headline-lg">{percent(result[set]?.[metric])}</div>
            {also(set) && <div className="text-label-sm text-on-surface-variant">{also(set)}</div>}
          </div>
        ))}
        {result.best_epoch ? <div className="text-body-sm text-on-surface-variant">The kept model is from epoch {result.best_epoch}, the best on validation.</div> : null}
      </div>
      {classes.length > 1 && (
        <table className="text-body-sm self-start">
          <thead className="text-label-sm text-on-surface-variant">
            <tr>
              <th className="text-left pr-space-lg font-medium">{metric === "accuracy" ? "Recognised, per class" : metric === "miou" ? "IoU per class" : "AP50 per class"}</th>
              {sets.map((set) => (
                <th key={set} className="text-right pl-space-md font-medium">
                  {SET_LABELS[set]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {classes.map((c) => (
              <tr key={c}>
                <td className="pr-space-lg">{c}</td>
                {sets.map((set) => (
                  <td key={set} className="text-right pl-space-md font-mono">
                    {percent(result[set]?.per_class?.[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-label-sm text-on-surface-variant">{METRIC_HELP[metric] ?? ""}</p>
    </div>
  );
}
