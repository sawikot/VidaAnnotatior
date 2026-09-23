import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Field, Toggle } from "../components/formControls";
import { Button, Card } from "../components/primitives";
import { CancerTypeSelect } from "../features/projects/CancerTypeSelect";
import { MVP_TOOLS } from "../features/projects/constants";
import { createProject } from "../services/api";
import type { ProjectType } from "../types/api";
import { useUiStore } from "../stores/uiStore";

const DEFAULT_CLASSES = [
  { name: "Tumor", color_hex: "#dc2626", hotkey: "1" },
  { name: "Stroma", color_hex: "#16a34a", hotkey: "2" },
  { name: "Necrosis", color_hex: "#eab308", hotkey: "3" },
  { name: "Normal", color_hex: "#2563eb", hotkey: "4" },
];

// Steps 2-4 only exist for whole-slide projects: an image project has no magnification,
// patch grid or tissue detection to configure.
const STEPS = [
  { anchor: "step-1", label: "Project Info", wsiOnly: false },
  { anchor: "step-2", label: "WSI Configuration", wsiOnly: true },
  { anchor: "step-3", label: "Patch Grid Matrix", wsiOnly: true },
  { anchor: "step-4", label: "Tissue Detection", wsiOnly: true },
  { anchor: "step-5", label: "Classes", wsiOnly: false },
  { anchor: "step-6", label: "Tools", wsiOnly: false },
  { anchor: "step-7", label: "Quality Control", wsiOnly: false },
];

const PROJECT_TYPES: { id: ProjectType; icon: string; title: string; body: string }[] = [
  {
    id: "wsi",
    icon: "biotech",
    title: "Whole-slide images (WSI)",
    body: "Gigapixel slides (.svs, .ndpi, .mrxs ...). Tissue is detected and cut into virtual patches that you annotate one by one.",
  },
  {
    id: "image",
    icon: "image",
    title: "Images / patches",
    body: "Ordinary images (PNG, JPEG, TIFF ...) or patches you already cut. Each image is annotated as it is -- no tiling, no tissue detection.",
  },
];

const PRESETS: [number, number, number, number][] = [
  [256, 256, 256, 256],
  [512, 512, 512, 512],
  [1024, 1024, 512, 512],
];

const PLANNED_TOOLS = [
  { id: "brush", label: "Brush Mask", icon: "brush" },
  { id: "sam", label: "SAM Assisted", icon: "auto_fix_high" },
  { id: "ruler", label: "Ruler / Caliper", icon: "straighten" },
];

export function NewProjectWizardPage() {
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const [submitting, setSubmitting] = useState(false);
  const [projectType, setProjectType] = useState<ProjectType>("wsi");
  const isImage = projectType === "image";
  const visibleSteps = STEPS.filter((st) => !(isImage && st.wsiOnly));
  const stepNumber = (anchor: string) => visibleSteps.findIndex((st) => st.anchor === anchor) + 1;

  // Step 1
  const [name, setName] = useState("");
  const [organ, setOrgan] = useState("Breast Cancer");
  const [description, setDescription] = useState("");
  const [team, setTeam] = useState("");

  // Step 2
  const [targetMag, setTargetMag] = useState(20);
  const [mppHandling, setMppHandling] = useState("auto");

  // Step 3
  const [patchWidth, setPatchWidth] = useState(512);
  const [patchHeight, setPatchHeight] = useState(512);
  const [strideX, setStrideX] = useState(512);
  const [strideY, setStrideY] = useState(512);
  const [minTissue, setMinTissue] = useState(60);
  const [allowPartial, setAllowPartial] = useState(false);
  const [includeEdge, setIncludeEdge] = useState(true);

  // Step 4
  const [otsuSensitivity, setOtsuSensitivity] = useState(0.65);
  const [morphOpen, setMorphOpen] = useState(3);
  const [morphClose, setMorphClose] = useState(5);
  const [minComponentPx, setMinComponentPx] = useState(400);

  // Step 5
  const [classes, setClasses] = useState(DEFAULT_CLASSES);

  // Step 6
  const [enabledTools, setEnabledTools] = useState<string[]>(MVP_TOOLS.map((t) => t.id));

  // Step 7
  const [allowSkip, setAllowSkip] = useState(true);
  const [allowUnsure, setAllowUnsure] = useState(true);
  const [requireAnnotation, setRequireAnnotation] = useState(false);
  const [reviewerMode, setReviewerMode] = useState(false);

  const overlapPct = Math.round(((patchWidth - strideX) / patchWidth) * 100);

  function addClass() {
    setClasses((c) => [...c, { name: "New Class", color_hex: "#64748b", hotkey: String(c.length + 1) }]);
  }
  function updateClass(i: number, patch: Partial<(typeof classes)[0]>) {
    setClasses((c) => c.map((cls, idx) => (idx === i ? { ...cls, ...patch } : cls)));
  }
  function removeClass(i: number) {
    setClasses((c) => c.filter((_, idx) => idx !== i));
  }

  async function handleCreate() {
    if (!name.trim()) {
      pushToast("Project name is required", "error");
      return;
    }
    if (classes.length === 0) {
      pushToast("At least one diagnostic class is required", "error");
      return;
    }
    setSubmitting(true);
    try {
      const project = await createProject({
        name,
        project_type: projectType,
        organ: organ.trim() || null,
        description,
        team,
        config: isImage
          ? {
              version_label: "v1.0",
              enabled_tools: enabledTools,
              allow_skip: allowSkip,
              allow_unsure: allowUnsure,
              require_annotation: requireAnnotation,
              reviewer_mode: reviewerMode,
              annotation_classes: classes,
            }
          : {
          version_label: "v1.0",
          target_magnification: targetMag,
          mpp_handling: mppHandling,
          patch_width: patchWidth,
          patch_height: patchHeight,
          stride_x: strideX,
          stride_y: strideY,
          min_tissue_fraction: minTissue / 100,
          allow_partial_patches: allowPartial,
          include_edge_patches: includeEdge,
          tissue_method: "hsv_otsu",
          tissue_params: {
            otsu_sensitivity: otsuSensitivity,
            morph_open_px: morphOpen,
            morph_close_px: morphClose,
            min_component_px: minComponentPx,
          },
          enabled_tools: enabledTools,
          allow_skip: allowSkip,
          allow_unsure: allowUnsure,
          require_annotation: requireAnnotation,
          reviewer_mode: reviewerMode,
          annotation_classes: classes,
        },
      });
      pushToast("Project created", "success");
      navigate(`/projects/${project.id}`);
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Failed to create project", "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg pb-32">
      <div className="flex items-center justify-between flex-wrap gap-space-sm">
        <div className="flex items-center gap-space-sm">
          <MaterialIcon name="auto_awesome" className="text-primary" />
          <div>
            <div className="text-label-sm text-on-surface-variant">Protocol Pipeline</div>
            <h1 className="font-headline-lg text-headline-lg text-on-surface">Create New Pathology Project</h1>
          </div>
        </div>
      </div>

      {/* Stepper (jump links) */}
      <div className={`grid grid-cols-2 sm:grid-cols-4 gap-space-xs ${isImage ? "lg:grid-cols-4" : "lg:grid-cols-7"}`}>
        {visibleSteps.map((s, idx) => (
          <a
            key={s.anchor}
            href={`#${s.anchor}`}
            className="flex flex-col gap-1 p-space-sm rounded bg-surface-container-lowest shadow-sm hover:shadow-md transition-shadow"
          >
            <span className="text-label-sm text-on-surface-variant">Step 0{idx + 1}</span>
            <span className="text-label-md font-headline-sm text-on-surface">{s.label}</span>
          </a>
        ))}
      </div>

      {/* Step 1 */}
      <Card className="p-space-lg" id="step-1">
        <SectionTitle n={1} title="Project Info" />
        <div className="mt-space-md">
          <div className="text-label-md text-on-surface-variant mb-1">What are you annotating?</div>
          <div role="radiogroup" aria-label="Project type" className="grid md:grid-cols-2 gap-space-md">
            {PROJECT_TYPES.map((t) => (
              <button
                key={t.id}
                type="button"
                role="radio"
                aria-checked={projectType === t.id}
                onClick={() => setProjectType(t.id)}
                className={`text-left p-space-md rounded-xl flex gap-space-md items-start transition-shadow ${
                  projectType === t.id ? "ring-2 ring-primary bg-primary-fixed/30" : "bg-surface-container-low hover:shadow-md"
                }`}
              >
                <MaterialIcon name={t.icon} className="text-primary mt-0.5" />
                <span className="flex flex-col gap-0.5">
                  <span className="font-headline-sm text-headline-sm">{t.title}</span>
                  <span className="text-body-sm text-on-surface-variant">{t.body}</span>
                </span>
                <MaterialIcon name={projectType === t.id ? "radio_button_checked" : "radio_button_unchecked"} className="text-primary ml-auto" />
              </button>
            ))}
          </div>
        </div>
        <div className="grid md:grid-cols-2 gap-space-md mt-space-md">
          <Field label="Project Name">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Breast Cancer Annotation" />
          </Field>
          <Field label="Cancer Type">
            <CancerTypeSelect value={organ} onChange={setOrgan} />
          </Field>
          <Field label="Researcher / Team">
            <input className="input" value={team} onChange={(e) => setTeam(e.target.value)} placeholder="Dr. Eliza Chen Lab" />
          </Field>
          <Field label="Description">
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Cohort description..." />
          </Field>
        </div>
      </Card>

      {!isImage && (
        <>
      {/* Step 2 */}
      <Card className="p-space-lg" id="step-2">
        <SectionTitle n={2} title="WSI Configuration" />
        <div className="grid md:grid-cols-3 gap-space-md mt-space-md">
          <Field label="Target Magnification">
            <select className="input" value={targetMag} onChange={(e) => setTargetMag(Number(e.target.value))}>
              {[10, 20, 40].map((m) => (
                <option key={m} value={m}>
                  {m}x
                </option>
              ))}
            </select>
          </Field>
          <Field label="Coordinate System">
            <input className="input bg-surface-container-low" value="Level-0 Absolute (px)" disabled />
          </Field>
          <Field label="MPP Handling">
            <select className="input" value={mppHandling} onChange={(e) => setMppHandling(e.target.value)}>
              <option value="auto">Auto (from slide metadata)</option>
              <option value="manual">Manual override</option>
            </select>
          </Field>
        </div>
      </Card>

      {/* Step 3 */}
      <Card className="p-space-lg" id="step-3">
        <SectionTitle n={3} title="Virtual Patch Extraction Grid" />
        <div className="flex gap-space-sm mt-space-md mb-space-md flex-wrap">
          {PRESETS.map(([w, h, sx, sy]) => (
            <button
              key={w}
              onClick={() => {
                setPatchWidth(w);
                setPatchHeight(h);
                setStrideX(sx);
                setStrideY(sy);
              }}
              className={`px-space-md py-1.5 rounded text-label-md font-headline-sm ${
                patchWidth === w ? "bg-primary text-on-primary" : "bg-surface-container-low text-on-surface"
              }`}
            >
              {w}x{h}
            </button>
          ))}
        </div>
        <div className="grid md:grid-cols-2 gap-space-lg">
          <div className="grid grid-cols-2 gap-space-md">
            <Field label="Patch Width (px)">
              <input type="number" className="input" value={patchWidth} onChange={(e) => setPatchWidth(Number(e.target.value))} />
            </Field>
            <Field label="Patch Height (px)">
              <input type="number" className="input" value={patchHeight} onChange={(e) => setPatchHeight(Number(e.target.value))} />
            </Field>
            <Field label="Stride X (px)">
              <input type="number" className="input" value={strideX} onChange={(e) => setStrideX(Number(e.target.value))} />
            </Field>
            <Field label="Stride Y (px)">
              <input type="number" className="input" value={strideY} onChange={(e) => setStrideY(Number(e.target.value))} />
            </Field>
            <div className="col-span-2 text-label-md text-on-surface-variant font-mono">
              Tile Overlap: {Number.isFinite(overlapPct) ? overlapPct : 0}%
            </div>
            <Field label="Minimum Tissue Threshold">
              <div className="flex items-center gap-space-sm">
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={minTissue}
                  onChange={(e) => setMinTissue(Number(e.target.value))}
                  className="flex-1"
                />
                <span className="font-mono text-label-md w-12">{minTissue}%</span>
              </div>
            </Field>
            <div className="col-span-2 flex flex-col gap-space-sm">
              <Toggle label="Allow Partial Patches" checked={allowPartial} onChange={setAllowPartial} />
              <Toggle label="Include Glass Edge Boundaries" checked={includeEdge} onChange={setIncludeEdge} />
            </div>
          </div>
          <PatchGridPreview patchWidth={patchWidth} strideX={strideX} minTissue={minTissue} />
        </div>
      </Card>

      {/* Step 4 */}
      <Card className="p-space-lg" id="step-4">
        <SectionTitle n={4} title="Tissue Segmentation & Filtering Pipeline" />
        <div className="text-body-md text-on-surface-variant mb-space-md">HSV + Otsu Thresholding with morphological cleanup</div>
        <div className="grid md:grid-cols-4 gap-space-md">
          <Field label="Otsu Sensitivity">
            <input
              type="range"
              min={0.1}
              max={0.95}
              step={0.01}
              value={otsuSensitivity}
              onChange={(e) => setOtsuSensitivity(Number(e.target.value))}
            />
            <span className="font-mono text-label-sm">{otsuSensitivity.toFixed(2)}</span>
          </Field>
          <Field label="Morph Open (px)">
            <input type="number" className="input" value={morphOpen} onChange={(e) => setMorphOpen(Number(e.target.value))} />
          </Field>
          <Field label="Morph Close (px)">
            <input type="number" className="input" value={morphClose} onChange={(e) => setMorphClose(Number(e.target.value))} />
          </Field>
          <Field label="Min Component (px²)">
            <input type="number" className="input" value={minComponentPx} onChange={(e) => setMinComponentPx(Number(e.target.value))} />
          </Field>
        </div>
      </Card>

        </>
      )}

      {/* Step 5 + 6 + 7 bento */}
      <div className="grid md:grid-cols-3 gap-space-md">
        <Card className="p-space-lg" id="step-5">
          <SectionTitle n={stepNumber("step-5")} title="Diagnostic Classes" />
          <div className="flex flex-col gap-space-sm mt-space-md">
            {classes.map((cls, i) => (
              <div key={i} className="flex items-center gap-space-sm">
                <input
                  type="color"
                  value={cls.color_hex}
                  onChange={(e) => updateClass(i, { color_hex: e.target.value })}
                  className="w-7 h-7 rounded border-0 cursor-pointer"
                />
                <input
                  className="input flex-1"
                  value={cls.name}
                  onChange={(e) => updateClass(i, { name: e.target.value })}
                />
                <input
                  className="input w-12 text-center font-mono"
                  value={cls.hotkey ?? ""}
                  onChange={(e) => updateClass(i, { hotkey: e.target.value })}
                  maxLength={2}
                />
                <button onClick={() => removeClass(i)} className="text-error">
                  <MaterialIcon name="close" className="!text-[16px]" />
                </button>
              </div>
            ))}
            <Button variant="ghost" icon="add" onClick={addClass}>
              Add Diagnostic Category
            </Button>
          </div>
        </Card>

        <Card className="p-space-lg" id="step-6">
          <SectionTitle n={stepNumber("step-6")} title="Clinical Tool Suite" />
          <div className="grid grid-cols-2 gap-space-sm mt-space-md">
            {MVP_TOOLS.map((t) => {
              const on = enabledTools.includes(t.id);
              return (
                <button
                  key={t.id}
                  onClick={() =>
                    setEnabledTools((cur) => (on ? cur.filter((x) => x !== t.id) : [...cur, t.id]))
                  }
                  className={`flex flex-col items-center gap-1 p-space-sm rounded border ${
                    on ? "border-primary bg-primary-fixed/40" : "border-outline-variant"
                  }`}
                >
                  <MaterialIcon name={t.icon} />
                  <span className="text-label-sm text-center">{t.label}</span>
                </button>
              );
            })}
            {PLANNED_TOOLS.map((t) => (
              <div
                key={t.id}
                title="Planned -- not available in this MVP"
                className="flex flex-col items-center gap-1 p-space-sm rounded border border-outline-variant opacity-40 cursor-not-allowed"
              >
                <MaterialIcon name={t.icon} />
                <span className="text-label-sm text-center">{t.label}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-space-lg" id="step-7">
          <SectionTitle n={stepNumber("step-7")} title="Quality Control Protocol" />
          <div className="flex flex-col gap-space-sm mt-space-md">
            <Toggle label="Allow Skip" checked={allowSkip} onChange={setAllowSkip} />
            <Toggle label="Allow Unsure Flag" checked={allowUnsure} onChange={setAllowUnsure} />
            <Toggle label="Require Annotation Before Advance" checked={requireAnnotation} onChange={setRequireAnnotation} />
            <Toggle label="Reviewer Mode (Second-pass QA)" checked={reviewerMode} onChange={setReviewerMode} />
          </div>
        </Card>
      </div>

      {/* Sticky footer */}
      <div className="fixed bottom-4 left-14 right-4 z-30 flex justify-center pointer-events-none">
        <div className="pointer-events-auto bg-surface-container-lowest rounded-xl shadow-xl border border-outline-variant px-space-lg py-space-md flex items-center gap-space-md">
          <span className="text-body-sm text-on-surface-variant hidden sm:block">
            {isImage
              ? `${classes.length} classes · image project`
              : `${classes.length} classes · ${patchWidth}x${patchHeight} @ ${targetMag}x · ${minTissue}% tissue`}
          </span>
          <Button variant="primary" icon="rocket_launch" onClick={handleCreate} disabled={submitting}>
            {submitting ? "Creating..." : "Create Project"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ n, title }: { n: number; title: string }) {
  return (
    <div className="flex items-center gap-space-sm">
      <span className="w-6 h-6 rounded-full bg-primary text-on-primary text-label-sm flex items-center justify-center font-headline-sm">
        {n}
      </span>
      <h2 className="font-headline-md text-headline-md text-on-surface">{title}</h2>
    </div>
  );
}

function PatchGridPreview({
  patchWidth,
  strideX,
  minTissue,
}: {
  patchWidth: number;
  strideX: number;
  minTissue: number;
}) {
  return (
    <div className="rounded-lg bg-surface-container-low p-space-md flex flex-col gap-space-sm">
      <span className="text-label-md text-on-surface-variant">Geometric Grid Preview</span>
      <svg viewBox="0 0 200 140" className="w-full h-40">
        <defs>
          <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
            <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#cbd5e1" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="200" height="140" fill="url(#grid)" />
        <rect x="40" y="30" width="60" height="60" fill="#007bb9" fillOpacity="0.35" stroke="#007bb9" strokeWidth="1.5" />
        <rect
          x={40 + (strideX / Math.max(patchWidth, 1)) * 60}
          y="30"
          width="60"
          height="60"
          fill="none"
          stroke="#008096"
          strokeWidth="1.5"
          strokeDasharray="3 2"
        />
        <rect x="120" y="70" width="40" height="40" fill="#ba1a1a" fillOpacity="0.15" stroke="#ba1a1a" strokeWidth="1.5" />
      </svg>
      <div className="flex items-center gap-space-md text-label-sm text-on-surface-variant flex-wrap">
        <LegendDot color="#007bb9" label="Selected Patch" />
        <LegendDot color="#008096" label="Next Stride" />
        <LegendDot color="#ba1a1a" label={`Excluded (<${minTissue}%)`} />
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}
