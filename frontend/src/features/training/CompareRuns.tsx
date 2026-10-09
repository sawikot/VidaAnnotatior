import { useEffect, useState } from "react";
import { getTrainingRun } from "../../services/api";
import type { TrainingRun } from "../../types/api";
import { MetricChart, SERIES_COLORS } from "./MetricChart";

export const METRIC_NAMES: Record<string, string> = { ap50: "AP50", accuracy: "Accuracy", miou: "Mean IoU" };
/** At most this many runs side by side: each needs a colour of its own on the chart. */
export const MAX_COMPARED = SERIES_COLORS.length;

const percent = (v: number | undefined) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "--");

function minutes(run: TrainingRun): string {
  if (!run.started_at || !run.finished_at) return "--";
  const m = (new Date(`${run.finished_at}Z`).getTime() - new Date(`${run.started_at}Z`).getTime()) / 60000;
  return m < 1 ? "under 1 min" : `${Math.round(m)} min`;
}

/**
 * Finished runs side by side: what each was trained with and on, its scores, and its validation
 * score epoch by epoch on one chart. Only runs of the same task are comparable -- they share a score.
 */
export function CompareRuns({ runs }: { runs: TrainingRun[] }) {
  const [full, setFull] = useState<Record<number, TrainingRun>>({}); // with their per-epoch numbers
  const ids = runs.map((r) => r.id).join(",");
  useEffect(() => {
    let stale = false;
    for (const run of runs) {
      if (!full[run.id]) getTrainingRun(run.id).then((r) => !stale && setFull((f) => ({ ...f, [r.id]: r }))).catch(() => undefined);
    }
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids]);

  const metric = runs[0]?.result?.primary_metric ?? "ap50";
  const metricName = METRIC_NAMES[metric] ?? metric;
  const mixed = runs.some((r) => r.task !== runs[0].task);
  const settingKeys = [...new Set(runs.flatMap((r) => Object.keys(r.settings)))];
  const differing = new Set(settingKeys.filter((k) => new Set(runs.map((r) => String(r.settings[k]))).size > 1));

  // One row per epoch, one column per run: the validation score each had reached by then.
  const epochs = Math.max(0, ...runs.map((r) => full[r.id]?.metrics?.length ?? 0));
  const rows = Array.from({ length: epochs }, (_, i) => {
    const row: Record<string, number> = { epoch: i + 1 };
    for (const run of runs) {
      const value = full[run.id]?.metrics?.[i]?.[`val_${metric}`];
      if (typeof value === "number") row[`run${run.id}`] = value;
    }
    return row;
  });
  const best = Math.max(...runs.map((r) => r.result?.val?.[metric as "ap50"] ?? -1));

  return (
    <div className="flex flex-col gap-space-md" data-testid="compare-runs">
      {mixed && <p className="text-body-sm text-error">These runs train different kinds of model, so their scores do not measure the same thing.</p>}
      <div className="overflow-auto">
        <table className="w-full text-body-sm">
          <thead className="text-label-sm text-on-surface-variant">
            <tr>
              <th className="text-left py-1 font-medium" />
              {runs.map((r, i) => (
                <th key={r.id} className="text-right py-1 pl-space-md font-medium whitespace-nowrap">
                  <span className="inline-block w-3 h-0.5 rounded align-middle mr-1" style={{ backgroundColor: SERIES_COLORS[i] }} />
                  Run #{r.id}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <Row label="Model" values={runs.map((r) => r.recipe_name)} />
            <Row label={`Validation ${metricName}`} values={runs.map((r) => percent(r.result?.val?.[metric as "ap50"]))} strong={runs.map((r) => (r.result?.val?.[metric as "ap50"] ?? -2) === best)} />
            <Row label={`Test ${metricName}`} values={runs.map((r) => percent(r.result?.test?.[metric as "ap50"]))} />
            <Row label="Best epoch" values={runs.map((r) => (r.result?.best_epoch ? `${r.result.best_epoch} of ${r.epochs}` : "--"))} />
            <Row label="Training images" values={runs.map((r) => (r.dataset?.sets.train?.images ?? 0).toLocaleString())} />
            <Row label="Training time" values={runs.map(minutes)} />
            {settingKeys.map((k) => (
              <Row key={k} label={k.replace(/_/g, " ")} values={runs.map((r) => String(r.settings[k] ?? "--"))} muted={!differing.has(k)} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-label-sm text-on-surface-variant">Settings that are the same in every run are greyed. The best validation score is in bold.</p>
      <div className="max-w-2xl">
        <MetricChart
          title={`Validation ${metricName} by epoch`}
          rows={rows}
          series={runs.map((r, i) => ({ key: `run${r.id}`, label: `Run #${r.id}`, color: SERIES_COLORS[i] }))}
          yMax={1}
          format={(v) => `${Math.round(v * 100)}%`}
        />
      </div>
    </div>
  );
}

function Row({ label, values, strong, muted }: { label: string; values: string[]; strong?: boolean[]; muted?: boolean }) {
  return (
    <tr className={`border-t border-outline-variant/40 ${muted ? "text-on-surface-variant" : ""}`}>
      <td className="py-1.5 capitalize">{label}</td>
      {values.map((v, i) => (
        <td key={i} className={`py-1.5 pl-space-md text-right font-mono ${strong?.[i] ? "font-bold" : ""}`}>
          {v}
        </td>
      ))}
    </tr>
  );
}
