import { MaterialIcon } from "../../components/MaterialIcon";
import { Card } from "../../components/primitives";
import { GridForm } from "../grids/GridForm";
import type { GridSpec } from "../../types/api";
import { CLASSIFICATION_FORMAT, isCombinable, type ExportOptions, type PatchScope } from "../../utils/exportOptions";

interface Props {
  options: ExportOptions;
  onChange: (next: ExportOptions) => void;
  /** The files chosen for download (format ids). */
  formats: string[];
  isImageProject: boolean;
  /** How many slides are chosen: combining only means something for more than one. */
  slideCount: number;
  /** Where a custom grid starts from: the project's grid. */
  defaultGrid?: GridSpec | null;
}

/** How the chosen slides are cut and which of their patches go in: grid, patch scope, classes, combining. */
export function ExportOptionsPanel({ options, onChange, formats, isImageProject, slideCount, defaultGrid }: Props) {
  const patch = isImageProject ? "image" : "patch";
  const plural = isImageProject ? "images" : "patches";
  const set = (over: Partial<ExportOptions>) => onChange({ ...options, ...over });
  const images = options.content === "images";
  const combinable = formats.filter(isCombinable);
  const combineEffective = options.combine ?? (isImageProject || images);
  const showCombine = slideCount > 1 && combinable.length > 0;
  const noun = isImageProject ? "images" : "slides";

  const scopes: { id: PatchScope; title: string; body: string }[] = [
    { id: "annotated", title: `Annotated ${plural} only`, body: `${patch === "image" ? "Images" : "Patches"} with at least one annotation or a label` },
    { id: "all", title: `All ${plural}`, body: `Every ${patch}, including empty ones (useful as negatives)` },
    { id: "empty", title: `Empty ${plural} only`, body: `${patch === "image" ? "Images" : "Patches"} with no annotation at all` },
    { id: "reviewed", title: "Reviewed only", body: "QA-approved, including confirmed negatives" },
  ];

  return (
    <Card className="p-space-lg flex flex-col gap-space-lg">
      {!isImageProject && (
        <div>
          <div className="text-label-md text-on-surface-variant mb-space-sm">Patch grid</div>
          <div role="radiogroup" aria-label="Patch grid" className="grid sm:grid-cols-2 gap-space-sm">
            <OptionCard
              selected={!options.grid}
              onSelect={() => set({ grid: null })}
              title="As annotated"
              body="Each slide's patches, in the patch size it is annotated in"
            />
            <OptionCard
              selected={!!options.grid}
              onSelect={() => defaultGrid && set({ grid: options.grid ?? { ...defaultGrid } })}
              title="Custom grid"
              body="Another patch size, stride or magnification -- cut at export time, nothing is saved"
            />
          </div>
          {options.grid && (
            <div className="mt-space-md flex flex-col gap-space-sm">
              <GridForm value={options.grid} onChange={(grid) => set({ grid })} />
              <Hint>
                Each slide is cut into this grid from its tissue mask when you download, and every annotation -- whatever
                patch size it was drawn in -- is cut into the new {plural}. Nothing in the project changes.
              </Hint>
            </div>
          )}
        </div>
      )}

      <div>
        <div className="text-label-md text-on-surface-variant mb-space-sm">Which {plural} to include</div>
        <div role="radiogroup" aria-label="Patches to include" className="grid sm:grid-cols-2 xl:grid-cols-4 gap-space-sm">
          {scopes.map((s) => (
            <OptionCard key={s.id} selected={options.patches === s.id} onSelect={() => set({ patches: s.id })} title={s.title} body={s.body} />
          ))}
        </div>
        {formats.includes("geojson") && (options.patches === "all" || options.patches === "empty") && (
          <Hint>GeoJSON holds shapes only, so empty {plural} add nothing to it; they are still written as images if you include images.</Hint>
        )}
        {formats.includes("stats_csv") && options.patches !== "annotated" && (
          <Hint>Statistics count only the shapes on the selected {plural}; the slide-level totals still describe the whole grid.</Hint>
        )}
      </div>

      {formats.includes(CLASSIFICATION_FORMAT) && (
        <div data-testid="classification-options">
          <div className="text-label-md text-on-surface-variant mb-space-sm">Patch classification: class of each {patch}</div>
          <p className="text-body-sm text-on-surface-variant max-w-3xl mb-space-sm">
            A {patch} with a {patch === "image" ? "Image" : "Patch"} Label takes that label. Otherwise it takes the drawn class covering at
            least this share of it:
          </p>
          <div className="flex items-center gap-space-md max-w-md">
            <input
              type="range"
              min={50}
              max={100}
              step={5}
              value={Math.round(options.minCoverage * 100)}
              onChange={(e) => set({ minCoverage: Number(e.target.value) / 100 })}
              aria-label="Minimum class coverage"
              className="flex-1"
            />
            <span className="font-mono text-body-md w-12 text-right">{Math.round(options.minCoverage * 100)}%</span>
          </div>
          <div className="mt-space-md text-label-md text-on-surface-variant mb-space-sm">{patch === "image" ? "Images" : "Patches"} with no clear class</div>
          <div role="radiogroup" aria-label="Patches with no clear class" className="grid sm:grid-cols-2 gap-space-sm">
            <OptionCard selected={options.unlabeled === "skip"} onSelect={() => set({ unlabeled: "skip" })} title="Leave them out" body="Only classified patches reach the dataset" />
            <OptionCard
              selected={options.unlabeled === "folder"}
              onSelect={() => set({ unlabeled: "folder" })}
              title="Put them in unlabeled"
              body="An unlabeled folder, for review or semi-supervised training"
            />
          </div>
          <label className="mt-space-md flex items-center gap-space-sm text-body-md cursor-pointer">
            <input type="checkbox" className="w-4 h-4" checked={options.otherLabels} onChange={(e) => set({ otherLabels: e.target.checked })} />
            Include Mixed and Artifact / Background labels, each in its own folder
          </label>
        </div>
      )}

      {showCombine && (
        <label className="flex items-start gap-space-sm text-body-md cursor-pointer">
          <input type="checkbox" className="w-4 h-4 mt-1" checked={combineEffective} onChange={(e) => set({ combine: e.target.checked })} />
          <span>
            Combine all chosen {noun} into one file per format ({combinable.join(", ")})
            <span className="block text-body-sm text-on-surface-variant">
              What a training pipeline wants. Unchecked gives one file per {isImageProject ? "image" : "slide"}.
            </span>
          </span>
        </label>
      )}
    </Card>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="mt-space-sm text-body-sm text-on-surface-variant max-w-3xl">{children}</p>;
}

export function OptionCard({ selected, onSelect, title, body }: { selected: boolean; onSelect: () => void; title: string; body: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={`text-left p-space-md rounded-xl flex gap-space-sm items-start transition-shadow ${
        selected ? "ring-2 ring-primary bg-primary-fixed/30" : "bg-surface-container-low hover:shadow-md"
      }`}
    >
      <MaterialIcon name={selected ? "radio_button_checked" : "radio_button_unchecked"} className="text-primary mt-0.5 shrink-0" />
      <span className="flex flex-col gap-0.5">
        <span className="font-headline-sm text-headline-sm">{title}</span>
        <span className="text-body-sm text-on-surface-variant">{body}</span>
      </span>
    </button>
  );
}
