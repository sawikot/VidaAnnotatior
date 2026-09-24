import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button } from "../../components/primitives";
import { exportProjectUrl, startDownload } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { ProjectType } from "../../types/api";
import { EXPORT_FORMATS, isMergedExport } from "./formats";

/** "Export All" button: pick a format, get one ZIP with every processed slide's export. */
export function ExportAllMenu({
  projectId,
  slideCount,
  projectType = "wsi",
  optionsPath,
  disabledReason,
}: {
  projectId: number;
  slideCount: number;
  projectType?: ProjectType;
  /** Where the full export options live (patches, images, masks ...). */
  optionsPath?: string;
  disabledReason?: string;
}) {
  const isImage = projectType === "image";
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

  function run(formatId: string) {
    // Straight from the click: the browser downloads the file itself (see startDownload).
    startDownload(exportProjectUrl(projectId, formatId));
    pushToast("Building the export on the server. Your browser saves it as soon as it is ready.", "info");
    setOpen(false);
    setBusyFormat(formatId);
    window.setTimeout(() => setBusyFormat(null), 3000);
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
            {isImage
              ? `Every image (${slideCount.toLocaleString()}). COCO and CSV as one merged file, others as a ZIP. Annotated only`
              : `Every processed slide, as one ZIP (${slideCount} in project)`}
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
                {busyFormat === f.id ? "Preparing..." : isMergedExport(projectType, f.id) ? f.ext : ".zip"}
              </span>
            </button>
          ))}
          {optionsPath && (
            <Link
              to={optionsPath}
              onClick={() => setOpen(false)}
              className="flex items-center gap-space-sm px-space-md py-space-sm border-t border-outline-variant/60 text-body-sm text-primary hover:bg-surface-container-low"
            >
              <MaterialIcon name="tune" className="!text-[16px]" />
              <span>
                More options
                <span className="block text-label-sm text-on-surface-variant">Empty patches, patch images, masks, reviewed only</span>
              </span>
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
