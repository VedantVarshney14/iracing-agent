import { lapTime, sessionDate, signed } from "../format";
import type { LapsResponse, Review, TrackRow } from "../types";

interface Props {
  tracks: TrackRow[];
  group: { track: string; car: string } | null;
  laps: LapsResponse | null;
  lapId: string | null;
  refId: string | null;
  review: Review | null;
  onGroup: (track: string, car: string) => void;
  onLap: (id: string) => void;
  onRef: (id: string) => void;
}

export function TopBar({ tracks, group, laps, lapId, refId, review, onGroup, onLap, onRef }: Props) {
  const own = laps?.laps.filter((l) => l.lap_time != null) ?? [];
  const refs = laps?.references ?? [];
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
      </header>

      <div className="lapbar">
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
            {refs.length > 0 && (
              <optgroup label="Garage61">
                {refs.map((r) => (
                  <option key={r.lap_id} value={r.lap_id}>
                    {lapTime(r.lap_time)} · {r.driver ?? r.lap_id}{r.date ? ` · ${r.date}` : ""}
                  </option>
                ))}
              </optgroup>
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
        {delta != null && (
          <div className={`delta-pill ${delta > 0 ? "slower" : "faster"}`}>
            <span className="mono">{signed(delta)} s</span>
            <span>{delta > 0 ? "slower than" : "faster than"} {review?.ref.driver ?? "ghost"}</span>
          </div>
        )}
      </div>
    </>
  );
}
