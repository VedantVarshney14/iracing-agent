import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { UiAction } from "./chat";
import { Chat } from "./components/Chat";
import { CornerTable } from "./components/CornerTable";
import { Telemetry } from "./components/Telemetry";
import { TopBar } from "./components/TopBar";
import { TrackMap } from "./components/TrackMap";
import type { Garage61Lap, Garage61Laps, LapsResponse, Review, TrackRow } from "./types";

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
  const [prefill, setPrefill] = useState<{ text: string; nonce: number } | null>(null);
  // A coach action that switched laps: applied once the new review has loaded.
  const pendingAction = useRef<UiAction | null>(null);
  const [garage61, setGarage61] = useState<Garage61Laps | null>(null);
  const [importing, setImporting] = useState<string | null>(null);
  // Once the driver (or the URL) picks a ghost, the Garage61 default no longer overrides it.
  const ghostChosen = useRef(params.has("ref"));

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
    setGarage61(null);
    api
      .garage61Laps(group.track, group.car)
      .then((data) => !stale && setGarage61(data))
      .catch(() => !stale && setGarage61({ available: false, reason: "Garage61 couldn't be reached.", laps: [] }));
    return () => {
      stale = true;
    };
  }, [group]);

  // Default ghost: the fastest Garage61 lap (a teammate's), imported on first use.
  useEffect(() => {
    if (!review || !garage61?.available || ghostChosen.current) return;
    const fastest = [...garage61.laps].filter((l) => l.lap_time != null).sort((a, b) => a.lap_time! - b.lap_time!)[0];
    ghostChosen.current = true;
    if (!fastest || fastest.lap_id === review.ref.lap_id) return;
    if (fastest.lap_id) setRefId(fastest.lap_id);
    else importGhost(fastest);
  }, [review, garage61]);

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
        setZoom(null);
        if (pendingAction.current) {
          applyAction(pendingAction.current);
          pendingAction.current = null;
          return;
        }
        // Start on the corner from the URL, else the one that cost the most time.
        const worst = [...data.corners].sort((a, b) => (b.delta_s ?? 0) - (a.delta_s ?? 0))[0];
        const start = data.corners.find((c) => c.id === urlState.corner) ?? worst;
        urlState.corner = null;
        setSelected(start ? [start.id] : []);
        setPrimary(start ? start.id : null);
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

  /** Download a Garage61 lap as a reference lap, then compare against it. */
  async function importGhost(lap: Garage61Lap) {
    if (!group) return;
    setImporting(lap.driver ?? lap.garage61_id);
    try {
      const { lap_id } = await api.garage61Import(lap.garage61_id);
      const [lapsNow, g61Now] = await Promise.all([api.laps(group.track, group.car), api.garage61Laps(group.track, group.car)]);
      setLaps(lapsNow);
      setGarage61(g61Now);
      setRefId(lap_id);
    } catch (e) {
      setError(`Couldn't import ${lap.driver ?? "the"} lap from Garage61: ${(e as Error).message}`);
    } finally {
      setImporting(null);
    }
  }

  const onRef = (value: string) => {
    ghostChosen.current = true;
    if (value.startsWith("g61:")) {
      const lap = garage61?.laps.find((l) => l.garage61_id === value.slice(4));
      if (lap) importGhost(lap);
    } else {
      setRefId(value);
    }
  };

  /** Show what the coach pointed at: corners, a zoomed range, the map view. */
  function applyAction(a: UiAction) {
    if (a.corners?.length) {
      setSelected(a.corners);
      setPrimary(a.corners[0]);
    }
    if (a.range) {
      setZoom(a.range);
      setMapMode("corner");
    }
    if (a.view) setMapMode(a.view);
  }

  const onUiAction = (a: UiAction) => {
    const switchLap = a.lap && a.lap !== review?.lap.lap_id;
    const switchRef = a.ref && a.ref !== review?.ref.lap_id;
    if (a.ref) ghostChosen.current = true;
    if (switchLap || switchRef) {
      pendingAction.current = a;
      if (switchLap) setLapId(a.lap!);
      if (a.ref) setRefId(a.ref);
    } else {
      applyAction(a);
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
        garage61={garage61}
        importing={importing}
        onGroup={(track, car) => {
          setReview(null);
          ghostChosen.current = false;
          setGroup({ track, car });
        }}
        onLap={(id) => {
          setLapId(id);
          if (id === (review?.ref.lap_id ?? refId)) setRefId(null);
        }}
        onRef={onRef}
      />
      {error && <div className="error" role="alert">{error}</div>}
      {review && group ? (
        <div className="workspace">
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
            onAsk={() => {
              const about = zoom
                ? `between ${Math.round(zoom[0])} and ${Math.round(zoom[1])} m`
                : primaryCorner ? `in ${primaryCorner.label}` : "on this lap";
              setPrefill({ text: `What am I doing differently from the ghost ${about}?`, nonce: Date.now() });
            }}
          />
        </main>
        <Chat
          context={{
            track: group.track,
            car: group.car,
            lap: review.lap.lap_id,
            ref: review.ref.lap_id,
            ref_driver: review.ref.driver,
            corners: selected,
            range: zoom,
          }}
          contextLabel={[
            `Lap ${review.lap.seq} vs ${review.ref.driver ?? `lap ${review.ref.seq}`}`,
            ...(selected.length ? [`${selected.map((c) => `T${c}`).join(", ")} selected`] : []),
            ...(zoom ? [`${Math.round(zoom[0])}–${Math.round(zoom[1])} m`] : []),
          ]}
          suggestions={[
            "Where am I losing the most time to the ghost?",
            ...(primaryCorner ? [`What am I doing differently in ${primaryCorner.label}?`] : []),
            "What one thing should I work on next session?",
          ]}
          prefill={prefill}
          onUiAction={onUiAction}
        />
        </div>
      ) : (
        !error && <p className="muted placeholder">Loading…</p>
      )}
    </div>
  );
}
