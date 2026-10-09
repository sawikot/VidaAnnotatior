import { useState } from "react";

export interface Series {
  key: string;
  label: string;
  color: string;
}

// Categorical slots 1-3 of the chart palette, in fixed order (never reassigned by rank).
export const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"];

const W = 520;
const H = 200;
const PAD = { left: 44, right: 16, top: 12, bottom: 26 };

/**
 * One measure per epoch as lines on a single y-axis. Hovering shows that epoch's values; with two
 * or more lines a legend names them, so no line is told apart by colour alone.
 */
export function MetricChart({
  title,
  rows,
  series,
  yMax,
  format = (v) => v.toFixed(3),
}: {
  title: string;
  /** One entry per epoch, each with `epoch` and the series' keys. */
  rows: Record<string, number>[];
  series: Series[];
  /** Fixed top of the axis (e.g. 1 for scores); left out, the data decides. */
  yMax?: number;
  format?: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = series.filter((s) => rows.some((r) => typeof r[s.key] === "number"));
  if (rows.length === 0 || shown.length === 0) {
    return (
      <figure className="flex flex-col gap-1">
        <figcaption className="text-label-md text-on-surface-variant">{title}</figcaption>
        <div className="h-[200px] rounded bg-surface-container-low flex items-center justify-center text-body-sm text-on-surface-variant">
          Appears after the first epoch
        </div>
      </figure>
    );
  }

  const values = rows.flatMap((r) => shown.map((s) => r[s.key]).filter((v): v is number => typeof v === "number"));
  const top = yMax ?? (Math.max(...values) || 1) * 1.08;
  const lastEpoch = Math.max(rows[rows.length - 1].epoch, 2);
  const x = (epoch: number) => PAD.left + ((epoch - 1) / (lastEpoch - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - Math.min(v, top) / top) * (H - PAD.top - PAD.bottom);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * top);
  const at = hover === null ? null : rows[hover];

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    rows.forEach((r, i) => {
      if (Math.abs(x(r.epoch) - px) < Math.abs(x(rows[best].epoch) - px)) best = i;
    });
    setHover(best);
  }

  return (
    <figure className="flex flex-col gap-1 min-w-0">
      <figcaption className="flex items-center gap-space-md flex-wrap text-label-md text-on-surface-variant">
        <span>{title}</span>
        {shown.length > 1 &&
          shown.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1 text-label-sm">
              <span className="w-3 h-0.5 rounded" style={{ backgroundColor: s.color }} />
              {s.label}
            </span>
          ))}
      </figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={title} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="currentColor" className="text-outline-variant" strokeWidth={0.5} />
              <text x={PAD.left - 6} y={y(t)} textAnchor="end" dominantBaseline="central" className="fill-on-surface-variant" fontSize={11}>
                {format(t)}
              </text>
            </g>
          ))}
          <text x={PAD.left} y={H - 6} className="fill-on-surface-variant" fontSize={11}>
            Epoch 1
          </text>
          <text x={W - PAD.right} y={H - 6} textAnchor="end" className="fill-on-surface-variant" fontSize={11}>
            {rows[rows.length - 1].epoch}
          </text>
          {shown.map((s) => {
            const points = rows.filter((r) => typeof r[s.key] === "number");
            return (
              <g key={s.key}>
                <polyline
                  fill="none"
                  stroke={s.color}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  points={points.map((r) => `${x(r.epoch)},${y(r[s.key])}`).join(" ")}
                />
                {points.length === 1 && <circle cx={x(points[0].epoch)} cy={y(points[0][s.key])} r={4} fill={s.color} />}
              </g>
            );
          })}
          {at && (
            <g>
              <line x1={x(at.epoch)} x2={x(at.epoch)} y1={PAD.top} y2={H - PAD.bottom} stroke="currentColor" className="text-on-surface-variant" strokeWidth={0.5} />
              {shown.map((s) =>
                typeof at[s.key] === "number" ? <circle key={s.key} cx={x(at.epoch)} cy={y(at[s.key])} r={4} fill={s.color} stroke="#fff" strokeWidth={2} /> : null,
              )}
            </g>
          )}
        </svg>
        {at && (
          <div
            className="absolute top-1 pointer-events-none rounded bg-surface-container-lowest shadow-md border border-outline-variant px-space-sm py-1 text-label-sm"
            style={x(at.epoch) > W / 2 ? { right: `${100 - (x(at.epoch) / W) * 100 + 2}%` } : { left: `${(x(at.epoch) / W) * 100 + 2}%` }}
          >
            <div className="text-on-surface-variant">Epoch {at.epoch}</div>
            {shown.map((s) =>
              typeof at[s.key] === "number" ? (
                <div key={s.key} className="flex items-center gap-1.5 whitespace-nowrap text-on-surface">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} />
                  {s.label}
                  <span className="font-mono ml-auto pl-space-sm">{format(at[s.key])}</span>
                </div>
              ) : null,
            )}
          </div>
        )}
      </div>
    </figure>
  );
}
