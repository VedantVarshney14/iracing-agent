import sys
import time

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live import report
from iagent.live.coach import CornerResult, LiveCoach, Settings
from iagent.live.components import CueCaller, Focus
from iagent.live.cues import build_plan, load_next_plan, save_next_plan, use_next_plan
from iagent.live.session import LiveSessions
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.telemetry.ibt import IbtSource
from iagent.testing.ibt_writer import write_ibt
from iagent.testing.synthetic import SyntheticSource
from iagent.testing.web import ui_client
from iagent.ui.coach import CoachRuns
from iagent.ui.server import create_app
from iagent.workspace import Workspace
from tests.ui.test_coach import TURN

TRACK, CAR = "synthetic", "synthcar"


@pytest.fixture
def coached(tmp_path):
    """A workspace whose laps include the recording, and one coached replay of it."""
    root = tmp_path / "ws"
    src = SyntheticSource(n_laps=5, seed=9, start_m=2900.0)
    (tmp_path / "rec").mkdir()
    rec = write_ibt(tmp_path / "rec" / "synthcar_synthetic 2026-10-10 10-00-00.ibt", src.session, src.frames())
    store = ParquetLapStore(root)
    record(IbtSource(rec), store, rec.name)
    store.close()
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice())
    live.start({"source": "replay", "file": str(rec), "speed": 1000, "voice": "alba", "learning_laps": 1})
    live.wait(timeout_s=60)
    return root, live.session_id


def test_a_session_report_reads_back_the_log(coached):
    root, sid = coached
    ws = Workspace(root)
    try:
        out = report.session_report(ws, sid)
    finally:
        ws.close()
    s = out["summary"]
    assert s["laps"] == len(out["laps"]) >= 3 and s["pushing"] + s["moments"] + s["tranquille"] == s["laps"]
    assert s["best_lap"] == min(lap["lap_time"] for lap in out["laps"] if lap["pace"] != "tranquille")
    assert all(lap["lap_id"] and lap["lap_id"].startswith("20261010-100000-L") for lap in out["laps"])  # linked
    lines = sum(1 for g in out["commentary"] for e in g["events"] if e["type"] == "line")
    assert lines == s["said"] + s["cut"] + s["held_back"]
    assert {g["lap"] for g in out["commentary"]} >= {"1", "2"}
    assert out["track"]["corners"] and out["map"] is not None and out["context"][0].startswith("coached session")
    assert [r["id"] for r in report.list_sessions(root)] == [sid]


def test_verdicts():
    assert report._verdict(0.5, [0.3, 0.2]) == "working"
    assert report._verdict(0.5, [0.05]) == "working"
    assert report._verdict(0.5, [0.5, 0.6]) == "not yet"
    assert report._verdict(0.5, [0.3, 0.6]) == "mixed"
    assert report._verdict(0.5, []) == "no laps since"


def test_a_planned_focus_lasts_a_set_number_of_sessions(tmp_path):
    save_next_plan(tmp_path, TRACK, CAR, 3, sessions=2)
    assert use_next_plan(tmp_path, TRACK, CAR)["sessions_left"] == 1
    assert use_next_plan(tmp_path, TRACK, CAR)["focus"] == 3
    assert use_next_plan(tmp_path, TRACK, CAR) is None and load_next_plan(tmp_path, TRACK, CAR) is None


def carried_coach(root, corner):
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        return LiveCoach(SyntheticSource(n_laps=1).session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id),
                         Arbiter(CapturedVoice()), Settings(learning_laps=0), carried_focus=corner)
    finally:
        ws.close()


def pushing_lap(coach, losses):
    coach.state.lap += 1
    return coach.pipeline.get(Focus).update([CornerResult(c, d, None, None, d >= 0.08) for c, d in losses.items()])


def test_a_carried_focus_is_dropped_when_it_is_fine_now(coached):
    root, _ = coached
    coach = carried_coach(root, 2)
    assert coach.focus == 2 and coach.pipeline.get(CueCaller).text(coach.plan.cue_for(2)).startswith("Focus. ")
    assert pushing_lap(coach, {1: 0.0, 2: 0.02, 3: 0.0, 4: 0.0}) is None  # one lap: not judged yet
    assert pushing_lap(coach, {1: 0.0, 2: 0.03, 3: 0.0, 4: 0.0}) == "Turn 2 is fine now."
    assert coach.focus is None


def test_a_carried_focus_gives_way_to_a_bigger_loss(coached):
    root, _ = coached
    coach = carried_coach(root, 2)
    pushing_lap(coach, {1: 0.6, 2: 0.2, 3: 0.0, 4: 0.0})
    news = pushing_lap(coach, {1: 0.5, 2: 0.2, 3: 0.0, 4: 0.0})
    assert coach.focus == 1 and news == "Focus now: Turn 1."
    assert coach.focus_log[0]["replaced"] is True


def test_coaching_api(coached, tmp_path):
    if sys.platform == "win32":
        pytest.skip("shebang scripts")
    root, sid = coached
    claude = tmp_path / "claude"
    claude.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nsys.stdout.write({''.join(TURN)!r})\n")
    claude.chmod(0o755)
    web = ui_client(create_app(root, static_dir=tmp_path / "no-build", coach=CoachRuns(root, claude=str(claude))))

    assert web.get("/api/coaching/sessions").json()[0]["id"] == sid
    out = web.get("/api/coaching/session", params={"id": sid}).json()
    assert out["id"] == sid and out["cues"] and out["debrief"] is None and out["next_plan"] is None
    assert web.get("/api/coaching/session", params={"id": "../../index"}).status_code == 404

    assert web.post("/api/coaching/debrief", json={"id": sid}).json() == {"running": True}
    for _ in range(100):
        out = web.get("/api/coaching/session", params={"id": sid}).json()
        if out["debrief"]:
            break
        time.sleep(0.05)
    assert out["debrief"].strip() == "Pouhon costs 0.48 s." and not out["debrief_running"]

    saved = web.post("/api/coaching/plan", json={"track": TRACK, "car": CAR, "focus": 3, "cue_text": "Three: big stop.",
                                                  "from_session": sid}).json()
    assert saved["focus"] == 3 and saved["sessions_left"] == 2
    out = web.get("/api/coaching/session", params={"id": sid}).json()
    assert out["next_plan"]["focus"] == 3 and any(c["text"] == "Three: big stop." for c in out["cues"])
    assert web.post("/api/coaching/plan", json={"track": TRACK, "car": CAR, "focus": 99}).status_code == 404
