import type { SplitMode, SplitName, SplitSettings } from "../../types/api";

export const SPLIT_NAMES: SplitName[] = ["train", "val", "test"];
export const SPLIT_LABELS: Record<SplitName, string> = { train: "Train", val: "Validation", test: "Test" };
export const DEFAULT_SPLIT: SplitSettings = { mode: "off", train: 70, val: 15, test: 15 };

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
}: {
  value: SplitSettings;
  onChange: (next: SplitSettings) => void;
  /** "Slides" or "Images": what is assigned to a set. */
  nouns: string;
  disabled?: boolean;
}) {
  const problem = sharesProblem(value);
  return (
    <div className="flex flex-col gap-space-md">
      <div role="radiogroup" aria-label="How to split" className="grid sm:grid-cols-3 gap-space-sm">
        {MODES.map((m) => {
          const selected = value.mode === m.id;
          return (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => onChange({ ...value, mode: m.id })}
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
