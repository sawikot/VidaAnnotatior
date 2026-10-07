import { MaterialIcon } from "../../components/MaterialIcon";
import { BRUSH_MAX_SIZE, BRUSH_MIN_SIZE, useAnnotationStore, type AnnotationTool, type BrushMode, type ShapeMode } from "../../stores/annotationStore";

const modes = (group: string): { id: BrushMode; icon: string; label: string; hint: string }[] => [
  { id: "new", icon: "add_circle", label: "New", hint: `Every stroke is a new shape of ${group}` },
  {
    id: "add",
    icon: "join",
    label: "Add",
    hint: `Grows the shapes of ${group} the stroke touches (or the shape it starts on) and joins them into one; touching none, it paints a new shape`,
  },
  { id: "erase", icon: "ink_eraser", label: "Erase", hint: "Cut the stroke out of the shapes it crosses (or hold Shift in the other modes)" },
];

const BAR = "mx-auto mt-1 max-w-[calc(100%-1rem)] shrink-0 z-20 bg-[#0f172a]/95 rounded-xl shadow-2xl justify-center";

/** The tools that draw an area, which can join what they draw onto the shapes already there. */
const AREA_TOOLS: AnnotationTool[] = ["rectangle", "circle", "polygon", "freehand"];

/** The options of the tool in hand, if it has any: the brush's, or new/add for a tool that draws an area. */
export function ToolOptions({ tool, ...rest }: Props & { tool: AnnotationTool }) {
  if (tool === "brush") return <BrushOptions {...rest} />;
  return AREA_TOOLS.includes(tool) ? <ShapeModeOptions {...rest} /> : null;
}

/** New or add, for the tools that draw an area. */
function ShapeModeOptions({ group = "the active class", className = BAR }: Props) {
  const shapeMode = useAnnotationStore((s) => s.shapeMode);
  const setShapeMode = useAnnotationStore((s) => s.setShapeMode);
  const options: { id: ShapeMode; icon: string; label: string; hint: string }[] = [
    { id: "new", icon: "add_circle", label: "New", hint: "Every shape you draw is a shape of its own" },
    {
      id: "add",
      icon: "join",
      label: "Add",
      hint: `A shape drawn onto shapes of ${group} joins them into one; touching none, it is a new shape`,
    },
    { id: "erase", icon: "ink_eraser", label: "Erase", hint: "The shape you draw is cut out of the shapes it crosses, and is not kept itself" },
  ];

  return (
    <div data-testid="shape-mode-options" className={`flex flex-wrap items-center gap-space-sm px-space-sm py-1 text-label-md text-slate-300 ${className}`}>
      <div className="flex bg-[#0b1329] rounded p-0.5" role="radiogroup" aria-label="Drawing mode">
        {options.map((m) => (
          <button
            key={m.id}
            role="radio"
            aria-checked={shapeMode === m.id}
            title={m.hint}
            onClick={() => setShapeMode(m.id)}
            className={`px-space-sm h-7 rounded flex items-center gap-1 ${shapeMode === m.id ? "bg-[#1e293b] text-white" : "text-slate-400 hover:text-white"}`}
          >
            <MaterialIcon name={m.icon} className="!text-[16px]" />
            {m.label}
          </button>
        ))}
      </div>
      {shapeMode === "erase" && <EraseScope group={group} />}
      <span className="text-label-sm text-slate-500">{options.find((m) => m.id === shapeMode)?.hint}</span>
    </div>
  );
}

/** Which shapes erasing cuts into -- one setting for the brush and the shape tools. */
function EraseScope({ group }: { group: string }) {
  const scope = useAnnotationStore((s) => s.brush.eraseScope);
  const setBrush = useAnnotationStore((s) => s.setBrush);
  return (
    <label className="flex items-center gap-1.5" title="Which shapes erasing cuts into">
      From
      <select
        value={scope}
        onChange={(e) => {
          setBrush({ eraseScope: e.target.value as "all" | "class" });
          e.currentTarget.blur();
        }}
        className="h-7 rounded bg-[#0b1329] text-slate-200 px-1 border border-slate-700"
      >
        <option value="all">every shape</option>
        <option value="class">{group} only</option>
      </select>
    </label>
  );
}

interface Props {
  /** What the brush paints with, as the hints call it. */
  group?: string;
  /** Where the bar sits; the default is centred under the workspace toolbar. */
  className?: string;
}

/** The brush's settings, shown under the toolbar while the brush is the tool. */
function BrushOptions({ group = "the active class", className = BAR }: Props) {
  const brush = useAnnotationStore((s) => s.brush);
  const setBrush = useAnnotationStore((s) => s.setBrush);
  const MODES = modes(group);

  return (
    <div data-testid="brush-options" className={`flex flex-wrap items-center gap-space-sm px-space-sm py-1 text-label-md text-slate-300 ${className}`}>
      <div className="flex bg-[#0b1329] rounded p-0.5" role="radiogroup" aria-label="Brush mode">
        {MODES.map((m) => (
          <button
            key={m.id}
            role="radio"
            aria-checked={brush.mode === m.id}
            title={m.hint}
            onClick={() => setBrush({ mode: m.id })}
            className={`px-space-sm h-7 rounded flex items-center gap-1 ${brush.mode === m.id ? "bg-[#1e293b] text-white" : "text-slate-400 hover:text-white"}`}
          >
            <MaterialIcon name={m.icon} className="!text-[16px]" />
            {m.label}
          </button>
        ))}
      </div>

      <label className="flex items-center gap-1.5" title="Brush size on screen, in pixels ( [ and ] )">
        Size
        <input
          type="range"
          min={BRUSH_MIN_SIZE}
          max={BRUSH_MAX_SIZE}
          value={brush.size}
          onChange={(e) => setBrush({ size: Number(e.target.value) })}
          onPointerUp={(e) => e.currentTarget.blur()} // give the keyboard back to the tools
          className="w-32 accent-sky-400"
        />
        <span className="font-mono text-label-sm text-slate-400 w-12">{brush.size} px</span>
      </label>

      {brush.mode === "erase" && <EraseScope group={group} />}

      <span className="text-label-sm text-slate-500">{MODES.find((m) => m.id === brush.mode)?.hint}</span>
    </div>
  );
}
