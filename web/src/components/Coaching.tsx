import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { streamChat } from "../chat";
import { lapTime, signed } from "../format";
import { useWidth } from "../geometry";
import type { AdviceOutcome, FocusOutcome, LiveEvent, LiveStatus, SessionLap, SessionReport, SessionRow, SystemInfo, Verdict } from "../types";
import { renderText } from "./Chat";
import { ACTIVE, Running, StartForm } from "./CoachControls";

type Open = (track: string, car: string, lap: string, ref: string, corner?: number) => void;

/** Coached sessions: start one, and look back at what happened and whether the advice worked. */
export function Coaching({ system, onOpenLap }: { system: SystemInfo | null; onOpenLap: Open }) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [selected, setSelected] = useState<string | null>(new URLSearchParams(window.location.search).get("session"));
  const [report, setReport] = useState<SessionReport | null>(null);
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0); // bumped after asking for a debrief, to follow it
  const running = live != null && ACTIVE.has(live.state);
  const liveId = useRef<string | null>(null);

  const loadSessions = () => api.sessions().then((rows) => {
    setSessions(rows);
    setSelected((s) => s ?? rows[0]?.id ?? null);
  }).catch((e: Error) => setError(e.message));
  useEffect(() => { loadSessions(); }, []);

  // The live coach: poll its state; while it runs, its session is the one shown (and refreshed).
  useEffect(() => {
    let stale = false;
    let timer = 0;
    const poll = async () => {
      try {
        const st = await api.live();
        if (stale) return;
        setLive(st);
        const active = ACTIVE.has(st.state);
        if (active && st.session_id && st.session_id !== liveId.current) {
          liveId.current = st.session_id;
          setSelected(st.session_id);
          loadSessions(); // so it's in the list, selected, while it runs
        }
        if (!active && liveId.current) {
          liveId.current = null;
          loadSessions();
        }
      } catch {
        // the page still works for past sessions
      }
      if (!stale) timer = window.setTimeout(poll, 3000);
    };
    poll();
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) return;
    let stale = false;
    let timer = 0;
    const load = () => api.session(selected).then((r) => {
      if (stale) return;
      setReport(r);
      setError(null);
      const again = (running && selected === liveId.current) || r.debrief_running;
      if (again) timer = window.setTimeout(load, 3000);
    }).catch((e: Error) => !stale && setError(e.message));
    load();
    const q = new URLSearchParams(window.location.search);
    q.set("page", "coaching");
    q.set("session", selected);
    window.history.replaceState(null, "", `?${q}`);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [selected, running, reload]);

  return (
    <div className="coaching">
      {running && <Running status={live!} onError={setError} />}
      <div className="coaching-body">
        <aside className="session-rail" aria-label="Coached sessions">
          <button type="button" className="btn primary" disabled={running} onClick={() => setStarting((s) => !s)}>
            {starting ? "Close" : "Start coaching…"}
          </button>
          {starting && !running && (
            <StartForm system={system} onStarted={(st) => { setLive(st); setStarting(false); }} onError={setError} />
          )}
          <h2 className="section-title">Coached sessions</h2>
          {sessions.length === 0 && <p className="muted small">None yet. Start coaching (live, or a replay of a recording).</p>}
          {sessions.map((s) => (
            <button key={s.id} type="button" className="session-card" aria-pressed={s.id === selected} onClick={() => setSelected(s.id)}>
              <b>{s.track ?? s.track_key} · {s.car ?? ""}</b>
              <span className="muted small">{when(s.started)} · {s.laps} laps{s.source === "replay" ? " · replay" : ""}</span>
              <span className="small"><span className="mono">{lapTime(s.best_lap)}</span>{s.focus ? ` · focus ${s.focus.label}` : ""}
                {s.focus && <span className={`verdict-text ${s.focus.verdict.replace(" ", "-")}`}> {verdictLabel(s.focus.verdict)}</span>}
              </span>
            </button>
          ))}
        </aside>

        <main className="session-main">
          {error && <div className="error" role="alert">{error}</div>}
          {report ? <SessionView key={report.id} report={report} live={running && report.id === liveId.current} onOpenLap={onOpenLap}
            onChanged={() => setReload((n) => n + 1)} /> : (
            !error && <p className="muted placeholder">{sessions.length ? "Loading…" : ""}</p>
          )}
        </main>
      </div>
    </div>
  );
}

function SessionView({ report, live, onOpenLap, onChanged }: {
  report: SessionReport; live: boolean; onOpenLap: Open; onChanged: () => void;
}) {
  const s = report.summary;
  const focus = report.focus[report.focus.length - 1] ?? null;
  const open = (lapId: string | null, corner?: number) =>
    lapId && report.track.key && report.track.car && report.ref.lap_id
      ? () => onOpenLap(report.track.key!, report.track.car!, lapId, report.ref.lap_id!, corner)
      : undefined;
  const bestLap = report.laps.find((l) => l.lap === s.best_lap_no);
  const reasons = Object.entries(s.held_reasons).sort((a, b) => b[1] - a[1]);
  return (
    <div className="session-columns">
      <div className="session-left">
        <section className="card" aria-label="Session">
          <div className="session-title">
            <h1>{report.track.name}</h1>
            <span className="muted">
              {report.track.car_name} · {when(report.started)}{report.duration_s ? ` · ${Math.round(report.duration_s / 60)} min` : ""}
              {" · following "}<span className="ghost-text">{report.ref.driver ?? "your lap"} {lapTime(report.ref.lap_time)}</span>
              {report.source === "replay" ? " · replay" : ""}{live ? " · in progress" : ""}
            </span>
          </div>
          <div className="stats">
            <Stat label="Best lap" value={lapTime(s.best_lap)} tone="you"
              note={s.best_lap_no ? `lap ${s.best_lap_no}${s.best_gap_s != null ? ` · ${signed(s.best_gap_s, 2)} on ${report.ref.driver ?? "your reference lap"}` : ""}` : undefined} />
            <Stat label="Laps" value={String(s.laps)} note={`${s.pushing} clean pushes · ${s.moments} with a moment${s.tranquille ? ` · ${s.tranquille} tranquille` : ""}`} />
            {focus && <Stat label={`Focus: ${focus.label}`} value={focus.change_s != null ? `${signed(focus.change_s, 2)} s` : "—"}
              tone={focus.change_s != null && focus.change_s < 0 ? "gain" : undefined}
              note={focus.before_s != null && focus.after.length ? `${focus.before_s.toFixed(2)} s down → ${focus.after[focus.after.length - 1].loss_s.toFixed(2)}` : verdictLabel(focus.verdict)} />}
            <Stat label="Coach" value={`${s.said} said`} note={s.held_back ? `${s.held_back} held back${reasons[0] ? `, ${reasons[0][1]} ${reasons[0][0]}` : ""}` : "nothing held back"} tone={undefined} />
            {bestLap?.lap_id && (
              <button type="button" className="btn stats-action" onClick={open(bestLap.lap_id)}>Review best lap vs {report.ref.driver ?? "reference"}</button>
            )}
          </div>
        </section>

        <div className="session-row">
          <section className="card pace-card" aria-label="Which laps were pushing">
            <div className="card-head">
              <h2>Which laps were pushing</h2>
              <span className="muted small">Pace over the last 400 m against your own best lap</span>
            </div>
            <PaceStrips report={report} />
            <div className="legend">
              <i style={{ background: "var(--you)" }} />Pushing <i style={{ background: "var(--loss-strong)" }} />A moment
              <i style={{ background: "#35506b" }} />Tranquille
            </div>
            <span className="small muted">The coach only coaches while you're pushing; after a moment it goes quiet until you're back on pace.</span>
          </section>
          {report.map && (
            <section className="card map-card" aria-label="Where time went">
              <h2>Where time went</h2>
              <SessionMap report={report} focus={focus} />
              <span className="small muted">Average loss to {report.ref.driver ?? "the reference"} per corner, while pushing.</span>
            </section>
          )}
        </div>

        <AdviceCard report={report} />
        <CommentaryCard report={report} open={open} />
      </div>

      <aside className="session-side">
        <DebriefCard report={report} onAsked={onChanged} />
        <NextSessionCard report={report} focus={focus} />
        <AskCard report={report} />
      </aside>
    </div>
  );
}

function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "you" | "gain" }) {
  return (
    <div className="stat">
      <span className="field-label">{label}</span>
      <span className={`mono stat-value ${tone ?? ""}`}>{value}</span>
      {note && <span className="small muted">{note}</span>}
    </div>
  );
}

function PaceStrips({ report }: { report: SessionReport }) {
  const length = report.track.length_m ?? 1;
  const pct = (m: number) => `${(Math.max(0, Math.min(length, m)) / length) * 100}%`;
  const axis = useRef<HTMLDivElement>(null);
  const width = useWidth(axis);
  // Corner names over the strips: skip any too close (in pixels) to the last one shown.
  const labels: { id: number; apex_m: number }[] = [];
  for (const c of report.track.corners) {
    const last = labels[labels.length - 1];
    if (!last || ((c.apex_m - last.apex_m) / length) * width >= 30) labels.push(c);
  }
  return (
    <div className="strips">
      <div className="strip-row axis-row">
        <span /><span />
        <div className="strip-axis" ref={axis}>
          {labels.map((c) => <span key={c.id} style={{ left: pct(c.apex_m) }}>T{c.id}</span>)}
        </div>
        <span />
      </div>
      {report.laps.map((lap) => (
        <div key={lap.lap} className="strip-row">
          <b>Lap {lap.lap}</b>
          <span className={`mono${lap.lap === report.summary.best_lap_no ? " you-text" : ""}`}>{lapTime(lap.lap_time)}</span>
          <div className="strip" aria-label={`Lap ${lap.lap}: ${paceLabel(lap)}`}>
            {lap.slow.map(([a, b], i) => (
              <span key={i} className={lap.pace === "tranquille" ? "tranquille" : "moment"} style={{ left: pct(a), width: `calc(${pct(b)} - ${pct(a)})` }} />
            ))}
            {report.track.corners.map((c) => <i key={c.id} style={{ left: pct(c.apex_m) }} />)}
          </div>
          <span className={`pace-chip ${lap.pace}`}>{paceLabel(lap)}</span>
        </div>
      ))}
    </div>
  );
}

function SessionMap({ report, focus }: { report: SessionReport; focus: FocusOutcome | null }) {
  const m = report.map!;
  const pts = m.x.map((x, i) => [x, m.y[i]] as const).filter((p): p is readonly [number, number] => p[0] != null && p[1] != null);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  // The frame follows the track's shape (Okayama is tall, Spa wide), within limits.
  const pad = 30, W = 600;
  const H = Math.round(Math.min(780, Math.max(300, W * (y1 - y0) / (x1 - x0))));
  const scale = Math.min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0));
  const px = (x: number) => pad + (x - x0) * scale + ((W - 2 * pad) - (x1 - x0) * scale) / 2;
  const py = (y: number) => pad + (y1 - y) * scale + ((H - 2 * pad) - (y1 - y0) * scale) / 2;
  const line = (from: number, to: number) => m.x.slice(from, to + 1).map((x, i) => {
    const y = m.y[from + i];
    return x == null || y == null ? "" : `${px(x).toFixed(1)},${py(y).toFixed(1)}`;
  }).filter(Boolean).join(" ");
  const loss = new Map(report.corners.map((c) => [c.corner, c.mean_s]));
  const focusCorners = new Set(focus && focus.verdict !== "sorted" ? focus.corners : []);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="session-map" role="img" aria-label="Track map coloured by time lost per corner">
      <polyline points={line(0, m.x.length - 1)} className="track-base" />
      {m.corners.filter((c) => focusCorners.has(c.id)).map((c) => <polyline key={`h${c.id}`} points={line(c.from, c.to)} className="halo" />)}
      {m.corners.map((c) => <polyline key={c.id} points={line(c.from, c.to)} className="seg-line" style={{ stroke: lossColor(loss.get(c.id)) }} />)}
      {m.corners.map((c) => {
        const x = m.x[c.apex], y = m.y[c.apex];
        return x == null || y == null ? null : <text key={`t${c.id}`} x={px(x)} y={py(y) - 12} className="map-label">T{c.id}</text>;
      })}
    </svg>
  );
}

const VERDICTS: Record<Verdict, string> = {
  working: "Working", mixed: "Mixed", "not yet": "Not yet", "no laps since": "No laps since", sorted: "Sorted", replaced: "Moved on",
};

function AdviceCard({ report }: { report: SessionReport }) {
  const items = report.advice.filter((a) => a.verdict !== "no laps since").slice(0, 6);
  const focusItems = report.focus.filter((f) => f.after.length);
  if (!items.length && !focusItems.length) return null;
  return (
    <section className="card" aria-label="Did the advice work">
      <div className="card-head">
        <h2>Did the advice work?</h2>
        <span className="muted small">The corner on laps you were pushing, before and after the coach said it</span>
      </div>
      <ul className="advice">
        {focusItems.map((f) => (
          <AdviceRow key={`f${f.cue}${f.set_lap}`} corner={f.corners.map((c) => `T${c}`).join("–")} said={`Focus${f.carried ? " (from last session)" : ""}`}
            when={`from lap ${f.set_lap + 1}`} before={{ lap: f.set_lap, loss_s: f.before_s }} after={f.after} verdict={f.verdict} />
        ))}
        {items.map((a) => <AdviceRow key={`${a.corner}${a.hint ?? a.advice}`} {...adviceProps(a)} />)}
      </ul>
    </section>
  );
}

function adviceProps(a: AdviceOutcome) {
  const said = a.hint ?? a.advice ?? "";
  const how = a.heard ? `${a.heard}${a.times > 1 ? `, ${a.times} times` : ""}` : "held back each time";
  return { corner: `T${a.corner}`, said: `“${said}”`, when: `lap ${a.lap} · ${how}`, before: { lap: a.lap, loss_s: a.before_s as number | null }, after: a.after, verdict: a.verdict };
}

function AdviceRow({ corner, said, when, before, after, verdict }: {
  corner: string; said: string; when: string; before: { lap: number; loss_s: number | null }; after: { lap: number; loss_s: number }[]; verdict: Verdict;
}) {
  const steps = [...(before.loss_s != null ? [before] : []), ...after.slice(0, 3)];
  return (
    <li>
      <b className="advice-corner">{corner}</b>
      <div className="advice-text"><span>{said}</span><span className="small muted">{when}</span></div>
      <div className="advice-steps">
        {steps.map((st, i) => (
          <span key={i} className="step">
            <span className="small muted">L{st.lap}</span>
            <span className="mono" style={{ background: lossColor(st.loss_s), color: st.loss_s != null && (st.loss_s >= 0.2 || st.loss_s <= -0.08) ? "var(--bg)" : undefined }}>
              {st.loss_s == null ? "—" : signed(st.loss_s, 2)}
            </span>
          </span>
        ))}
        <span className={`verdict ${verdict.replace(" ", "-")}`}>{VERDICTS[verdict]}</span>
      </div>
    </li>
  );
}

const BADGES: Record<string, string> = { approach: "Cue", feedback: "Feedback", summary: "Lap", focus: "Focus", answer: "Answer" };

function CommentaryCard({ report, open }: { report: SessionReport; open: (lapId: string | null, corner?: number) => (() => void) | undefined }) {
  const [filter, setFilter] = useState<"all" | "held" | "you">("all");
  const laps = useMemo(() => new Map(report.laps.map((l) => [String(l.lap), l])), [report.laps]);
  const keep = (e: LiveEvent) => {
    if (filter === "held") return e.type === "line" && e.status !== "said";
    if (filter === "you") return e.type === "driver" || e.type === "answer";
    return true;
  };
  return (
    <section className="card" aria-label="What the coach said">
      <div className="card-head">
        <h2>What the coach said</h2>
        <div className="segmented" role="group" aria-label="Show">
          <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All</button>
          <button type="button" aria-pressed={filter === "held"} onClick={() => setFilter("held")}>Held back ({report.summary.held_back})</button>
          <button type="button" aria-pressed={filter === "you"} onClick={() => setFilter("you")}>You</button>
        </div>
      </div>
      <div className="lap-groups">
        {report.commentary.map((g) => {
          const lap = laps.get(g.lap);
          const events = g.events.filter(keep);
          if (!events.length && filter !== "all") return null;
          const focusSet = g.events.find((e) => e.type === "focus");
          return (
            <details key={g.lap} className="lap-group" open={filter !== "all"}>
              <summary>
                <b>{lap ? `Lap ${lap.lap}` : g.lap === "out" ? "Out lap" : "After the last lap"}</b>
                {lap && <span className="mono">{lapTime(lap.lap_time)}</span>}
                {lap?.pace === "pushing" && lap.gap_s != null && <span className="mono muted">{signed(lap.gap_s, 2)}</span>}
                {lap && lap.pace !== "pushing" && <span className={`pace-chip ${lap.pace}`}>{paceLabel(lap)}</span>}
                {lap?.lap === report.summary.best_lap_no && <span className="pace-chip best">Best</span>}
                {focusSet?.type === "focus" && focusSet.focus && <span className="pace-chip focus">Focus: {focusSet.focus.label}</span>}
                <span className="small muted push-right">{lap ? `${lap.said} said · ${lap.held_back} held back` : `${events.length} lines`}</span>
              </summary>
              <ol className="lines">
                {events.map((e) => <Line key={e.seq} e={e} openCorner={lap ? (c) => open(lap.lap_id, c) : undefined} />)}
              </ol>
            </details>
          );
        })}
      </div>
    </section>
  );
}

function Line({ e, openCorner }: { e: LiveEvent; openCorner?: (corner: number) => (() => void) | undefined }) {
  const t = e.at != null ? clock(e.at) : "";
  switch (e.type) {
    case "line": {
      const go = e.corner != null && openCorner ? openCorner(e.corner) : undefined;
      return (
        <li className={`line ${e.status}`}>
          <span className="mono t">{t}</span>
          <div>
            <span><span className={`badge ${e.kind}`}>{BADGES[e.kind] ?? e.kind}</span>{e.text}</span>
            {e.status === "dropped" && <span className="small warn">Not said{e.note ? `: ${e.note}` : ""}</span>}
            {e.status === "cut" && <span className="small warn">Cut off by a corner cue</span>}
          </div>
          {go ? <button type="button" className="link-btn" onClick={go}>Open ↗</button> : <span />}
        </li>
      );
    }
    case "driver":
      return <li className="line you-line"><span className="mono t">{t}</span><div><span><span className="badge">You</span>{e.text}</span></div><span /></li>;
    case "answer":
      return <li className="line"><span className="mono t">{t}</span><div><span><span className="badge answer">Answer</span>{e.text}</span></div><span /></li>;
    case "focus":
      return <li className="line note-line"><span className="mono t">{t}</span><div><span>Focus set by the {e.by}: {e.focus?.label ?? "none"}</span></div><span /></li>;
    case "pace":
      return <li className="line note-line"><span className="mono t">{t}</span><div><span>{e.mode === "tranquille" ? `Not pushing from ${Math.round(e.lap_dist)} m: quiet` : `Pushing again at ${Math.round(e.lap_dist)} m`}</span></div><span /></li>;
    case "error":
      return <li className="line dropped"><span className="mono t">{t}</span><div><span>{e.message}</span></div><span /></li>;
    default:
      return null;
  }
}

function DebriefCard({ report, onAsked }: { report: SessionReport; onAsked: () => void }) {
  const [asked, setAsked] = useState(false);
  const running = report.debrief_running || (asked && !report.debrief);
  const write = async () => {
    setAsked(true);
    try {
      await api.debrief(report.id);
      onAsked(); // reload: the report keeps checking while the coach writes
    } catch {
      setAsked(false);
    }
  };
  return (
    <section className="card" aria-label="Debrief">
      <div className="card-head"><h2>Debrief</h2><span className="small muted">by the coach · claude -p</span></div>
      {report.debrief ? report.debrief.split(/\n\s*\n/).map((p, i) => <p key={i} className="debrief">{p}</p>)
        : <p className="muted small">{running ? "The coach is writing it (about half a minute)…" : "Have the coach sum up the session: what worked, what still costs the most, and what to focus on next."}</p>}
      <div className="row">
        <button type="button" className="btn" disabled={running} onClick={write}>{report.debrief ? "Rewrite" : running ? "Writing…" : "Write debrief"}</button>
      </div>
    </section>
  );
}

function NextSessionCard({ report, focus }: { report: SessionReport; focus: FocusOutcome | null }) {
  // Candidates: the focus if it isn't sorted, then the cues losing the most while pushing.
  const loss = new Map(report.corners.map((c) => [c.corner, c.mean_s]));
  const ranked = report.cues
    .map((c) => ({ cue: c, loss: c.corners.reduce((sum, k) => sum + (loss.get(k) ?? 0), 0) }))
    .filter((c) => c.loss >= 0.15)
    .sort((a, b) => b.loss - a.loss);
  const open = focus && !["sorted", "replaced"].includes(focus.verdict) ? ranked.find((r) => r.cue.corners[0] === focus.cue) : undefined;
  const options = [...(open ? [open] : []), ...ranked.filter((r) => r !== open)].slice(0, 3);
  const [pick, setPick] = useState<number | null>(null);
  // Until the driver picks, the top option (options change as a running session fills in).
  const chosen = options.find((o) => o.cue.corners[0] === pick) ?? options[0];
  const [text, setText] = useState(chosen?.cue.text ?? "");
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => { setText(chosen?.cue.text ?? ""); setSaved(null); }, [chosen?.cue.text]);
  if (!report.track.key || !options.length) return null;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!chosen) return;
    try {
      const out = await api.savePlan({ track: report.track.key!, car: report.track.car!, focus: chosen.cue.corners[0],
        cue_text: text.trim() !== chosen.cue.text ? text.trim() : undefined, from_session: report.id });
      setSaved(`Saved: the next ${out.sessions_left} sessions here start with this focus (re-checked after two pushing laps).`);
    } catch (err) {
      setSaved((err as Error).message);
    }
  };
  return (
    <form className="card" aria-label="Next session" onSubmit={save}>
      <h2>Next session</h2>
      {report.next_plan && <span className="small muted">Planned now: T{report.next_plan.focus} ({report.next_plan.sessions_left} session{report.next_plan.sessions_left === 1 ? "" : "s"} left)</span>}
      <div className="next-options" role="radiogroup" aria-label="Focus for next session">
        {options.map((o) => {
          const label = o.cue.corners.map((c) => `T${c}`).join("–");
          return (
            <label key={label} className="next-option">
              <input type="radio" name="next-focus" checked={o === chosen} onChange={() => setPick(o.cue.corners[0])} />
              <span><b>{label}</b> <span className="muted">· {o.loss.toFixed(2)} s a lap{o === open ? " · this session's focus, not sorted yet" : ""}</span></span>
            </label>
          );
        })}
      </div>
      <label className="field-col">
        <span className="field-label">Its cue (spoken on the approach)</span>
        <textarea rows={3} className="text-input" value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="row"><button type="submit" className="btn primary">Save plan</button></div>
      {saved && <span className="small muted">{saved}</span>}
    </form>
  );
}

function AskCard({ report }: { report: SessionReport }) {
  const [text, setText] = useState("");
  const [answer, setAnswer] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const worst = [...report.corners].sort((a, b) => b.mean_s - a.mean_s)[0];
  const suggestions = [
    ...(worst ? [`Why am I losing time at Turn ${worst.corner}?`] : []),
    "What should I practise first next time?",
  ];
  const ask = async (q: string) => {
    if (!q.trim() || busy) return;
    setBusy(true);
    setAnswer("");
    let current = "";
    try {
      await streamChat({ message: q.trim(), session_id: null, context: {
        page: "session", track: report.track.key ?? undefined, car: report.track.car ?? undefined,
        ref: report.ref.lap_id ?? undefined, live: report.context,
      } }, (ev) => {
        if (ev.type === "text_start") current = "";
        if (ev.type === "text") { current += ev.text; setAnswer(current); }
        if (ev.type === "error") setAnswer(ev.message);
      });
    } catch (e) {
      setAnswer((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card" aria-label="Ask about this session">
      <h2>Ask about this session</h2>
      <div className="suggestions">
        {suggestions.map((q) => <button key={q} type="button" className="btn ghost-btn" disabled={busy} onClick={() => { setText(q); ask(q); }}>{q}</button>)}
      </div>
      <form className="row ask-row" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <label htmlFor="session-ask" className="sr-only">Ask the coach</label>
        <input id="session-ask" className="text-input" placeholder="Ask the coach…" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn primary" disabled={busy || !text.trim()}>{busy ? "…" : "Ask"}</button>
      </form>
      {answer && <div className="debrief answer-text">{renderText(answer)}</div>}
      <span className="small muted">For a longer conversation, open the lap review's chat.</span>
    </section>
  );
}

function paceLabel(lap: SessionLap): string {
  if (lap.pace === "moment") return lap.moment_at ? `Moment at T${lap.moment_at}` : "A moment";
  if (lap.pace === "tranquille") return "Tranquille";
  return "Pushing";
}

function verdictLabel(v: Verdict): string {
  return VERDICTS[v].toLowerCase();
}

function lossColor(v: number | null | undefined): string {
  if (v == null) return "#2a3036";
  if (v >= 0.5) return "var(--loss-strong)";
  if (v >= 0.2) return "var(--loss)";
  if (v >= 0.08) return "#c9a595";
  if (v <= -0.08) return "var(--gain-strong)";
  return "#56616c";
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? `Today ${time}` : `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })} ${time}`;
}

function clock(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s - m * 60).toString().padStart(2, "0")}`;
}
