import { useEffect, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button } from "../../components/primitives";
import { ApiError, acceptSuggestion, acceptSuggestions, clearSuggestions, listPatchSuggestions, rejectSuggestion, suggestForPatch } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { AnnotationClass, Suggestion } from "../../types/api";
import { SlideSuggestControls } from "./SlideSuggestControls";
import { useSuggestModels } from "./useSuggestModels";

const DEFAULT_MIN_SCORE = 0.5;

/**
 * The workspace's AI suggestions for one patch: pick one of the project's trained models, ask it what
 * it sees, and accept or reject what it proposes -- shapes (drawn dashed on the patch) or a label for
 * the whole patch. Nothing is an annotation until accepted. The parent draws `onShown`'s shapes and
 * reloads on `onAccepted`. In a slide project it also runs the model over the whole slide.
 */
export function SuggestionsPanel({
  projectId,
  patchId,
  patchIndex,
  slideId,
  classes,
  noun,
  onShown,
  onHighlight,
  onAccepted,
  onOpenPatch,
}: {
  projectId: number;
  patchId: number;
  patchIndex: number;
  /** Set in a slide project: offers the whole-slide run and stepping through its patches. */
  slideId: number | null;
  classes: AnnotationClass[];
  /** "Patch" or "Image". */
  noun: string;
  /** The suggestions to draw: those at least as sure as the slider says. */
  onShown: (shown: Suggestion[]) => void;
  onHighlight: (id: number | null) => void;
  /** Suggestions became annotations: the patch's annotations need loading again. */
  onAccepted: () => void;
  onOpenPatch: (patchId: number) => void;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const { models, modelId, setModelId, online, setOnline, refreshOnline } = useSuggestModels(projectId);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [minScore, setMinScore] = useState(DEFAULT_MIN_SCORE);
  const [busy, setBusy] = useState<"asking" | "deciding" | null>(null);
  const [asked, setAsked] = useState(false); // this patch was asked about, so "nothing found" can be said
  const [decided, setDecided] = useState(0); // counts decisions, so the whole-slide numbers follow

  const reload = () => listPatchSuggestions(patchId).then(setSuggestions).catch(() => undefined);
  useEffect(() => {
    let stale = false;
    setSuggestions([]);
    setAsked(false);
    listPatchSuggestions(patchId).then((list) => !stale && setSuggestions(list)).catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [patchId]);

  const shown = suggestions.filter((s) => s.score >= minScore);
  const shownKey = shown.map((s) => s.id).join(",");
  useEffect(() => {
    onShown(shown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownKey]);
  useEffect(() => () => onShown([]), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!models || models.length === 0) return null; // nothing to offer until the project has a model

  const fail = (e: unknown, fallback: string) => pushToast(e instanceof ApiError ? e.message : fallback, "error");

  async function ask() {
    if (modelId === null) return;
    setBusy("asking");
    try {
      setSuggestions(await suggestForPatch(patchId, modelId));
      setAsked(true);
      setOnline(true);
      setDecided((n) => n + 1);
    } catch (e) {
      fail(e, "The model could not make suggestions");
      void refreshOnline();
    } finally {
      setBusy(null);
    }
  }

  async function decide(s: Suggestion, accept: boolean) {
    setBusy("deciding");
    try {
      await (accept ? acceptSuggestion(s.id) : rejectSuggestion(s.id));
      setSuggestions((list) => list.filter((x) => x.id !== s.id));
      onHighlight(null);
      setDecided((n) => n + 1);
      if (accept) onAccepted();
    } catch (e) {
      fail(e, "Could not save the decision");
    } finally {
      setBusy(null);
    }
  }

  async function many(action: () => Promise<unknown>, done: (result: unknown) => void, failed: string) {
    setBusy("deciding");
    try {
      done(await action());
      await reload();
      setDecided((n) => n + 1);
    } catch (e) {
      fail(e, failed);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid="suggestions-panel" className="flex flex-col gap-space-sm">
      <div>
        <div className="flex items-center gap-1 text-label-md text-on-surface-variant mb-1">
          <MaterialIcon name="auto_awesome" className="!text-[16px]" />
          AI suggestions
        </div>
        <div className="flex items-center gap-space-sm">
          <select className="input min-w-0 flex-1" aria-label="Model" value={modelId ?? ""} onChange={(e) => setModelId(Number(e.target.value))}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            onClick={() => void ask()}
            disabled={busy !== null || online === false}
            title={online === false ? "The trainer is not running" : `Ask the model what it sees in this ${noun.toLowerCase()}`}
          >
            {busy === "asking" ? "Looking..." : "Suggest"}
          </Button>
        </div>
        {online === false && <div className="text-label-sm text-on-surface-variant mt-1">The trainer is not running, so models cannot make suggestions right now.</div>}
        {busy === "asking" && <div className="text-label-sm text-on-surface-variant mt-1">The first request loads the model, which takes a little while.</div>}
      </div>

      {suggestions.length > 0 && (
        <div className="flex flex-col gap-space-sm">
          <label className="flex items-center gap-space-sm text-label-sm text-on-surface-variant">
            <span className="whitespace-nowrap">At least {Math.round(minScore * 100)}% sure</span>
            <input type="range" min={5} max={95} step={5} value={Math.round(minScore * 100)} onChange={(e) => setMinScore(Number(e.target.value) / 100)} className="flex-1 min-w-0" aria-label="Minimum confidence" />
          </label>
          <div className="flex flex-col gap-1">
            {shown.map((s) => {
              const cls = classes.find((c) => c.id === s.class_id);
              const what = s.type === "patch_label" ? `Label this ${noun.toLowerCase()}: ${cls?.name ?? "?"}` : (cls?.name ?? "Unclassed");
              return (
                <div
                  key={s.id}
                  onMouseEnter={() => onHighlight(s.id)}
                  onMouseLeave={() => onHighlight(null)}
                  className="flex items-center justify-between gap-1 pl-space-sm pr-1 py-1 rounded border border-dashed border-outline bg-surface-container-lowest"
                >
                  <span className="flex items-center gap-1.5 text-label-md min-w-0">
                    <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: cls?.color_hex ?? "#94a3b8" }} />
                    <span className="truncate" title={what}>
                      {what}
                    </span>
                    <span className="font-mono text-label-sm text-on-surface-variant">{Math.round(s.score * 100)}%</span>
                  </span>
                  <span className="flex items-center shrink-0">
                    <button className="w-7 h-7 rounded hover:bg-emerald-100 text-emerald-700 flex items-center justify-center disabled:opacity-40" disabled={busy !== null} onClick={() => void decide(s, true)} title={s.type === "patch_label" ? "Accept: set it as the label" : "Accept: make it an annotation"} aria-label={`Accept ${what}`}>
                      <MaterialIcon name="check" className="!text-[18px]" />
                    </button>
                    <button className="w-7 h-7 rounded hover:bg-red-100 text-red-700 flex items-center justify-center disabled:opacity-40" disabled={busy !== null} onClick={() => void decide(s, false)} title="Reject: the model will not propose it here again" aria-label={`Reject ${what}`}>
                      <MaterialIcon name="close" className="!text-[18px]" />
                    </button>
                  </span>
                </div>
              );
            })}
            {shown.length === 0 && <div className="text-body-sm text-on-surface-variant">None this sure; lower the slider to see {suggestions.length} more.</div>}
          </div>
          <div className="flex items-center gap-space-sm">
            <Button
              onClick={() =>
                void many(
                  () => acceptSuggestions(patchId, minScore),
                  (made) => {
                    const n = (made as unknown[]).length;
                    pushToast(`Accepted ${n} suggestion${n === 1 ? "" : "s"}`, "success");
                    onAccepted();
                  },
                  "Could not accept the suggestions",
                )
              }
              disabled={busy !== null || shown.length === 0}
            >
              Accept {shown.length} shown
            </Button>
            <Button variant="ghost" onClick={() => void many(() => clearSuggestions(patchId), () => setAsked(false), "Could not clear the suggestions")} disabled={busy !== null}>
              Clear
            </Button>
          </div>
        </div>
      )}
      {asked && suggestions.length === 0 && busy === null && <div className="text-body-sm text-on-surface-variant">Nothing new found in this {noun.toLowerCase()}.</div>}

      {slideId !== null && (
        <SlideSuggestControls slideId={slideId} modelId={modelId} online={online} currentPatchIndex={patchIndex} onOpenPatch={onOpenPatch} onChanged={() => void reload()} refreshKey={decided} />
      )}
    </div>
  );
}
