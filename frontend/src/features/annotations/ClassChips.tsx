import { MaterialIcon } from "../../components/MaterialIcon";
import { useAnnotationStore } from "../../stores/annotationStore";
import type { AnnotationClass } from "../../types/api";

/** The classes in the workspace toolbar: click a name to draw in that class, the eye to hide or show
 *  its annotations (in this patch, its neighbours and on the whole slide). */
export function ClassChips({ classes }: { classes: AnnotationClass[] }) {
  const activeClassId = useAnnotationStore((s) => s.activeClassId);
  const setActiveClassId = useAnnotationStore((s) => s.setActiveClassId);
  const hidden = useAnnotationStore((s) => s.hiddenClassIds);
  const toggleHidden = useAnnotationStore((s) => s.toggleClassHidden);
  const showAll = useAnnotationStore((s) => s.showAllClasses);
  const anyHidden = classes.some((c) => hidden.includes(c.id));

  return (
    <>
      {classes.map((c, i) => {
        const active = activeClassId === c.id;
        const isHidden = hidden.includes(c.id);
        return (
          <span
            key={c.id}
            className="h-8 rounded flex items-center"
            style={{
              backgroundColor: active ? `${c.color_hex}33` : "transparent",
              boxShadow: active ? `inset 0 0 0 1px ${c.color_hex}80` : undefined,
            }}
          >
            <button
              onClick={() => setActiveClassId(c.id)}
              className={`pl-space-sm pr-1 h-8 text-label-md flex items-center gap-1.5 ${isHidden ? "line-through opacity-60" : ""}`}
              style={{ color: active ? c.color_hex : "#94a3b8" }}
              title={isHidden ? `${c.name} (hidden)` : c.name}
            >
              {i < 9 && <span className="font-mono">{i + 1}</span>}
              {c.name}
            </button>
            <button
              onClick={() => toggleHidden(c.id)}
              className="pr-1.5 h-8 flex items-center text-slate-400 hover:text-white"
              title={isHidden ? `Show ${c.name} annotations` : `Hide ${c.name} annotations`}
              aria-label={isHidden ? `Show ${c.name} annotations` : `Hide ${c.name} annotations`}
              aria-pressed={isHidden}
            >
              <MaterialIcon name={isHidden ? "visibility_off" : "visibility"} className="!text-[15px]" />
            </button>
          </span>
        );
      })}
      {anyHidden && (
        <button onClick={showAll} className="px-space-sm h-8 rounded text-label-md text-sky-300 hover:text-white" title="Show the annotations of every class">
          Show all
        </button>
      )}
    </>
  );
}

/** Keeps the annotations whose class is not hidden (unclassified ones always show). */
export function visibleByClass<T extends { class_id: number | null }>(list: T[], hidden: number[]): T[] {
  return hidden.length ? list.filter((a) => a.class_id == null || !hidden.includes(a.class_id)) : list;
}
