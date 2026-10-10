import type { Page } from "../App";
import { lapTime, sessionDate, signed } from "../format";
import type { Garage61Laps, LapsResponse, Review, SystemInfo, TrackRow } from "../types";
import { GhostButton, TelemetryIndicator } from "./Status";

interface Props {
  tracks: TrackRow[];
  group: { track: string; car: string } | null;
  laps: LapsResponse | null;
  lapId: string | null;
  refId: string | null;
  review: Review | null;
  garage61: Garage61Laps | null;
  importing: string | null; // whose Garage61 lap is being imported
  system: SystemInfo | null;
  page: Page;
  canReview: boolean;
  canCorner: boolean;
  onPage: (page: Page) => void;
  onGroup: (track: string, car: string) => void;
  onLap: (id: string) => void;
  onRef: (id: string) => void; // a lap id, or "g61:<garage61 id>" for a lap still to import
}

const PAGES: { page: Page; label: string }[] = [
  { page: "library", label: "Library" },
  { page: "review", label: "Lap review" },
  { page: "corner", label: "Corner view" },
  { page: "coaching", label: "Coaching" },
];

export function TopBar(props: Props) {
  const { tracks, group, laps, lapId, refId, review, garage61, importing, system, page, onGroup, onLap, onRef } = props;
  const own = laps?.laps.filter((l) => l.lap_time != null) ?? [];
  // Garage61's list (imported or not), plus any imported reference laps it no longer lists.
  const g61 = garage61?.laps ?? [];
  const listed = new Set(g61.map((l) => l.lap_id).filter(Boolean));
  const otherRefs = (laps?.references ?? []).filter((r) => !listed.has(r.lap_id));
  const delta = review?.total_delta_s ?? null;
  return (
    <>
      <header className="topbar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--you)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 18c3-9 6-12 9-12 4 0 2 6 6 6" />
            <circle cx="4" cy="18" r="1.5" />
            <circle cx="19" cy="12" r="1.5" />
          </svg>
          <span>iRacing Coach</span>
        </div>
        <nav aria-label="Views" className="views">
          {PAGES.map((p) => {
            const enabled = p.page === "library" || p.page === "coaching" || (p.page === "review" ? props.canReview : props.canCorner);
            return (
              <button key={p.page} type="button" aria-current={page === p.page ? "page" : undefined} disabled={!enabled}
                onClick={() => props.onPage(p.page)}>
                {p.label}
              </button>
            );
          })}
        </nav>
        {tracks.length > 0 && page !== "library" && page !== "coaching" && (
          <label className="field">
            <span className="sr-only">Track and car</span>
            <select
              value={group ? `${group.track}|${group.car}` : ""}
              onChange={(e) => {
                const [track, car] = e.target.value.split("|");
                onGroup(track, car);
              }}
            >
              {tracks.map((t) => (
                <option key={`${t.track}|${t.car}`} value={`${t.track}|${t.car}`}>
                  {t.track_name} · {t.car_name}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className="topbar-end">
          <TelemetryIndicator system={system} onClick={() => props.onPage("library")} />
        </span>
      </header>

      {tracks.length > 0 && page !== "library" && page !== "coaching" && <div className="lapbar">
        <label className="field">
          <span className="legend-key"><span className="swatch you" />Lap</span>
          <select value={lapId ?? ""} onChange={(e) => onLap(e.target.value)}>
            {own.map((l) => (
              <option key={l.lap_id} value={l.lap_id}>
                {lapTime(l.lap_time)} · lap {l.seq} · {sessionDate(l.session_id)}
                {l.valid ? "" : " · invalid"}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="legend-key"><span className="swatch ghost dashed" />vs ghost</span>
          <select value={refId ?? ""} onChange={(e) => onRef(e.target.value)}>
            {(g61.length > 0 || otherRefs.length > 0) && (
              <optgroup label="Garage61">
                {g61.map((l) => (
                  <option key={l.garage61_id} value={l.lap_id ?? `g61:${l.garage61_id}`}>
                    {lapTime(l.lap_time)} · {l.driver ?? l.garage61_id}{l.date ? ` · ${l.date}` : ""}
                    {l.lap_id ? "" : " · import"}
                  </option>
                ))}
                {otherRefs.map((r) => (
                  <option key={r.lap_id} value={r.lap_id}>
                    {lapTime(r.lap_time)} · {r.driver ?? r.lap_id}{r.date ? ` · ${r.date}` : ""}
                  </option>
                ))}
              </optgroup>
            )}
            {garage61 && !garage61.available && (
              <option disabled value="">
                {garage61.reason?.startsWith("No Garage61 token") ? "Garage61: add a token to see teammates' laps" : "Garage61: unavailable"}
              </option>
            )}
            <optgroup label="Your laps">
              {own
                .filter((l) => l.lap_id !== lapId)
                .map((l) => (
                  <option key={l.lap_id} value={l.lap_id}>
                    {lapTime(l.lap_time)} · lap {l.seq} · {sessionDate(l.session_id)}
                  </option>
                ))}
            </optgroup>
          </select>
        </label>
        {importing && <span className="muted">Importing {importing}'s lap from Garage61…</span>}
        {delta != null && (
          <div className={`delta-pill ${delta > 0 ? "slower" : "faster"}`}>
            <span className="mono">{signed(delta)} s</span>
            <span>{delta > 0 ? "slower than" : "faster than"} {review?.ref.driver ?? "ghost"}</span>
          </div>
        )}
        {review && <GhostButton key={review.ref.lap_id} ghost={review.ref} system={system} />}
      </div>}
    </>
  );
}
