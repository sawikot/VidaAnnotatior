import { useEffect, useMemo, useState } from "react";
import { Button, Card, IconButton, Modal } from "../../components/primitives";
import {
  addMember,
  listMembers,
  listSlides,
  listUsers,
  removeMember,
  setMemberSlides,
  type AuthUser,
  type ProjectMemberInfo,
  type SlideAccess,
} from "../../services/api";
import type { Slide } from "../../types/api";
import { ROLE_LABELS } from "../../stores/authStore";
import { useUiStore } from "../../stores/uiStore";

/** Who works in the project. Managers and administrators add and remove people, and choose which
 *  slides (or images) each annotator may open; everyone sees the list. */
export function MembersPanel({
  projectId,
  canEdit,
  currentUserId,
  isImage = false,
}: {
  projectId: number;
  canEdit: boolean;
  currentUserId?: number;
  isImage?: boolean;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [members, setMembers] = useState<ProjectMemberInfo[] | null>(null);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [slides, setSlides] = useState<Slide[]>([]);
  const [picked, setPicked] = useState("");
  // The annotator whose slides are being chosen; `adding` when they are not a member yet.
  const [choosing, setChoosing] = useState<{ user: { id: number; name: string }; access: SlideAccess; adding: boolean } | null>(null);
  const noun = isImage ? "images" : "slides";

  useEffect(() => {
    listMembers(projectId).then(setMembers).catch(() => setMembers([]));
    if (canEdit) {
      listUsers().then(setUsers).catch(() => setUsers([]));
      listSlides(projectId).then(setSlides).catch(() => setSlides([]));
    }
  }, [projectId, canEdit]);

  const memberIds = new Set((members ?? []).map((m) => m.id));
  // Administrators already see every project, so they are never added.
  const addable = users.filter((u) => u.is_active && u.role !== "admin" && !memberIds.has(u.id));

  async function run(action: () => Promise<ProjectMemberInfo[]>, done: string) {
    try {
      setMembers(await action());
      pushToast(done, "success");
      return true;
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Could not change the members", "error");
      return false;
    }
  }

  function accessText(m: ProjectMemberInfo) {
    if (m.role !== "annotator" || m.all_slides) return `All ${noun}`;
    return `${m.slide_ids.length} of ${slides.length || "?"} ${noun}`;
  }

  return (
    <Card className="p-space-md flex flex-col gap-space-md" id="members">
      <div>
        <h2 className="font-headline-sm text-headline-sm">Members</h2>
        <p className="text-body-sm text-on-surface-variant">
          The people who can open this project. Annotators annotate, label, review and export -- in all of its {noun}, or only the ones you
          give them (the same {isImage ? "image" : "slide"} can go to several annotators). Project managers also change its settings, slides
          and members. Administrators see every project without being added.
        </p>
      </div>

      <ul className="divide-y divide-outline-variant/40">
        {(members ?? []).map((m) => (
          <li key={m.id} className={`flex items-center justify-between gap-space-md py-space-sm ${m.is_active ? "" : "opacity-60"}`}>
            <div className="min-w-0">
              <div className="text-body-md text-on-surface">
                {m.name}
                {m.id === currentUserId && <span className="ml-1 text-on-surface-variant">(you)</span>}
                {!m.is_active && <span className="ml-1 text-error text-label-sm">disabled</span>}
              </div>
              <div className="text-label-sm text-on-surface-variant">
                {m.email} · {ROLE_LABELS[m.role] ?? m.role} · {accessText(m)}
              </div>
            </div>
            {canEdit && (
              <div className="flex items-center gap-1 shrink-0">
                {m.role === "annotator" && (
                  <Button
                    variant="ghost"
                    icon="checklist"
                    title={`Choose which ${noun} ${m.name} can open`}
                    onClick={() => setChoosing({ user: m, access: { all_slides: m.all_slides, slide_ids: m.slide_ids }, adding: false })}
                  >
                    {isImage ? "Images" : "Slides"}
                  </Button>
                )}
                <IconButton
                  icon="person_remove"
                  title={`Remove ${m.name} from this project`}
                  aria-label={`Remove ${m.name}`}
                  onClick={() => run(() => removeMember(projectId, m.id), `${m.name} removed`)}
                />
              </div>
            )}
          </li>
        ))}
        {members?.length === 0 && <li className="py-space-sm text-body-sm text-on-surface-variant">No members yet.</li>}
        {members === null && <li className="py-space-sm text-body-sm text-on-surface-variant">Loading...</li>}
      </ul>

      {canEdit && (
        <div className="flex items-center gap-space-sm flex-wrap">
          <select className="input !w-72 max-w-full" value={picked} onChange={(e) => setPicked(e.target.value)} aria-label="Person to add">
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
              if (!user) return;
              setPicked("");
              // Annotators: choose their slides first (all of them unless you say otherwise).
              if (user.role === "annotator") setChoosing({ user, access: { all_slides: true, slide_ids: [] }, adding: true });
              else run(() => addMember(projectId, user.id), `${user.name} added`);
            }}
          >
            Add
          </Button>
        </div>
      )}

      <SlideAccessModal
        choosing={choosing}
        slides={slides}
        members={members ?? []}
        isImage={isImage}
        onClose={() => setChoosing(null)}
        onSave={async (access) => {
          if (!choosing) return;
          const { user, adding } = choosing;
          const ok = adding
            ? await run(() => addMember(projectId, user.id, access), `${user.name} added`)
            : await run(() => setMemberSlides(projectId, user.id, access), `${user.name}'s ${noun} saved`);
          if (ok) setChoosing(null);
        }}
      />
    </Card>
  );
}

function SlideAccessModal({
  choosing,
  slides,
  members,
  isImage,
  onClose,
  onSave,
}: {
  choosing: { user: { id: number; name: string }; access: SlideAccess; adding: boolean } | null;
  slides: Slide[];
  members: ProjectMemberInfo[];
  isImage: boolean;
  onClose: () => void;
  onSave: (access: SlideAccess) => Promise<void>;
}) {
  const [all, setAll] = useState(true);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const noun = isImage ? "images" : "slides";

  useEffect(() => {
    if (!choosing) return;
    setAll(choosing.access.all_slides);
    setChosen(new Set(choosing.access.slide_ids));
    setFilter("");
  }, [choosing]);

  // Who else has each slide, so you can see where work is shared.
  const othersBySlide = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const m of members) {
      if (m.role !== "annotator" || m.all_slides || m.id === choosing?.user.id) continue;
      for (const id of m.slide_ids) map.set(id, [...(map.get(id) ?? []), m.name]);
    }
    return map;
  }, [members, choosing]);

  const sorted = useMemo(() => [...slides].sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true })), [slides]);
  const shown = sorted.filter((s) => s.filename.toLowerCase().includes(filter.trim().toLowerCase()));

  function toggle(id: number, on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function setShown(on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      for (const s of shown) {
        if (on) next.add(s.id);
        else next.delete(s.id);
      }
      return next;
    });
  }

  async function save() {
    setBusy(true);
    try {
      await onSave({ all_slides: all, slide_ids: all ? [] : [...chosen] });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={!!choosing} onClose={onClose} widthClass="max-w-xl">
      {choosing && (
        <div className="p-space-lg flex flex-col gap-space-md">
          <div>
            <h2 className="font-headline-md text-headline-md">
              {choosing.adding ? `Add ${choosing.user.name}` : `${choosing.user.name}'s ${noun}`}
            </h2>
            <p className="text-body-sm text-on-surface-variant">Which {noun} of this project can they open and annotate?</p>
          </div>

          <div className="flex flex-col gap-space-sm">
            <label className="flex items-center gap-space-sm cursor-pointer text-body-md">
              <input type="radio" name="slide-access" checked={all} onChange={() => setAll(true)} />
              All {noun}, including ones added later
            </label>
            <label className="flex items-center gap-space-sm cursor-pointer text-body-md">
              <input type="radio" name="slide-access" checked={!all} onChange={() => setAll(false)} />
              Only the {noun} I choose
            </label>
          </div>

          {!all && (
            <div className="flex flex-col gap-space-sm">
              <div className="flex items-center gap-space-sm flex-wrap">
                <input
                  className="input flex-1 min-w-40"
                  placeholder={`Search ${noun}...`}
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  aria-label={`Search ${noun}`}
                />
                <Button variant="ghost" onClick={() => setShown(true)} disabled={!shown.length}>
                  Select {filter.trim() ? "shown" : "all"}
                </Button>
                <Button variant="ghost" onClick={() => setShown(false)} disabled={!shown.length}>
                  Clear
                </Button>
              </div>
              <div className="border border-outline-variant/60 rounded-lg max-h-72 overflow-y-auto divide-y divide-outline-variant/30">
                {shown.map((s) => {
                  const others = othersBySlide.get(s.id);
                  return (
                    <label key={s.id} className="flex items-center gap-space-sm px-space-sm py-1.5 cursor-pointer hover:bg-surface-container-low">
                      <input type="checkbox" className="w-4 h-4 shrink-0" checked={chosen.has(s.id)} onChange={(e) => toggle(s.id, e.target.checked)} />
                      <span className="text-body-sm text-on-surface truncate flex-1" title={s.filename}>
                        {s.filename}
                      </span>
                      {others && (
                        <span className="text-label-sm text-on-surface-variant shrink-0 truncate max-w-[45%]" title={`Also assigned to ${others.join(", ")}`}>
                          also {others.join(", ")}
                        </span>
                      )}
                    </label>
                  );
                })}
                {!shown.length && (
                  <div className="px-space-sm py-space-md text-body-sm text-on-surface-variant text-center">
                    {slides.length ? `No ${noun} match.` : `This project has no ${noun} yet.`}
                  </div>
                )}
              </div>
              <div className="text-label-sm text-on-surface-variant">
                {chosen.size} of {slides.length} {noun} chosen
              </div>
            </div>
          )}

          <div className="flex justify-end gap-space-sm">
            <Button type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={save} disabled={busy || (!all && chosen.size === 0)}>
              {busy ? "Saving..." : choosing.adding ? "Add" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
