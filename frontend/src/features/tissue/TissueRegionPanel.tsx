import { MaterialIcon } from "../../components/MaterialIcon";
import { IconButton } from "../../components/primitives";
import type { AnnotationTool } from "../../stores/annotationStore";
import { ToolOptions } from "../annotations/BrushOptions";
import type { TissueRegion, TissueRegionMode, TissueSource } from "../../types/api";
import { REGION_COLORS, REGION_TOOLS } from "./regionTools";

const TYPE_LABEL: Record<TissueRegion["type"], string> = {
  rectangle: "Rectangle",
  polygon: "Polygon",
  freehand: "Freehand",
  circle: "Circle",
};

interface Props {
  source: TissueSource;
  onSourceChange: (source: TissueSource) => void;
  regions: TissueRegion[];
  drawMode: TissueRegionMode;
  onDrawModeChange: (mode: TissueRegionMode) => void;
  tool: AnnotationTool;
  onToolChange: (tool: AnnotationTool) => void;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onFlip: (id: number) => void;
  onDelete: (id: number) => void;
  onClear: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  saving: boolean;
  disabled: boolean;
}

export function TissueRegionPanel(p: Props) {
  const adds = p.regions.filter((r) => r.mode === "add").length;
  const removes = p.regions.length - adds;

  return (
    <div className="flex flex-col gap-space-md">
      <div>
        <div className="text-label-md text-slate-300 mb-1">Mask starts from</div>
        <div className="flex bg-[#070d1e] rounded-lg p-0.5">
          {(
            [
              ["auto", "Automatic detection", "auto_awesome"],
              ["manual", "Manual only", "draw"],
            ] as const
          ).map(([s, label, icon]) => (
            <button
              key={s}
              disabled={p.disabled}
              onClick={() => p.onSourceChange(s)}
              className={`flex-1 flex items-center justify-center gap-1 px-space-sm py-1.5 rounded-lg text-label-md disabled:opacity-50 ${
                p.source === s ? "bg-[#007bb9] text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              <MaterialIcon name={icon} className="!text-[16px]" />
              {label}
            </button>
          ))}
        </div>
        <p className="text-label-sm text-slate-500 mt-1">
          {p.source === "auto"
            ? "The detected tissue, plus the areas you add, minus the areas you remove."
            : "Only the areas you add count as tissue (minus any you remove). Detection is ignored."}
        </p>
      </div>

      <div className="flex flex-col gap-space-sm">
        <div className="flex items-center justify-between">
          <span className="text-label-md text-slate-300">Manual regions</span>
          <span className="flex items-center gap-1">
            <IconButton icon="undo" onClick={p.onUndo} disabled={!p.canUndo || p.disabled} title="Undo (Ctrl+Z)" />
            <IconButton icon="redo" onClick={p.onRedo} disabled={!p.canRedo || p.disabled} title="Redo (Ctrl+Shift+Z)" />
          </span>
        </div>

        <div className="grid grid-cols-2 gap-1">
          {(["add", "remove"] as const).map((m) => (
            <button
              key={m}
              onClick={() => p.onDrawModeChange(m)}
              className="h-8 rounded text-label-md flex items-center justify-center gap-1.5"
              style={{
                backgroundColor: p.drawMode === m ? `${REGION_COLORS[m]}33` : "#070d1e",
                color: p.drawMode === m ? REGION_COLORS[m] : "#94a3b8",
                boxShadow: p.drawMode === m ? `inset 0 0 0 1px ${REGION_COLORS[m]}99` : undefined,
              }}
            >
              <MaterialIcon name={m === "add" ? "add_circle" : "remove_circle"} className="!text-[16px]" />
              {m === "add" ? "Add tissue" : "Remove tissue"}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-1 bg-[#070d1e] rounded-lg p-1">
          {REGION_TOOLS.map((t) => (
            <IconButton key={t.id} icon={t.icon} active={p.tool === t.id} onClick={() => p.onToolChange(t.id)} title={`${t.label} (${t.key})`} />
          ))}
        </div>
        <ToolOptions tool={p.tool} group={p.drawMode === "add" ? "added tissue" : "removed tissue"} className="bg-[#070d1e] rounded-lg" />
        <p className="text-label-sm text-slate-500">
          Pick a shape and draw on the slide. Hold Space to move around while drawing. Where an added and a removed area
          overlap, the area is removed.
        </p>

        <div className="flex items-center justify-between text-label-sm text-slate-400">
          <span>
            {adds} added · {removes} removed
            {p.saving && <span className="ml-1 text-slate-500">· saving...</span>}
          </span>
          {p.regions.length > 0 && (
            <button className="text-red-400 hover:underline disabled:opacity-50" onClick={p.onClear} disabled={p.disabled}>
              Clear all
            </button>
          )}
        </div>

        <div className="flex flex-col gap-1">
          {p.regions.map((r) => (
            <div
              key={r.id}
              onClick={() => p.onSelect(r.id)}
              className={`flex items-center gap-space-sm px-space-sm py-1.5 rounded bg-[#0b1329] cursor-pointer ${
                p.selectedId === r.id ? "ring-1 ring-sky-400" : ""
              }`}
            >
              <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: REGION_COLORS[r.mode] }} />
              <span className="flex-1 text-label-md">
                {r.mode === "add" ? "Add" : "Remove"} · {TYPE_LABEL[r.type]} #{r.id}
              </span>
              <button
                title={r.mode === "add" ? "Make this a removed area" : "Make this an added area"}
                onClick={(e) => {
                  e.stopPropagation();
                  p.onFlip(r.id);
                }}
                disabled={p.disabled}
                className="text-slate-400 hover:text-white disabled:opacity-40"
              >
                <MaterialIcon name="swap_horiz" className="!text-[16px]" />
              </button>
              <button
                title="Delete this area"
                aria-label={`Delete region ${r.id}`}
                onClick={(e) => {
                  e.stopPropagation();
                  p.onDelete(r.id);
                }}
                disabled={p.disabled}
                className="text-red-400 hover:text-red-300 disabled:opacity-40"
              >
                <MaterialIcon name="delete" className="!text-[16px]" />
              </button>
            </div>
          ))}
          {p.regions.length === 0 && <div className="text-body-sm text-slate-500">No manual regions yet.</div>}
        </div>
      </div>
    </div>
  );
}
