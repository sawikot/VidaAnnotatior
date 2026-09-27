import { Field, Toggle } from "../../components/formControls";
import type { GridSpec } from "../../types/api";

const SIZES = [256, 512, 1024, 2048];
const MAGNIFICATIONS = [5, 10, 20, 40];

interface Props {
  value: GridSpec;
  onChange: (next: GridSpec) => void;
}

/** Patch size, stride, magnification and tissue threshold -- with quick picks for the usual choices. */
export function GridForm({ value: g, onChange }: Props) {
  const set = (patch: Partial<GridSpec>) => onChange({ ...g, ...patch });
  const num = (key: keyof GridSpec) => (e: React.ChangeEvent<HTMLInputElement>) => set({ [key]: e.target.value === "" ? NaN : Number(e.target.value) } as Partial<GridSpec>);
  const square = g.patch_width === g.patch_height;
  const overlap = g.stride_x === g.patch_width && g.stride_y === g.patch_height ? "none" : g.stride_x * 2 === g.patch_width && g.stride_y * 2 === g.patch_height ? "half" : "custom";
  const pickSize = (s: number) => set({ patch_width: s, patch_height: s, stride_x: overlap === "half" ? s / 2 : s, stride_y: overlap === "half" ? s / 2 : s });
  // No tissue threshold means the grid covers the whole slide, glass included, up to its edges.
  const wholeSlide = g.min_tissue_fraction <= 0;

  return (
    <div className="flex flex-col gap-space-md">
      <div className="flex flex-wrap items-center gap-space-md">
        <div className="flex items-center gap-1">
          <span className="text-label-md text-on-surface-variant mr-1">Patch size</span>
          {SIZES.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => pickSize(s)}
              className={`h-8 px-space-sm rounded text-label-md font-mono ${square && g.patch_width === s ? "bg-primary text-on-primary" : "bg-surface-container-low hover:bg-surface-container"}`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-label-md text-on-surface-variant mr-1">Overlap</span>
          {(
            [
              ["none", "None", 1],
              ["half", "50%", 2],
            ] as const
          ).map(([id, label, div]) => (
            <button
              key={id}
              type="button"
              onClick={() => set({ stride_x: Math.max(1, Math.round(g.patch_width / div)), stride_y: Math.max(1, Math.round(g.patch_height / div)) })}
              className={`h-8 px-space-sm rounded text-label-md ${overlap === id ? "bg-primary text-on-primary" : "bg-surface-container-low hover:bg-surface-container"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-label-md text-on-surface-variant mr-1">Patch area</span>
          {(
            [
              [false, "Tissue only"],
              [true, "Whole slide"],
            ] as const
          ).map(([whole, label]) => (
            <button
              key={label}
              type="button"
              onClick={() =>
                set(whole ? { min_tissue_fraction: 0, include_edge_patches: true } : { min_tissue_fraction: wholeSlide ? 0.5 : g.min_tissue_fraction })
              }
              title={whole ? "Every patch of the slide, glass included -- no tissue detection needed" : "Only patches with enough tissue"}
              className={`h-8 px-space-sm rounded text-label-md ${wholeSlide === whole ? "bg-primary text-on-primary" : "bg-surface-container-low hover:bg-surface-container"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md">
        <Field label="Patch width (px)">
          <input type="number" className="input" value={Number.isNaN(g.patch_width) ? "" : g.patch_width} onChange={num("patch_width")} />
        </Field>
        <Field label="Patch height (px)">
          <input type="number" className="input" value={Number.isNaN(g.patch_height) ? "" : g.patch_height} onChange={num("patch_height")} />
        </Field>
        <Field label="Stride X (px)">
          <input type="number" className="input" value={Number.isNaN(g.stride_x) ? "" : g.stride_x} onChange={num("stride_x")} />
        </Field>
        <Field label="Stride Y (px)">
          <input type="number" className="input" value={Number.isNaN(g.stride_y) ? "" : g.stride_y} onChange={num("stride_y")} />
        </Field>
        <Field label="Magnification">
          <select
            className="input"
            value={g.target_magnification ?? ""}
            onChange={(e) => set({ target_magnification: e.target.value === "" ? null : Number(e.target.value) })}
          >
            <option value="">Default</option>
            {[...new Set([...MAGNIFICATIONS, ...(g.target_magnification ? [g.target_magnification] : [])])]
              .sort((a, b) => a - b)
              .map((m) => (
                <option key={m} value={m}>
                  {m}x
                </option>
              ))}
          </select>
        </Field>
        {!wholeSlide && (
          <Field label="Minimum tissue (%)">
            <input
              type="number"
              className="input"
              min={0}
              max={100}
              value={Number.isNaN(g.min_tissue_fraction) ? "" : Math.round(g.min_tissue_fraction * 1000) / 10}
              onChange={(e) => set({ min_tissue_fraction: e.target.value === "" ? NaN : Math.round(Number(e.target.value) * 10) / 1000 })}
            />
          </Field>
        )}
      </div>
      <Toggle label="Include patches at the slide's edge" checked={g.include_edge_patches} onChange={(v) => set({ include_edge_patches: v })} />
    </div>
  );
}
