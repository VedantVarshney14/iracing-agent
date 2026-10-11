import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { lapTime, signed } from "../format";
import type { DebugEvent } from "../types";

/** What each event type is about, for the filters. Types not listed are "pipeline". */
const GROUPS: Record<string, string> = {
  ask_engineer: "engineer", engineer_asked: "engineer", engineer_turn: "engineer", engineer_reply: "engineer", debrief: "engineer",
  line: "speech", say: "speech", driver: "speech", answer: "speech",
  crossing: "track", lap: "track", corner_exit: "track", approach: "track", pace: "track", slowing: "track",
  cool_down: "track", pit: "track", advice: "track", focus: "track", set_focus: "track",
};
const FILTERS: { key: string; label: string }[] = [
  { key: "engineer", label: "Engineer" },
  { key: "agent-rules", label: "Agent rules" },
  { key: "coach-rules", label: "Coach's rules" },
  { key: "speech", label: "Speech" },
  { key: "track", label: "Track" },
  { key: "pipeline", label: "Pipeline" },
];
const DEFAULT_FILTERS = new Set(["engineer", "agent-rules", "speech", "track"]);

function groupOf(e: DebugEvent): string {
  if (e.type === "rule") return e.group === "builtin" ? "coach-rules" : "agent-rules";
  return GROUPS[e.type] ?? "pipeline";
}

/** The whole loop of a session, from its debug log: every event, each rule firing with the values it
 * saw, and each engineer turn with the exact prompt and reply. Follows a running session. */
export function DebugView({ id, live }: { id: string; live: boolean }) {
  const [events, setEvents] = useState<DebugEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<Set<string>>(DEFAULT_FILTERS);
  const [search, setSearch] = useState("");

  useEffect(() => {
    let stale = false;
    let timer = 0;
    let have: DebugEvent[] = [];
    const load = async () => {
      try {
        const out = await api.debugLog(id, have.length);
        if (stale) return;
        have = [...have, ...out.events];
        setEvents(have);
        setError(null);
      } catch (e) {
        if (!stale) setError((e as Error).message);
      }
      if (!stale && live) timer = window.setTimeout(load, 2000);
    };
    load();
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [id, live]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of events) c[groupOf(e)] = (c[groupOf(e)] ?? 0) + 1;
    return c;
  }, [events]);
  const laps = useMemo(() => byLap(events), [events]);
  const refused = useMemo(() => notTaken(events), [events]);
  const needle = search.trim().toLowerCase();
  const keep = (e: DebugEvent) => shown.has(groupOf(e)) && (!needle || JSON.stringify(e).toLowerCase().includes(needle));
  const toggle = (key: string) => setShown((s) => {
    const next = new Set(s);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });

  if (error) return <section className="card"><p className="muted">{error} Sessions recorded before the debug log existed don't have one.</p></section>;
  return (
    <div className="debug-view">
      <section className="card" aria-label="The loop at a glance">
        <div className="card-head">
          <h2>The loop{live ? " (following the session)" : ""}</h2>
          <span className="small muted">{events.length} events · sessions/live/debug/{id}.jsonl</span>
        </div>
        <Totals events={events} />
      </section>
      <section className="card" aria-label="Events">
        <div className="debug-filters">
          {FILTERS.map((f) => (
            <label key={f.key} className="chip debug-chip">
              <input type="checkbox" checked={shown.has(f.key)} onChange={() => toggle(f.key)} /> {f.label} ({counts[f.key] ?? 0})
            </label>
          ))}
          <label htmlFor="debug-search" className="sr-only">Search events</label>
          <input id="debug-search" className="text-input debug-search" placeholder="Search (rule id, corner, words…)" value={search}
            onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="lap-groups">
          {laps.map((g, i) => {
            const rows = g.events.filter(keep);
            return (
              <details key={i} className="lap-group" open={i === laps.length - 1 || !!needle}>
                <summary>
                  <b>{g.label}</b>
                  {g.lap && <span className="mono">{lapTime(g.lap.lap_time as number | null)}</span>}
                  {g.lap?.gap_s != null && <span className="mono muted">{signed(g.lap.gap_s as number, 2)}</span>}
                  <span className="small muted push-right">{rows.length} of {g.events.length} events</span>
                </summary>
                <ol className="lines">{rows.map((e) => <Row key={e.debug_seq} e={e} refused={refused.has(e.debug_seq)} />)}</ol>
              </details>
            );
          })}
        </div>
      </section>
    </div>
  );
}

/** What the harness most needs at a glance: how the engineer's turns went, and what the coach held back. */
function Totals({ events }: { events: DebugEvent[] }) {
  const turns = events.filter((e) => e.type === "engineer_turn");
  const outcomes = tally(turns.map((e) => String(e.outcome)));
  const took = turns.filter((e) => e.prompt).map((e) => Number(e.took_s));
  const lines = events.filter((e) => e.type === "line");
  const said = tally(lines.map((e) => String(e.status)));
  const fired = events.filter((e) => e.type === "rule");
  const results = tally(fired.map((e) => String(e.result)));
  return (
    <div className="stats">
      <div className="stat">
        <span className="field-label">Engineer turns</span>
        <span className="mono stat-value">{turns.length}</span>
        <span className="small muted">{entries(outcomes) || "none"}{took.length ? ` · ${(took.reduce((a, b) => a + b, 0) / took.length).toFixed(1)} s a turn, longest ${Math.max(...took).toFixed(1)} s` : ""}</span>
      </div>
      <div className="stat">
        <span className="field-label">Lines</span>
        <span className="mono stat-value">{lines.length}</span>
        <span className="small muted">{entries(said) || "none"}</span>
      </div>
      <div className="stat">
        <span className="field-label">Rule firings</span>
        <span className="mono stat-value">{fired.length}</span>
        <span className="small muted">{entries(results) || "none"}</span>
      </div>
    </div>
  );
}

function Row({ e, refused }: { e: DebugEvent; refused: boolean }) {
  const { badge, text: said, warn } = describe(e);
  const text = refused ? `${said} · not taken: one already waiting, or too soon after the last` : said;
  return (
    <li className={`line debug-row${warn || refused ? " dropped" : ""}`}>
      <span className="mono t">{e.at != null ? clock(e.at) : ""}</span>
      <details>
        <summary><span className={`badge ${badgeClass(e)}`}>{badge}</span>{text}</summary>
        <Details e={e} />
      </details>
      <span />
    </li>
  );
}

/** The event in full; an engineer turn's prompt and reply as they were. */
function Details({ e }: { e: DebugEvent }) {
  const { debug_seq, seq, wall, type, prompt, reply, ...rest } = e;
  void debug_seq; void seq; void type;
  return (
    <div className="debug-details">
      {typeof prompt === "string" && (<><span className="field-label">What the engineer was shown</span><pre>{prompt}</pre></>)}
      {reply !== undefined && (<><span className="field-label">What it replied</span><pre>{JSON.stringify(reply, null, 1)}</pre></>)}
      <span className="field-label">Fields · {wall}</span>
      <pre>{JSON.stringify(rest, null, 1)}</pre>
    </div>
  );
}

function badgeClass(e: DebugEvent): string {
  if (e.type === "line") return String(e.kind);
  if (e.type === "rule") return "rule";
  if (groupOf(e) === "engineer") return "answer";
  return "";
}

/** One line about an event: what happened, in the terms the coach works in. */
function describe(e: DebugEvent): { badge: string; text: string; warn?: boolean } {
  const f = (k: string) => e[k] as never;
  switch (e.type) {
    case "engineer_turn":
      return {
        badge: `turn · ${f("kind")}`,
        text: `${f("outcome")}${e.rule ? ` (${f("rule")})` : ""} · waited ${f("waited_s")} s${e.prompt ? `, took ${f("took_s")} s${e.resumed ? "" : " · new conversation"}` : ""}`,
        warn: e.outcome === "too late" || e.outcome === "failed",
      };
    case "ask_engineer":
      return { badge: "ask", text: `${e.rule ? `rule ${f("rule")}` : f("kind")}${e.stretch != null ? ` (stretch ${f("stretch")})` : ""}` };
    case "engineer_asked":
      return { badge: "taken", text: `the engineer took the ${f("kind")} request` };
    case "engineer_reply":
      return { badge: `reply · ${f("kind")}`, text: `${f("text") ?? "nothing"}${e.status ? ` (${f("status")})` : ""}`, warn: !e.text };
    case "rule": {
      const values = Object.entries((e.values as Record<string, unknown>) ?? {}).map(([k, v]) => `${k}=${v}`).join(" ");
      const did = ((e.actions as Record<string, unknown>[]) ?? []).map((a) => Object.keys(a).filter((k) => k !== "missing")[0]).join(", ");
      return {
        badge: e.group === "builtin" ? "coach rule" : "rule",
        text: `${f("rule")} on ${f("trigger")}: ${e.result === "limited" ? `held (${f("why")})` : `fired → ${did || "nothing"}`}${values ? ` · ${values}` : ""}`,
        warn: e.result === "limited",
      };
    }
    case "line":
      return { badge: String(e.kind), text: `${e.status === "said" ? "" : `${f("status")}: `}${f("text")}${e.note ? ` (${f("note")})` : ""}`, warn: e.status !== "said" };
    case "say":
      return { badge: "say", text: `${f("kind")}: ${f("text")}` };
    case "driver":
      return { badge: "driver", text: f("text") };
    case "answer":
      return { badge: "answer", text: f("text") };
    case "corner_exit":
      return { badge: "corner", text: `T${f("corner")} ${signed(f("delta_s"), 2)} s${e.at_pace ? "" : " (not at pace)"}${e.cause ? ` · ${f("cause")}` : ""}${e.feedback ? ` · “${f("feedback")}”` : ""}` };
    case "approach":
      return { badge: "approach", text: `${f("watch")} in ${Number(e.eta_s).toFixed(1)} s${e.text ? ` · ${e.wanted ? "" : "not wanted: "}“${f("text")}”` : ""}` };
    case "crossing":
      return { badge: "crossing", text: `crossed the line · ${e.complete ? "complete" : "incomplete"}, ${e.clean ? "clean" : "not clean"}, ${f("pace")}` };
    case "lap":
      return { badge: "lap", text: `lap ${f("lap")} ${lapTime(f("lap_time"))}${e.gap_s != null ? ` (${signed(f("gap_s"), 2)})` : ""} · ${f("pace")}${e.worst_name ? ` · worst ${f("worst_name")} ${signed(f("worst_delta_s"), 2)}` : ""}` };
    case "pace":
      return { badge: "pace", text: `${f("mode")} at ${Math.round(Number(e.lap_dist))} m` };
    case "focus":
      return { badge: "focus", text: `${(e.focus as { label?: string } | null)?.label ?? "none"}, by the ${f("by")}` };
    case "status":
      return { badge: "status", text: String(e.state) };
    case "error":
      return { badge: "error", text: f("message"), warn: true };
    default: {
      const { debug_seq, seq, wall, type, at, ...rest } = e;
      void debug_seq; void seq; void wall; void at;
      return { badge: type.replace(/_/g, " "), text: Object.entries(rest).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ") };
    }
  }
}

/** The engineer requests it didn't take on: no `engineer_asked` of that kind at the same moment. */
function notTaken(events: DebugEvent[]): Set<number> {
  const taken = new Set(events.filter((e) => e.type === "engineer_asked").map((e) => `${e.at}|${e.kind}`));
  return new Set(events.filter((e) => e.type === "ask_engineer" && !taken.has(`${e.at}|${e.kind}`)).map((e) => e.debug_seq));
}

/** Events in laps: a lap ends with the events at its line crossing; after the last, what's left. */
function byLap(events: DebugEvent[]): { label: string; lap?: DebugEvent; events: DebugEvent[] }[] {
  const groups: { label: string; lap?: DebugEvent; events: DebugEvent[] }[] = [];
  let current: DebugEvent[] = [];
  let lineAt: number | null = null;
  const close = () => {
    const lap = current.find((e) => e.type === "lap");
    const label = lap ? `Lap ${lap.lap}` : groups.length === 0 ? "Out lap" : "Lap not counted";
    groups.push({ label, lap, events: current });
    current = [];
  };
  for (const e of events) {
    if (lineAt != null && e.at != null && e.at > lineAt) {
      close();
      lineAt = null;
    }
    current.push(e);
    if (e.type === "crossing") lineAt = e.at ?? null;
  }
  if (current.length) {
    if (lineAt != null) close();
    else groups.push({ label: groups.length ? "Since the last crossing" : "Before the first crossing", events: current });
  }
  return groups;
}

function tally(xs: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const x of xs) out[x] = (out[x] ?? 0) + 1;
  return out;
}

function entries(t: Record<string, number>): string {
  return Object.entries(t).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${k}`).join(" · ");
}

function clock(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}
