import { useState } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { MaterialIcon } from "./MaterialIcon";

export function Button({
  variant = "secondary",
  icon,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  icon?: string;
}) {
  const base = "inline-flex items-center gap-1.5 h-8 px-space-md rounded font-headline-sm text-label-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const variants: Record<string, string> = {
    primary: "bg-primary text-on-primary hover:bg-[#004b73]",
    secondary: "bg-surface-container-high text-on-surface hover:bg-surface-container-highest",
    ghost: "bg-transparent text-on-surface-variant hover:bg-surface-container-low",
    danger: "bg-error-container text-on-error-container hover:bg-error hover:text-on-error",
  };
  return (
    <button className={`${base} ${variants[variant]} ${className}`} {...rest}>
      {icon && <MaterialIcon name={icon} className="!text-[16px]" />}
      {children}
    </button>
  );
}

export function IconButton({
  icon,
  className = "",
  active,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; active?: boolean }) {
  return (
    <button
      className={`w-8 h-8 rounded flex items-center justify-center transition-colors disabled:opacity-30 ${
        active ? "bg-primary-container text-on-primary-container" : "text-on-surface-variant hover:bg-surface-container-low"
      } ${className}`}
      {...rest}
    >
      <MaterialIcon name={icon} />
    </button>
  );
}

export function Pill({ children, className = "", dot }: { children: ReactNode; className?: string; dot?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-space-sm py-0.5 rounded-full text-label-sm font-mono ${className}`}>
      {dot && <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: dot }} />}
      {children}
    </span>
  );
}

export function Card({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <div id={id} className={`bg-surface-container-lowest rounded shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export function CoordinateBadge({ x, y, extra }: { x: number; y: number; extra?: string }) {
  return (
    <span className="font-mono text-label-sm text-secondary tabular-nums">
      X: {x.toLocaleString()} Y: {y.toLocaleString()}
      {extra ? ` ${extra}` : ""}
    </span>
  );
}

export function Modal({
  open,
  onClose,
  children,
  widthClass = "max-w-2xl",
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  widthClass?: string;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 bg-inverse-surface/40 backdrop-blur-sm z-[90] flex items-center justify-center p-space-lg"
      onClick={onClose}
    >
      <div
        className={`bg-surface-container-lowest text-on-surface rounded-xl shadow-xl w-full ${widthClass} max-h-[90vh] overflow-y-auto`}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-800",
  review: "bg-amber-100 text-amber-800",
  completed: "bg-blue-100 text-blue-800",
  imported: "bg-slate-100 text-slate-700",
  tissue_detected: "bg-cyan-100 text-cyan-800",
  patches_generated: "bg-sky-100 text-sky-800",
  annotating: "bg-primary-fixed text-on-primary-fixed-variant",
  reviewed: "bg-tertiary-fixed text-on-tertiary-fixed-variant",
  error: "bg-red-100 text-red-800",
};

export function StatusPill({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? "bg-slate-100 text-slate-700";
  return (
    <span className={`px-space-sm py-0.5 rounded-full text-label-sm font-headline-sm capitalize ${style}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

/** A destructive-action confirmation that requires typing an exact phrase
 * (usually the resource's own name/slug) before the confirm button unlocks --
 * the same pattern GitHub uses for deleting a repository. Prevents a stray
 * click from destroying something irreversible. */
export function ConfirmDeleteModal({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmPhrase,
  confirmLabel = "Delete",
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
  title: string;
  description: ReactNode;
  confirmPhrase: string;
  confirmLabel?: string;
  busy?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === confirmPhrase;

  function handleClose() {
    setTyped("");
    onClose();
  }

  return (
    <Modal open={open} onClose={handleClose} widthClass="max-w-md">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center gap-space-sm">
          <span className="w-10 h-10 rounded-full bg-error-container flex items-center justify-center shrink-0">
            <MaterialIcon name="warning" className="text-error" />
          </span>
          <h2 className="font-headline-md text-headline-md text-on-surface">{title}</h2>
        </div>
        <div className="text-body-md text-on-surface-variant">{description}</div>
        <label className="flex flex-col gap-1">
          <span className="text-label-md text-on-surface-variant">
            Type <span className="font-mono text-on-surface">{confirmPhrase}</span> to confirm
          </span>
          <input
            className="input font-mono"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={confirmPhrase}
            autoFocus
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <div className="flex justify-end gap-space-sm">
          <Button variant="ghost" onClick={handleClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" disabled={!matches || busy} onClick={onConfirm}>
            {busy ? "Deleting..." : confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
