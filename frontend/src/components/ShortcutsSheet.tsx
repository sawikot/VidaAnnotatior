import { useEffect } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { PAN_TOOL, TOOLS } from "../features/annotations/tools";
import { useUiStore } from "../stores/uiStore";
import { MaterialIcon } from "./MaterialIcon";
import { Modal } from "./primitives";

type Row = [keys: string[], what: string];
interface Section {
  id: "patch" | "wsi" | "processing" | "general";
  title: string;
  rows: Row[];
}

const REGION_TOOL_IDS = ["select", "rectangle", "polygon", "freehand", "circle", "brush"];
const toolRows = (ids?: string[]): Row[] =>
  TOOLS.filter((t) => !ids || ids.includes(t.id)).map((t) => [[t.key], t.label.replace(/ \(.*\)$/, "")]);

const EDITING: Row[] = [
  [["Drag"], "Move the selected shape (Select tool)"],
  [["Drag a handle"], "Reshape it"],
  [["Double-click"], "On an outline: add a point. On a point: remove it"],
  [["Delete", "Backspace"], "Delete the selected shape"],
  [["Enter"], "Finish a polygon (or double-click, or click its first point)"],
  [["Backspace"], "While drawing a polygon: take back the last point"],
  [["Esc"], "Abandon the shape being drawn"],
];
const BRUSH: Row[] = [
  [["[", "]"], "Brush: smaller, larger"],
  [["Shift", "Drag"], "Brush: erase, whatever its mode"],
];
const UNDO: Row[] = [
  [["Ctrl", "Z"], "Undo"],
  [["Ctrl", "Shift", "Z"], "Redo"],
];

const SECTIONS: Section[] = [
  {
    id: "patch",
    title: "Annotating a patch",
    rows: [
      ...toolRows(),
      [["1", "...", "9"], "Pick a diagnostic class"],
      [["A", "←"], "Previous patch"],
      [["D", "→"], "Next patch"],
      [["Space"], "Next patch that is not annotated yet"],
      [["Wheel"], "Zoom in and out around the cursor"],
      [["Middle drag"], "Move around a zoomed patch"],
      ...BRUSH,
      ...EDITING,
      ...UNDO,
    ],
  },
  {
    id: "wsi",
    title: "Annotating the whole slide",
    rows: [
      [[PAN_TOOL.key], "Pan tool: dragging moves around the slide"],
      [["Hold Space"], "Move around with any tool"],
      ...toolRows(),
      [["1", "...", "9"], "Pick a diagnostic class"],
      [["Wheel"], "Zoom"],
      ...BRUSH,
      ...EDITING,
      ...UNDO,
    ],
  },
  {
    id: "processing",
    title: "Slide processing (drawing tissue regions)",
    rows: [
      [[PAN_TOOL.key], "Pan tool"],
      [["Hold Space"], "Move around with any tool"],
      ...toolRows(REGION_TOOL_IDS),
      [["Wheel"], "Zoom"],
      ...BRUSH,
      ...EDITING,
      ...UNDO,
    ],
  },
  {
    id: "general",
    title: "Everywhere",
    rows: [
      [["?"], "Show or hide this list"],
      [["Esc"], "Close this list"],
    ],
  },
];

function currentSection(path: string, mode: string | null): Section["id"] {
  if (path.endsWith("/workspace")) return mode === "wsi" ? "wsi" : "patch";
  if (path.endsWith("/processing")) return "processing";
  return "general";
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/** Every keyboard and mouse shortcut, the ones for the current page first. Opened with "?" or the header's help button. */
export function ShortcutsSheet() {
  const open = useUiStore((s) => s.shortcutsOpen);
  const setOpen = useUiStore((s) => s.setShortcutsOpen);
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const here = currentSection(pathname, params.get("mode"));

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const { shortcutsOpen } = useUiStore.getState();
      if (e.key === "?" && !isTyping(e.target)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        setOpen(!shortcutsOpen);
        return;
      }
      if (!shortcutsOpen) return;
      // While the list is open, keys belong to it: "D" must not move to the next patch behind it.
      e.stopImmediatePropagation();
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKeyDown, true); // capture: runs before the pages' own shortcuts
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [setOpen]);

  const ordered = [...SECTIONS.filter((s) => s.id === here), ...SECTIONS.filter((s) => s.id !== here && s.id !== "general"), ...SECTIONS.filter((s) => s.id === "general" && here !== "general")];

  return (
    <Modal open={open} onClose={() => setOpen(false)} widthClass="max-w-3xl">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md flex items-center gap-space-sm">
            <MaterialIcon name="keyboard" />
            Keyboard and mouse shortcuts
          </h2>
          <button onClick={() => setOpen(false)} aria-label="Close">
            <MaterialIcon name="close" />
          </button>
        </div>
        <div className="grid md:grid-cols-2 gap-space-lg">
          {ordered.map((section) => (
            <section key={section.id} className={section.id === here && here !== "general" ? "md:col-span-2" : ""}>
              <h3 className="font-headline-sm text-label-lg mb-space-sm flex items-center gap-space-sm">
                {section.title}
                {section.id === here && here !== "general" && (
                  <span className="px-space-sm py-0.5 rounded-full bg-primary-fixed text-label-sm">on this page</span>
                )}
              </h3>
              <dl className={`grid gap-x-space-lg gap-y-1 ${section.id === here && here !== "general" ? "md:grid-cols-2" : ""}`}>
                {section.rows.map(([keys, what]) => (
                  <div key={`${keys.join("+")}-${what}`} className="flex items-center justify-between gap-space-md text-body-sm">
                    <dt className="text-on-surface-variant">{what}</dt>
                    <dd className="flex items-center gap-1 shrink-0">
                      {keys.map((k, i) => (
                        <kbd key={i} className="px-1.5 min-w-[1.5rem] text-center rounded border border-outline-variant bg-surface-container-low font-mono text-label-sm">
                          {k}
                        </kbd>
                      ))}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <p className="text-body-sm text-on-surface-variant">
          Tools a project has switched off (Settings, Annotation tools) have no effect. Press <kbd className="font-mono">?</kbd> any time to
          open this list.
        </p>
      </div>
    </Modal>
  );
}
