import { useState } from "react";
import type { ResultFigure } from "../../types/api";

type Curve = Extract<ResultFigure, { type: "curve" }>;
type Matrix = Extract<ResultFigure, { type: "matrix" }>;
type Bars = Extract<ResultFigure, { type: "bars" }>;

// Categorical slots of the chart palette, in fixed order; the first three are the epoch charts' own.
const COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#8e5bd0", "#d4a017", "#d6457a", "#3aa7b8", "#7a8a3a", "#8a6a55"];
const OVERALL = "#334155"; // "All classes": not one of the classes, so not one of their colours
const CELL = "0, 97, 148"; // the primary colour, as shading

const percent = (v: number) => `${(v * 100).toFixed(1)}%`;
const whole = (v: number) => `${Math.round(v * 100)}%`;

/** One figure of a run's results, drawn from the numbers the recipe gave. */
export function ResultFigureView({ figure }: { figure: ResultFigure }) {
  return (
    <figure className="flex flex-col gap-1 min-w-0 rounded-xl bg-surface-container-lowest p-space-md shadow-sm" data-testid={`figure-${figure.type}`}>
      <figcaption className="font-headline-sm text-label-lg">{figure.title}</figcaption>
      {figure.type === "curve" ? <CurveFigure figure={figure} /> : figure.type === "matrix" ? <MatrixFigure figure={figure} /> : <BarsFigure figure={figure} />}
      {figure.help && <p className="text-label-sm text-on-surface-variant">{figure.help}</p>}
    </figure>
  );
}

const W = 520;
const H = 300;
const PAD = { left: 46, right: 14, top: 12, bottom: 40 };

function CurveFigure({ figure }: { figure: Curve }) {
  const [hover, setHover] = useState<number | null>(null);
  const series = figure.series.filter((s) => s.points.length > 0);
  if (series.length === 0) return <Nothing />;
  const colorOf = (label: string, i: number) => (label === "All classes" ? OVERALL : COLORS[(series[0].label === "All classes" ? i - 1 : i) % COLORS.length]);
  const top = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p[1])));
  const right = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p[0])));
  const x = (v: number) => PAD.left + (v / right) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - v / top) * (H - PAD.top - PAD.bottom);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  // What each line reads at the hovered place: its point nearest to it.
  const at = (points: [number, number][], where: number) => points.reduce((best, p) => (Math.abs(p[0] - where) < Math.abs(best[0] - where) ? p : best), points[0]);

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    setHover(Math.min(right, Math.max(0, ((px - PAD.left) / (W - PAD.left - PAD.right)) * right)));
  }

  return (
    <>
      <div className="flex items-center gap-x-space-md gap-y-0.5 flex-wrap text-label-sm text-on-surface-variant">
        {series.map((s, i) => (
          <span key={s.label} className="inline-flex items-center gap-1">
            <span className="w-3 h-0.5 rounded" style={{ backgroundColor: colorOf(s.label, i) }} />
            {s.label}
          </span>
        ))}
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={figure.title} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(t * top)} y2={y(t * top)} stroke="currentColor" className="text-outline-variant" strokeWidth={0.5} />
              <text x={PAD.left - 6} y={y(t * top)} textAnchor="end" dominantBaseline="central" className="fill-on-surface-variant" fontSize={11}>
                {whole(t * top)}
              </text>
              <text x={x(t * right)} y={H - PAD.bottom + 14} textAnchor="middle" className="fill-on-surface-variant" fontSize={11}>
                {whole(t * right)}
              </text>
            </g>
          ))}
          <text x={(PAD.left + W - PAD.right) / 2} y={H - 6} textAnchor="middle" className="fill-on-surface-variant" fontSize={11}>
            {figure.x}
          </text>
          <text transform={`translate(11 ${(PAD.top + H - PAD.bottom) / 2}) rotate(-90)`} textAnchor="middle" className="fill-on-surface-variant" fontSize={11}>
            {figure.y}
          </text>
          {figure.diagonal && <line x1={x(0)} y1={y(0)} x2={x(right)} y2={y(top)} stroke="currentColor" className="text-on-surface-variant" strokeWidth={1} strokeDasharray="4 4" />}
          {figure.mark && (
            <g>
              <line x1={x(figure.mark.x)} x2={x(figure.mark.x)} y1={PAD.top} y2={H - PAD.bottom} stroke={OVERALL} strokeWidth={1} strokeDasharray="4 3" />
              <text x={x(figure.mark.x) + (figure.mark.x > right * 0.7 ? -5 : 5)} y={PAD.top + 9} textAnchor={figure.mark.x > right * 0.7 ? "end" : "start"} className="fill-on-surface" fontSize={11}>
                {figure.mark.label} · {figure.mark.x.toFixed(figure.mark.x < 0.1 ? 3 : 2)}
              </text>
            </g>
          )}
          {series.map((s, i) => (
            <polyline
              key={s.label}
              fill="none"
              stroke={colorOf(s.label, i)}
              strokeWidth={s.label === "All classes" ? 2.5 : 1.75}
              strokeLinejoin="round"
              strokeLinecap="round"
              points={s.points.map((p) => `${x(p[0])},${y(p[1])}`).join(" ")}
            />
          ))}
          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} stroke="currentColor" className="text-on-surface-variant" strokeWidth={0.5} />
              {series.map((s, i) => {
                const p = at(s.points, hover);
                return <circle key={s.label} cx={x(p[0])} cy={y(p[1])} r={3.5} fill={colorOf(s.label, i)} stroke="#fff" strokeWidth={1.5} />;
              })}
            </g>
          )}
        </svg>
        {hover !== null && (
          <div
            className="absolute top-1 pointer-events-none rounded bg-surface-container-lowest shadow-md border border-outline-variant px-space-sm py-1 text-label-sm"
            style={x(hover) > W / 2 ? { right: `${100 - (x(hover) / W) * 100 + 2}%` } : { left: `${(x(hover) / W) * 100 + 2}%` }}
          >
            <div className="text-on-surface-variant">
              {figure.x.split(" (")[0]} {hover.toFixed(2)}
            </div>
            {series.map((s, i) => (
              <div key={s.label} className="flex items-center gap-1.5 whitespace-nowrap text-on-surface">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: colorOf(s.label, i) }} />
                {s.label}
                <span className="font-mono ml-auto pl-space-sm">{percent(at(s.points, hover)[1])}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/** A table of counts (or shares of each row), each cell shaded by its share of its row. */
function MatrixFigure({ figure }: { figure: Matrix }) {
  const shares = figure.format === "percent";
  if (figure.values.length === 0) return <Nothing />;
  return (
    <div className="overflow-auto">
      <table className="text-label-sm border-separate border-spacing-0.5">
        <thead>
          <tr>
            <th />
            <th colSpan={figure.column_labels.length} className="text-center font-medium text-on-surface-variant pb-0.5">
              {figure.columns} →
            </th>
          </tr>
          <tr>
            <th className="text-left font-medium text-on-surface-variant pr-space-sm align-bottom">{figure.rows} ↓</th>
            {figure.column_labels.map((label) => (
              <th key={label} className="font-medium px-1 max-w-[6rem] truncate align-bottom" title={label}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {figure.values.map((row, r) => {
            const total = shares ? 1 : row.reduce((sum, v) => sum + v, 0);
            return (
              <tr key={r}>
                <th className="text-left font-medium pr-space-sm max-w-[9rem] truncate" title={figure.row_labels[r]}>
                  {figure.row_labels[r]}
                </th>
                {row.map((value, c) => {
                  const share = total > 0 ? value / total : 0;
                  return (
                    <td
                      key={c}
                      className={`text-center font-mono px-2 py-1.5 min-w-[3.25rem] rounded ${share > 0.55 ? "text-white" : "text-on-surface"}`}
                      style={{ backgroundColor: `rgba(${CELL}, ${(share * 0.9).toFixed(3)})` }}
                      title={`${figure.rows} ${figure.row_labels[r]}, ${figure.columns.toLowerCase()} ${figure.column_labels[c]}: ${shares ? percent(value) : `${value.toLocaleString()} (${percent(share)} of the row)`}`}
                    >
                      {shares ? whole(value) : value.toLocaleString()}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A group of bars per class, one bar per measure, all on the same 0-100% scale. */
function BarsFigure({ figure }: { figure: Bars }) {
  if (figure.labels.length === 0) return <Nothing />;
  return (
    <>
      <div className="flex items-center gap-x-space-md gap-y-0.5 flex-wrap text-label-sm text-on-surface-variant">
        {figure.series.map((s, i) => (
          <span key={s.label} className="inline-flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
            {s.label}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-space-sm max-h-80 overflow-y-auto pr-1">
        {figure.labels.map((label, k) => (
          <div key={label} className="grid grid-cols-[7rem_1fr] gap-space-sm items-center">
            <span className="text-label-sm truncate" title={label}>
              {label}
            </span>
            <div className="flex flex-col gap-0.5">
              {figure.series.map((s, i) => (
                <div key={s.label} className="flex items-center gap-1.5" title={`${label} · ${s.label}: ${percent(s.values[k] ?? 0)}`}>
                  <div className="flex-1 h-2.5 rounded-sm bg-surface-container-high overflow-hidden">
                    <div className="h-full rounded-sm" style={{ width: `${Math.min(100, (s.values[k] ?? 0) * 100)}%`, backgroundColor: COLORS[i % COLORS.length] }} />
                  </div>
                  <span className="font-mono text-label-sm w-12 text-right">{whole(s.values[k] ?? 0)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Nothing() {
  return <div className="h-24 rounded bg-surface-container-low flex items-center justify-center text-body-sm text-on-surface-variant">Nothing to draw: this set had nothing to score.</div>;
}
