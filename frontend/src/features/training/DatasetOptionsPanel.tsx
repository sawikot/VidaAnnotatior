import type { DatasetOptions } from "../../types/api";

export const DEFAULT_DATASET: DatasetOptions = {
  patch_size: null,
  stride: null,
  area: "tissue",
  use: "annotated",
  empty_percent: 0,
  empty_from: "any",
  class_ids: null,
};

/** What a run's dataset options come to, in a line or two (for a finished run's page). */
export function describeDataset(o: DatasetOptions | null | undefined, classNames: (ids: number[]) => string): string[] {
  if (!o) return [];
  const out = [
    o.patch_size
      ? `Patches of ${o.patch_size} px${o.stride && o.stride !== o.patch_size ? `, stride ${o.stride} px` : ""}, from ${o.area === "whole" ? "the whole slide" : "the tissue"}`
      : "Patches as annotated",
  ];
  if (o.use === "reviewed") out.push("Only reviewed ones");
  if (o.empty_percent > 0) out.push(`${o.empty_percent} empty for every 100 annotated${o.empty_from === "reviewed" ? " (reviewed ones only)" : ""}`);
  if (o.class_ids) out.push(`Classes: ${classNames(o.class_ids)}`);
  return out;
}

/**
 * How a run's dataset is cut from the slides and which patches go into it: the patch size and stride,
 * tissue or whole slide, empty patches as background, and the classes to learn. Slides are always cut
 * afresh for the dataset (nothing in the project changes), so the Reviewed mark of the project's own
 * patches plays no part; an image project has no patches to cut and offers that choice instead.
 */
export function DatasetOptionsPanel({
  value,
  onChange,
  task,
  isImageProject,
  projectClasses,
  disabled = false,
}: {
  value: DatasetOptions;
  onChange: (next: DatasetOptions) => void;
  task: string;
  isImageProject: boolean;
  projectClasses: { id: number; name: string }[];
  disabled?: boolean;
}) {
  const set = (patch: Partial<DatasetOptions>) => onChange({ ...value, ...patch });
  const custom = value.patch_size !== null;
  const noun = isImageProject ? "image" : "patch";
  const nouns = isImageProject ? "images" : "patches";
  const classifier = task === "classification";
  const chosen = value.class_ids ?? projectClasses.map((c) => c.id);

  function toggleClass(id: number) {
    const next = chosen.includes(id) ? chosen.filter((c) => c !== id) : [...chosen, id];
    // Every class ticked is "all of them": new classes then join by themselves.
    set({ class_ids: next.length === projectClasses.length ? null : next });
  }

  return (
    <div className="grid lg:grid-cols-2 gap-x-space-lg gap-y-space-md" data-testid="dataset-options">
      {!isImageProject && (
        <fieldset disabled={disabled} className="flex flex-col gap-space-sm min-w-0">
          <legend className="text-label-md text-on-surface-variant mb-1">Patches</legend>
          <span className="text-body-sm text-on-surface-variant">
            Every slide is cut into patches of this size for the training, and each annotation is cut into them. Nothing in the project changes.
          </span>
          {custom && (
            <div className="flex flex-col gap-space-sm">
              <div className="flex items-end gap-space-md flex-wrap">
                <label className="flex flex-col gap-1">
                  <span className="text-label-sm text-on-surface-variant">Patch size (px)</span>
                  <input
                    type="number"
                    className="input !w-28"
                    min={32}
                    max={8192}
                    step={32}
                    value={value.patch_size ?? ""}
                    onChange={(e) => set({ patch_size: e.target.value === "" ? NaN : Number(e.target.value) })}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-label-sm text-on-surface-variant">Stride (px)</span>
                  <input
                    type="number"
                    className="input !w-28"
                    min={8}
                    max={8192}
                    step={8}
                    placeholder={String(value.patch_size || "")}
                    value={value.stride ?? ""}
                    onChange={(e) => set({ stride: e.target.value === "" ? null : Number(e.target.value) })}
                  />
                </label>
              </div>
              <span className="text-label-sm text-on-surface-variant">
                Stride is how far apart patches start. Left empty it equals the size (no overlap); smaller makes patches overlap, so an object near
                an edge is seen whole in a neighbour.
              </span>
              <div role="radiogroup" aria-label="Area to cut patches from" className="flex gap-space-sm flex-wrap">
                {(
                  [
                    ["tissue", "Tissue only", "Where the slide's tissue mask is"],
                    ["whole", "Whole slide", "Everything, glass included"],
                  ] as const
                ).map(([id, title, body]) => (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={value.area === id}
                    onClick={() => set({ area: id })}
                    className={`text-left px-space-md py-space-sm rounded-lg text-body-sm ${value.area === id ? "ring-2 ring-primary bg-surface-container-lowest" : "bg-surface-container-low hover:bg-surface-container-high"}`}
                  >
                    <span className="block font-headline-sm text-label-lg">{title}</span>
                    <span className="block text-on-surface-variant">{body}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </fieldset>
      )}

      <div className="flex flex-col gap-space-md min-w-0">
        {isImageProject && (
        <fieldset disabled={disabled} className="flex flex-col gap-space-sm">
          <legend className="text-label-md text-on-surface-variant mb-1">Which annotated {nouns}</legend>
          <label className="flex items-center gap-space-sm text-body-sm cursor-pointer">
            <input type="radio" checked={value.use === "annotated"} onChange={() => set({ use: "annotated" })} />
            Every annotated {noun}
          </label>
          <label className={`flex items-start gap-space-sm text-body-sm ${custom ? "opacity-50" : "cursor-pointer"}`}>
            <input type="radio" className="mt-1" checked={value.use === "reviewed"} disabled={custom} onChange={() => set({ use: "reviewed" })} />
            <span>
              Only those marked Reviewed
              <span className="block text-on-surface-variant">
                {custom ? "Not with a patch size of your own: freshly cut patches have no Reviewed mark." : `Leaves out ${nouns} that may be half annotated.`}
              </span>
            </span>
          </label>
        </fieldset>
        )}

        {!classifier && (
          <fieldset disabled={disabled} className="flex flex-col gap-space-sm">
            <legend className="text-label-md text-on-surface-variant mb-1">Empty {nouns}</legend>
            <label className="flex items-start gap-space-sm text-body-sm cursor-pointer">
              <input type="checkbox" className="mt-1 w-4 h-4" checked={value.empty_percent > 0} onChange={(e) => set({ empty_percent: e.target.checked ? 50 : 0 })} />
              <span>
                Add {nouns} with nothing in them
                <span className="block text-on-surface-variant">They teach the model what background looks like, which cuts false alarms.</span>
              </span>
            </label>
            {value.empty_percent > 0 && (
              <div className="pl-6 flex flex-col gap-space-sm">
                <label className="flex items-center gap-space-sm text-body-sm flex-wrap">
                  For every 100 annotated {nouns}, add
                  <input
                    type="number"
                    className="input !w-24"
                    min={1}
                    max={100000}
                    value={Number.isFinite(value.empty_percent) ? value.empty_percent : ""}
                    onChange={(e) => set({ empty_percent: e.target.value === "" ? NaN : Number(e.target.value) })}
                  />
                  empty ones, picked at random{custom && value.area === "whole" ? " from the whole slide" : custom ? " from the tissue" : ""}.
                </label>
                {isImageProject && (
                  <select
                    className="input !w-auto"
                    aria-label="Which empty images"
                    value={value.empty_from}
                    onChange={(e) => set({ empty_from: e.target.value as DatasetOptions["empty_from"] })}
                  >
                    <option value="any">Any {noun} without annotations</option>
                    <option value="reviewed">Only empty {nouns} marked Reviewed (confirmed empty)</option>
                  </select>
                )}
                {(custom || value.empty_from === "any") && (
                  <span className="text-label-sm text-on-surface-variant">
                    A {noun} with nothing drawn in it may still hold objects nobody has annotated yet; counted as empty, it teaches the model to
                    miss them. Add empty {nouns} once the slides are annotated completely.
                  </span>
                )}
              </div>
            )}
          </fieldset>
        )}

        {projectClasses.length > 1 && (
          <fieldset disabled={disabled} className="flex flex-col gap-1">
            <legend className="text-label-md text-on-surface-variant mb-1">Classes to learn</legend>
            <div className="flex flex-wrap gap-x-space-md gap-y-1">
              {projectClasses.map((c) => (
                <label key={c.id} className="flex items-center gap-1.5 text-body-sm cursor-pointer">
                  <input type="checkbox" className="w-4 h-4" checked={chosen.includes(c.id)} onChange={() => toggleClass(c.id)} />
                  {c.name}
                </label>
              ))}
            </div>
            <span className="text-label-sm text-on-surface-variant">Shapes of an unticked class are left out, as if they were not drawn.</span>
          </fieldset>
        )}
      </div>
    </div>
  );
}
