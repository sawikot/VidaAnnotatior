import { MaterialIcon } from "../../components/MaterialIcon";
import { Card } from "../../components/primitives";
import { formatBytes } from "../slides/uploadSelection";
import {
  isCombinable,
  type ExportOptions,
  type ExportSummary,
  type PatchScope,
} from "../../utils/exportOptions";

interface Props {
  options: ExportOptions;
  onChange: (next: ExportOptions) => void;
  format: string;
  /** "slide": just the open slide. "project": every slide of the project. */
  scope: "slide" | "project";
  isImageProject: boolean;
  summary: ExportSummary | null;
  problem: string | null;
}

export function ExportOptionsPanel({ options, onChange, format, scope, isImageProject, summary, problem }: Props) {
  const patch = isImageProject ? "image" : "patch";
  const plural = isImageProject ? "images" : "patches";
  const set = (over: Partial<ExportOptions>) => onChange({ ...options, ...over });
  const images = options.content === "images";
  const combineEffective = options.combine ?? (isImageProject || images);
  const showCombine = scope === "project" && isCombinable(format);

  const scopes: { id: PatchScope; title: string; body: string }[] = [
    { id: "annotated", title: `Annotated ${plural} only`, body: `${patch === "image" ? "Images" : "Patches"} with at least one annotation` },
    { id: "all", title: `All ${plural}`, body: `Every ${patch}, including empty ones (useful as negatives)` },
    { id: "empty", title: `Empty ${plural} only`, body: `${patch === "image" ? "Images" : "Patches"} with no annotation at all` },
    { id: "reviewed", title: "Reviewed only", body: "QA-approved, including confirmed negatives" },
  ];

  return (
    <Card className="p-space-lg flex flex-col gap-space-lg">
      <div>
        <div className="text-label-md text-on-surface-variant mb-space-sm">Which {plural} to include</div>
        <div role="radiogroup" aria-label="Patches to include" className="grid sm:grid-cols-2 xl:grid-cols-4 gap-space-sm">
          {scopes.map((s) => (
            <OptionCard key={s.id} selected={options.patches === s.id} onSelect={() => set({ patches: s.id })} title={s.title} body={s.body} />
          ))}
        </div>
        {(format === "geojson" && (options.patches === "all" || options.patches === "empty")) && (
          <Hint>GeoJSON holds shapes only, so empty {plural} add nothing to it; they are still written as images if you include images.</Hint>
        )}
        {format === "stats_csv" && options.patches !== "annotated" && (
          <Hint>Statistics count only the shapes on the selected {plural}; the slide-level totals still describe the whole grid.</Hint>
        )}
      </div>

      <div>
        <div className="text-label-md text-on-surface-variant mb-space-sm">What to export</div>
        <div role="radiogroup" aria-label="Export content" className="grid sm:grid-cols-2 gap-space-sm">
          <OptionCard
            selected={!images}
            onSelect={() => set({ content: "annotations", masks: false })}
            title="Annotation file only"
            body="Just the data, in the format chosen above"
          />
          <OptionCard
            selected={images}
            onSelect={() => set({ content: "images" })}
            title={`Annotation file + ${patch} images`}
            body={
              isImageProject
                ? "A ZIP with the annotation file and the images, re-encoded from your originals"
                : "A ZIP with the annotation file and the patch images, cut from the original slide"
            }
          />
        </div>

        {images && (
          <div className="mt-space-md flex flex-wrap items-center gap-x-space-lg gap-y-space-sm">
            <div className="flex items-center gap-space-sm">
              <span className="text-label-md text-on-surface-variant">Image format</span>
              <div role="group" aria-label="Image format" className="flex rounded-lg bg-surface-container-high p-0.5">
                {(
                  [
                    ["jpg", "JPEG · smaller"],
                    ["png", "PNG · lossless"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    aria-pressed={options.imageFormat === value}
                    onClick={() => set({ imageFormat: value })}
                    className={`px-space-md py-1 rounded-md text-label-md ${
                      options.imageFormat === value ? "bg-surface-container-lowest shadow-sm text-primary" : "text-on-surface-variant"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-space-sm text-body-md cursor-pointer">
              <input type="checkbox" className="w-4 h-4" checked={options.masks} onChange={(e) => set({ masks: e.target.checked })} />
              Also write label masks (PNG, one pixel value per class)
            </label>
          </div>
        )}
        {images && (
          <Hint>
            Images are generated for this download only; nothing is stored on the server. {patch === "image" ? "Images" : "Patches"} flagged
            &ldquo;Exclude from training&rdquo; get no image.
          </Hint>
        )}
      </div>

      {showCombine && (
        <label className="flex items-start gap-space-sm text-body-md cursor-pointer">
          <input
            type="checkbox"
            className="w-4 h-4 mt-1"
            checked={combineEffective}
            onChange={(e) => set({ combine: e.target.checked })}
          />
          <span>
            Combine all {isImageProject ? "images" : "slides"} into one {format === "coco" ? "COCO dataset file" : "table"}
            <span className="block text-body-sm text-on-surface-variant">
              What a training pipeline wants. Unchecked gives one file per {isImageProject ? "image" : "slide"}.
            </span>
          </span>
        </label>
      )}

      <div className="border-t border-outline-variant pt-space-md text-body-md">
        {problem ? (
          <div className="flex gap-space-sm text-error" role="alert">
            <MaterialIcon name="error" className="!text-[18px] shrink-0" />
            {problem}
          </div>
        ) : summary ? (
          <div className="flex flex-wrap items-center gap-x-space-lg gap-y-1 text-on-surface-variant">
            <span className="font-headline-sm text-on-surface">This export covers</span>
            {scope === "project" && <Count n={summary.slides} one="slide" many="slides" />}
            <Count n={summary.patches} one={patch} many={plural} />
            <Count n={summary.annotations} one="annotation" many="annotations" />
            {images && (
              <span>
                {summary.images.toLocaleString()} image file{summary.images === 1 ? "" : "s"} &asymp; {formatBytes(summary.approx_image_bytes)}
              </span>
            )}
          </div>
        ) : (
          <span className="text-on-surface-variant">Counting...</span>
        )}
      </div>
    </Card>
  );
}

function Count({ n, one, many }: { n: number; one: string; many: string }) {
  return (
    <span>
      <span className="font-mono text-on-surface">{n.toLocaleString()}</span> {n === 1 ? one : many}
    </span>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="mt-space-sm text-body-sm text-on-surface-variant max-w-3xl">{children}</p>;
}

function OptionCard({ selected, onSelect, title, body }: { selected: boolean; onSelect: () => void; title: string; body: string }) {
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
