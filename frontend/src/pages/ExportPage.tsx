import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { exportSlideUrl, getSlide } from "../services/api";
import type { Slide } from "../types/api";
import { useUiStore } from "../stores/uiStore";

const FORMATS = [
  { id: "wsi_json", name: "Full WSI JSON", desc: "Native hierarchical matrix", ext: ".json", implemented: true },
  { id: "coco", name: "COCO Pathology Format", desc: "Instance segmentation standard", ext: ".json", implemented: false },
  { id: "geojson", name: "GeoJSON / Spatial Vectors", desc: "Multi-polygon feature collections", ext: ".geojson", implemented: false },
  { id: "patch_csv", name: "Patch-Coordinate CSV", desc: "Tabular spatial registry", ext: ".csv", implemented: false },
  { id: "stats_csv", name: "Annotation Statistics CSV", desc: "Quantitative summary", ext: ".csv", implemented: false },
];

export function ExportPage() {
  const { slideId } = useParams();
  const sid = Number(slideId);
  const pushToast = useUiStore((s) => s.pushToast);

  const [slide, setSlide] = useState<Slide | null>(null);
  const [format, setFormat] = useState("wsi_json");
  const [preview, setPreview] = useState<string>("");
  const [loadingPreview, setLoadingPreview] = useState(false);

  useEffect(() => {
    getSlide(sid).then(setSlide);
  }, [sid]);

  useEffect(() => {
    const chosen = FORMATS.find((f) => f.id === format);
    if (!chosen?.implemented) {
      setPreview("");
      return;
    }
    setLoadingPreview(true);
    fetch(exportSlideUrl(sid, format))
      .then((r) => r.text())
      .then(setPreview)
      .catch(() => setPreview("// failed to load preview"))
      .finally(() => setLoadingPreview(false));
  }, [sid, format]);

  async function handleExport() {
    const chosen = FORMATS.find((f) => f.id === format);
    if (!chosen?.implemented) {
      pushToast(`${chosen?.name} is architected but not yet implemented`, "error");
      return;
    }
    window.location.href = exportSlideUrl(sid, format);
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(preview);
    pushToast("Copied to clipboard", "success");
  }

  if (!slide) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

  return (
    <div className="max-w-6xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg pb-28">
      <div className="bg-surface-container-low rounded-xl p-space-lg">
        <div className="text-label-sm text-on-surface-variant mb-1">Pipelines & Artifacts / Export Engine</div>
        <h1 className="font-headline-lg text-headline-lg mb-space-sm">Export Annotations & Coordinate Datasets</h1>
        <div className="flex items-center gap-space-sm flex-wrap text-label-md text-on-surface-variant mb-space-md">
          <Link to={`/projects/${slide.project_id}`} className="hover:underline text-primary">
            {slide.filename}
          </Link>
        </div>
        <Card className="p-space-md bg-primary-fixed/40 flex items-center justify-between flex-wrap gap-space-sm">
          <div className="flex items-center gap-space-sm">
            <MaterialIcon name="verified" className="text-primary" />
            <span className="font-headline-sm text-headline-sm">Level-0 Coordinate Guarantee Active</span>
          </div>
          <div className="flex items-center gap-space-lg font-mono text-label-md">
            <span>Pixel Scale (MPP): {slide.mpp_x?.toFixed(4) ?? "--"} µm/px</span>
          </div>
        </Card>
      </div>

      <div>
        <h2 className="font-headline-md text-headline-md mb-space-md">1. Select Target Specification</h2>
        <div className="grid md:grid-cols-5 gap-space-md">
          {FORMATS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFormat(f.id)}
              className={`relative text-left p-space-md rounded-xl bg-surface-container-lowest shadow-sm transition-shadow ${
                format === f.id ? "ring-2 ring-primary" : "hover:shadow-md"
              } ${!f.implemented ? "opacity-60" : ""}`}
            >
              {f.id === "wsi_json" && (
                <span className="absolute top-2 right-2 px-space-sm py-0.5 rounded-full bg-primary text-on-primary text-label-sm">
                  Recommended
                </span>
              )}
              <MaterialIcon name={format === f.id ? "radio_button_checked" : "radio_button_unchecked"} className="text-primary" />
              <div className="font-headline-sm text-headline-sm mt-space-sm">{f.name}</div>
              <div className="text-body-sm text-on-surface-variant">{f.desc}</div>
              <div className="mt-space-sm flex items-center gap-1.5 flex-wrap">
                <span className="px-space-sm py-0.5 rounded-full bg-surface-container-high text-label-sm font-mono">{f.ext}</span>
                {!f.implemented && <span className="px-space-sm py-0.5 rounded-full bg-error-container text-on-error-container text-label-sm">Not yet implemented</span>}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div>
        <h2 className="font-headline-md text-headline-md mb-space-md">2. Live Manifest Preview</h2>
        <div className="bg-[#0f172a] rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-space-md py-space-sm border-b border-slate-800">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
              <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
              <span className="ml-space-sm text-label-sm text-slate-400 font-mono">
                {slide.filename.replace(/\.[^.]+$/, "")}_{format}.{FORMATS.find((f) => f.id === format)?.ext.replace(".", "")}
              </span>
            </div>
            <div className="flex items-center gap-space-sm">
              <button onClick={handleCopy} disabled={!preview} className="text-label-sm text-slate-300 hover:text-white disabled:opacity-30">
                Copy
              </button>
            </div>
          </div>
          <pre className="p-space-md text-label-sm font-mono text-slate-300 overflow-auto max-h-96">
            {loadingPreview ? "Loading preview..." : preview || "// This format is not yet implemented in this MVP."}
          </pre>
        </div>
      </div>

      <div className="fixed bottom-4 left-14 right-4 z-30 flex justify-center pointer-events-none">
        <div className="pointer-events-auto bg-surface-container-highest rounded-2xl shadow-lg px-space-lg py-space-md flex items-center gap-space-md">
          <span className="text-body-sm text-on-surface-variant">
            Export scope: this slide ({slide.filename})
          </span>
          <Button variant="primary" icon="download" onClick={handleExport}>
            Generate & Download {FORMATS.find((f) => f.id === format)?.name}
          </Button>
        </div>
      </div>
    </div>
  );
}

