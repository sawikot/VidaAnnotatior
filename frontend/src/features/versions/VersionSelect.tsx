import { useEffect, useState } from "react";
import { MaterialIcon } from "../../components/MaterialIcon";
import { listProjectVersions } from "../../services/api";
import type { ProjectVersions } from "../../types/api";

/**
 * Which state of the project a screen reads: the work as it is now (`null`, the default -- whatever
 * version is open on the dashboard, with any unsaved changes) or one of the saved versions. Choosing
 * a version here only changes what this screen reads; the project itself stays on the version that is
 * open. Shows nothing while the project has no saved version.
 */
export function VersionSelect({
  projectId,
  value,
  onChange,
  purpose,
  disabled,
}: {
  projectId: number;
  value: number | null;
  onChange: (versionId: number | null) => void;
  /** What is done with the choice, e.g. "export" or "train on". */
  purpose: string;
  disabled?: boolean;
}) {
  const [data, setData] = useState<ProjectVersions | null>(null);

  useEffect(() => {
    let stale = false;
    listProjectVersions(projectId)
      .then((d) => !stale && setData(d))
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [projectId]);

  if (!data || data.versions.length === 0) return null;

  const current = data.versions.find((v) => v.id === data.current_version_id);
  const work = current
    ? `Current work -- v${current.number} · ${current.name}${data.has_unsaved_changes ? ", with unsaved changes" : ""}`
    : "Current work (not saved as a version)";

  return (
    <div className="mt-space-md flex items-start gap-space-sm flex-wrap">
      <label className="flex items-center gap-space-sm text-body-md">
        <MaterialIcon name="history" className="!text-[18px] text-on-surface-variant" />
        <span className="text-on-surface-variant">Version to {purpose}</span>
        <select
          className="input !w-auto max-w-full"
          value={value ?? ""}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">{work}</option>
          {data.versions.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.number} · {v.name}
            </option>
          ))}
        </select>
      </label>
      {value !== null && (
        <span className="text-body-sm text-on-surface-variant self-center">
          Read from the saved version; the project stays on {current ? `v${current.number}` : "its current work"}.
        </span>
      )}
    </div>
  );
}
