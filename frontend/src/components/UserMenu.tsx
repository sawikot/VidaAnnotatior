import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { MaterialIcon } from "./MaterialIcon";
import { Button, Modal } from "./primitives";
import { Field } from "./formControls";
import { changePassword } from "../services/api";
import { ROLE_LABELS, useAuthStore } from "../stores/authStore";
import { useUiStore } from "../stores/uiStore";

/** The signed-in person in the header: their name and role, and a menu to change the password or sign out. */
export function UserMenu() {
  const user = useAuthStore((s) => s.user);
  const signOut = useAuthStore((s) => s.signOut);
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;
  const initials = user.name
    .split(/\s+/)
    .filter(Boolean)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div ref={root} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg px-1 py-0.5 hover:bg-[#1e293b]"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Your account"
      >
        <span className="hidden lg:flex flex-col items-end leading-tight">
          <span className="text-label-md text-white">{user.name}</span>
          <span className="text-body-sm text-slate-400">{ROLE_LABELS[user.role] ?? user.role}</span>
        </span>
        <span className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-label-md text-white">{initials}</span>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-11 z-50 w-60 bg-surface-container-lowest text-on-surface rounded shadow-lg border border-outline-variant py-1">
          <div className="px-space-md py-space-sm border-b border-outline-variant/60">
            <div className="text-body-md">{user.name}</div>
            <div className="text-label-sm text-on-surface-variant">{user.email}</div>
          </div>
          {user.role === "admin" && (
            <>
              <MenuButton icon="group" label="Users" onClick={() => (setOpen(false), navigate("/admin/users"))} />
              <MenuButton icon="deployed_code" label="Version & updates" onClick={() => (setOpen(false), navigate("/admin/version"))} />
            </>
          )}
          <MenuButton icon="key" label="Change password" onClick={() => (setOpen(false), setPasswordOpen(true))} />
          <MenuButton
            icon="logout"
            label="Sign out"
            onClick={async () => {
              setOpen(false);
              await signOut();
              navigate("/login", { replace: true });
            }}
          />
        </div>
      )}
      <ChangePasswordModal open={passwordOpen} onClose={() => setPasswordOpen(false)} />
    </div>
  );
}

function MenuButton({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <button role="menuitem" onClick={onClick} className="w-full text-left px-space-md py-space-sm flex items-center gap-space-sm text-body-sm hover:bg-surface-container-low">
      <MaterialIcon name={icon} className="!text-[18px] text-on-surface-variant" />
      {label}
    </button>
  );
}

function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next.length < 8) return setError("The new password must be at least 8 characters.");
    if (next !== confirm) return setError("The two new passwords are not the same.");
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      onClose();
      pushToast("Password changed. Your other devices are signed out.", "success");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} widthClass="max-w-sm">
      <form onSubmit={submit} className="p-space-lg flex flex-col gap-space-md text-on-surface">
        <h2 className="font-headline-md text-headline-md">Change password</h2>
        <Field label="Current password">
          <input className="input" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field label="New password (at least 8 characters)">
          <input className="input" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Repeat the new password">
          <input className="input" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        {error && <div className="text-body-sm text-error">{error}</div>}
        <div className="flex justify-end gap-space-sm">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? "Saving..." : "Change password"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
