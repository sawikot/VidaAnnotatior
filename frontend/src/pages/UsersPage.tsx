import { useEffect, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, Modal } from "../components/primitives";
import { Field } from "../components/formControls";
import { createUser, listUsers, newPasswordLink, passwordLinkUrl, updateUser, type AuthUser, type UserRole } from "../services/api";
import { ROLE_LABELS, useCan } from "../stores/authStore";
import { useUiStore } from "../stores/uiStore";

const ROLE_HELP: Record<UserRole, string> = {
  admin: "Manages users; sees and manages every project",
  manager: "Creates projects; manages settings, slides and members of their projects",
  annotator: "Annotates, labels, reviews and exports in their projects",
};

/** Administrators: everyone who can sign in -- add people, change roles, disable, send password links. */
export function UsersPage() {
  const { admin, user: me } = useCan();
  const pushToast = useUiStore((s) => s.pushToast);
  const [users, setUsers] = useState<AuthUser[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [link, setLink] = useState<{ user: AuthUser; url: string } | null>(null);

  const reload = () => listUsers().then(setUsers).catch(() => setUsers([]));
  useEffect(() => {
    if (admin) reload();
  }, [admin]);

  if (!admin) return <Navigate to="/projects" replace />;

  async function change(user: AuthUser, body: { role?: UserRole; is_active?: boolean }) {
    try {
      const updated = await updateUser(user.id, body);
      setUsers((list) => list?.map((u) => (u.id === updated.id ? updated : u)) ?? null);
      pushToast(`${updated.name} updated`, "success");
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not update", "error");
    }
  }

  async function sendLink(user: AuthUser) {
    try {
      const res = await newPasswordLink(user.id);
      if (res.password_link_token) setLink({ user: res.user, url: passwordLinkUrl(res.password_link_token) });
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not make a link", "error");
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div className="flex items-end justify-between gap-space-md flex-wrap">
        <div>
          <h1 className="font-headline-lg text-headline-lg">Users</h1>
          <p className="text-body-sm text-on-surface-variant">
            Everyone who can sign in. People see only the projects they are members of (add them in each project&apos;s Settings);
            administrators see all.
          </p>
        </div>
        <Button variant="primary" icon="person_add" onClick={() => setAdding(true)}>
          Add user
        </Button>
      </div>

      <Card className="p-0 overflow-hidden">
        <table className="w-full text-body-sm">
          <thead className="bg-surface-container-low text-label-sm text-on-surface-variant">
            <tr>
              <th className="text-left px-space-md py-space-sm font-medium">Name</th>
              <th className="text-left px-space-sm py-space-sm font-medium">Role</th>
              <th className="text-right px-space-sm py-space-sm font-medium">Projects</th>
              <th className="text-left px-space-sm py-space-sm font-medium">Status</th>
              <th className="px-space-md py-space-sm" />
            </tr>
          </thead>
          <tbody>
            {(users ?? []).map((u) => (
              <tr key={u.id} className={`border-t border-outline-variant/40 ${u.is_active ? "" : "opacity-60"}`}>
                <td className="px-space-md py-space-sm">
                  <div className="text-on-surface">
                    {u.name}
                    {u.id === me?.id && <span className="ml-1 text-on-surface-variant">(you)</span>}
                  </div>
                  <div className="text-label-sm text-on-surface-variant">{u.email}</div>
                </td>
                <td className="px-space-sm py-space-sm">
                  <select
                    className="input !w-auto !py-1"
                    value={u.role}
                    aria-label={`Role of ${u.name}`}
                    onChange={(e) => change(u, { role: e.target.value as UserRole })}
                  >
                    {(Object.keys(ROLE_LABELS) as UserRole[]).map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABELS[r]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-space-sm py-space-sm text-right font-mono">{u.role === "admin" ? "all" : u.project_count}</td>
                <td className="px-space-sm py-space-sm">
                  {!u.is_active ? (
                    <span className="text-error">Disabled</span>
                  ) : u.has_password ? (
                    <span className="text-emerald-700">Active</span>
                  ) : (
                    <span className="text-amber-700">Waiting for password</span>
                  )}
                </td>
                <td className="px-space-md py-space-sm">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" icon="link" onClick={() => sendLink(u)} disabled={!u.is_active} title="A one-time link to set or reset the password">
                      Password link
                    </Button>
                    {u.id !== me?.id && (
                      <Button variant="ghost" icon={u.is_active ? "block" : "check_circle"} onClick={() => change(u, { is_active: !u.is_active })}>
                        {u.is_active ? "Disable" : "Enable"}
                      </Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {users === null && (
              <tr>
                <td colSpan={5} className="px-space-md py-space-lg text-center text-on-surface-variant">
                  Loading...
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      <AddUserModal
        open={adding}
        onClose={() => setAdding(false)}
        onCreated={(user, token) => {
          setAdding(false);
          reload();
          if (token) setLink({ user, url: passwordLinkUrl(token) });
          else pushToast(`${user.name} can now sign in`, "success");
        }}
      />
      <LinkModal link={link} onClose={() => setLink(null)} />
    </div>
  );
}

function AddUserModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (user: AuthUser, token: string | null) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<UserRole>("annotator");
  const [withPassword, setWithPassword] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (withPassword && password.length < 8) return setError("The password must be at least 8 characters.");
    setBusy(true);
    setError(null);
    try {
      const res = await createUser({ name, email, role, password: withPassword ? password : undefined });
      setName("");
      setEmail("");
      setPassword("");
      onCreated(res.user, res.password_link_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the user");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} widthClass="max-w-md">
      <form onSubmit={submit} className="p-space-lg flex flex-col gap-space-md">
        <h2 className="font-headline-md text-headline-md">Add user</h2>
        <Field label="Name">
          <input className="input" required autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Email (they sign in with it)">
          <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <div className="flex flex-col gap-space-sm">
          <span className="text-label-md text-on-surface-variant">Role</span>
          {(Object.keys(ROLE_LABELS) as UserRole[]).map((r) => (
            <label key={r} className="flex items-start gap-space-sm cursor-pointer">
              <input type="radio" name="role" className="mt-1" checked={role === r} onChange={() => setRole(r)} />
              <span>
                <span className="text-body-md text-on-surface">{ROLE_LABELS[r]}</span>
                <span className="block text-body-sm text-on-surface-variant">{ROLE_HELP[r]}</span>
              </span>
            </label>
          ))}
        </div>
        <label className="flex items-center gap-space-sm text-body-md cursor-pointer">
          <input type="checkbox" className="w-4 h-4" checked={withPassword} onChange={(e) => setWithPassword(e.target.checked)} />
          Set a password now (otherwise you get a link to send them)
        </label>
        {withPassword && (
          <Field label="Password (at least 8 characters)">
            <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        )}
        {error && <div className="text-body-sm text-error">{error}</div>}
        <div className="flex justify-end gap-space-sm">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? "Adding..." : "Add user"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function LinkModal({ link, onClose }: { link: { user: AuthUser; url: string } | null; onClose: () => void }) {
  const pushToast = useUiStore((s) => s.pushToast);
  return (
    <Modal open={!!link} onClose={onClose} widthClass="max-w-lg">
      {link && (
        <div className="p-space-lg flex flex-col gap-space-md">
          <h2 className="font-headline-md text-headline-md flex items-center gap-space-sm">
            <MaterialIcon name="link" className="text-primary" />
            Password link for {link.user.name}
          </h2>
          <p className="text-body-sm text-on-surface-variant">
            Send this link to {link.user.email}. It works once, for 7 days, and lets them choose their password. Any older link stops working.
          </p>
          <div className="flex gap-space-sm">
            <input className="input font-mono text-label-sm" readOnly value={link.url} onFocus={(e) => e.target.select()} />
            <Button
              icon="content_copy"
              onClick={() =>
                navigator.clipboard.writeText(link.url).then(
                  () => pushToast("Link copied", "success"),
                  () => pushToast("Could not access the clipboard -- select the link and copy it", "error"),
                )
              }
            >
              Copy
            </Button>
          </div>
          <div className="flex justify-end">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
