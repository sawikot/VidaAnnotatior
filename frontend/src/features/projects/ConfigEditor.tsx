import { useEffect, useMemo, useState } from "react";
import { Field, Toggle } from "../../components/formControls";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button } from "../../components/primitives";
import { updateConfig } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { ConfigVersion, ProjectType } from "../../types/api";
import { MVP_TOOLS } from "./constants";
import { diffDraft, draftFromConfig, validateDraft, type ConfigDraft } from "./configDraft";

interface Props {
  config: ConfigVersion;
  /** Image projects have no patch grid or tissue detection to edit. */
  projectType?: ProjectType;
  onSaved: (config: ConfigVersion) => void;
}

/**
 * The project's configuration -- slide handling, tissue detection, classes, tools, QC -- saved in place.
 * Patch sizes (grid, stride, magnification, tissue threshold) are managed in PatchSizesPanel.
 */
export function ConfigEditor({ config, projectType = "wsi", onSaved }: Props) {
  const isImage = projectType === "image";
  const pushToast = useUiStore((s) => s.pushToast);

  const [draft, setDraft] = useState<ConfigDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(draftFromConfig(config));
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.id, config.updated_at]);

  const diff = useMemo(() => (draft ? diffDraft(draft, config) : null), [draft, config]);
  const problem = draft ? validateDraft(draft) : null;

  if (!draft || !diff) return null;

  const canSave = !busy && !problem && diff.changed;

  const set = <K extends keyof ConfigDraft>(key: K, value: ConfigDraft[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));
  const setNum = (key: keyof ConfigDraft) => (e: React.ChangeEvent<HTMLInputElement>) =>
    set(key, (e.target.value === "" ? NaN : Number(e.target.value)) as never);

  function updateClass(i: number, patch: Partial<ConfigDraft["classes"][number]>) {
    set("classes", draft!.classes.map((k, idx) => (idx === i ? { ...k, ...patch } : k)));
  }

  async function handleSave() {
    if (!diff) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await updateConfig(config.id, {
        ...diff.fields,
        ...(diff.classes ? { annotation_classes: diff.classes } : {}),
      });
      pushToast("Settings saved", "success");
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-space-lg">
        {!isImage && (
        <>
        <Section title="Slides">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md">
            <Field label="MPP handling">
              <select className="input" value={draft.mpp_handling} onChange={(e) => set("mpp_handling", e.target.value)}>
                <option value="auto">Auto (slide metadata)</option>
                <option value="manual">Manual override</option>
              </select>
            </Field>
          </div>
          <p className="text-body-sm text-on-surface-variant">Patch size, stride and magnification are set under <em>Patch sizes</em> above.</p>
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
                <input
                  className="input w-32 font-mono"
                  inputMode="numeric"
                  value={k.code ?? ""}
                  onChange={(e) => updateClass(i, { code: e.target.value.replace(/\D/g, "") })}
                  aria-label="Class ID"
                  placeholder="Class ID (optional)"
                  title="Optional: your own ID for this class (any length, e.g. a Cytomine term ID). Imports match term/category IDs against it."
                />
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
                onClick={() => set("classes", [...draft.classes, { name: "", color_hex: "#64748b" }])}
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

        {(problem || error) && (
          <div className="rounded bg-error-container text-on-error-container px-space-md py-space-sm text-body-md" role="alert">
            {error ?? problem}
          </div>
        )}

        <div className="flex items-center justify-end gap-space-sm sticky bottom-0 bg-surface-container-lowest py-space-sm">
          {diff.changed && (
            <Button variant="ghost" onClick={() => setDraft(draftFromConfig(config))} disabled={busy}>
              Discard changes
            </Button>
          )}
          <Button variant="primary" icon="save" onClick={handleSave} disabled={!canSave}>
            {busy ? "Saving..." : "Save changes"}
          </Button>
        </div>
    </div>
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

