import type { RunUsage } from "../../types/api";

const gb = (mb: number) => `${(mb / 1024).toFixed(1)} GB`;

/** What the machine is using while a run trains: its graphics cards, processor and memory -- whatever the trainer could measure. */
export function RunUsagePanel({ usage }: { usage: RunUsage }) {
  const gpus = usage.gpus ?? [];
  const meters: { label: string; value: string; pct: number | null; note?: string }[] = [];
  for (const g of gpus) {
    const name = gpus.length > 1 ? `GPU ${g.index}` : "GPU";
    if (g.memory_used_mb != null) {
      meters.push({
        label: `${name} memory`,
        value: `${gb(g.memory_used_mb)} of ${gb(g.memory_total_mb)}`,
        pct: (g.memory_used_mb / g.memory_total_mb) * 100,
      });
    }
    if (g.load_pct != null) {
      meters.push({ label: `${name} load`, value: `${Math.round(g.load_pct)}%`, pct: g.load_pct, note: g.temperature_c != null ? `${Math.round(g.temperature_c)} °C` : undefined });
    }
  }
  if (usage.cpu_pct != null) meters.push({ label: "CPU", value: `${Math.round(usage.cpu_pct)}%`, pct: usage.cpu_pct });
  if (usage.ram_used_mb != null && usage.ram_total_mb) {
    meters.push({
      label: "Memory (RAM)",
      value: `${gb(usage.ram_used_mb)} of ${gb(usage.ram_total_mb)}`,
      pct: (usage.ram_used_mb / usage.ram_total_mb) * 100,
      note: usage.run_ram_mb != null ? `this run: ${gb(usage.run_ram_mb)}` : undefined,
    });
  }
  if (meters.length === 0) return null;

  return (
    <div data-testid="run-usage">
      <div className="text-label-md text-on-surface-variant mb-1">Using now</div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-space-sm">
        {meters.map((m) => (
          <div key={m.label} className="rounded bg-surface-container-low p-space-sm min-w-0">
            <div className="text-label-sm text-on-surface-variant">{m.label}</div>
            <div className="font-mono text-label-lg truncate" title={m.value}>
              {m.value}
            </div>
            {m.pct != null && (
              <div className="h-1.5 rounded-full bg-surface-container-high overflow-hidden mt-1" role="meter" aria-label={m.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(m.pct)}>
                <div
                  className={`h-full rounded-full ${m.pct >= 95 ? "bg-error" : m.pct >= 85 ? "bg-amber-500" : "bg-primary"}`}
                  style={{ width: `${Math.min(100, Math.max(0, m.pct))}%` }}
                />
              </div>
            )}
            {m.note && <div className="text-label-sm text-on-surface-variant mt-1">{m.note}</div>}
          </div>
        ))}
      </div>
      {gpus.length > 0 && <p className="text-label-sm text-on-surface-variant mt-1">The graphics card's figures count everything on it, not only this run.</p>}
    </div>
  );
}
