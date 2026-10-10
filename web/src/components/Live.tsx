import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { lapTime, signed } from "../format";
import type { LiveEvent, LiveStatus, Recording, SystemInfo } from "../types";

const VOICES = ["alba", "charles", "jane", "paul", "silent"];
const ACTIVE = new Set(["starting", "waiting", "running", "stopping"]);

/** The live coach: start it (iRacing or a replay), see what it says and why, ask it something. */
export function Live({ system }: { system: SystemInfo | null }) {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(0);
  const session = useRef<string | null>(null);
  const activeNow = useRef(false);

  // Poll the session: every second while it runs, every few seconds otherwise.
  useEffect(() => {
    let stale = false;
    let timer = 0;
    const poll = async () => {
      try {
        const out = await api.liveEvents(seen.current);
        if (stale) return;
        if (out.status.session_id !== session.current) {
          session.current = out.status.session_id;
          seen.current = 0;
          const all = await api.liveEvents(0);
          if (stale) return;
          setEvents(all.events);
          seen.current = all.events.length;
        } else if (out.events.length) {
          setEvents((e) => [...e, ...out.events]);
          seen.current += out.events.length;
        }
        setStatus(out.status);
        activeNow.current = ACTIVE.has(out.status.state);
      } catch (e) {
        if (!stale) setError((e as Error).message);
      }
      if (!stale) timer = window.setTimeout(poll, activeNow.current ? 1000 : 3000);
    };
    poll();
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one polling loop for the page
  }, []);

  const active = status != null && ACTIVE.has(status.state);
  return (
    <main className="live">
      {active ? <Running status={status!} onError={setError} /> : <StartForm system={system} onStarted={setStatus} onError={setError} />}
      {(error || status?.error) && <div className="error" role="alert">{error ?? status?.error}</div>}
      <div className="live-body">
        <section className="card live-feed" aria-label="Commentary">
          <div className="card-head">
            <h2>Commentary {status?.session_id && <span className="muted">· session {status.session_id}</span>}</h2>
          </div>
          <Feed events={events} />
          <AskBox enabled={active} onError={setError} />
        </section>
        {status?.cues && <CueList status={status} onError={setError} />}
      </div>
    </main>
  );
}

function StartForm({ system, onStarted, onError }: {
  system: SystemInfo | null;
  onStarted: (s: LiveStatus) => void;
  onError: (e: string | null) => void;
}) {
  const iracingHere = system?.lapfiles_found ?? false;
  const [source, setSource] = useState<"iracing" | "replay">(iracingHere ? "iracing" : "replay");
  const [files, setFiles] = useState<Recording[]>([]);
  const [file, setFile] = useState("");
  const [speed, setSpeed] = useState(1);
  const [voice, setVoice] = useState("alba");
  const [learning, setLearning] = useState(2);
  const [focus, setFocus] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.liveRecordings().then((rows) => {
      setFiles(rows);
      setFile((f) => f || rows[0]?.path || "");
    }).catch(() => setFiles([]));
  }, []);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    onError(null);
    try {
      onStarted(await api.liveStart({ source, file: source === "replay" ? file : null, speed, voice, learning_laps: learning, focus }));
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="live-controls" onSubmit={start}>
      <div className="field-col">
        <span className="field-label">Telemetry</span>
        <div className="segmented" role="group" aria-label="Telemetry source">
          <button type="button" aria-pressed={source === "iracing"} onClick={() => setSource("iracing")}>
            iRacing live{iracingHere ? "" : " · not on this computer"}
          </button>
          <button type="button" aria-pressed={source === "replay"} onClick={() => setSource("replay")}>Replay a recording</button>
        </div>
      </div>
      {source === "replay" && (
        <>
          <label className="field-col">
            <span className="field-label">Recording</span>
            <select value={file} onChange={(e) => setFile(e.target.value)} className="wide">
              {files.length === 0 && <option value="">No .ibt files found</option>}
              {files.map((f) => <option key={f.path} value={f.path}>{f.name}</option>)}
            </select>
          </label>
          <label className="field-col">
            <span className="field-label">Speed</span>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              <option value={1}>1× (hear it)</option>
              <option value={4}>4×</option>
              <option value={20}>20× (text only)</option>
            </select>
          </label>
        </>
      )}
      <label className="field-col">
        <span className="field-label">Voice</span>
        <select value={voice} onChange={(e) => setVoice(e.target.value)}>
          {VOICES.map((v) => <option key={v} value={v}>{v === "silent" ? "Silent (log only)" : v}</option>)}
        </select>
      </label>
      <label className="field-col">
        <span className="field-label">Every corner for</span>
        <select value={learning} onChange={(e) => setLearning(Number(e.target.value))}>
          {[1, 2, 3, 5].map((n) => <option key={n} value={n}>{n} lap{n > 1 ? "s" : ""}</option>)}
        </select>
      </label>
      <label className="check">
        <input type="checkbox" checked={focus} onChange={(e) => setFocus(e.target.checked)} />
        One focus at a time
      </label>
      <button type="submit" className="btn primary start" disabled={busy || (source === "replay" && !file)}>
        {busy ? "Starting…" : "Start coaching"}
      </button>
    </form>
  );
}

function Running({ status, onError }: { status: LiveStatus; onError: (e: string | null) => void }) {
  const [stopping, setStopping] = useState(false);
  const label = {
    starting: "Starting (loading the voice)…",
    waiting: "Waiting for iRacing…",
    running: "Coaching",
    stopping: "Stopping…",
  }[status.state as "starting" | "waiting" | "running" | "stopping"];
  const opts = status.options;
  return (
    <section className="live-controls running" aria-label="Coach controls">
      <span className="row"><span className="live-dot" aria-hidden="true" /><b>{label}</b></span>
      {status.track && <span className="muted">{status.track.name} · {status.track.car_name}</span>}
      {opts?.source === "replay" && <span className="muted">Replay · {opts.speed}×</span>}
      {status.ref && <span className="muted">Following <span className="ghost-text">{status.ref.lap_id} · {lapTime(status.ref.lap_time)}</span></span>}
      {status.laps_done != null && <span className="muted">{status.laps_done} laps{status.learning ? " · learning" : ""}</span>}
      {status.mode === "tranquille" && <span className="muted">· not pushing: quiet</span>}
      {status.focus && <span className="focus-pill">Focus: {status.focus.label}</span>}
      <button type="button" className="btn stop" disabled={stopping} onClick={async () => {
        setStopping(true);
        try {
          await api.liveStop();
        } catch (e) {
          onError((e as Error).message);
        } finally {
          setStopping(false);
        }
      }}>
        Stop coaching
      </button>
    </section>
  );
}

const BADGES: Record<string, string> = { approach: "Cue", feedback: "Feedback", summary: "Lap", focus: "Focus", answer: "Answer" };

function Feed({ events }: { events: LiveEvent[] }) {
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = list.current; // follow the newest line, inside the list only (not the page)
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);
  const shown = events.filter((e) => e.type !== "status" || e.state === "running" || e.state === "stopped" || e.state === "ended");
  if (shown.length === 0) return <p className="muted">Start coaching to see what the coach says, and what it held back and why.</p>;
  return (
    <ol className="feed" ref={list}>
      {shown.map((e) => {
        const t = e.at != null ? <span className="mono t">{clock(e.at)}</span> : <span className="t" />;
        switch (e.type) {
          case "status":
            return <li key={e.seq} className="feed-status">{e.state === "running" ? `Coaching ${e.track ?? ""} · following ${e.ref ?? ""}` : `Session ${e.state}`}</li>;
          case "lap":
            return (
              <li key={e.seq} className="feed-lap">
                <b>Lap {e.lap}</b>
                <span className="muted">
                  {lapTime(e.lap_time)}{e.pace === "pushing" && e.gap_s != null ? ` · ${signed(e.gap_s)} s` : ""}
                  {e.pace === "moment" ? ` · moment at T${e.moment_at}` : e.pace === "tranquille" ? " · tranquille" : ""}
                </span>
                <span className="rule" />
              </li>
            );
          case "focus":
            return <li key={e.seq} className="feed-lap"><b className="focus-text">Focus: {e.focus?.label ?? "coach picks"}</b><span className="muted">set by {e.by}</span><span className="rule" /></li>;
          case "pace":
            return (
              <li key={e.seq} className="feed-status">
                {e.mode === "tranquille" ? `Not pushing from ${Math.round(e.lap_dist)} m: quiet until you're back on pace` : `Pushing again at ${Math.round(e.lap_dist)} m`}
              </li>
            );
          case "driver":
            return <li key={e.seq} className="feed-driver"><span>{e.text}</span></li>;
          case "answer":
            return <li key={e.seq} className="feed-line said">{t}<div><span className="badge answer">Answer</span><span>{e.text}</span></div></li>;
          case "error":
            return <li key={e.seq} className="feed-line dropped">{t}<div><span className="badge">Error</span><span>{e.message}</span></div></li>;
          case "line":
            if (e.kind === "answer" && e.status === "said") return null; // shown with the answer itself
            return (
              <li key={e.seq} className={`feed-line ${e.status}`}>
                {t}
                <div>
                  <span className={`badge ${e.kind}`}>{BADGES[e.kind] ?? e.kind}</span>
                  <span>{e.text}</span>
                  {e.status === "dropped" && <span className="small warn">Not said{e.note ? `: ${e.note}` : ""}</span>}
                  {e.status === "cut" && <span className="small warn">Cut off by a corner cue</span>}
                </div>
              </li>
            );
        }
      })}
    </ol>
  );
}

function AskBox({ enabled, onError }: { enabled: boolean; onError: (e: string | null) => void }) {
  const [text, setText] = useState("");
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    try {
      await api.liveAsk(text.trim());
      setText("");
    } catch (err) {
      onError((err as Error).message);
    }
  };
  return (
    <form className="ask" onSubmit={send}>
      <label htmlFor="live-ask" className="small muted">Ask the coach. The answer is spoken on the next straight.</label>
      <div className="row">
        <input id="live-ask" className="text-input" value={text} disabled={!enabled}
          placeholder={enabled ? "e.g. what am I doing wrong at Turn 9?" : "Start coaching to ask"} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn primary" disabled={!enabled || !text.trim()}>Ask</button>
      </div>
    </form>
  );
}

function CueList({ status, onError }: { status: LiveStatus; onError: (e: string | null) => void }) {
  const running = status.state === "running";
  const focus = status.focus?.cue;
  return (
    <section className="card live-cues" aria-label="Cues">
      <div className="card-head">
        <h2>Cues <span className="muted">· {status.cues!.filter((c) => c.cued).length} of {status.cues!.length} cued now</span></h2>
      </div>
      <ul>
        {status.cues!.map((c) => {
          const isFocus = c.corners[0] === focus;
          return (
            <li key={c.corners.join("-")} className={isFocus ? "focus" : c.cued ? "cued" : "quiet"}>
              <span className="mono">{c.corners.map((n) => `T${n}`).join("+")}</span>
              <span className="cue-text">{c.text}</span>
              <span className="small">{isFocus ? "Focus" : c.cued ? "Cued" : "Quiet"}</span>
              <button type="button" className="btn" disabled={!running} onClick={() =>
                api.liveFocus(isFocus ? null : c.corners[0]).catch((e) => onError((e as Error).message))}>
                {isFocus ? "Unfocus" : "Focus"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function clock(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s - m * 60).toString().padStart(2, "0")}`;
}
