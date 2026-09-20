import { useEffect, useMemo, useState } from "react";
import { Field, Toggle } from "../../components/formControls";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Modal } from "../../components/primitives";
import { forkConfig, getConfigUsage, updateConfig } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { ConfigUsage, ConfigVersion, ProjectType } from "../../types/api";
import { MVP_TOOLS } from "./constants";
import {
  diffDraft,
  draftFromConfig,
  suggestVersionLabel,
  validateDraft,
  type ConfigDraft,
} from "./configDraft";

interface Props {
  open: boolean;
  onClose: () => void;
  config: ConfigVersion | null;
  existingLabels: string[];
  /** "edit" changes this version in place where that is safe and otherwise
   * offers a new version; "fork" always creates a new version from it. */
  mode: "edit" | "fork";
  /** Image projects have no patch grid or tissue detection to edit, and never fork. */
  projectType?: ProjectType;
  onSaved: (result: { kind: "updated" | "forked"; config: ConfigVersion }) => void;
}

export function EditConfigModal({ open, onClose, config, existingLabels, mode, projectType = "wsi", onSaved }: Props) {
  const isImage = projectType === "image";
  const pushToast = useUiStore((s) => s.pushToast);
  const annotatorName = useUiStore((s) => s.annotatorName);

  const [draft, setDraft] = useState<ConfigDraft | null>(null);
  const [usage, setUsage] = useState<ConfigUsage | null>(null);
  const [usageLoaded, setUsageLoaded] = useState(false);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !config) return;
    setDraft(draftFromConfig(config));
    setLabel(suggestVersionLabel(existingLabels));
    setError(null);
    setUsage(null);
    setUsageLoaded(false);
    // Whether this version already has data decides in-place vs. new version,
    // so Save stays disabled until we know. If the lookup fails we still allow
    // saving -- the server enforces the same rule and answers 409.
    getConfigUsage(config.id)
      .then(setUsage)
      .catch(() => setUsage(null))
      .finally(() => setUsageLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, config?.id]);

  const diff = useMemo(() => (draft && config ? diffDraft(draft, config) : null), [draft, config]);
  const problem = draft ? validateDraft(draft) : null;

  if (!config || !draft || !diff) return null;

  const hasData = (usage?.patch_count ?? 0) > 0;
  const geometryLocked = hasData || config.status === "locked";
  const willFork = !isImage && (mode === "fork" || (geometryLocked && diff.touchedCritical.length > 0));
  const labelTaken = willFork && existingLabels.includes(label.trim());
  const labelOk = !willFork || (!!label.trim() && !labelTaken);
  const canSave = !busy && usageLoaded && !problem && labelOk && (mode === "fork" || diff.changed);

  const set = <K extends keyof ConfigDraft>(key: K, value: ConfigDraft[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));
  const setNum = (key: keyof ConfigDraft) => (e: React.ChangeEvent<HTMLInputElement>) =>
    set(key, (e.target.value === "" ? NaN : Number(e.target.value)) as never);

  function updateClass(i: number, patch: Partial<ConfigDraft["classes"][number]>) {
    set("classes", draft!.classes.map((k, idx) => (idx === i ? { ...k, ...patch } : k)));
  }

  async function handleSave() {
    if (!draft || !config || !diff) return;
    setBusy(true);
    setError(null);
    try {
      if (willFork) {
        const created = await forkConfig(config.id, {
          new_version_label: label.trim(),
          overrides: diff.fields,
          created_by: annotatorName,
          annotation_classes: diff.classes ?? undefined,
        });
        pushToast(`Created ${created.version_label} -- ${config.version_label} is unchanged`, "success");
        onSaved({ kind: "forked", config: created });
      } else {
        const updated = await updateConfig(config.id, {
          ...diff.fields,
          ...(diff.classes ? { annotation_classes: diff.classes } : {}),
        });
        pushToast(`${updated.version_label} updated`, "success");
        onSaved({ kind: "updated", config: updated });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "fork" ? `New version from ${config.version_label}` : `Edit ${config.version_label}`;

  return (
    <Modal open={open} onClose={onClose} widthClass="max-w-3xl">
      <div className="p-space-lg flex flex-col gap-space-lg">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-md text-headline-md">{title}</h2>
          <button onClick={onClose} aria-label="Close">
            <MaterialIcon name="close" />
          </button>
        </div>

        {mode === "fork" ? (
          <p className="text-body-md text-on-surface-variant">
            Creates an independent version starting from {config.version_label}'s settings. Nothing already generated
            or annotated is touched.
          </p>
        ) : geometryLocked && !isImage ? (
          <div className="flex gap-space-sm rounded bg-surface-container-low border-l-4 border-primary p-space-md text-body-md">
            <MaterialIcon name="lock" className="text-primary shrink-0" />
            <span>
              {hasData ? (
                <>
                  {config.version_label} has <strong>{usage?.patch_count.toLocaleString()}</strong> generated patches and{" "}
                  <strong>{usage?.annotation_count.toLocaleString()}</strong> annotations.
                </>
              ) : (
                <>{config.version_label} is locked.</>
              )}{" "}
              Everything below except patch size, stride, magnification and tissue threshold can be changed in place.
              Changing those saves as a <strong>new version</strong>, so existing annotations keep the geometry they
              were drawn against.
            </span>
          </div>
        ) : null}

        <Section title="General">
          <Field label="Version title">
            <input className="input" value={draft.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Standard clinical 20x lattice" />
          </Field>
        </Section>

        {!isImage && (
        <>
        <Section title="WSI & patch grid">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md">
            <Field label="Target magnification (x)">
              <input type="number" className="input" value={Number.isNaN(draft.target_magnification) ? "" : draft.target_magnification} onChange={setNum("target_magnification")} />
            </Field>
            <Field label="Patch width (px)">
              <input type="number" className="input" value={Number.isNaN(draft.patch_width) ? "" : draft.patch_width} onChange={setNum("patch_width")} />
            </Field>
            <Field label="Patch height (px)">
              <input type="number" className="input" value={Number.isNaN(draft.patch_height) ? "" : draft.patch_height} onChange={setNum("patch_height")} />
            </Field>
            <Field label="Min tissue (%)">
              <input type="number" className="input" value={Number.isNaN(draft.min_tissue_pct) ? "" : draft.min_tissue_pct} onChange={setNum("min_tissue_pct")} />
            </Field>
            <Field label="Stride X (px)">
              <input type="number" className="input" value={Number.isNaN(draft.stride_x) ? "" : draft.stride_x} onChange={setNum("stride_x")} />
            </Field>
            <Field label="Stride Y (px)">
              <input type="number" className="input" value={Number.isNaN(draft.stride_y) ? "" : draft.stride_y} onChange={setNum("stride_y")} />
            </Field>
            <Field label="MPP handling">
              <select className="input" value={draft.mpp_handling} onChange={(e) => set("mpp_handling", e.target.value)}>
                <option value="auto">Auto (slide metadata)</option>
                <option value="manual">Manual override</option>
              </select>
            </Field>
          </div>
          <div className="flex flex-wrap gap-x-space-xl gap-y-space-sm">
            <Toggle label="Allow partial patches" checked={draft.allow_partial_patches} onChange={(v) => set("allow_partial_patches", v)} />
            <Toggle label="Include glass-edge patches" checked={draft.include_edge_patches} onChange={(v) => set("include_edge_patches", v)} />
          </div>
          {!geometryLocked && (
            <p className="text-body-sm text-on-surface-variant">
              Grid changes take effect the next time you press <em>Generate Coords</em> on a slide.
            </p>
          )}
        </Section>

        <Section title="Tissue detection (HSV + Otsu defaults)">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md">
            <Field label="Otsu sensitivity">
              <input type="number" step="0.05" min="0.1" max="0.95" className="input" value={Number.isNaN(draft.otsu_sensitivity) ? "" : draft.otsu_sensitivity} onChange={setNum("otsu_sensitivity")} />
            </Field>
            <Field label="Morph open (px)">
              <input type="number" className="input" value={Number.isNaN(draft.morph_open_px) ? "" : draft.morph_open_px} onChange={setNum("morph_open_px")} />
            </Field>
            <Field label="Morph close (px)">
              <input type="number" className="input" value={Number.isNaN(draft.morph_close_px) ? "" : draft.morph_close_px} onChange={setNum("morph_close_px")} />
            </Field>
            <Field label="Min component (px²)">
              <input type="number" className="input" value={Number.isNaN(draft.min_component_px) ? "" : draft.min_component_px} onChange={setNum("min_component_px")} />
            </Field>
          </div>
        </Section>

        </>
        )}

        <Section title="Diagnostic classes">
          <div className="flex flex-col gap-space-sm">
            {draft.classes.map((k, i) => (
              <div key={k.id ?? `new-${i}`} className="flex items-center gap-space-sm">
                <input type="color" value={k.color_hex} onChange={(e) => updateClass(i, { color_hex: e.target.value })} className="w-8 h-8 rounded border-0 cursor-pointer" aria-label="Class color" />
                <input className="input flex-1" value={k.name} onChange={(e) => updateClass(i, { name: e.target.value })} aria-label="Class name" />
                <input className="input w-14 text-center font-mono" maxLength={2} value={k.hotkey} onChange={(e) => updateClass(i, { hotkey: e.target.value })} aria-label="Hotkey" placeholder="key" />
                <button
                  type="button"
                  className="text-error"
                  aria-label={`Remove ${k.name || "class"}`}
                  onClick={() => set("classes", draft.classes.filter((_, idx) => idx !== i))}
                >
                  <MaterialIcon name="close" className="!text-[18px]" />
                </button>
              </div>
            ))}
            <div className="flex items-center justify-between">
              <Button
                variant="ghost"
                icon="add"
                onClick={() => set("classes", [...draft.classes, { name: "", color_hex: "#64748b", hotkey: String(draft.classes.length + 1) }])}
              >
                Add class
              </Button>
              <span className="text-body-sm text-on-surface-variant">
                Renaming or recoloring keeps existing annotations. A class that annotations still use can't be removed.
              </span>
            </div>
          </div>
        </Section>

        <Section title="Annotation tools">
          <div className="flex flex-wrap gap-space-sm">
            {MVP_TOOLS.map((t) => {
              const on = draft.enabled_tools.includes(t.id);
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => set("enabled_tools", on ? draft.enabled_tools.filter((x) => x !== t.id) : [...draft.enabled_tools, t.id])}
                  className={`flex items-center gap-1.5 px-space-md h-8 rounded border text-label-md ${on ? "border-primary bg-primary-fixed/40" : "border-outline-variant text-on-surface-variant"}`}
                >
                  <MaterialIcon name={t.icon} className="!text-[16px]" />
                  {t.label}
                </button>
              );
            })}
          </div>
        </Section>

        <Section title="Quality control">
          <div className="grid md:grid-cols-2 gap-space-sm">
            <Toggle label="Allow skip" checked={draft.allow_skip} onChange={(v) => set("allow_skip", v)} />
            <Toggle label="Allow unsure flag" checked={draft.allow_unsure} onChange={(v) => set("allow_unsure", v)} />
            <Toggle label="Require annotation before advancing" checked={draft.require_annotation} onChange={(v) => set("require_annotation", v)} />
            <Toggle label="Reviewer mode (second-pass QA)" checked={draft.reviewer_mode} onChange={(v) => set("reviewer_mode", v)} />
          </div>
        </Section>

        {willFork && (
          <div className="rounded border border-outline-variant p-space-md flex flex-col gap-space-sm">
            {mode === "edit" && (
              <div className="flex gap-space-sm text-body-md">
                <MaterialIcon name="call_split" className="text-error shrink-0" />
                <span>
                  You changed <strong>{diff.touchedCritical.join(", ")}</strong>. Saving will create a new version
                  with all of your edits and leave {config.version_label} exactly as it is.
                </span>
              </div>
            )}
            <Field label="New version label">
              <input className="input font-mono max-w-[12rem]" value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            {labelTaken && <span className="text-body-sm text-error">A version named {label.trim()} already exists.</span>}
            <span className="text-body-sm text-on-surface-variant">
              To use it on an existing slide, pick it in that slide's <em>Config version</em> selector on Slide
              Processing, then run <em>Generate Coords</em>.
            </span>
          </div>
        )}

        {(problem || error) && (
          <div className="rounded bg-error-container text-on-error-container px-space-md py-space-sm text-body-md" role="alert">
            {error ?? problem}
          </div>
        )}

        <div className="flex items-center justify-end gap-space-sm">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon={willFork ? "call_split" : "save"} onClick={handleSave} disabled={!canSave}>
            {busy ? "Saving..." : willFork ? "Save as new version" : "Save changes"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-space-sm">
      <h3 className="font-headline-sm text-headline-sm text-on-surface border-b border-outline-variant pb-1">{title}</h3>
      {children}
    </section>
  );
}

