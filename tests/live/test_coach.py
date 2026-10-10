import dataclasses

import pytest
from click.testing import CliRunner

from iagent.cli import cli
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import Cue, build_plan, load_plan, merge_close, save_plan
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.testing.synthetic import DEFAULT_TRACK, LapKind, SyntheticSource
from iagent.workspace import Workspace

TRACK, CAR = "synthetic", "synthcar"


@pytest.fixture
def ws(tmp_path):
    store = ParquetLapStore(tmp_path)
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    w = Workspace(tmp_path)
    yield w
    w.close()


def test_cue_plan_follows_the_reference_brake_points(ws):
    plan = build_plan(ws, TRACK, CAR)
    assert [c.corners for c in plan.cues] == [[1], [2], [3], [4]]
    for cue, truth in zip(plan.cues, DEFAULT_TRACK.corners):
        assert abs(cue.target_m - truth.brake_m) < 15  # jittered by a few metres per lap
        assert "brake" in cue.text and ("left" in cue.text or "right" in cue.text)
    assert plan.ref_lap_time == min(r.lap_time for r in ws.store().list() if r.valid)


def test_rewritten_cues_survive_a_rebuild(ws):
    plan = build_plan(ws, TRACK, CAR)
    plan.cues[0].text, plan.cues[0].source = "Hairpin. Big stop.", "driver"
    save_plan(ws.root, plan)
    again = build_plan(ws, TRACK, CAR)
    assert again.cues[0].text == "Hairpin. Big stop." and again.cues[1].source == "template"


def test_close_corners_share_a_cue(ws):
    cmap = ws.corner_map(TRACK)
    c1, c2, c3 = (dataclasses.replace(cmap.corners[0], id=i) for i in (1, 2, 3))
    c1.exit_m, c2.exit_m = 650.0, 760.0
    cmap = dataclasses.replace(cmap, corners=[c1, c2, c3])
    cues = [Cue(1, [1], 500, "One, right."), Cue(2, [2], 700, "Two."), Cue(3, [3], 800, "Three.")]
    merged = merge_close(cmap, cues)
    assert [c.corners for c in merged] == [[1, 2], [3]]  # two corners per cue at most
    assert merged[0].text == "One, right. Then right."


class Where(CapturedVoice):
    """Remembers where the car was, and how many laps were done, when each line started."""

    def __init__(self):
        super().__init__()
        self.at: list[tuple[str, float, float, int]] = []  # text, LapDist, Speed, laps done
        self.frame = None
        self.coach = None

    def play(self, utterance, now):
        super().play(utterance, now)
        self.at.append((utterance.text, self.frame.get("LapDist"), self.frame.get("Speed"), self.coach._laps_done))


def drive(ws, source, settings=None):
    plan = build_plan(ws, TRACK, CAR)
    voice = Where()
    coach = LiveCoach(source.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(voice), settings)
    voice.coach = coach
    last = None
    for f in source.frames():
        voice.frame = f
        coach.push(f)
        last = f
    coach.finish(last.session_time)
    return coach, voice


def test_cues_finish_before_the_brake_point(ws):
    coach, voice = drive(ws, SyntheticSource(n_laps=3, seed=7, start_m=2900.0))
    approach = [(s, at) for s, at in zip(voice.spoken, voice.at) if s.kind == "approach"]
    assert sorted(s.corner for s, _ in approach) == [1, 1, 2, 2, 3, 3, 4, 4]  # both learning laps, every corner
    for spoken, (text, d, v, _) in approach:
        cue = coach.plan.cue_for(spoken.corner)
        to_target = (cue.target_m - d) % coach.length
        assert to_target / v >= len(text) / 14.0 + 0.25, text  # it can be said before the brake point
    assert coach.arbiter.dropped == [] or all(u.kind != "approach" for u in coach.arbiter.dropped)


def test_after_the_learning_laps_only_struggling_corners_are_cued(ws):
    coach, voice = drive(ws, SyntheticSource(n_laps=6, seed=2, start_m=2900.0), Settings(learning_laps=1))
    cues = [(laps, s.corner) for s, (_, _, _, laps) in zip(voice.spoken, voice.at) if s.kind == "approach"]
    first = [c for laps, c in cues if laps == 0]
    later = [c for laps, c in cues if laps >= 1]
    assert sorted(set(first)) == [1, 2, 3, 4]
    struggled = {r.corner for lap in coach.results for r in lap if r.struggling or r.hint}
    assert set(later) <= struggled  # only corners that went badly (or have a hint) get cued again
    assert len(later) < 4 * 4


def test_nothing_is_said_on_pit_road(ws):
    _, voice = drive(ws, SyntheticSource(n_laps=2, seed=3, kinds=[LapKind.OUT_LAP, LapKind.PIT_IN]))
    assert all(s.kind != "feedback" for s in voice.spoken)


def test_cues_cli(ws, tmp_path):
    runner = CliRunner()
    args = ["--workspace", str(tmp_path), "cues"]
    built = runner.invoke(cli, [*args, "build", "--track", TRACK, "--car", CAR])
    assert built.exit_code == 0, built.output and "T1" in built.output
    out = runner.invoke(cli, [*args, "set", "--track", TRACK, "--car", CAR, "1", "Big stop, second gear."])
    assert out.exit_code == 0, out.output
    assert load_plan(tmp_path, TRACK, CAR).cue_for(1).text == "Big stop, second gear."
    shown = runner.invoke(cli, [*args, "show", "--track", TRACK, "--car", CAR, "--json"])
    assert '"source": "coach"' in shown.output


def test_a_new_track_is_mapped_from_reference_laps(tmp_path):
    refs = ParquetLapStore(tmp_path / "reference")
    record(SyntheticSource(n_laps=3, seed=4), refs, "garage61")
    refs.close()
    w = Workspace(tmp_path)  # no laps of the driver's own
    try:
        plan = build_plan(w, TRACK, CAR)
        assert len(plan.cues) == 4 and plan.ref_lap_id.startswith("synthetic")
    finally:
        w.close()


def test_advice_names_the_cause_and_hints_for_next_lap(ws):
    plan = build_plan(ws, TRACK, CAR)
    coach = LiveCoach(SyntheticSource(n_laps=1).session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id),
                      Arbiter(CapturedVoice()))
    ref = {"brake_m": 480, "min_speed_kph": 100, "full_throttle_m": 620, "min_throttle": 0.0}
    early = coach._advice("T1", {**ref, "brake_m": 455}, ref, 0.2)
    assert early == ("T1: braked 25 metres early. Brake later.", "Brake later than last lap.")
    assert coach._advice("T1", {**ref, "min_speed_kph": 92}, ref, 0.2)[0].startswith("T1: 8 kilometres an hour slower")
    assert coach._advice("T1", {**ref, "off_track_m": 12}, ref, 0.0)[1] == "Tidy entry, you ran wide last lap."
    assert coach._advice("T1", {**ref, "brake_m": 455}, ref, 0.02) == (None, None)  # it cost nothing
