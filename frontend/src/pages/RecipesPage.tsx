import { useEffect, useRef, useState } from "react";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, Modal } from "../components/primitives";
import { formatBytes } from "../features/slides/uploadSelection";
import {
  ApiError,
  checkRecipe,
  createRecipe,
  deleteRecipe,
  deleteRecipeFile,
  getRecipe,
  getRecipeFile,
  getRecipeFileVersion,
  getTrainerStatus,
  listAllRecipes,
  recipeDownloadUrl,
  removeRecipePackages,
  saveRecipeFile,
  startDownload,
  uploadRecipe,
} from "../services/api";
import { useCan } from "../stores/authStore";
import { useUiStore } from "../stores/uiStore";
import type { RecipeDetail } from "../types/api";

const CHECK: Record<string, { label: string; tone: string; icon: string }> = {
  passed: { label: "Check passed", tone: "bg-emerald-100 text-emerald-900", icon: "check_circle" },
  failed: { label: "Check failed", tone: "bg-red-100 text-red-900", icon: "error" },
  queued: { label: "Waiting for the trainer", tone: "bg-sky-100 text-sky-900", icon: "hourglass_top" },
  running: { label: "Checking...", tone: "bg-sky-100 text-sky-900", icon: "hourglass_top" },
  stale: { label: "Changed since the last check", tone: "bg-amber-100 text-amber-900", icon: "warning" },
  none: { label: "Not checked yet", tone: "bg-amber-100 text-amber-900", icon: "warning" },
};
const TASKS = ["detection", "classification", "segmentation"] as const;
const FILE_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}\.(py|json|txt|md|ya?ml|cfg|toml|ini)$/;

/**
 * The model recipes: the code that trains and runs models. Built-in ones are read-only; administrators
 * duplicate them, start new ones or upload their own, edit the code here, and have the trainer check
 * it. A recipe of your own can be trained with once its check has passed.
 */
export function RecipesPage() {
  const admin = useCan().admin;
  const pushToast = useUiStore((s) => s.pushToast);
  const [list, setList] = useState<RecipeDetail[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null);
  const [fileName, setFileName] = useState("train.py");
  const [saved, setSaved] = useState(""); // the file as it is on the server
  const [text, setText] = useState(""); // ...and as it is in the editor
  const [versions, setVersions] = useState<{ id: string; saved_at: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [trainerOnline, setTrainerOnline] = useState<boolean | null>(null);
  const [dialog, setDialog] = useState<"new" | "duplicate" | "upload" | null>(null);
  const [help, setHelp] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);

  const dirty = text !== saved;
  const editable = admin && !!recipe && !recipe.builtin;
  const fail = (e: unknown, fallback: string) => pushToast(e instanceof ApiError ? e.message : fallback, "error");

  const reloadList = () =>
    listAllRecipes()
      .then((all) => {
        setList(all);
        return all;
      })
      .catch(() => {
        setList((l) => l ?? []);
        return [] as RecipeDetail[];
      });

  useEffect(() => {
    void reloadList().then((all) => setOpenId((id) => id ?? all[0]?.id ?? null));
    getTrainerStatus().then((s) => setTrainerOnline(s.online)).catch(() => setTrainerOnline(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The open recipe; while it is being checked, ask again until the trainer has answered.
  useEffect(() => {
    if (!openId) return;
    let stale = false;
    const load = () => getRecipe(openId).then((r) => !stale && setRecipe(r)).catch(() => undefined);
    setRecipe(null);
    void load();
    const timer = window.setInterval(() => {
      setRecipe((r) => {
        if (r && ["queued", "running"].includes(r.check?.status ?? "")) void load();
        return r;
      });
    }, 2500);
    return () => {
      stale = true;
      window.clearInterval(timer);
    };
  }, [openId]);

  // The list shows each recipe's state too: keep it in step with the open one (a check that just ended).
  useEffect(() => {
    if (recipe) void reloadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipe?.check?.status, recipe?.usable]);

  // The open file.
  const shownFile = recipe && recipe.files.includes(fileName) ? fileName : (recipe?.files[0] ?? null);
  useEffect(() => {
    if (!recipe || !shownFile) return;
    let stale = false;
    getRecipeFile(recipe.id, shownFile)
      .then((f) => {
        if (stale) return;
        setSaved(f.content);
        setText(f.content);
        setVersions(f.versions);
      })
      .catch((e) => !stale && fail(e, "Could not open the file"));
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipe?.id, shownFile]);

  const leave = () => !dirty || window.confirm("This file has changes that are not saved. Leave them behind?");

  async function save() {
    if (!recipe || !shownFile || !dirty) return;
    setBusy(true);
    try {
      setRecipe(await saveRecipeFile(recipe.id, shownFile, text));
      const f = await getRecipeFile(recipe.id, shownFile);
      setSaved(f.content);
      setText(f.content);
      setVersions(f.versions);
      void reloadList();
    } catch (e) {
      fail(e, "Could not save the file");
    } finally {
      setBusy(false);
    }
  }

  async function run(action: () => Promise<RecipeDetail | void>, failed: string) {
    setBusy(true);
    try {
      const next = await action();
      if (next) setRecipe(next);
      void reloadList();
    } catch (e) {
      fail(e, failed);
    } finally {
      setBusy(false);
    }
  }

  function addFile() {
    if (!recipe) return;
    const name = window.prompt("Name of the new file (for example helpers.py):")?.trim();
    if (!name) return;
    if (!FILE_NAME.test(name)) return pushToast("Use a simple name ending in .py, .json, .txt, .md, .yaml, .cfg, .toml or .ini.", "error");
    if (recipe.files.includes(name)) return setFileName(name);
    void run(async () => {
      const next = await saveRecipeFile(recipe.id, name, "");
      setFileName(name);
      return next;
    }, "Could not add the file");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      void save();
    } else if (e.key === "Tab" && !e.shiftKey && editable) {
      // Tab indents, as in any code editor (Escape then Tab leaves the editor, for keyboard users).
      e.preventDefault();
      const el = e.currentTarget;
      const { selectionStart: a, selectionEnd: b } = el;
      setText(text.slice(0, a) + "    " + text.slice(b));
      requestAnimationFrame(() => el.setSelectionRange(a + 4, a + 4));
    } else if (e.key === "Escape") {
      e.currentTarget.blur();
    }
  }

  if (!list) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  const check = recipe && !recipe.builtin ? CHECK[recipe.check?.status ?? "none"] : null;
  const checking = ["queued", "running"].includes(recipe?.check?.status ?? "");
  const lines = text.split("\n").length;

  return (
    <div className="max-w-7xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div className="bg-surface-container-low rounded-xl p-space-lg">
        <h1 className="font-headline-lg text-headline-lg mb-space-sm">Model Recipes</h1>
        <p className="text-body-md text-on-surface-variant max-w-3xl">
          A recipe is the code that trains a model and uses it for suggestions. The built-in ones cannot be changed; duplicate one to change
          it, or bring your own. Recipes are shared by every project.
        </p>
        <div className="mt-space-md flex items-center gap-space-sm flex-wrap">
          {admin ? (
            <>
              <Button icon="add" onClick={() => setDialog("new")}>
                New recipe
              </Button>
              <Button icon="upload_file" onClick={() => setDialog("upload")}>
                Upload a ZIP
              </Button>
            </>
          ) : (
            <span className="text-body-sm text-on-surface-variant">Only administrators can add or change recipes: their code runs on the training machine.</span>
          )}
          <button className="text-primary text-label-md hover:underline ml-auto" onClick={() => setHelp((v) => !v)} aria-expanded={help}>
            {help ? "Hide" : "How recipes work"}
          </button>
        </div>
        {help && <HowRecipesWork />}
      </div>

      <div className="grid lg:grid-cols-[17rem_1fr] gap-space-md items-start">
        <Card className="p-0 overflow-hidden">
          {list.map((r) => (
            <button
              key={r.id}
              aria-current={r.id === openId}
              onClick={() => {
                if (r.id === openId || !leave()) return;
                setOpenId(r.id);
                setFileName(r.trainable ? "train.py" : "predict.py");
              }}
              className={`w-full text-left px-space-md py-space-sm border-b border-outline-variant/40 last:border-b-0 ${
                r.id === openId ? "bg-primary-container/40" : "hover:bg-surface-container-low"
              }`}
            >
              <span className="flex items-center gap-1.5">
                <MaterialIcon name={r.builtin ? "lock" : r.usable ? "check_circle" : "edit_note"} className={`!text-[16px] ${r.builtin ? "text-on-surface-variant" : r.usable ? "text-emerald-700" : "text-amber-700"}`} />
                <span className="font-headline-sm text-label-lg truncate">{r.name}</span>
              </span>
              <span className="block text-label-sm text-on-surface-variant capitalize">
                {r.task} · {r.builtin ? "built-in" : r.trainable ? "yours" : "yours · importer"}
                {!r.trainable && r.builtin && " · importer"}
              </span>
            </button>
          ))}
        </Card>

        {!recipe ? (
          <Card className="p-space-lg text-body-sm text-on-surface-variant">{openId ? "Loading..." : "No recipes."}</Card>
        ) : (
          <Card className="p-space-lg flex flex-col gap-space-md min-w-0">
            <div className="flex items-start justify-between gap-space-md flex-wrap">
              <div className="min-w-0">
                <div className="flex items-center gap-space-sm flex-wrap">
                  <h2 className="font-headline-md text-headline-md">{recipe.name}</h2>
                  <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm capitalize">{recipe.task}</span>
                  {recipe.builtin ? (
                    <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm">Built-in · read-only</span>
                  ) : (
                    check && (
                      <span className={`inline-flex items-center gap-1 px-space-sm py-0.5 rounded-full text-label-sm ${check.tone}`}>
                        <MaterialIcon name={check.icon} className="!text-[14px]" />
                        {check.label}
                      </span>
                    )
                  )}
                </div>
                <p className="text-body-sm text-on-surface-variant max-w-2xl">{recipe.description}</p>
              </div>
              <div className="flex items-center gap-space-sm flex-wrap">
                {editable && (
                  <Button
                    variant="primary"
                    icon="rule"
                    disabled={busy || dirty || checking || !!recipe.problem || trainerOnline === false}
                    title={dirty ? "Save the file first" : trainerOnline === false ? "The trainer is not running" : "Train one epoch on a few made-up images, then try a suggestion"}
                    onClick={() => void run(() => checkRecipe(recipe.id), "Could not start the check")}
                  >
                    {checking ? "Checking..." : "Check"}
                  </Button>
                )}
                {admin && (
                  <Button icon="content_copy" disabled={busy} onClick={() => leave() && setDialog("duplicate")}>
                    Duplicate
                  </Button>
                )}
                <Button icon="download" onClick={() => startDownload(recipeDownloadUrl(recipe.id))}>
                  Download
                </Button>
                {editable && (
                  <Button
                    variant="ghost"
                    icon="delete"
                    disabled={busy}
                    onClick={() => {
                      if (!window.confirm(`Delete the recipe "${recipe.name}"? Training runs and models already made with it keep their own copy of the code.`)) return;
                      void run(async () => {
                        await deleteRecipe(recipe.id);
                        setRecipe(null);
                        setOpenId(list.find((r) => r.id !== recipe.id)?.id ?? null);
                      }, "Could not delete the recipe");
                    }}
                  >
                    Delete
                  </Button>
                )}
              </div>
            </div>

            {recipe.problem && (
              <p className="flex items-start gap-1.5 text-body-sm text-error" role="alert">
                <MaterialIcon name="error" className="!text-[18px] shrink-0" />
                This recipe cannot be used: {recipe.problem}
              </p>
            )}
            {!recipe.builtin && !recipe.problem && !recipe.usable && !checking && (
              <p className="text-body-sm text-on-surface-variant">
                {recipe.check?.status === "failed"
                  ? "It cannot be trained with until the check passes."
                  : "Run the check to make it available for training. Any later change needs a new check."}
              </p>
            )}
            {editable && trainerOnline === false && <p className="text-body-sm text-on-surface-variant">The trainer is not running, so recipes cannot be checked right now.</p>}

            {recipe.check?.report && <CheckReport report={recipe.check.report} />}

            {recipe.packages && (
              <div className="flex items-center gap-space-sm flex-wrap text-body-sm rounded bg-surface-container-low px-space-md py-space-sm">
                <MaterialIcon name="inventory_2" className="!text-[18px] text-on-surface-variant" />
                <span>
                  Extra packages: <span className="font-mono">{recipe.packages.wanted.join(", ")}</span> ·{" "}
                  {recipe.packages.installed ? `installed, ${formatBytes(recipe.packages.bytes)}` : "installed the first time it is checked or trained (needs internet)"}
                </span>
                {editable && recipe.packages.installed && (
                  <button className="text-primary text-label-md hover:underline" disabled={busy} onClick={() => void run(() => removeRecipePackages(recipe.id), "Could not remove the packages")}>
                    Remove to free space
                  </button>
                )}
              </div>
            )}

            {/* Files */}
            <div className="flex items-center gap-1 flex-wrap border-b border-outline-variant" role="tablist" aria-label="Files">
              {recipe.files.map((name) => (
                <button
                  key={name}
                  role="tab"
                  aria-selected={name === shownFile}
                  onClick={() => name !== shownFile && leave() && setFileName(name)}
                  className={`px-space-md py-1.5 text-label-md font-mono rounded-t ${name === shownFile ? "bg-[#0f172a] text-white" : "text-on-surface-variant hover:bg-surface-container-low"}`}
                >
                  {name}
                  {name === shownFile && dirty && " •"}
                </button>
              ))}
              {editable && (
                <button className="px-space-sm py-1.5 text-on-surface-variant hover:text-primary" onClick={addFile} title="Add a file" aria-label="Add a file">
                  <MaterialIcon name="add" className="!text-[18px]" />
                </button>
              )}
            </div>

            {shownFile && (
              <>
                <textarea
                  ref={editor}
                  aria-label={`${shownFile}${editable ? "" : " (read-only)"}`}
                  className="w-full h-[60vh] min-h-[320px] bg-[#0f172a] text-slate-200 font-mono text-[13px] leading-5 p-space-md rounded resize-y outline-none focus:ring-2 focus:ring-primary whitespace-pre overflow-auto"
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  wrap="off"
                  readOnly={!editable}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={onKeyDown}
                />
                <div className="flex items-center gap-space-sm flex-wrap">
                  {editable ? (
                    <>
                      <Button variant="primary" icon="save" onClick={() => void save()} disabled={busy || !dirty} title="Ctrl+S">
                        {dirty ? "Save" : "Saved"}
                      </Button>
                      {dirty && (
                        <Button variant="ghost" onClick={() => setText(saved)}>
                          Undo changes
                        </Button>
                      )}
                      {versions.length > 0 && (
                        <select
                          className="input !w-auto"
                          aria-label="Earlier versions"
                          value=""
                          onChange={(e) => {
                            const id = e.target.value;
                            if (!id) return;
                            getRecipeFileVersion(recipe.id, shownFile, id)
                              .then((v) => {
                                setText(v.content);
                                pushToast("The earlier version is in the editor. Save to keep it.", "info");
                              })
                              .catch((err) => fail(err, "Could not open that version"));
                          }}
                        >
                          <option value="">Earlier versions ({versions.length})...</option>
                          {versions.map((v) => (
                            <option key={v.id} value={v.id}>
                              {new Date(v.saved_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" })}
                            </option>
                          ))}
                        </select>
                      )}
                      {shownFile !== "recipe.json" && (
                        <button
                          className="text-label-md text-on-surface-variant hover:text-error ml-auto"
                          disabled={busy}
                          onClick={() => window.confirm(`Delete ${shownFile} from this recipe?`) && void run(() => deleteRecipeFile(recipe.id, shownFile), "Could not delete the file")}
                        >
                          Delete this file
                        </button>
                      )}
                    </>
                  ) : (
                    <span className="text-body-sm text-on-surface-variant">
                      {recipe.builtin ? "Built-in recipes are read-only." : "Only administrators can change recipes."}
                      {admin && recipe.builtin && " Duplicate it to change the code."}
                    </span>
                  )}
                  <span className={`text-label-sm text-on-surface-variant ${editable ? "" : "ml-auto"}`}>{lines.toLocaleString()} lines</span>
                </div>
              </>
            )}
          </Card>
        )}
      </div>

      <RecipeDialog
        kind={dialog}
        source={dialog === "duplicate" ? recipe : null}
        onClose={() => setDialog(null)}
        onMade={(made) => {
          setDialog(null);
          void reloadList();
          setOpenId(made.id);
          setFileName(made.trainable ? "train.py" : "predict.py");
        }}
      />
    </div>
  );
}

function CheckReport({ report }: { report: NonNullable<NonNullable<RecipeDetail["check"]>["report"]> }) {
  return (
    <div className="rounded-xl bg-surface-container-low p-space-md flex flex-col gap-1.5" data-testid="check-report">
      {report.steps.map((s) => (
        <div key={s.name} className="flex items-start gap-1.5 text-body-sm">
          <MaterialIcon name={s.ok ? "check_circle" : "cancel"} className={`!text-[18px] shrink-0 ${s.ok ? "text-emerald-700" : "text-error"}`} />
          <span>
            {s.name}
            {s.detail && <span className={`block whitespace-pre-wrap ${s.ok ? "text-on-surface-variant" : "text-error"}`}>{s.detail}</span>}
          </span>
        </div>
      ))}
      {report.log && (
        <details open={report.steps.some((s) => !s.ok)}>
          <summary className="cursor-pointer text-label-md text-on-surface-variant">What the code printed</summary>
          <pre className="mt-space-sm bg-[#0f172a] text-slate-300 rounded p-space-md text-label-sm font-mono overflow-auto max-h-64 whitespace-pre-wrap">{report.log}</pre>
        </details>
      )}
    </div>
  );
}

function RecipeDialog({
  kind,
  source,
  onClose,
  onMade,
}: {
  kind: "new" | "duplicate" | "upload" | null;
  source: RecipeDetail | null;
  onClose: () => void;
  onMade: (recipe: RecipeDetail) => void;
}) {
  const [name, setName] = useState("");
  const [task, setTask] = useState<(typeof TASKS)[number]>("detection");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(kind === "duplicate" && source ? `${source.name} (copy)` : "");
    setFile(null);
    setError(null);
  }, [kind, source]);

  if (!kind) return null;
  const title = { new: "New Recipe", duplicate: "Duplicate Recipe", upload: "Upload a Recipe" }[kind];

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      onMade(kind === "upload" ? await uploadRecipe(file as File, name) : await createRecipe(name, kind === "new" ? task : undefined, kind === "duplicate" ? source?.id : undefined));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The recipe could not be made");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={() => !busy && onClose()} widthClass="max-w-lg">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">{title}</h2>
          <button onClick={onClose} aria-label="Close" disabled={busy}>
            <MaterialIcon name="close" />
          </button>
        </div>
        <p className="text-body-md text-on-surface-variant">
          {kind === "new" && "Starts from a template that already has the whole shape of a recipe, with the places for your code marked."}
          {kind === "duplicate" && `A copy of "${source?.name}" that is yours to change. The original stays as it is.`}
          {kind === "upload" && "A ZIP holding recipe.json and the code beside it (train.py, predict.py, requirements.txt). Text files only; trained models are added under a project's Training page."}
        </p>
        {kind === "upload" && (
          <label className="flex flex-col gap-1">
            <span className="text-label-md text-on-surface-variant">ZIP file</span>
            <input type="file" accept=".zip,application/zip" className="text-body-md" disabled={busy} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className="text-label-md text-on-surface-variant">Name{kind === "upload" ? " (left empty, the one in its recipe.json)" : ""}</span>
          <input className="input" value={name} maxLength={80} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && kind !== "upload" && void submit()} />
        </label>
        {kind === "new" && (
          <label className="flex flex-col gap-1">
            <span className="text-label-md text-on-surface-variant">Task</span>
            <select className="input capitalize" value={task} onChange={(e) => setTask(e.target.value as (typeof TASKS)[number])}>
              {TASKS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        )}
        {error && (
          <div className="rounded bg-error-container text-on-error-container p-space-sm text-body-md" role="alert">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-space-sm">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={busy || (kind === "upload" ? !file : !name.trim())}>
            {busy ? "Working..." : kind === "upload" ? "Upload" : kind === "duplicate" ? "Duplicate" : "Create"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function HowRecipesWork() {
  return (
    <div className="mt-space-md grid md:grid-cols-2 gap-space-md text-body-sm">
      <div>
        <div className="font-headline-sm text-label-lg mb-1">The files</div>
        <ul className="list-disc pl-5 flex flex-col gap-1 text-on-surface-variant">
          <li>
            <span className="font-mono">recipe.json</span>: the name, the task, and the settings shown on the Start form.
          </li>
          <li>
            <span className="font-mono">train.py</span>: learns from the dataset and writes the trained model.
          </li>
          <li>
            <span className="font-mono">predict.py</span>: uses the trained model on new images, for suggestions.
          </li>
          <li>
            <span className="font-mono">requirements.txt</span>: extra packages; installed once and kept. PyTorch, torchvision, Pillow and numpy are already there.
          </li>
        </ul>
      </div>
      <div>
        <div className="font-headline-sm text-label-lg mb-1">What the app gives and expects</div>
        <ul className="list-disc pl-5 flex flex-col gap-1 text-on-surface-variant">
          <li>
            <span className="font-mono">train.py --dataset D --output O --settings S.json</span>: D holds <span className="font-mono">dataset.json</span> and, for
            each of train / val / test, a COCO file and its images.
          </li>
          <li>
            Print one <span className="font-mono">VP_METRIC {"{json}"}</span> line per epoch with <span className="font-mono">epoch</span> and{" "}
            <span className="font-mono">epochs</span>; every other number becomes a curve.
          </li>
          <li>
            Write <span className="font-mono">O/model.pt</span> and <span className="font-mono">O/result.json</span>, and end without an error.
          </li>
          <li>
            <span className="font-mono">predict.py --model M --device cuda|cpu</span>: print <span className="font-mono">VP_READY</span>, then answer each request line
            with a <span className="font-mono">VP_RESULT {"{json}"}</span> line.
          </li>
          <li>The blank template shows all of this in working code; start there.</li>
        </ul>
      </div>
    </div>
  );
}
