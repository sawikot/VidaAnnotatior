import { useEffect, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button } from "../../components/primitives";
import { ApiError, acceptSlideSuggestions, clearSlideSuggestions, listSlideSuggestions } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { Suggestion } from "../../types/api";
import { SlideSuggestControls } from "./SlideSuggestControls";
import { useSuggestModels } from "./useSuggestModels";

/**
 * AI suggestions on the whole slide: run a model over its patches in the background, see everything
 * it proposes drawn on the slide (via `onShown`), accept or clear it in bulk, or go through it patch
 * by patch. Each suggestion belongs to the patch it was found in; accepted, it is that patch's annotation.
 */
export function WsiSuggestionsPanel({
  projectId,
  slideId,
  onShown,
  onAccepted,
  onOpenPatch,
}: {
  projectId: number;
  slideId: number;
  onShown: (shown: Suggestion[]) => void;
  onAccepted: () => void;
  onOpenPatch: (patchId: number) => void;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const { models, modelId, setModelId, online } = useSuggestModels(projectId);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [minScore, setMinScore] = useState(0.5);
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState(0);

  const reload = () => listSlideSuggestions(slideId).then(setSuggestions).catch(() => undefined);
  useEffect(() => {
    setSuggestions([]);
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideId]);

  const shown = suggestions.filter((s) => s.score >= minScore);
  const shownKey = shown.map((s) => s.id).join(",");
  useEffect(() => {
    onShown(shown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownKey]);
  useEffect(() => () => onShown([]), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!models || models.length === 0) return null;

  async function run(action: () => Promise<unknown>, after: (result: unknown) => void, failed: string) {
    setBusy(true);
    try {
      after(await action());
      await reload();
      setDecided((n) => n + 1);
    } catch (e) {
      pushToast(e instanceof ApiError ? e.message : failed, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="wsi-suggestions-panel" className="flex flex-col gap-space-sm">
      <div>
        <div className="flex items-center gap-1 text-label-md text-on-surface-variant mb-1">
          <MaterialIcon name="auto_awesome" className="!text-[16px]" />
          AI suggestions
        </div>
        <select className="input" aria-label="Model" value={modelId ?? ""} onChange={(e) => setModelId(Number(e.target.value))}>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        {online === false && <div className="text-label-sm text-on-surface-variant mt-1">The trainer is not running, so models cannot make suggestions right now.</div>}
      </div>

      <SlideSuggestControls slideId={slideId} modelId={modelId} online={online} onOpenPatch={onOpenPatch} onChanged={() => void reload()} refreshKey={decided} />

      {suggestions.length > 0 && (
        <>
          <label className="flex items-center gap-space-sm text-label-sm text-on-surface-variant">
            <span className="whitespace-nowrap">At least {Math.round(minScore * 100)}% sure</span>
            <input type="range" min={5} max={95} step={5} value={Math.round(minScore * 100)} onChange={(e) => setMinScore(Number(e.target.value) / 100)} className="flex-1 min-w-0" aria-label="Minimum confidence" />
          </label>
          <div className="text-body-sm text-on-surface-variant">
            {shown.length.toLocaleString()} of {suggestions.length.toLocaleString()} shown on the slide, dashed. To decide one by one, go through them patch by patch.
          </div>
          <div className="flex items-center gap-space-sm">
            <Button
              disabled={busy || shown.length === 0}
              onClick={() => {
                if (!window.confirm(`Accept all ${shown.length} suggestions that are at least ${Math.round(minScore * 100)}% sure? Each becomes an annotation.`)) return;
                void run(
                  () => acceptSlideSuggestions(slideId, minScore),
                  (result) => {
                    pushToast(`Accepted ${(result as { accepted: number }).accepted} suggestions`, "success");
                    onAccepted();
                  },
                  "Could not accept the suggestions",
                );
              }}
            >
              Accept {shown.length.toLocaleString()} shown
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => window.confirm("Clear every waiting suggestion on this slide?") && void run(() => clearSlideSuggestions(slideId), () => undefined, "Could not clear the suggestions")}>
              Clear all
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
