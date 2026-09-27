import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, Modal, Pill } from "../components/primitives";
import { getSystemVersion, listReleases, switchVersion, type DataBackup, type ReleaseInfo, type SystemVersion } from "../services/api";
import { useCan } from "../stores/authStore";

const formatDate = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

/** Administrators: which version is running, the released ones, and switching between them. The
 * switch itself is done by the updater service next to the app (Docker install only). */
export function VersionPage() {
  const { admin } = useCan();
  const [info, setInfo] = useState<SystemVersion | null>(null);
  const [releases, setReleases] = useState<ReleaseInfo[] | null>(null);
  const [releasesError, setReleasesError] = useState<string | null>(null);
  const [picked, setPicked] = useState<ReleaseInfo | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);

  useEffect(() => {
    if (!admin) return;
    getSystemVersion()
      .then((v) => {
        setInfo(v);
        const busy = v.updater && (v.updater.state === "pulling" || v.updater.state === "switching");
        if (busy && v.updater?.target) setSwitching(v.updater.target); // someone else started one
        if (v.can_update) {
          listReleases()
            .then(setReleases)
            .catch((e) => setReleasesError(e instanceof Error ? e.message : "Could not load the versions"));
        }
      })
      .catch(() => setInfo(null));
  }, [admin]);

  if (!admin) return <Navigate to="/projects" replace />;

  const current = info?.updater?.current_version ?? info?.version ?? null;
  const currentIndex = releases?.findIndex((r) => r.version === current) ?? -1;

  return (
    <div className="max-w-4xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div>
        <h1 className="font-headline-lg text-headline-lg">Version</h1>
        <p className="text-body-sm text-on-surface-variant">
          Install a newer version, or go back to an earlier one. The database is backed up before every switch.
        </p>
      </div>

      <Card className="p-space-md flex items-center gap-space-md flex-wrap">
        <MaterialIcon name="deployed_code" className="text-primary" />
        <div className="flex-1 min-w-0">
          <div className="text-label-md text-on-surface-variant">Running now</div>
          <div className="font-mono text-headline-sm text-on-surface">{current ?? "..."}</div>
        </div>
        {releases && currentIndex > 0 && (
          <Button variant="primary" icon="upgrade" onClick={() => setPicked(releases[0])}>
            Update to {releases[0].version}
          </Button>
        )}
        {releases && currentIndex === 0 && <Pill className="bg-emerald-100 text-emerald-800">Up to date</Pill>}
      </Card>

      {info && !info.can_update && (
        <Card className="p-space-md text-body-sm text-on-surface-variant">
          This copy of the app is run from the source code, not the Docker install, so versions are not switched from here.
        </Card>
      )}
      {info?.updater_error && <Card className="p-space-md text-body-sm text-error">{info.updater_error}</Card>}
      {info?.updater?.state === "failed" && !switching && (
        <Card className="p-space-md text-body-sm text-error whitespace-pre-wrap">Last switch failed: {info.updater.message}</Card>
      )}
      {releasesError && <Card className="p-space-md text-body-sm text-error">{releasesError}</Card>}

      {releases && (
        <div className="flex flex-col gap-space-sm">
          <h2 className="font-headline-md text-headline-md">Released versions</h2>
          {releases.length === 0 && <Card className="p-space-md text-body-sm text-on-surface-variant">No versions have been released yet.</Card>}
          {releases.map((r, i) => (
            <ReleaseCard
              key={r.version}
              release={r}
              installed={r.version === current}
              newer={currentIndex < 0 ? null : i < currentIndex}
              onInstall={() => setPicked(r)}
            />
          ))}
        </div>
      )}

      <ConfirmSwitchModal
        key={picked?.version}
        release={picked}
        current={current}
        older={!!picked && currentIndex >= 0 && (releases?.indexOf(picked) ?? 0) > currentIndex}
        backups={info?.updater?.backups ?? []}
        onClose={() => setPicked(null)}
        onStarted={(version) => {
          setPicked(null);
          setSwitching(version);
        }}
      />
      {switching && <SwitchProgress target={switching} onGiveUp={() => setSwitching(null)} />}
    </div>
  );
}

function ReleaseCard({ release, installed, newer, onInstall }: { release: ReleaseInfo; installed: boolean; newer: boolean | null; onInstall: () => void }) {
  const [open, setOpen] = useState(installed || newer === true);
  return (
    <Card className={`p-space-md flex flex-col gap-space-sm ${installed ? "ring-2 ring-primary/40" : ""}`}>
      <div className="flex items-center gap-space-sm flex-wrap">
        <span className="font-mono text-body-md text-on-surface">{release.version}</span>
        {release.name !== release.version && <span className="text-body-md text-on-surface">{release.name}</span>}
        {installed && <Pill className="bg-primary-fixed text-on-primary-fixed-variant">Installed</Pill>}
        {release.prerelease && <Pill className="bg-amber-100 text-amber-800">Pre-release</Pill>}
        <span className="text-label-sm text-on-surface-variant">{formatDate(release.published_at)}</span>
        <span className="flex-1" />
        {release.notes && (
          <Button variant="ghost" icon={open ? "expand_less" : "expand_more"} onClick={() => setOpen((v) => !v)}>
            Notes
          </Button>
        )}
        {!installed && (
          <Button variant={newer ? "primary" : "secondary"} icon={newer === false ? "history" : "download"} onClick={onInstall}>
            {newer === false ? "Go back to this" : "Install"}
          </Button>
        )}
      </div>
      {open && release.notes && (
        <div className="text-body-sm text-on-surface-variant whitespace-pre-wrap border-t border-outline-variant/40 pt-space-sm">{release.notes}</div>
      )}
    </Card>
  );
}

function ConfirmSwitchModal({
  release,
  current,
  older,
  backups,
  onClose,
  onStarted,
}: {
  release: ReleaseInfo | null;
  current: string | null;
  older: boolean;
  backups: DataBackup[];
  onClose: () => void;
  onStarted: (version: string) => void;
}) {
  // The data as it was when this version was last left: the newest backup taken while switching away from it.
  const backup = release ? backups.find((b) => b.from_version === release.version) ?? null : null;
  const [restore, setRestore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!release) return null;

  async function start() {
    if (!release) return;
    setBusy(true);
    setError(null);
    try {
      await switchVersion(release.version, restore && backup ? backup.id : null);
      onStarted(release.version);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the switch");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={busy ? () => undefined : onClose} widthClass="max-w-lg">
      <div className="p-space-lg flex flex-col gap-space-md">
        <h2 className="font-headline-md text-headline-md">
          {older ? "Go back to" : "Install"} {release.version}?
        </h2>
        <p className="text-body-sm text-on-surface-variant">
          The app is unavailable for everyone for about a minute while it restarts on {release.version}
          {current ? ` (now ${current})` : ""}. Anyone annotating should save their work first. The database is backed up
          before the switch, and if {release.version} does not start, {current ?? "the current version"} comes back by itself.
        </p>
        {older && (
          <div className="flex flex-col gap-space-sm rounded bg-amber-50 border border-amber-200 p-space-md text-body-sm text-amber-900">
            <span>
              An older version may not understand data saved by a newer one. If something looks wrong after going back, install the
              newer version again: nothing is lost.
            </span>
            {backup && (
              <>
                <label className="flex items-start gap-space-sm cursor-pointer">
                  <input type="radio" className="mt-1" checked={!restore} onChange={() => setRestore(false)} />
                  <span>Keep the current data (recommended)</span>
                </label>
                <label className="flex items-start gap-space-sm cursor-pointer">
                  <input type="radio" className="mt-1" checked={restore} onChange={() => setRestore(true)} />
                  <span>
                    Restore the data as it was when {release.version} was last used ({formatDate(backup.created_at)}).
                    <strong> Everything done since then is set aside</strong> (it stays in the backups).
                  </span>
                </label>
              </>
            )}
          </div>
        )}
        {error && <div className="text-body-sm text-error">{error}</div>}
        <div className="flex justify-end gap-space-sm">
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" icon={older ? "history" : "download"} onClick={start} disabled={busy}>
            {busy ? "Starting..." : older ? `Go back to ${release.version}` : `Install ${release.version}`}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** Follows a switch: the updater's progress while the app is still up, then waits for the app to come
 * back on the new version and reloads the page into it. */
function SwitchProgress({ target, onGiveUp }: { target: string; onGiveUp: () => void }) {
  const [phase, setPhase] = useState<"downloading" | "restarting" | "failed" | "done">("downloading");
  const [message, setMessage] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const startedAt = Date.now();
    const tick = async () => {
      try {
        const v = await getSystemVersion();
        const u = v.updater;
        if (u?.log) setLog(u.log);
        if (u?.state === "failed") {
          setPhase("failed");
          setMessage(u.message);
          return;
        }
        if (v.version === target && (!u || u.state === "done" || u.state === "idle")) {
          setPhase("done");
          window.setTimeout(() => window.location.reload(), 1200);
          return;
        }
        setPhase(u?.state === "switching" ? "restarting" : "downloading");
      } catch {
        setPhase("restarting"); // the app itself is restarting
      }
      setSlow(Date.now() - startedAt > 5 * 60 * 1000);
      if (Date.now() - startedAt > 20 * 60 * 1000) {
        setPhase("failed");
        setMessage("This is taking much longer than it should. Reload the page to see which version is running.");
        return;
      }
      if (!stopped) timer = window.setTimeout(tick, 2000);
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [target]);

  const title = {
    downloading: `Downloading ${target}...`,
    restarting: `Restarting on ${target}...`,
    done: `${target} is running. Reloading...`,
    failed: "The switch did not work",
  }[phase];

  return (
    <Modal open onClose={() => undefined} widthClass="max-w-lg">
      <div className="p-space-lg flex flex-col gap-space-md">
        <div className="flex items-center gap-space-sm">
          <MaterialIcon
            name={phase === "failed" ? "error" : phase === "done" ? "check_circle" : "progress_activity"}
            className={phase === "failed" ? "text-error" : phase === "done" ? "text-emerald-700" : "text-primary animate-spin"}
          />
          <h2 className="font-headline-md text-headline-md">{title}</h2>
        </div>
        {phase === "downloading" && (
          <p className="text-body-sm text-on-surface-variant">The app keeps working while the new version downloads. The first download can take a few minutes.</p>
        )}
        {phase === "restarting" && <p className="text-body-sm text-on-surface-variant">The app is unavailable for a moment. This page reloads by itself.</p>}
        {phase === "failed" && <p className="text-body-sm text-error whitespace-pre-wrap">{message}</p>}
        {log.length > 0 && (
          <pre className="text-label-sm font-mono bg-surface-container-low rounded p-space-sm max-h-48 overflow-y-auto whitespace-pre-wrap">{log.join("\n")}</pre>
        )}
        {phase === "failed" && (
          <div className="flex justify-end">
            <Button onClick={() => window.location.reload()}>Close</Button>
          </div>
        )}
        {phase !== "failed" && phase !== "done" && slow && (
          <div className="flex justify-end">
            <Button variant="ghost" onClick={onGiveUp}>
              Hide
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
