import json
import sys
import time

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live.coach import CornerResult, LiveCoach, Settings
from iagent.live.components import CueCaller, Focus, Pace
from iagent.live.events import SetFocus
from iagent.live.cues import build_plan
from iagent.live.session import LiveSessions, recordings
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.testing.ibt_writer import write_ibt
from iagent.testing.synthetic import SyntheticSource
from iagent.testing.web import ui_client
from iagent.ui.coach import CoachRuns
from iagent.ui.server import create_app
from iagent.workspace import Workspace
from tests.ui.test_coach import TURN

TRACK, CAR = "synthetic", "synthcar"


@pytest.fixture
def root(tmp_path):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"


@pytest.fixture
def recording(tmp_path):
    src = SyntheticSource(n_laps=4, seed=9, start_m=2900.0)
    (tmp_path / "rec").mkdir()
    return write_ibt(tmp_path / "rec" / "synthcar_synthetic 2026-10-10 10-00-00.ibt", src.session, src.frames())


def coach_for(root, **settings) -> LiveCoach:
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        return LiveCoach(SyntheticSource(n_laps=1).session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id),
                         Arbiter(CapturedVoice()), Settings(**settings))
    finally:
        ws.close()


def lap(coach: LiveCoach, losses: dict[int, float]) -> str | None:
    coach.state.lap += 1
    return coach.pipeline.get(Focus).update([CornerResult(c, d, None, None, d >= 0.08) for c, d in losses.items()])


def test_the_focus_is_the_biggest_loss_and_moves_on_once_sorted(root):
    coach = coach_for(root, learning_laps=2)
    assert lap(coach, {1: 0.3, 2: 0.1, 3: 0.0, 4: 0.0}) is None  # still learning
    news = lap(coach, {1: 0.3, 2: 0.2, 3: 0.0, 4: 0.0})
    assert coach.focus == 1 and news == "Focus now: Turn 1."
    assert lap(coach, {1: 0.05, 2: 0.2}) is None  # one good lap isn't enough
    news = lap(coach, {1: 0.02, 2: 0.25})
    assert news == "Turn 1 sorted. Focus now: Turn 2." and coach.focus == 2
    assert coach.focus_log[0]["done_lap"] == 4 and coach.focus_log[1]["set_lap"] == 4


def test_with_a_focus_only_it_and_big_losses_are_cued(root):
    coach = coach_for(root, learning_laps=0)
    st = coach.state
    st.lap, st.learning, st.focus, st.big_trouble = 2, False, 2, {4}
    wanted = [c.corner for c in coach.plan.cues if coach.wanted(c)]
    assert wanted == [2, 4]
    coach.post(SetFocus(corner=3))
    coach.pipeline.run_inbox()
    assert coach.focus == 3 and coach.focus_log[-1]["manual"] is True
    assert coach.pipeline.get(CueCaller).text(coach.plan.cue_for(3)).startswith("Focus. ")


def test_a_session_replays_and_logs_what_was_said(root, recording):
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice())
    live.start({"source": "replay", "file": str(recording), "speed": 500, "voice": "alba"})
    live.wait(timeout_s=60)
    events = live.events()
    states = [e["state"] for e in events if e["type"] == "status"]
    assert states[:2] == ["starting", "running"] and states[-1] == "ended"
    said = [e for e in events if e["type"] == "line" and e["status"] == "said"]
    assert {e["kind"] for e in said} >= {"approach"} and len([e for e in events if e["type"] == "lap"]) >= 2
    log = root / "sessions" / "live" / f"{live.session_id}.jsonl"
    assert [json.loads(line)["seq"] for line in log.read_text().splitlines()] == list(range(len(events)))
    assert live.status()["state"] == "idle"


def test_a_running_session_can_be_stopped(root, recording):
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice())
    live.start({"source": "replay", "file": str(recording), "speed": 1, "voice": "silent"})
    for _ in range(100):
        if live.status()["state"] == "running":
            break
        time.sleep(0.05)
    assert live.status()["track"]["key"] == TRACK
    assert live.stop()["state"] == "idle"
    assert live.events()[-1]["state"] in ("stopped", "ended")


def test_live_api_starts_a_replay_and_answers_a_question(root, recording, tmp_path):
    if sys.platform == "win32":
        pytest.skip("shebang scripts")
    claude = tmp_path / "claude"
    claude.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nsys.stdout.write({''.join(TURN)!r})\n")
    claude.chmod(0o755)
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice())
    web = ui_client(create_app(root, static_dir=tmp_path / "no-build", live=live, coach=CoachRuns(root, claude=str(claude))))

    assert web.post("/api/live/start", json={"source": "replay", "file": str(tmp_path / "nope.ibt")}).status_code == 409
    assert web.post("/api/live/start", json={"source": "replay", "file": str(recording), "speed": 2}).status_code == 200
    assert web.post("/api/live/ask", json={"text": "Where am I slow?"}).json() == {"ok": True}
    for _ in range(100):
        events = web.get("/api/live/events").json()["events"]
        if any(e["type"] == "answer" for e in events):
            break
        time.sleep(0.05)
    kinds = [e["type"] for e in events]
    assert "driver" in kinds and [e["text"] for e in events if e["type"] == "answer"] == ["Pouhon costs 0.48 s."]
    for _ in range(200):  # the answer can come back before the coach has started
        if live.status()["state"] == "running":
            break
        time.sleep(0.05)
    assert web.post("/api/live/focus", json={"corner": 3}).json() == {"ok": True}
    assert web.post("/api/live/stop").json()["state"] == "idle"


def test_recordings_lists_ibt_files_newest_first(tmp_path, recording):
    rows = recordings([recording.parent, tmp_path / "missing"])
    assert [r["name"] for r in rows] == [recording.name]


def drive_kinds(root, kinds, seed=11):
    """Replay synthetic laps of the given kinds through a coach judged against the stored best."""
    from iagent.live.run import own_best

    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        src = SyntheticSource(n_laps=len(kinds), kinds=kinds, seed=seed, start_m=2900.0)
        coach = LiveCoach(src.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(CapturedVoice()),
                          Settings(learning_laps=0), own_best=own_best(ws, TRACK, CAR))
    finally:
        ws.close()
    laps = []
    coach.on("lap", lambda e: laps.append({**e.public(), "at": e.at}))
    for f in src.frames():
        coach.push(f)
    return coach, laps


def test_slow_laps_are_tranquille_and_get_no_cues_or_numbers(root):
    from iagent.testing.synthetic import LapKind

    coach, laps = drive_kinds(root, [LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW, LapKind.CLEAN])
    assert [lap["pace"] for lap in laps] == ["pushing", "tranquille", "pushing"]
    slow = laps[1]
    assert slow["pushing_share"] < 0.6 and slow["corners"] == []  # nothing assessed while cruising
    spoken = coach.arbiter.voice.spoken
    cruising = [s for s in spoken if s.kind == "approach" and laps[0]["at"] + 15 < s.at_s < slow["at"]]
    assert cruising == []  # no corner cues on the slow lap (after it was spotted)
    assert not any(s.kind == "summary" and laps[0]["at"] < s.at_s < slow["at"] + 5 and "down" in s.text for s in spoken)


def test_pace_is_judged_against_your_own_best_not_the_reference(root):
    from iagent.testing.synthetic import LapKind

    coach, laps = drive_kinds(root, [LapKind.CLEAN] * 3)
    pace = coach.pipeline.get(Pace)
    assert pace._own and all(lap["pace"] == "pushing" for lap in laps)
    assert 0.9 < pace.ratio(coach.state.lap_dist) < 1.1  # where the car is now
