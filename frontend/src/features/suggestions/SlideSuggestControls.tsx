import { useEffect, useRef, useState } from "react";
import { ApiError, getSlideSuggestState, nextPatchWithSuggestions, startSlideSuggest, stopSlideSuggest } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { SlideSuggestState } from "../../types/api";

/**
 * A model working through a whole slide in the background: start it, watch how far it has got, stop
 * it, and step through the patches it left suggestions in. `onChanged` fires whenever the number of
 * waiting suggestions changes, so whoever draws them can load them again.
 */
export function SlideSuggestControls({
  slideId,
  modelId,
  online,
  currentPatchIndex,
  onOpenPatch,
  onChanged,
  refreshKey,
}: {
  slideId: number;
  modelId: number | null;
  online: boolean | null;
  /** The patch on screen, so "next" continues from it (the first one when left out). */
  currentPatchIndex?: number;
  onOpenPatch: (patchId: number) => void;
  onChanged?: (state: SlideSuggestState) => void;
  /** Changes when suggestions were decided on elsewhere on the page. */
  refreshKey?: unknown;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [state, setState] = useState<SlideSuggestState | null>(null);
  const [scope, setScope] = useState<"unannotated" | "all">("unannotated");
  const [busy, setBusy] = useState(false);
  const last = useRef("");

  function accept(next: SlideSuggestState) {
    setState(next);
    const key = `${next.pending}:${next.job?.status}`;
    if (key !== last.current) {
      last.current = key;
      onChanged?.(next);
    }
  }

  const running = state?.job?.status === "running";
  useEffect(() => {
    let stale = false;
    const load = () => getSlideSuggestState(slideId).then((s) => !stale && accept(s)).catch(() => undefined);
    void load();
    if (!running) return () => void (stale = true);
    const timer = window.setInterval(load, 1500);
    return () => {
      stale = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideId, running, refreshKey]);

  async function run(action: () => Promise<SlideSuggestState>, failed: string) {
    setBusy(true);
    try {
      accept(await action());
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : failed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function next() {
    try {
      const { patch_id } = await nextPatchWithSuggestions(slideId, currentPatchIndex);
      if (patch_id === null) pushToast("No patch has suggestions waiting.", "info");
      else onOpenPatch(patch_id);
    } catch {
      pushToast("Could not find the next patch", "error");
    }
  }

  if (!state) return null;
  const job = state.job;
  return (
    <div className="flex flex-col gap-1.5 rounded bg-surface-container-low p-space-sm text-body-sm" data-testid="slide-suggest">
      <div className="text-label-md text-on-surface-variant">Whole slide</div>
      {running && job ? (
        <>
          <div className="flex items-center justify-between gap-space-sm">
            <span>
              Looking at patch {Math.min(job.done + 1, job.total)} of {job.total} · {job.found} found
            </span>
            <button className="text-primary text-label-md hover:underline" disabled={busy} onClick={() => void run(() => stopSlideSuggest(slideId), "Could not stop")}>
              Stop
            </button>
          </div>
          <div className="h-1.5 rounded-full bg-surface-container-high overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done}>
            <div className="h-full bg-primary" style={{ width: `${(job.done / Math.max(job.total, 1)) * 100}%` }} />
          </div>
        </>
      ) : (
        <div className="flex items-center gap-space-sm flex-wrap">
          <select className="input !w-auto !py-0.5 min-w-0 flex-1" aria-label="Which patches" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
            <option value="unannotated">Patches not annotated yet</option>
            <option value="all">Every patch</option>
          </select>
          <button
            className="px-space-sm h-7 rounded bg-surface-container-high text-label-md hover:bg-surface-container-highest disabled:opacity-40"
            disabled={busy || modelId === null || online === false}
            title={online === false ? "The trainer is not running" : "Have the model go through the slide in the background"}
            onClick={() => modelId !== null && void run(() => startSlideSuggest(slideId, modelId, scope), "Could not start")}
          >
            Suggest on all
          </button>
        </div>
      )}
      {job?.status === "failed" && <div className="text-error">It stopped: {job.error}</div>}
      {job?.status === "stopped" && <div className="text-on-surface-variant">Stopped after {job.done} of {job.total} patches.</div>}
      {state.pending > 0 ? (
        <div className="flex items-center justify-between gap-space-sm">
          <span>
            {state.pending.toLocaleString()} waiting on {state.patches.toLocaleString()} patch{state.patches === 1 ? "" : "es"}
          </span>
          <button className="text-primary text-label-md hover:underline whitespace-nowrap" onClick={() => void next()}>
            Next patch with suggestions
          </button>
        </div>
      ) : (
        job?.status === "done" && <div className="text-on-surface-variant">Finished: nothing new found.</div>
      )}
    </div>
  );
}
