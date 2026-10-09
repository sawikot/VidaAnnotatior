import { useEffect, useState } from "react";
import { Button, Card } from "../../components/primitives";
import { ApiError, getSplit, saveSplit, type SplitUpdate } from "../../services/api";
import { useCan } from "../../stores/authStore";
import { useUiStore } from "../../stores/uiStore";
import type { DatasetSplit, SplitMode, SplitName, SplitSettings } from "../../types/api";
import { SPLIT_LABELS, SPLIT_NAMES, SplitFields, sharesProblem } from "./SplitFields";

// Rows drawn at once in a very large image project; the search narrows it.
const ROW_LIMIT = 200;

const TONE: Record<SplitName | "unassigned", string> = {
  train: "bg-sky-100 text-sky-900",
  val: "bg-amber-100 text-amber-900",
  test: "bg-violet-100 text-violet-900",
  unassigned: "bg-surface-container-high text-on-surface-variant",
};

/**
 * The project's train / validation / test split: turn it on, deal the slides out at random by shares,
 * or assign each one by hand. Whole slides (images, in an image project) are assigned, never single
 * patches. Saved with the project, so the export screen and the settings show the same split.
 */
export function SplitPanel({
  projectId,
  isImageProject,
  onModeChange,
}: {
  projectId: number;
  isImageProject: boolean;
  /** Told the split's mode whenever it is loaded or changed. */
  onModeChange?: (mode: SplitMode) => void;
}) {
  const canManage = useCan().manage;
  const pushToast = useUiStore((s) => s.pushToast);
  const [split, setSplit] = useState<DatasetSplit | null>(null);
  const [draft, setDraft] = useState<SplitSettings | null>(null); // the shares being typed
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");

  const noun = isImageProject ? "image" : "slide";
  const nouns = isImageProject ? "Images" : "Slides";

  function accept(next: DatasetSplit) {
    setSplit(next);
    setDraft({ mode: next.mode, train: next.train, val: next.val, test: next.test });
    onModeChange?.(next.mode);
  }

  useEffect(() => {
    getSplit(projectId)
      .then(accept)
      .catch(() => pushToast("Failed to load the dataset split", "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function save(update: SplitUpdate) {
    setBusy(true);
    try {
      accept(await saveSplit(projectId, update));
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : "Could not save the split", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!split || !draft) return <Card className="p-space-lg text-body-sm text-on-surface-variant">Loading the split...</Card>;

  const saved: SplitSettings = { mode: split.mode, train: split.train, val: split.val, test: split.test };
  const problem = sharesProblem(draft);
  const sharesChanged = SPLIT_NAMES.some((n) => draft[n] !== saved[n]);

  function changeFields(next: SplitSettings) {
    setDraft(next);
    // Picking a mode takes effect at once; shares wait for Apply so half-typed numbers are never dealt out.
    if (next.mode !== saved.mode) void save({ ...saved, mode: next.mode });
  }

  const needle = query.trim().toLowerCase();
  const matching = needle ? split.slides.filter((s) => s.filename.toLowerCase().includes(needle)) : split.slides;
  const rows = matching.slice(0, ROW_LIMIT);

  return (
    <Card className="p-space-lg flex flex-col gap-space-md" data-testid="split-panel">
      <p className="text-body-sm text-on-surface-variant max-w-3xl">
        Each {noun} goes to one set{isImageProject ? "" : ", with all of its patches, so the same tissue is never in both training and test data"}.
        The split is saved with the project.
      </p>

      <SplitFields value={draft} onChange={changeFields} nouns={nouns} disabled={!canManage || busy} />
      {!canManage && <p className="text-body-sm text-on-surface-variant">Only project managers and administrators can change the split.</p>}

      {split.mode === "random" && canManage && (
        <div className="flex items-center gap-space-sm flex-wrap">
          <Button variant="primary" disabled={busy || !sharesChanged || !!problem} onClick={() => void save(draft)}>
            Apply shares
          </Button>
          <Button
            icon="shuffle"
            disabled={busy || sharesChanged || split.slides.length === 0}
            onClick={() => void save({ ...saved, reshuffle: true })}
            title={`Deal every ${noun} out again at random`}
          >
            Shuffle again
          </Button>
          <span className="text-body-sm text-on-surface-variant">
            New {nouns.toLowerCase()} join a set as they are added; the others stay where they are.
          </span>
        </div>
      )}

      {split.mode !== "off" && (
        <>
          <div className="flex items-center gap-space-sm flex-wrap">
            {([...SPLIT_NAMES, "unassigned"] as const).map((name) =>
              name === "unassigned" && split.counts.unassigned === 0 ? null : (
                <span key={name} className={`px-space-sm py-0.5 rounded-full text-label-md ${TONE[name]}`}>
                  {name === "unassigned" ? "Not assigned" : SPLIT_LABELS[name]} · {split.counts[name]}
                </span>
              ),
            )}
            <div className="flex-1" />
            {split.slides.length > 8 && (
              <input className="input !w-56" placeholder={`Search ${nouns.toLowerCase()}...`} value={query} onChange={(e) => setQuery(e.target.value)} />
            )}
          </div>
          {split.mode === "random" && split.slides.length > 0 && SPLIT_NAMES.some((n) => split[n] > 0 && split.counts[n] === 0) && (
            <p className="text-body-sm text-on-surface-variant" role="status">
              There are too few {nouns.toLowerCase()} to fill every set at these shares (
              {SPLIT_NAMES.filter((n) => split[n] > 0 && split.counts[n] === 0)
                .map((n) => SPLIT_LABELS[n])
                .join(" and ")}{" "}
              got none). Add more, change the shares, or assign them by hand with Manual.
            </p>
          )}
          {split.mode === "manual" && split.counts.unassigned > 0 && (
            <p className="text-body-sm text-on-surface-variant">
              {nouns} not assigned to a set are exported into an <span className="font-mono">unassigned</span> folder.
            </p>
          )}

          <div className="max-h-[320px] overflow-auto rounded border border-outline-variant/60">
            <table className="w-full text-body-sm">
              <thead className="sticky top-0 bg-surface-container-low text-label-sm text-on-surface-variant">
                <tr>
                  <th className="text-left px-space-md py-space-sm font-medium">{isImageProject ? "Image" : "Slide"}</th>
                  <th className="text-left px-space-md py-space-sm font-medium w-48">Set</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.slide_id} className="border-t border-outline-variant/40">
                    <td className="px-space-md py-1.5 font-mono text-label-md truncate max-w-[28rem]" title={s.filename}>
                      {s.filename}
                    </td>
                    <td className="px-space-md py-1.5">
                      {split.mode === "manual" && canManage ? (
                        <select
                          className="input !w-40 !py-0.5"
                          aria-label={`Set of ${s.filename}`}
                          disabled={busy}
                          value={s.split ?? ""}
                          onChange={(e) =>
                            void save({ ...saved, assignments: { [s.slide_id]: (e.target.value || null) as SplitName | null } })
                          }
                        >
                          <option value="">Not assigned</option>
                          {SPLIT_NAMES.map((name) => (
                            <option key={name} value={name}>
                              {SPLIT_LABELS[name]}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className={`px-space-sm py-0.5 rounded-full text-label-md ${TONE[s.split ?? "unassigned"]}`}>
                          {s.split ? SPLIT_LABELS[s.split] : "Not assigned"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={2} className="px-space-md py-space-lg text-center text-on-surface-variant">
                      {split.slides.length === 0
                        ? `No ${nouns.toLowerCase()} yet. ${split.mode === "random" ? "They are dealt out as they are added." : "Assign them here once they are added."}`
                        : `No ${noun} matches "${query}".`}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {matching.length > ROW_LIMIT && (
            <div className="text-label-sm text-on-surface-variant">
              Showing {ROW_LIMIT} of {matching.length.toLocaleString()} -- search to find others.
            </div>
          )}
        </>
      )}
    </Card>
  );
}
