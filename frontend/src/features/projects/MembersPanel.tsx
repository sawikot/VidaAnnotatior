import { useEffect, useState } from "react";
import { Button, Card, IconButton } from "../../components/primitives";
import { addMember, listMembers, listUsers, removeMember, type AuthUser, type ProjectMemberInfo } from "../../services/api";
import { ROLE_LABELS } from "../../stores/authStore";
import { useUiStore } from "../../stores/uiStore";

/** Who works in the project. Managers and administrators add and remove people; everyone sees the list. */
export function MembersPanel({ projectId, canEdit, currentUserId }: { projectId: number; canEdit: boolean; currentUserId?: number }) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [members, setMembers] = useState<ProjectMemberInfo[] | null>(null);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [picked, setPicked] = useState("");

  useEffect(() => {
    listMembers(projectId).then(setMembers).catch(() => setMembers([]));
    if (canEdit) listUsers().then(setUsers).catch(() => setUsers([]));
  }, [projectId, canEdit]);

  const memberIds = new Set((members ?? []).map((m) => m.id));
  // Administrators already see every project, so they are never added.
  const addable = users.filter((u) => u.is_active && u.role !== "admin" && !memberIds.has(u.id));

  async function run(action: () => Promise<ProjectMemberInfo[]>, done: string) {
    try {
      setMembers(await action());
      pushToast(done, "success");
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not change the members", "error");
    }
  }

  return (
    <Card className="p-space-md flex flex-col gap-space-md" id="members">
      <div>
        <h2 className="font-headline-sm text-headline-sm">Members</h2>
        <p className="text-body-sm text-on-surface-variant">
          The people who can open this project. Annotators annotate, label, review and export; project managers also change its settings,
          slides and members. Administrators see every project without being added.
        </p>
      </div>

      <ul className="divide-y divide-outline-variant/40">
        {(members ?? []).map((m) => (
          <li key={m.id} className={`flex items-center justify-between gap-space-md py-space-sm ${m.is_active ? "" : "opacity-60"}`}>
            <div>
              <div className="text-body-md text-on-surface">
                {m.name}
                {m.id === currentUserId && <span className="ml-1 text-on-surface-variant">(you)</span>}
                {!m.is_active && <span className="ml-1 text-error text-label-sm">disabled</span>}
              </div>
              <div className="text-label-sm text-on-surface-variant">
                {m.email} · {ROLE_LABELS[m.role] ?? m.role}
              </div>
            </div>
            {canEdit && (
              <IconButton
                icon="person_remove"
                title={`Remove ${m.name} from this project`}
                aria-label={`Remove ${m.name}`}
                onClick={() => run(() => removeMember(projectId, m.id), `${m.name} removed`)}
              />
            )}
          </li>
        ))}
        {members?.length === 0 && <li className="py-space-sm text-body-sm text-on-surface-variant">No members yet.</li>}
        {members === null && <li className="py-space-sm text-body-sm text-on-surface-variant">Loading...</li>}
      </ul>

      {canEdit && (
        <div className="flex items-center gap-space-sm flex-wrap">
          <select className="input !w-72" value={picked} onChange={(e) => setPicked(e.target.value)} aria-label="Person to add">
            <option value="">{addable.length ? "Add a person..." : "Everyone is already a member"}</option>
            {addable.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} ({ROLE_LABELS[u.role] ?? u.role}) · {u.email}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            icon="person_add"
            disabled={!picked}
            onClick={() => {
              const user = addable.find((u) => u.id === Number(picked));
              setPicked("");
              if (user) run(() => addMember(projectId, user.id), `${user.name} added`);
            }}
          >
            Add
          </Button>
        </div>
      )}
    </Card>
  );
}
