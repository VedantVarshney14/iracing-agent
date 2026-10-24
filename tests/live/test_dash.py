import dataclasses
import json

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import build_plan
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.testing.synthetic import DEFAULT_TRACK, SyntheticSource
from iagent.workspace import Workspace


def test_the_dashboard_shows_last_laps_trace_and_the_fix_for_the_coming_corner(
    tmp_path,
):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    ws = Workspace(tmp_path / "ws")
    plan = build_plan(ws, "synthetic", "synthcar")
    # The driver brakes 30 m early for T2 every lap.
    early = dataclasses.replace(
        DEFAULT_TRACK,
        corners=[
            dataclasses.replace(c, brake_m=c.brake_m - 30) if c.name == "T2" else c
            for c in DEFAULT_TRACK.corners
        ],
    )
    source = SyntheticSource(track=early, n_laps=3, seed=5, start_m=2900.0)
    path = tmp_path / "dash.json"
    coach = LiveCoach(
        source.session,
        plan,
        ws.corner_map("synthetic"),
        ws.load(plan.ref_lap_id),
        Arbiter(CapturedVoice()),
        Settings(dash_file=str(path)),
    )
    ref_t2 = next(m for m in plan.ref_metrics if m["corner"] == 2)["brake_m"]
    seen = {}  # (laps done, coming corner) -> the feed approaching it
    for f in source.frames():
        coach.push(f)
        feed = json.loads(path.read_text())
        seen[(coach.state.lap, feed["corner"]["id"])] = feed
    ws.close()

    first, second = seen[(0, 2)], seen[(1, 2)]
    assert (
        first["trace"]["last_brake"] is None
    )  # nothing to show from before the first lap
    assert first["trace"]["target_brake"]
    assert second["corner"]["action"] == "BRAKE"
    assert second["cue"].startswith("BRAKE") and second["cue"].endswith("m LATER")
    t = second["trace"]
    assert len(t["last_brake"]) == len(t["target_brake"])
    # The arrow runs from where the driver braked to the reference brake point, later on.
    lo, to = t["arrow"]
    assert lo < to
    assert abs(second["corner"]["target_m"] - ref_t2) < 1
    # Last lap's brake comes on earlier in the trace than the reference's.
    on = lambda v: next(i for i, p in enumerate(v) if p > 0.1)  # noqa: E731
    assert on(t["last_brake"]) < on(t["target_brake"])
    # Approaching T2, only T1 has been driven this lap.
    assert [s["state"] == "todo" for s in second["strip"]["sectors"]] == [
        False,
        True,
        True,
        True,
    ]
