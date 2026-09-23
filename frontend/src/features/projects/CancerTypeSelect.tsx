import { useState } from "react";
import { CANCER_TYPE_GROUPS, CANCER_TYPES } from "./constants";

const OTHER = "__other__";
const MAX_LENGTH = 100; // projects.organ column width

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Offer a blank choice (for editing, where the field may be cleared). */
  allowEmpty?: boolean;
}

/** Pick a cancer type from the list, or choose "Other" and type any name. */
export function CancerTypeSelect({ value, onChange, allowEmpty = false }: Props) {
  const [otherPicked, setOtherPicked] = useState(false);
  // A saved value that isn't in the list (typed earlier, or from an older version) shows in the text box.
  const isOther = otherPicked || (value !== "" && !CANCER_TYPES.includes(value));

  return (
    <div className="flex flex-col gap-1.5">
      <select
        className="input"
        value={isOther ? OTHER : value}
        onChange={(e) => {
          const picked = e.target.value;
          setOtherPicked(picked === OTHER);
          onChange(picked === OTHER ? "" : picked);
        }}
      >
        {(allowEmpty || value === "") && !isOther && <option value="">{allowEmpty ? "--" : "Select a cancer type..."}</option>}
        {CANCER_TYPE_GROUPS.map((g) => (
          <optgroup key={g.group} label={g.group}>
            {g.types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </optgroup>
        ))}
        <option value={OTHER}>Other -- type your own...</option>
      </select>
      {isOther && (
        <input
          className="input"
          value={value}
          maxLength={MAX_LENGTH}
          autoFocus={otherPicked}
          placeholder="Type the cancer type"
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
