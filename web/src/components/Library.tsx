import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { api } from "../api";
import { lapTime, sessionDate, signed } from "../format";
import type { Garage61Account, Garage61Laps, IngestResult, LapRow, LapsResponse, SystemInfo, TrackRow } from "../types";

type Group = { track: string; car: string };

interface Props {
  tracks: TrackRow[];
  system: SystemInfo | null;
  current: { group: Group | null; lapId: string | null; refId: string | null };
  onOpen: (group: Group, lapId: string, refId: string) => void;
  onImported: () => Promise<void>; // reload the track list (and machine status)
}

/** Screen 3: where laps come from, every track driven, and picking a lap and ghost to review. */
export function Library({ tracks, system, current, onOpen, onImported }: Props) {
  const [group, setGroup] = useState<Group | null>(current.group ?? (tracks[0] ? { track: tracks[0].track, car: tracks[0].car } : null));
  const [laps, setLaps] = useState<LapsResponse | null>(null);
  const [garage61, setGarage61] = useState<Garage61Laps | null>(null);
  const [session, setSession] = useState<string | null>(null);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [ghostId, setGhostId] = useState<string | null>(null); // a lap id, or "g61:<id>" still to import
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0); // bumped when new laps may have arrived for this track
  const isCurrent = (g: Group | null) => !!g && g.track === current.group?.track && g.car === current.group?.car;

  useEffect(() => {
    if (!group && tracks[0]) setGroup({ track: tracks[0].track, car: tracks[0].car });
  }, [tracks, group]);

  // Load the chosen track's laps, then pick what to review: what's open now, else the defaults.
  useEffect(() => {
    if (!group) return;
    let stale = false;
    setLaps(null);
    setGarage61(null);
    setError(null);
    const lapsLoaded = api.laps(group.track, group.car);
    const g61Loaded = api.garage61Laps(group.track, group.car)
      .catch((): Garage61Laps => ({ available: false, reason: "Garage61 couldn't be reached.", laps: [] }));
    lapsLoaded
      .then((data) => {
        if (stale) return;
        setLaps(data);
        const here = isCurrent(group);
        const lap = (here && current.lapId) || data.default_lap;
        setReviewId(lap);
        setSession(data.laps.find((l) => l.lap_id === lap)?.session_id ?? latestSession(data.laps));
        setGhostId((here && current.refId) || data.default_ghost);
        if (!here) {
          // Prefer the fastest teammate on Garage61, as the review screen does.
          g61Loaded.then((g61) => {
            const fastest = fastestGarage61(g61);
            if (!stale && fastest) setGhostId(fastest.lap_id ?? `g61:${fastest.garage61_id}`);
          });
        }
      })
      .catch((e: Error) => !stale && setError(e.message));
    g61Loaded.then((g61) => !stale && setGarage61(g61));
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload on a new track only
  }, [group?.track, group?.car, reloads]);

  const reloadAfterImport = async (imported: IngestResult[]) => {
    await onImported();
    const last = [...imported].reverse().find((r) => r.laps > 0 && r.track_key && r.car_key);
    if (last && (last.track_key !== group?.track || last.car_key !== group?.car)) {
      setGroup({ track: last.track_key!, car: last.car_key! });
    } else {
      setReloads((n) => n + 1);
    }
  };

  const open = async () => {
    if (!group || !reviewId || !ghostId) return;
    setOpening(true);
    setError(null);
    try {
      const ref = ghostId.startsWith("g61:") ? (await api.garage61Import(ghostId.slice(4))).lap_id : ghostId;
      onOpen(group, reviewId, ref);
    } catch (e) {
      setError(`Couldn't import the ghost from Garage61: ${(e as Error).message}`);
      setOpening(false);
    }
  };

  return (
    <main className="library">
      <aside className="sources" aria-label="Sources">
        <h2 className="section-title">Sources</h2>
        <TelemetrySource system={system} onImported={reloadAfterImport} />
        <Garage61Card system={system} />
        <CoachCard system={system} />
      </aside>

      <div className="library-main">
        <section aria-label="Tracks" className="library-section">
          <h2 className="section-title">Tracks</h2>
          {tracks.length === 0 ? (
            <NoLaps system={system} />
          ) : (
            <div className="track-grid">
              {tracks.map((t) => {
                const on = group?.track === t.track && group?.car === t.car;
                return (
                  <button key={`${t.track}|${t.car}`} type="button" className="track-card" aria-pressed={on}
                    onClick={() => setGroup({ track: t.track, car: t.car })}>
                    <span className="track-name">{t.track_name}</span>
                    <span className="muted small">{t.car_name} · {sessionDate(t.last_session).split(",")[0]}</span>
                    <span className="mono track-best">{lapTime(t.best_lap_time)}</span>
                    <span className="muted small">
                      {t.laps} laps · {t.valid_laps} valid{t.reference_laps ? ` · ${t.reference_laps} from Garage61` : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        {error && <div className="error" role="alert">{error}</div>}
        {group && laps && (
          <LapPicker
            laps={laps}
            garage61={garage61}
            session={session}
            reviewId={reviewId}
            ghostId={ghostId}
            opening={opening}
            onSession={setSession}
            onReview={(id) => {
              setReviewId(id);
              if (id === ghostId) setGhostId(null);
            }}
            onGhost={setGhostId}
            onOpen={open}
          />
        )}
      </div>
    </main>
  );
}

function LapPicker({ laps, garage61, session, reviewId, ghostId, opening, onSession, onReview, onGhost, onOpen }: {
  laps: LapsResponse;
  garage61: Garage61Laps | null;
  session: string | null;
  reviewId: string | null;
  ghostId: string | null;
  opening: boolean;
  onSession: (id: string) => void;
  onReview: (id: string) => void;
  onGhost: (id: string) => void;
  onOpen: () => void;
}) {
  const sessions = useMemo(() => {
    const ids = [...new Set(laps.laps.map((l) => l.session_id))].sort().reverse();
    return ids.map((id) => ({ id, laps: laps.laps.filter((l) => l.session_id === id) }));
  }, [laps]);
  const shown = sessions.find((s) => s.id === session) ?? sessions[0];
  const bestTime = Math.min(...laps.laps.filter((l) => l.valid && l.lap_time != null).map((l) => l.lap_time!));

  // Teammates' laps: Garage61's list, plus imported reference laps it no longer lists.
  const g61 = garage61?.laps ?? [];
  const listed = new Set(g61.map((l) => l.lap_id).filter(Boolean));
  const teammates = [
    ...g61.map((l) => ({ id: l.lap_id ?? `g61:${l.garage61_id}`, driver: l.driver ?? l.garage61_id, time: l.lap_time, date: l.date, imported: !!l.lap_id })),
    ...laps.references.filter((r) => !listed.has(r.lap_id))
      .map((r) => ({ id: r.lap_id, driver: r.driver ?? r.lap_id, time: r.lap_time, date: r.date ?? "", imported: true })),
  ].sort((a, b) => (a.time ?? Infinity) - (b.time ?? Infinity));

  const reviewLap = laps.laps.find((l) => l.lap_id === reviewId);
  const ghostLap = laps.laps.find((l) => l.lap_id === ghostId);
  const ghostMate = teammates.find((t) => t.id === ghostId);
  const ghostText = ghostMate
    ? `${ghostMate.driver}, ${lapTime(ghostMate.time)}${ghostMate.imported ? "" : " (imports from Garage61)"}`
    : ghostLap
      ? `Lap ${ghostLap.seq}${ghostLap.session_id !== shown?.id ? ` of ${sessionDate(ghostLap.session_id)}` : ""}${ghostLap.lap_time === bestTime ? ", your best" : ""}`
      : "none chosen";

  return (
    <section className="card" aria-label="Laps">
      <div className="card-head">
        {sessions.length > 1 ? (
          <label className="field">
            <span className="sr-only">Session</span>
            <select value={shown?.id} onChange={(e) => onSession(e.target.value)}>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>Session {sessionDate(s.id)} · {s.laps.length} laps</option>
              ))}
            </select>
          </label>
        ) : (
          <h2>Session {shown ? sessionDate(shown.id) : ""} <span className="muted">· {shown?.laps.length ?? 0} laps</span></h2>
        )}
        <span className="muted small">Pick a lap to review and a ghost to compare against</span>
      </div>
      <div className="table-scroll">
        <table className="lap-table">
          <thead>
            <tr>
              <th>Lap</th><th>Time</th><th>vs best</th><th>Off track</th><th>Status</th>
              <th className="center">Review</th><th className="center">Ghost</th>
            </tr>
          </thead>
          <tbody>
            {shown?.laps.map((l) => (
              <tr key={l.lap_id} className={[l.lap_time == null ? "dim" : "", l.representative ? "rep" : ""].join(" ")}>
                <td className="mono">L{l.seq}</td>
                <td className={`mono${l.lap_time === bestTime ? " best" : ""}`}>{lapTime(l.lap_time)}</td>
                <td className="mono">{l.lap_time === bestTime ? <span className="sans">best</span> : l.vs_best_pct != null ? `${signed(l.vs_best_pct, 1)}%` : ""}</td>
                <td className="mono">{l.off_track_s.toFixed(1)} s</td>
                <td>{status(l)}</td>
                <td className="center">
                  {l.lap_time != null && (
                    <input type="radio" name="review" className="radio you" checked={l.lap_id === reviewId}
                      aria-label={`Review lap ${l.seq}`} onChange={() => onReview(l.lap_id)} />
                  )}
                </td>
                <td className="center">
                  {l.lap_time != null && (
                    <input type="radio" name="ghost" className="radio ghost" checked={l.lap_id === ghostId}
                      disabled={l.lap_id === reviewId} aria-label={`Lap ${l.seq} as ghost`} onChange={() => onGhost(l.lap_id)} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
          {teammates.length > 0 && (
            <tbody className="teammates">
              <tr><th colSpan={7}>Teammates on Garage61</th></tr>
              {teammates.map((t) => (
                <tr key={t.id}>
                  <td colSpan={1} className="sans ellipsis">{t.driver}</td>
                  <td className="mono">{lapTime(t.time)}</td>
                  <td className="mono">{t.time != null && Number.isFinite(bestTime) ? `${signed(t.time - bestTime, 3)} s` : ""}</td>
                  <td />
                  <td className="muted">{t.date}{t.imported ? "" : " · not imported yet"}</td>
                  <td />
                  <td className="center">
                    <input type="radio" name="ghost" className="radio ghost" checked={t.id === ghostId}
                      aria-label={`${t.driver}'s lap as ghost`} onChange={() => onGhost(t.id)} />
                  </td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </div>
      {garage61 && !garage61.available && <p className="muted small note">Garage61: {garage61.reason}</p>}
      <div className="picker-foot">
        <span className="legend-key"><span className="swatch you" />{reviewLap ? `Lap ${reviewLap.seq}` : "No lap chosen"}</span>
        <span className="muted">vs</span>
        <span className="legend-key"><span className="swatch ghost dashed" />Ghost: {ghostText}</span>
        <button type="button" className="btn primary" disabled={!reviewId || !ghostId || opening} onClick={onOpen}>
          {opening ? "Importing ghost…" : "Open lap review"}
        </button>
      </div>
    </section>
  );
}

function status(l: LapRow) {
  if (l.lap_time == null || !l.valid) return <span className="muted">{l.reasons.map((r) => r.replace(/_/g, " ")).join(", ") || "invalid"}</span>;
  if (l.representative) return <><span className="ok-dot" aria-hidden="true">●</span> representative</>;
  return <span className="muted">valid · off pace</span>;
}

function latestSession(laps: LapRow[]): string | null {
  return laps.map((l) => l.session_id).sort().at(-1) ?? null;
}

function fastestGarage61(g61: Garage61Laps) {
  if (!g61.available) return undefined;
  return [...g61.laps].filter((l) => l.lap_time != null).sort((a, b) => a.lap_time! - b.lap_time!)[0];
}

function NoLaps({ system }: { system: SystemInfo | null }) {
  const t = system?.telemetry;
  return (
    <div className="card no-laps">
      <b>No laps yet</b>
      <span className="muted">
        {t?.found
          ? <>Recordings in <code>{t.folder}</code> appear here once a session ends. Recording is on when you press Alt+L in the car.</>
          : <>Drop <code>.ibt</code> recordings into Sources, or watch a folder synced from your sim PC.</>}
      </span>
    </div>
  );
}

/** The sim PC's telemetry folder when iRacing is here; otherwise a drop zone and a synced folder. */
function TelemetrySource({ system, onImported }: { system: SystemInfo | null; onImported: (r: IngestResult[]) => Promise<void> }) {
  const t = system?.telemetry;
  const [editing, setEditing] = useState(false);
  const [rescanning, setRescanning] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const rescan = async () => {
    setRescanning(true);
    setNote(null);
    try {
      const out = await api.rescan();
      setNote(out.ingested.length ? `Imported ${out.ingested.length} new recording${out.ingested.length > 1 ? "s" : ""}.` : "Nothing new.");
      if (out.ingested.length) await onImported([]);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setRescanning(false);
    }
  };

  const folderForm = (
    <FolderForm
      initial={t?.found ? t.folder : ""}
      onDone={async () => {
        setEditing(false);
        await onImported([]);
        await rescan();
      }}
      onCancel={() => setEditing(false)}
    />
  );

  if (t?.found) {
    return (
      <div className="card source">
        <div className="source-head">
          <FolderIcon />
          <b>Telemetry folder</b>
        </div>
        <code className="path">{t.folder}</code>
        <span className="row small">
          <span className={`status-dot ${t.watching ? (t.error ? "warn" : "ok") : ""}`} aria-hidden="true" />
          {t.watching ? "Watching. New recordings are imported when a session ends." : "Not watching (started with --no-watch)."}
        </span>
        {t.error && <span className="small warn">{t.error}</span>}
        <span className="muted small">iRacing only keeps telemetry for sessions you recorded. Turn recording on in the car with Alt+L.</span>
        {editing ? folderForm : (
          <div className="row">
            <button type="button" className="btn" disabled={!t.watching || rescanning} onClick={rescan}>{rescanning ? "Scanning…" : "Rescan"}</button>
            <button type="button" className="btn ghost-btn" disabled={!t.watching} onClick={() => setEditing(true)}>Change folder</button>
            <FilePicker onImported={onImported} label="Import .ibt files…" />
          </div>
        )}
        {note && <span className="muted small">{note}</span>}
      </div>
    );
  }

  return (
    <div className="card source">
      <DropZone onImported={onImported} />
      <div className="synced">
        <b>Or watch a synced folder</b>
        <span className="muted small">
          A OneDrive, Dropbox or network-share copy of the sim PC's <code>Documents/iRacing/telemetry</code>. New files are
          picked up automatically.
        </span>
        {editing ? folderForm : (
          <button type="button" className="btn ghost-btn self-start" onClick={() => setEditing(true)}>Choose folder…</button>
        )}
        {note && <span className="muted small">{note}</span>}
      </div>
    </div>
  );
}

function FolderForm({ initial, onDone, onCancel }: { initial: string; onDone: () => Promise<void>; onCancel: () => void }) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.telemetryFolder(value.trim());
      await onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <form className="folder-form" onSubmit={submit}>
      <label className="small muted" htmlFor="telemetry-folder">Folder path (paste it from Finder or Explorer)</label>
      <input id="telemetry-folder" className="text-input mono" value={value} autoFocus spellCheck={false}
        placeholder="/Users/you/OneDrive/iRacing/telemetry" onChange={(e) => setValue(e.target.value)} />
      {error && <span className="small warn" role="alert">{error}</span>}
      <div className="row">
        <button type="submit" className="btn" disabled={!value.trim()}>Watch this folder</button>
        <button type="button" className="btn ghost-btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

type Upload = { name: string; state: "waiting" | "importing" | "done" | "error"; text?: string };

/** Uploads .ibt files one at a time (the server ingests one at a time anyway). */
function useUploads(onImported: (r: IngestResult[]) => Promise<void>) {
  const [uploads, setUploads] = useState<Upload[]>([]);
  const queue = useRef<File[]>([]);
  const busy = useRef(false);
  const add = async (files: File[]) => {
    const ibts = files.filter((f) => f.name.toLowerCase().endsWith(".ibt"));
    const others = files.filter((f) => !ibts.includes(f));
    const queued: Upload[] = [
      ...ibts.map((f): Upload => ({ name: f.name, state: "waiting" })),
      ...others.map((f): Upload => ({ name: f.name, state: "error", text: "not an iRacing .ibt recording" })),
    ];
    setUploads((u) => [...u.filter((x) => x.state === "importing" || x.state === "waiting"), ...queued]);
    queue.current.push(...ibts);
    if (busy.current) return; // the running loop picks these up
    busy.current = true;
    const results: IngestResult[] = [];
    const set = (name: string, patch: Partial<Upload>) =>
      setUploads((u) => u.map((x) => (x.name === name ? { ...x, ...patch } : x)));
    for (let file = queue.current.shift(); file; file = queue.current.shift()) {
      set(file.name, { state: "importing" });
      try {
        const out = await api.ingest(file);
        results.push(out);
        set(file.name, {
          state: "done",
          text: out.skipped ? "already in the library" : `${out.laps} laps (${out.valid} valid) · ${out.track}`,
        });
      } catch (e) {
        set(file.name, { state: "error", text: (e as Error).message });
      }
    }
    busy.current = false;
    if (results.some((r) => r.laps > 0)) await onImported(results);
  };
  return { uploads, add };
}

function DropZone({ onImported }: { onImported: (r: IngestResult[]) => Promise<void> }) {
  const { uploads, add } = useUploads(onImported);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    add([...e.dataTransfer.files]);
  };
  return (
    <>
      <div className={`drop-zone${over ? " over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)} onDrop={onDrop}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
        </svg>
        <b>Drop .ibt files here</b>
        <span className="muted small">Copied from Documents/iRacing/telemetry on your sim PC</span>
        <button type="button" className="btn primary" onClick={() => input.current?.click()}>Choose files…</button>
        <input ref={input} type="file" accept=".ibt" multiple hidden onChange={(e) => { add([...(e.target.files ?? [])]); e.target.value = ""; }} />
      </div>
      <UploadList uploads={uploads} />
    </>
  );
}

function FilePicker({ onImported, label }: { onImported: (r: IngestResult[]) => Promise<void>; label: string }) {
  const { uploads, add } = useUploads(onImported);
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className="btn ghost-btn" onClick={() => input.current?.click()}>{label}</button>
      <input ref={input} type="file" accept=".ibt" multiple hidden onChange={(e) => { add([...(e.target.files ?? [])]); e.target.value = ""; }} />
      {uploads.length > 0 && <UploadList uploads={uploads} />}
    </>
  );
}

function UploadList({ uploads }: { uploads: Upload[] }) {
  if (uploads.length === 0) return null;
  return (
    <ul className="uploads" aria-live="polite">
      {uploads.map((u) => (
        <li key={u.name} data-state={u.state}>
          <span className="ellipsis" title={u.name}>{u.name}</span>
          <span className="small result">
            {u.state === "waiting" ? "waiting" : u.state === "importing" ? "importing…" : u.text}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Garage61Card({ system }: { system: SystemInfo | null }) {
  const hasToken = system?.garage61.token ?? false;
  const [account, setAccount] = useState<Garage61Account | null>(null);
  const [adding, setAdding] = useState(false);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (hasToken) api.garage61Status().then(setAccount).catch(() => setAccount({ connected: false, reason: "Garage61 couldn't be reached." }));
  }, [hasToken]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const out = await api.garage61Token(token);
      setAccount(out);
      if (out.connected) {
        setAdding(false);
        setToken("");
      }
    } catch (err) {
      setAccount({ connected: false, reason: (err as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const connected = account?.connected ?? false;
  return (
    <div className="card source">
      <div className="row between">
        <b>Garage61</b>
        <span className="row small muted">
          <span className={`status-dot ${connected ? "ok" : hasToken && account ? "warn" : ""}`} aria-hidden="true" />
          {connected ? `connected as ${account!.user}` : hasToken && !account ? "checking…" : "not connected"}
        </span>
      </div>
      <span className="muted small">
        {connected && account!.teams?.length
          ? `Teammates' laps from ${account!.teams.join(", ")} as ghosts, plus their iRacing ghost files.`
          : "Teammates' laps to use as ghosts, plus their iRacing ghost files."}
      </span>
      {account && !account.connected && hasToken && <span className="small warn">{account.reason}</span>}
      {adding ? (
        <form className="folder-form" onSubmit={save}>
          <label className="small muted" htmlFor="g61-token">
            Personal access token from <a href="https://garage61.net/developer" target="_blank" rel="noreferrer">garage61.net/developer</a>.
            Saved on this computer only.
          </label>
          <input id="g61-token" className="text-input mono" type="password" autoComplete="off" value={token} autoFocus
            onChange={(e) => setToken(e.target.value)} />
          {account && !account.connected && !hasToken && <span className="small warn" role="alert">{account.reason}</span>}
          <div className="row">
            <button type="submit" className="btn" disabled={!token.trim() || saving}>{saving ? "Checking…" : "Save token"}</button>
            <button type="button" className="btn ghost-btn" onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </form>
      ) : (
        !connected && (
          <button type="button" className="btn ghost-btn self-start" onClick={() => setAdding(true)}>
            {hasToken ? "Replace access token" : "Add access token"}
          </button>
        )
      )}
    </div>
  );
}

function CoachCard({ system }: { system: SystemInfo | null }) {
  const found = system?.coach.found ?? false;
  return (
    <div className="card source">
      <div className="row between">
        <b>Coach</b>
        <span className="row small muted">
          <span className={`status-dot ${found ? "ok" : "warn"}`} aria-hidden="true" />
          {system == null ? "" : found ? "claude found" : "claude not found"}
        </span>
      </div>
      <span className="muted small">
        {found || system == null
          ? <>Runs <code>claude -p</code> with the coach plugin on your subscription. No API key.</>
          : <>Install Claude Code and sign in, or set <code>IAGENT_CLAUDE</code> to its path, then restart <code>iagent ui</code>.</>}
      </span>
    </div>
  );
}

function FolderIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}
