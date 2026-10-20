import { useEffect, useState } from "react";
import { Button } from "../../components/primitives";
import { getSplit } from "../../services/api";
import type { DatasetSplit, RunSplit, SplitName, SplitSettings } from "../../types/api";
import { SPLIT_LABELS, SPLIT_NAMES, SplitFields, sharesProblem, unitOf } from "../split/SplitFields";

// Rows drawn at once in a very large image project; the search narrows it.
const ROW_LIMIT = 200;

const newSeed = () => 1 + Math.floor(Math.random() * 2 ** 30);

/**
 * The train / validation / test split a training run uses. It starts as the project's own split (the
 * one in the settings, or the chosen version's) and can be changed here -- other shares, another
 * deal, or sets given by hand -- for this run alone: `value` is then that split, and null while the
 * project's is used. Nothing here is saved to the project.
 */
export function RunSplitPanel({
  projectId,
  version,
  isImageProject,
  value,
  onChange,
  disabled = false,
}: {
  projectId: number;
  /** The saved version trained on (its split is the starting point); null: the work as it is. */
  version: number | null;
  isImageProject: boolean;
  value: RunSplit | null;
  onChange: (split: RunSplit | null) => void;
  disabled?: boolean;
}) {
  const [base, setBase] = useState<DatasetSplit | null>(null); // the project's split
  const [draft, setDraft] = useState<SplitSettings | null>(null); // what is shown and typed
  const [seed, setSeed] = useState(newSeed);
  const [moved, setMoved] = useState<Record<number, SplitName | null>>({}); // sets given by hand, where they differ
  const [query, setQuery] = useState("");

  const nouns = isImageProject ? "Images" : "Slides";

  useEffect(() => {
    let stale = false;
    getSplit(projectId, version)
      .then((split) => {
        if (stale) return;
        setBase(split);
        setDraft({ mode: split.mode, train: split.train, val: split.val, test: split.test, unit: split.unit ?? "slide" });
        setMoved({});
      })
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [projectId, version]);

  if (!base || !draft) return <span className="text-body-sm text-on-surface-variant">Loading the split...</span>;

  /** Tell the page which split the run uses: its own, or null when it is the project's as it is. */
  function emit(next: SplitSettings, nextSeed: number | null, nextMoved: Record<number, SplitName | null>) {
    if (!base) return;
    if (next.mode === "random") {
      if (sharesProblem(next)) return; // half-typed shares: keep what was last valid
      const asProject =
        base.mode === "random" && nextSeed === null && unitOf(next) === unitOf(base) && SPLIT_NAMES.every((n) => next[n] === base[n]);
      onChange(asProject ? null : { mode: "random", train: next.train, val: next.val, test: next.test, seed: nextSeed ?? seed, unit: unitOf(next) });
    } else if (next.mode === "manual") {
      const asProject = base.mode === "manual" && Object.keys(nextMoved).length === 0;
      onChange(asProject ? null : { mode: "manual", assignments: nextMoved });
    }
  }

  function changeFields(next: SplitSettings) {
    setDraft(next);
    emit(next, value?.mode === "random" ? value.seed : null, moved);
  }

  function shuffle() {
    const next = newSeed();
    setSeed(next);
    emit(draft!, next, moved);
  }

  function assign(slideId: number, name: SplitName | null) {
    const next = { ...moved };
    if ((base!.slides.find((s) => s.slide_id === slideId)?.split ?? null) === name) delete next[slideId];
    else next[slideId] = name;
    setMoved(next);
    emit(draft!, null, next);
  }

  function reset() {
    setDraft({ mode: base!.mode, train: base!.train, val: base!.val, test: base!.test, unit: base!.unit ?? "slide" });
    setMoved({});
    onChange(null);
  }

  const setOf = (s: DatasetSplit["slides"][number]) => (s.slide_id in moved ? moved[s.slide_id] : s.split);
  const needle = query.trim().toLowerCase();
  const matching = needle ? base.slides.filter((s) => s.filename.toLowerCase().includes(needle)) : base.slides;
  const rows = matching.slice(0, ROW_LIMIT);
  const where = version === null ? "the project settings" : "this version";

  return (
    <div className="flex flex-col gap-space-md" data-testid="run-split">
      <div className="flex items-start justify-between gap-space-md flex-wrap">
        <div>
          <div className="text-label-md text-on-surface-variant">Train / validation / test split</div>
          <p className="text-body-sm text-on-surface-variant max-w-3xl">
            {value === null
              ? base.mode === "off"
                ? `There is no split in ${where} yet. Choose one here for this training run; ${where} stay${version === null ? "" : "s"} as ${version === null ? "they are" : "it is"}.`
                : `The split from ${where}. Changing it here changes it for this training run only.`
              : `Changed for this training run only: the split in ${where} stays as it is.`}
          </p>
        </div>
        {value !== null && base.mode !== "off" && (
          <Button icon="undo" onClick={reset} disabled={disabled}>
            Use the split from {version === null ? "the settings" : "the version"}
          </Button>
        )}
      </div>

      <SplitFields value={draft} onChange={changeFields} nouns={nouns} disabled={disabled} hideOff allowPatch={!isImageProject} />

      {draft.mode === "random" && (
        <div className="flex items-center gap-space-sm flex-wrap">
          <Button icon="shuffle" onClick={shuffle} disabled={disabled || !!sharesProblem(draft) || base.slides.length === 0}>
            Shuffle again
          </Button>
          <span className="text-body-sm text-on-surface-variant">
            {unitOf(draft) === "patch"
              ? "Each slide's patches are dealt out for this run by these shares; the table below shows how many each set gets."
              : value?.mode === "random"
                ? `${nouns} are dealt out for this run by these shares; the table below shows how many each set gets.`
                : `${nouns} are in the sets ${where} dealt them to.`}
          </span>
        </div>
      )}

      {draft.mode === "manual" && (
        <>
          {base.slides.length > 8 && (
            <input className="input !w-56" placeholder={`Search ${nouns.toLowerCase()}...`} value={query} onChange={(e) => setQuery(e.target.value)} />
          )}
          <div className="max-h-[280px] overflow-auto rounded border border-outline-variant/60">
            <table className="w-full text-body-sm">
              <thead className="sticky top-0 bg-surface-container-low text-label-sm text-on-surface-variant">
                <tr>
                  <th className="text-left px-space-md py-space-sm font-medium">{isImageProject ? "Image" : "Slide"}</th>
                  <th className="text-left px-space-md py-space-sm font-medium w-48">Set for this run</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.slide_id} className="border-t border-outline-variant/40">
                    <td className="px-space-md py-1.5 font-mono text-label-md truncate max-w-[28rem]" title={s.filename}>
                      {s.filename}
                    </td>
                    <td className="px-space-md py-1.5">
                      <select
                        className="input !w-40 !py-0.5"
                        aria-label={`Set of ${s.filename} for this run`}
                        disabled={disabled}
                        value={setOf(s) ?? ""}
                        onChange={(e) => assign(s.slide_id, (e.target.value || null) as SplitName | null)}
                      >
                        <option value="">Not used</option>
                        {SPLIT_NAMES.map((name) => (
                          <option key={name} value={name}>
                            {SPLIT_LABELS[name]}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={2} className="px-space-md py-space-lg text-center text-on-surface-variant">
                      {base.slides.length === 0 ? `No ${nouns.toLowerCase()} yet.` : `Nothing matches "${query}".`}
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
    </div>
  );
}
