import { useEffect, useState } from "react";
import { api } from "./api";
import { CornerTable } from "./components/CornerTable";
import { Telemetry } from "./components/Telemetry";
import { TopBar } from "./components/TopBar";
import { TrackMap } from "./components/TrackMap";
import type { LapsResponse, Review, TrackRow } from "./types";

type Group = { track: string; car: string };

const params = new URLSearchParams(window.location.search);
// The URL's lap, ghost, corner and map view apply to the first load only.
const urlState: { lap: string | null; ref: string | null; corner: number | null } = {
  lap: params.get("lap"),
  ref: params.get("ref"),
  corner: params.get("corner") ? Number(params.get("corner")) : null,
};

export function App() {
  const [tracks, setTracks] = useState<TrackRow[]>([]);
  const [group, setGroup] = useState<Group | null>(null);
  const [laps, setLaps] = useState<LapsResponse | null>(null);
  const [lapId, setLapId] = useState<string | null>(null);
  const [refId, setRefId] = useState<string | null>(null); // null: the server's default ghost
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useState<number[]>([]);
  const [primary, setPrimary] = useState<number | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [mapMode, setMapMode] = useState<"lap" | "corner">(params.get("view") === "corner" ? "corner" : "lap");

  useEffect(() => {
    let stale = false;
    api
      .tracks()
      .then((rows) => {
        if (stale) return;
        setTracks(rows);
        const fromUrl = rows.find((r) => r.track === params.get("track") && r.car === params.get("car"));
        const first = fromUrl ?? rows[0];
        if (first) setGroup({ track: first.track, car: first.car });
        else setError("No laps yet. Ingest a recording with `iagent ingest <file.ibt>`.");
      })
      .catch((e: Error) => !stale && setError(e.message));
    return () => {
      stale = true;
    };
  }, []);

  useEffect(() => {
    if (!group) return;
    let stale = false;
    api
      .laps(group.track, group.car)
      .then((data) => {
        if (stale) return;
        setLaps(data);
        const urlLap = urlState.lap;
        const lap = urlLap && data.laps.some((l) => l.lap_id === urlLap) ? urlLap : data.default_lap;
        setLapId(lap);
        setRefId(lap === urlLap ? urlState.ref : null);
        urlState.lap = urlState.ref = null;
      })
      .catch((e: Error) => !stale && setError(e.message));
    return () => {
      stale = true;
    };
  }, [group]);

  useEffect(() => {
    if (!lapId) return;
    let stale = false;
    setLoading(true);
    api
      .review(lapId, refId ?? undefined)
      .then((data) => {
        if (stale) return;
        setReview(data);
        setError(null);
        // Start on the corner from the URL, else the one that cost the most time.
        const worst = [...data.corners].sort((a, b) => (b.delta_s ?? 0) - (a.delta_s ?? 0))[0];
        const start = data.corners.find((c) => c.id === urlState.corner) ?? worst;
        urlState.corner = null;
        setSelected(start ? [start.id] : []);
        setPrimary(start ? start.id : null);
        setZoom(null);
      })
      .catch((e: Error) => !stale && setError(e.message))
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [lapId, refId]);

  useEffect(() => {
    if (!group || !review) return;
    const q = new URLSearchParams({ track: group.track, car: group.car, lap: review.lap.lap_id, ref: review.ref.lap_id });
    if (primary != null) q.set("corner", String(primary));
    if (mapMode === "corner") q.set("view", "corner");
    window.history.replaceState(null, "", `?${q}`);
  }, [group, review, primary, mapMode]);

  const pick = (id: number, add = false) => {
    if (add) {
      setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
      setPrimary(id);
    } else {
      setSelected([id]);
      setPrimary(id);
    }
  };

  const length = review?.track.length_m ?? 1;
  const range: [number, number] = zoom ?? [0, length];
  const primaryCorner = review?.corners.find((c) => c.id === primary);
  const mapWindow: [number, number] | null =
    zoom ?? (primaryCorner ? [primaryCorner.entry_m - 80, primaryCorner.exit_m + 80] : null);

  return (
    <div className="app">
      <TopBar
        tracks={tracks}
        group={group}
        laps={laps}
        lapId={lapId}
        refId={review?.ref.lap_id ?? refId}
        review={review}
        onGroup={(track, car) => {
          setReview(null);
          setGroup({ track, car });
        }}
        onLap={(id) => {
          setLapId(id);
          if (id === (review?.ref.lap_id ?? refId)) setRefId(null);
        }}
        onRef={setRefId}
      />
      {error && <div className="error" role="alert">{error}</div>}
      {review ? (
        <main className={`layout${loading ? " loading" : ""}`}>
          <div className="upper">
            <TrackMap
              review={review}
              mode={mapMode}
              window={mapWindow}
              selected={selected}
              primary={primary}
              cursor={cursor}
              onMode={setMapMode}
              onPickCorner={(id) => pick(id)}
            />
            <CornerTable
              corners={review.corners}
              totalDelta={review.total_delta_s}
              selected={selected}
              primary={primary}
              onPick={pick}
            />
          </div>
          <Telemetry
            review={review}
            range={range}
            cursor={cursor}
            selected={selected}
            primary={primary}
            onCursor={setCursor}
            onZoom={(r) => {
              setZoom(r ? [Math.max(0, r[0]), Math.min(length, r[1])] : null);
              if (r) setMapMode("corner");
            }}
            onPickCorner={(id) => pick(id)}
          />
        </main>
      ) : (
        !error && <p className="muted placeholder">Loading…</p>
      )}
    </div>
  );
}
