import { useState } from "react";
import type { TrainingRun, TrainingScores } from "../../types/api";
import { METRIC_NAMES } from "./CompareRuns";
import { ResultFigureView } from "./ResultFigures";

type SetName = "val" | "test";
type Result = NonNullable<TrainingRun["result"]>;

const SET_NAMES: Record<SetName, string> = { val: "Validation", test: "Test" };
// The scores a recipe may give, in the order shown, with what each means. Others are shown by their own name.
const SCORES: [key: string, label: string, help: string][] = [
  ["ap50", "AP50", "Average precision counting an object as found when the box overlaps it by at least half. One number for the whole precision-recall curve."],
  ["ap75", "AP75", "The same, but the box must overlap the object by three quarters: how exactly it is placed."],
  ["ap", "AP50-95", "Average precision averaged over overlaps from a half to nearly exact. The strictest of the three."],
  ["accuracy", "Accuracy", "The share of images given their right class."],
  ["top5_accuracy", "Top-5 accuracy", "The share of images whose right class is among the model's five surest."],
  ["balanced_accuracy", "Balanced accuracy", "Recall averaged over the classes, so a rare class weighs as much as a common one."],
  ["miou", "Mean IoU", "For each class, the area both marked and drawn divided by the area either covers; averaged over the classes, background left out."],
  ["dice", "Dice", "Like IoU but more forgiving: twice the shared area divided by the marked plus the drawn area."],
  ["pixel_accuracy", "Pixel accuracy", "The share of all pixels given their right class, background included; high whenever most of the image is background."],
  ["precision", "Precision", "Of what the model marked, the share that is right. Low precision means false alarms."],
  ["recall", "Recall", "Of what is really there, the share the model found. Low recall means misses."],
  ["f1", "F1", "Precision and recall in one number; high only when both are."],
  ["auc", "AUC", "Area under the ROC curve: how well the model's sureness tells a class from the rest. 50% is guessing, 100% perfect."],
  ["best_confidence", "Best confidence", "The confidence at which F1 is highest. Precision, recall and F1 are measured there; a good place for the workspace's confidence slider."],
];
const COUNTS: Record<string, string> = {
  images: "Images",
  objects: "Objects",
  found: "Found",
  missed: "Missed",
  false_alarms: "False alarms",
  right: "Right",
  wrong: "Wrong",
};
// Columns of the per-class table that are whole numbers, not shares.
const WHOLE = new Set(["objects", "images", "pixels", "found", "missed", "false_alarms"]);
const COLUMN_NAMES: Record<string, string> = { ...Object.fromEntries(SCORES.map(([key, label]) => [key, label])), iou: "IoU", area: "Share of the area", ...COUNTS };

const percent = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "--");
/** A confidence, with a third decimal where two would say nothing. */
export const level = (v: number) => v.toFixed(v < 0.1 ? 3 : 2);
const pretty = (key: string) => key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/**
 * A finished run's final scores: the test set's first (images the model never saw), the validation
 * set's beside them; every score the recipe gave, a table per class, and its figures.
 */
export function RunResults({ result }: { result: Result }) {
  const sets = (["test", "val"] as const).filter((name) => result[name]);
  const [chosen, setChosen] = useState<SetName | null>(null);
  if (sets.length === 0) return null;
  const set = chosen && sets.includes(chosen) ? chosen : sets[0];
  const scores = result[set] as TrainingScores;
  const primary = result.primary_metric ?? "ap50";
  const primaryName = METRIC_NAMES[primary] ?? COLUMN_NAMES[primary] ?? pretty(primary);

  const numbers = Object.entries(scores).filter((entry): entry is [string, number] => typeof entry[1] === "number");
  const known = SCORES.filter(([key]) => numbers.some(([k]) => k === key));
  const tiles = [
    ...known.map(([key, label, help]) => ({ key, label, help })),
    ...numbers.filter(([k]) => !SCORES.some(([key]) => key === k)).map(([key]) => ({ key, label: pretty(key), help: "" })),
  ];
  const rows = scores.classes ?? Object.entries(scores.per_class ?? {}).map(([name, value]) => ({ name, [primary]: value }));
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((key) => key !== "name");
  columns.sort((a, b) => order(a) - order(b));

  return (
    <div className="rounded-xl bg-surface-container-low p-space-md flex flex-col gap-space-md" data-testid="run-results">
      <div className="flex items-end gap-space-lg flex-wrap">
        {sets.map((name) => (
          <div key={name}>
            <div className="text-label-sm text-on-surface-variant">{name === "test" ? `Test ${primaryName} (final score)` : `Validation ${primaryName}`}</div>
            <div className="font-headline-lg text-headline-lg">{percent((result[name] as Record<string, unknown>)[primary])}</div>
          </div>
        ))}
        {result.best_epoch ? <div className="text-body-sm text-on-surface-variant pb-1">The kept model is from epoch {result.best_epoch}, the best on validation.</div> : null}
      </div>
      {!result.test && (
        <p className="text-body-sm text-on-surface-variant">
          There is no test score: the project&apos;s test set had nothing to train on. The validation set chose the best epoch, so its score flatters the model a
          little. Put slides into the test set (project settings) for a score on images it has never seen.
        </p>
      )}

      <div className="flex items-center gap-space-md flex-wrap">
        <h4 className="font-headline-sm text-headline-sm">{set === "test" ? "Test results" : "Validation results"}</h4>
        {sets.length > 1 && (
          <div role="group" aria-label="Which set's results" className="flex rounded-lg bg-surface-container-high p-0.5">
            {sets.map((name) => (
              <button
                key={name}
                type="button"
                aria-pressed={set === name}
                onClick={() => setChosen(name)}
                className={`px-space-md py-1 rounded-md text-label-md ${set === name ? "bg-surface-container-lowest shadow-sm text-primary" : "text-on-surface-variant"}`}
              >
                {SET_NAMES[name]}
              </button>
            ))}
          </div>
        )}
        <span className="text-label-sm text-on-surface-variant">
          {set === "test" ? "Measured once, on the test set, after training: images the model never saw." : "Measured on the validation set, which also chose the best epoch."}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-space-sm">
        {tiles.map((tile) => (
          <div key={tile.key} className={`rounded-lg p-space-sm bg-surface-container-lowest ${tile.key === primary ? "ring-1 ring-primary" : ""}`} title={tile.help}>
            <div className="text-label-sm text-on-surface-variant">{tile.label}</div>
            <div className="font-headline-sm text-headline-sm font-mono">{tile.key === "best_confidence" ? level(scores[tile.key] as number) : percent(scores[tile.key as keyof TrainingScores])}</div>
          </div>
        ))}
      </div>
      {scores.counts && (
        <div className="flex gap-x-space-lg gap-y-1 flex-wrap text-body-sm">
          {Object.entries(scores.counts).map(([key, value]) => (
            <span key={key}>
              <span className="text-on-surface-variant">{COUNTS[key] ?? pretty(key)}</span> <span className="font-mono">{value.toLocaleString()}</span>
            </span>
          ))}
        </div>
      )}

      {rows.length > 0 && columns.length > 0 && (
        <div className="overflow-auto">
          <table className="text-body-sm">
            <thead className="text-label-sm text-on-surface-variant">
              <tr>
                <th className="text-left pr-space-lg font-medium">Per class</th>
                {columns.map((key) => (
                  <th key={key} className="text-right pl-space-md font-medium whitespace-nowrap">
                    {COLUMN_NAMES[key] ?? pretty(key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row.name)} className="border-t border-outline-variant/40">
                  <td className="pr-space-lg py-1">{String(row.name)}</td>
                  {columns.map((key) => {
                    const value = (row as Record<string, number | string>)[key];
                    return (
                      <td key={key} className="text-right pl-space-md py-1 font-mono">
                        {typeof value !== "number" ? (value ?? "--") : WHOLE.has(key) ? value.toLocaleString() : percent(value)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {scores.figures && scores.figures.length > 0 && (
        <div className="grid lg:grid-cols-2 gap-space-md items-start">
          {scores.figures.map((figure) => (
            <ResultFigureView key={figure.title} figure={figure} />
          ))}
        </div>
      )}

      {known.length > 0 && (
        <details>
          <summary className="cursor-pointer text-label-md text-on-surface-variant">What these numbers mean</summary>
          <dl className="mt-space-sm grid sm:grid-cols-2 gap-x-space-lg gap-y-1 text-label-sm">
            {known.map(([key, label, help]) => (
              <div key={key}>
                <dt className="inline font-medium">{label}: </dt>
                <dd className="inline text-on-surface-variant">{help}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}

function order(key: string): number {
  const place = SCORES.findIndex(([k]) => k === key);
  return WHOLE.has(key) || key === "area" ? 100 : key === "iou" ? -1 : place === -1 ? 50 : place;
}
