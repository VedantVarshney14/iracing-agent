import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { lapTime } from "../format";
import type { LiveStatus, Recording, SystemInfo } from "../types";

// Starting the live coach (iRacing or a replay) and the strip shown while it runs.

const VOICES = ["alba", "charles", "jane", "paul", "silent"];
export const ACTIVE = new Set(["starting", "waiting", "running", "stopping"]);

export function StartForm({ system, onStarted, onError }: {
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

export function Running({ status, onError }: { status: LiveStatus; onError: (e: string | null) => void }) {
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
      {status.ref && <span className="muted">Following <span className="ghost-text">{status.ref.driver ?? "your lap"} · {lapTime(status.ref.lap_time)}</span></span>}
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
