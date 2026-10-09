import type { RecipeSetting } from "../../types/api";

export type SettingValue = string | number | boolean;

/** A recipe's settings at their defaults. */
export function defaultSettings(settings: RecipeSetting[]): Record<string, SettingValue> {
  return Object.fromEntries(settings.map((s) => [s.key, s.default]));
}

/** One setting of a model recipe (recipe.json), as the form control its type calls for. */
export function SettingField({
  spec,
  value,
  onChange,
  disabled = false,
}: {
  spec: RecipeSetting;
  value: SettingValue | undefined;
  onChange: (v: SettingValue) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-label-md text-on-surface-variant">{spec.label}</span>
      {spec.type === "choice" ? (
        <select className="input" value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          {(spec.choices ?? []).map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      ) : spec.type === "bool" ? (
        <input type="checkbox" className="w-4 h-4" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      ) : (
        <input
          type="number"
          className="input"
          min={spec.min}
          max={spec.max}
          step={spec.type === "int" ? 1 : "any"}
          disabled={disabled}
          value={typeof value === "number" && Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
        />
      )}
      {spec.help && <span className="text-label-sm text-on-surface-variant">{spec.help}</span>}
    </label>
  );
}
