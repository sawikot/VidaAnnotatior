import { MaterialIcon } from "../../components/MaterialIcon";

export type AnnotationMode = "patch" | "wsi";

const STORAGE_KEY = "vp.annotationMode";

/** The mode last used (per browser), so the workspace opens where the annotator left off. */
export function rememberedMode(): AnnotationMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === "wsi" ? "wsi" : "patch";
  } catch {
    return "patch"; // storage can be blocked; the choice just isn't remembered
  }
}

export function rememberMode(mode: AnnotationMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* not remembered */
  }
}

/** Patch | WSI: annotate one patch at a time, or draw directly on the whole slide. */
export function AnnotationModeSwitch({ mode, onChange }: { mode: AnnotationMode; onChange: (mode: AnnotationMode) => void }) {
  const options: { id: AnnotationMode; label: string; icon: string; title: string }[] = [
    { id: "patch", label: "Patch", icon: "grid_view", title: "Annotate patch by patch, at full detail" },
    { id: "wsi", label: "WSI", icon: "map", title: "Annotate directly on the whole slide (Level-0 coordinates)" },
  ];
  return (
    <div role="group" aria-label="Annotation mode" className="flex rounded-lg bg-[#0f172a] p-0.5 border border-[#1e293b]">
      {options.map((o) => (
        <button
          key={o.id}
          aria-pressed={mode === o.id}
          title={o.title}
          onClick={() => mode !== o.id && onChange(o.id)}
          className={`flex items-center gap-1 px-space-sm py-1 rounded-md text-label-md transition-colors ${
            mode === o.id ? "bg-[#0284c7] text-white" : "text-slate-400 hover:text-white"
          }`}
        >
          <MaterialIcon name={o.icon} className="!text-[16px]" />
          {o.label}
        </button>
      ))}
    </div>
  );
}
