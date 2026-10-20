import type { SplitMode, SplitName, SplitSettings, SplitUnit } from "../../types/api";

export const SPLIT_NAMES: SplitName[] = ["train", "val", "test"];
export const SPLIT_LABELS: Record<SplitName, string> = { train: "Train", val: "Validation", test: "Test" };
export const DEFAULT_SPLIT: SplitSettings = { mode: "off", train: 70, val: 15, test: 15, unit: "slide" };

/** What a split deals out; splits from before the choice are by slide. */
export const unitOf = (s: Pick<SplitSettings, "mode" | "unit">): SplitUnit => (s.mode === "random" && s.unit === "patch" ? "patch" : "slide");

const UNITS: { id: SplitUnit; title: string; body: string }[] = [
  { id: "slide", title: "By slide", body: "Each slide goes to one set with all of its patches" },
  { id: "patch", title: "By patch", body: "Each slide's patches are dealt out between the sets -- for one slide, or too few to split" },
];

const MODES: { id: SplitMode; title: string; body: (nouns: string) => string }[] = [
  { id: "off", title: "No split", body: () => "Everything is exported as one dataset" },
  { id: "random", title: "Random", body: (nouns) => `${nouns} are dealt out by chance to match the shares` },
  { id: "manual", title: "Manual", body: (nouns) => `You choose the set of each of the ${nouns.toLowerCase()}` },
];

/** Why these shares can't be used for a random split, or null. Mirrors backend/app/services/dataset_split.py. */
export function sharesProblem(s: SplitSettings): string | null {
  if (s.mode !== "random") return null;
  if (SPLIT_NAMES.some((n) => !Number.isInteger(s[n]) || s[n] < 0)) return "Shares are whole percentages, 0 or more.";
  const total = s.train + s.val + s.test;
  if (total !== 100) return `The shares add up to ${total}%; they must add up to 100%.`;
  if (s.train === 0) return "The training set needs a share above 0%.";
  return null;
}

/**
 * How a dataset is split into train / validation / test: not at all, at random by shares, or by hand.
 * Shared by the new-project wizard (no slides yet) and the split panel.
 */
export function SplitFields({
  value,
  onChange,
  nouns,
  disabled = false,
  hideOff = false,
  allowPatch = false,
}: {
  value: SplitSettings;
  onChange: (next: SplitSettings) => void;
  /** "Slides" or "Images": what is assigned to a set. */
  nouns: string;
  disabled?: boolean;
  /** Leave out "No split", where a split is needed (training). */
  hideOff?: boolean;
  /** Offer splitting each slide's patches, not only whole slides (a slide project; images have no patches to split). */
  allowPatch?: boolean;
}) {
  const problem = sharesProblem(value);
  const unit = unitOf(value);
  return (
    <div className="flex flex-col gap-space-md">
      <div role="radiogroup" aria-label="How to split" className={`grid gap-space-sm ${hideOff ? "sm:grid-cols-2" : "sm:grid-cols-3"}`}>
        {MODES.filter((m) => !(hideOff && m.id === "off")).map((m) => {
          const selected = value.mode === m.id;
          return (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              // Patches are only dealt at random: assigning by hand is slide by slide.
              onClick={() => onChange({ ...value, mode: m.id, unit: m.id === "random" ? value.unit : "slide" })}
              className={`text-left p-space-md rounded-xl bg-surface-container-lowest shadow-sm transition-shadow disabled:cursor-not-allowed ${
                selected ? "ring-2 ring-primary" : "hover:shadow-md disabled:opacity-60"
              }`}
            >
              <span className="block font-headline-sm text-headline-sm">{m.title}</span>
              <span className="block text-body-sm text-on-surface-variant">{m.body(nouns)}</span>
            </button>
          );
        })}
      </div>

      {value.mode === "random" && allowPatch && (
        <div className="flex flex-col gap-space-sm">
          <span className="text-label-md text-on-surface-variant">Split by</span>
          <div role="radiogroup" aria-label="What to split" className="grid gap-space-sm sm:grid-cols-2">
            {UNITS.map((u) => {
              const selected = unit === u.id;
              return (
                <button
                  key={u.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={disabled}
                  onClick={() => onChange({ ...value, unit: u.id })}
                  className={`text-left px-space-md py-space-sm rounded-xl bg-surface-container-lowest shadow-sm transition-shadow disabled:cursor-not-allowed ${
                    selected ? "ring-2 ring-primary" : "hover:shadow-md disabled:opacity-60"
                  }`}
                >
                  <span className="block font-headline-sm text-label-lg">{u.title}</span>
                  <span className="block text-body-sm text-on-surface-variant">{u.body}</span>
                </button>
              );
            })}
          </div>
          {unit === "patch" && (
            <p className="text-body-sm text-on-surface-variant max-w-3xl" role="note">
              Patches of the same slide then end up in both training and validation / test, so the scores will look better than
              they would on a new slide -- more so with overlapping patches. Split by slide once there are enough slides.
            </p>
          )}
        </div>
      )}

      {value.mode === "random" && (
        <div className="flex flex-col gap-space-sm">
          <div className="flex items-end gap-space-md flex-wrap">
            {SPLIT_NAMES.map((name) => (
              <label key={name} className="flex flex-col gap-1">
                <span className="text-label-md text-on-surface-variant">{SPLIT_LABELS[name]} %</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  className="input !w-24"
                  disabled={disabled}
                  value={Number.isFinite(value[name]) ? value[name] : ""}
                  onChange={(e) => onChange({ ...value, [name]: e.target.value === "" ? NaN : Number(e.target.value) })}
                />
              </label>
            ))}
          </div>
          {problem && (
            <p className="text-body-sm text-error" role="alert">
              {problem}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
