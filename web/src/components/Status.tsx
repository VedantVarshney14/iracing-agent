import { useState } from "react";
import { api } from "../api";
import type { LapInfo, SystemInfo } from "../types";

/** Header pill: is this machine watching iRacing's telemetry folder for new recordings? */
export function TelemetryIndicator({ system }: { system: SystemInfo | null }) {
  if (!system) return null;
  const t = system.telemetry;
  let state: "ok" | "warn" | "off";
  let text: string;
  let detail: string;
  if (t.watching && t.error) {
    [state, text, detail] = ["warn", "iRacing telemetry · problem", t.error];
  } else if (t.watching && t.found) {
    const last = t.last && t.last.laps ? ` · last: ${t.last.track ?? t.last.file} (${t.last.laps} laps)` : "";
    [state, text, detail] = ["ok", `iRacing telemetry · watching${last}`,
      `Watching ${t.folder}. New recordings appear here when the session ends. ${t.files_ingested} recordings ingested.`];
  } else if (t.watching) {
    [state, text, detail] = ["off", "iRacing telemetry · folder not found",
      `Waiting for ${t.folder} to be created. The folder will be watched for new recordings.`];
  } else if (t.found) {
    [state, text, detail] = ["off", "iRacing telemetry · not watching", `${t.folder} exists but isn't watched (started with --no-watch).`];
  } else {
    [state, text, detail] = ["off", "No iRacing on this computer",
      `No telemetry folder at ${t.folder}. On the sim PC this watches it for new recordings; here, ingest .ibt files with \`iagent ingest\` or set IAGENT_TELEMETRY_DIR to a synced folder.`];
  }
  return (
    <span className={`status-pill ${state}`} title={detail}>
      <span className="dot" aria-hidden="true" />
      {text}
    </span>
  );
}

/** Put the ghost's Garage61 lap into iRacing (on the sim PC), or download its file elsewhere. */
export function GhostButton({ ghost, system }: { ghost: LapInfo; system: SystemInfo | null }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!ghost.garage61_id) return null;
  const install = system?.lapfiles_found ?? false;
  const unavailable = ghost.ghost_available === false;

  const run = async () => {
    setBusy(true);
    setNote(null);
    try {
      const out = await api.garage61Ghost(ghost.garage61_id!, install);
      if (out.installed) {
        setNote("Installed. In iRacing: Options › Driving Aids › Load Comparison Lap, tick Display Reference Car.");
      } else {
        const a = document.createElement("a");
        a.href = out.download;
        a.download = "";
        a.click();
        setNote("Downloaded. Copy it to Documents/iRacing/lapfiles/<track> on the sim PC.");
      }
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="ghost-action">
      <button
        type="button"
        className="btn"
        disabled={busy || unavailable}
        title={unavailable ? "Garage61 has no iRacing ghost file for this lap." : undefined}
        onClick={run}
      >
        {busy ? "Working…" : install ? "Install ghost in iRacing" : "Download ghost (.blap)"}
      </button>
      {note && <span className="muted small ghost-note">{note}</span>}
    </span>
  );
}
