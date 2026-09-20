import { useEffect, useRef, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button } from "../../components/primitives";
import { downloadProjectExport } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import { EXPORT_FORMATS } from "./formats";

/** "Export All" button: pick a format, get one ZIP with every processed slide's export. */
export function ExportAllMenu({ projectId, slideCount, disabledReason }: { projectId: number; slideCount: number; disabledReason?: string }) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const [busyFormat, setBusyFormat] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function run(formatId: string) {
    setBusyFormat(formatId);
    try {
      const name = await downloadProjectExport(projectId, formatId);
      pushToast(`Exported all slides (${name})`, "success");
      setOpen(false);
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Export failed", "error");
    } finally {
      setBusyFormat(null);
    }
  }

  return (
    <div ref={root} className="relative">
      <Button icon="download" onClick={() => setOpen((v) => !v)} disabled={!!disabledReason} title={disabledReason}>
        Export All
        <MaterialIcon name={open ? "expand_less" : "expand_more"} className="!text-[16px]" />
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 top-10 z-20 w-72 bg-surface-container-lowest rounded shadow-lg border border-outline-variant py-1">
          <div className="px-space-md py-space-sm text-label-sm text-on-surface-variant border-b border-outline-variant/60">
            Every processed slide, as one ZIP ({slideCount} in project)
          </div>
          {EXPORT_FORMATS.map((f) => (
            <button
              key={f.id}
              role="menuitem"
              disabled={busyFormat !== null}
              onClick={() => run(f.id)}
              className="w-full text-left px-space-md py-space-sm flex items-center justify-between gap-space-sm hover:bg-surface-container-low disabled:opacity-50"
            >
              <span>
                <span className="block text-body-sm text-on-surface">{f.name}</span>
                <span className="block text-label-sm text-on-surface-variant">{f.space}</span>
              </span>
              <span className="font-mono text-label-sm text-on-surface-variant">
                {busyFormat === f.id ? "Preparing..." : f.ext}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
