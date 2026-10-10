"""How the coach talks: short when the driver is busy, fuller when there's time, and around CrewChief."""

import json

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live import crewchief
from iagent.live.coach import LiveCoach, Settings, _longer
from iagent.live.cues import build_plan, load_plan, plan_path, save_plan
from iagent.live.rules import Rule, RuleEngine
from iagent.live.run import own_best, plan_for
from iagent.live.speech import FEEDBACK, Arbiter, CapturedVoice, Utterance
from iagent.testing.synthetic import LapKind, SyntheticSource
from iagent.workspace import Workspace

TRACK, CAR = "synthetic", "synthcar"


@pytest.fixture
def root(tmp_path):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"


def drive(root, kinds, settings, rules=None, seed=11):
    """Synthetic laps of KINDS through a coach judged against the stored best."""
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        src = SyntheticSource(n_laps=len(kinds), kinds=kinds, seed=seed, start_m=2900.0)
        coach = LiveCoach(src.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(CapturedVoice()),
                          settings, own_best=own_best(ws, TRACK, CAR), rules=RuleEngine(rules or []))
    finally:
        ws.close()
    laps, modes = [], []
    coach.on_lap = laps.append
    coach.on_mode = lambda mode, now, d: modes.append((mode, now))
    last = None
    for f in src.frames():
        coach.push(f)
        last = f
    coach.finish(last.session_time)
    return coach, laps, modes


def test_a_line_has_a_longer_version_for_when_the_driver_has_time():
    voice = CapturedVoice()
    arb = Arbiter(voice)
    long = "Turn 5: you're braking about 20 metres early. Move it a few metres a lap."
    arb.say(Utterance("Turn 5: braked 20 metres early.", FEEDBACK, "feedback", 0.0, 30.0, longer=long))
    arb.tick(0.0, free_for_s=20.0)  # pushing: the short one, even with room
    arb.say(Utterance("Turn 5: braked 20 metres early.", FEEDBACK, "feedback", 10.0, 30.0, longer=long))
    arb.tick(10.0, free_for_s=2.0, long_ok=True)  # cruising, but no room before the next cue
    arb.tick(15.0, free_for_s=30.0, long_ok=True)
    assert [s.text for s in voice.spoken] == ["Turn 5: braked 20 metres early.", long]


def test_cues_get_shorter_once_the_driver_has_heard_them(root):
    coach, _, _ = drive(root, [LapKind.CLEAN] * 5, Settings(learning_laps=2, focus=False, summary=False))
    by_corner: dict[int, list[str]] = {}
    for s in coach.arbiter.voice.spoken:
        if s.kind == "approach":
            by_corner.setdefault(s.corner, []).append(s.text)
    cue = coach.plan.cue_for(1)
    texts = by_corner[1]
    assert texts[:2] == [cue.text, cue.text]  # the learning laps: in full
    assert all(t.startswith(cue.short) for t in texts[2:])  # after that, the short form (plus any hint)
    assert len(cue.short) < len(cue.text)


def test_a_cool_down_lap_gets_a_debrief_not_silence(root):
    kinds = [LapKind.CLEAN, LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW, LapKind.CLEAN]
    coach, laps, modes = drive(root, kinds, Settings(learning_laps=1, debrief_after_s=10.0))
    slow_from = next(t for mode, t in modes if mode == "tranquille")
    debrief = [s for s in coach.arbiter.voice.spoken if s.kind == "debrief"]
    assert debrief and debrief[0].text.startswith("While you cool them down")
    assert all(s.at_s >= slow_from + 10.0 for s in debrief)  # not straight after slowing (a moment)
    approach = [s for s in coach.arbiter.voice.spoken if s.kind == "approach" and slow_from + 15 < s.at_s < laps[-1]["at"] - 60]
    assert approach == []  # still no corner cues on the cool-down


def test_the_longer_advice_says_why_and_how():
    text = _longer("Pouhon", "early_brake", 22.0, {"min_speed_kph": 180}, {"min_speed_kph": 172})
    assert text.startswith("Pouhon: you're braking about 20 metres before the reference and you're slower at the apex")
    assert "a few metres a lap" in text
    assert _longer("T1", "slow_apex", 7.6, {}, {}, named=False).startswith("You're about 8 kilometres an hour slower")
    assert _longer("T1", None, None, {}, {}) is None


def test_with_crewchief_lap_times_are_left_to_it(root):
    coach, _, _ = drive(root, [LapKind.CLEAN] * 4, Settings(learning_laps=1, crewchief=True))
    summaries = [s for s in coach.arbiter.voice.spoken if s.kind == "summary"]
    assert all("down" not in s.text and "up." not in s.text for s in summaries)
    plain, _, _ = drive(root, [LapKind.CLEAN] * 4, Settings(learning_laps=1, crewchief=False))
    assert any(" down." in s.text or " up." in s.text for s in plain.arbiter.voice.spoken if s.kind == "summary")


def test_crewchief_quiet_window_after_the_line(root):
    coach, laps, _ = drive(root, [LapKind.CLEAN] * 4, Settings(learning_laps=1, crewchief=True, crewchief_quiet_s=6.0))
    line_times = [lap["at"] for lap in laps]
    for s in coach.arbiter.voice.spoken:
        if s.kind != "approach" and s.at_s < line_times[-1]:  # (after the last line: the replay's end flush)
            assert not any(0 <= s.at_s - t < 6.0 for t in line_times), (s.text, s.at_s)


def test_rules_that_duplicate_crewchief_are_flagged():
    lap_time = Rule.from_dict({"id": "a", "when": {"lap": "complete"}, "action": {"say": "{say_time(lap_time)}."}})
    pb = Rule.from_dict({"id": "b", "when": {"lap": "complete"}, "if": "new_best", "action": {"say": "New best."}})
    coaching = Rule.from_dict({"id": "c", "when": {"lap": "complete"}, "if": "worst_delta_s > 0.3",
                               "action": {"say": "{worst_name} again."}})
    assert "lap times" in crewchief.overlaps(lap_time) and "personal bests" in crewchief.overlaps(pb)
    assert crewchief.overlaps(coaching) is None
    assert crewchief.resolve("on") and not crewchief.resolve("off") and crewchief.resolve(True)


def test_a_rule_can_say_more_when_the_driver_is_cruising(root):
    rule = Rule.from_dict({"id": "cool", "when": {"pace": "tranquille"}, "action": {
        "say": "Cool-down. Tyres.", "long": "Cool-down lap. Keep some heat in the tyres, gentle weaving on the straights."}})
    assert Rule.from_dict(rule.to_dict()).actions[0].longer.text.startswith("Cool-down lap.")
    kinds = [LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW, LapKind.CLEAN]
    coach, _, _ = drive(root, kinds, Settings(learning_laps=1, debrief=False, debrief_after_s=5.0), [rule])
    said = [s.text for s in coach.arbiter.voice.spoken if s.kind == "rule"]
    assert said and said[0].startswith("Cool-down lap. Keep some heat")


def test_old_cue_plans_are_rebuilt_with_short_forms_keeping_rewritten_cues(root):
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        plan.cues[0].text, plan.cues[0].source = "Hairpin. Big stop.", "driver"
        save_plan(root, plan)
        raw = json.loads(plan_path(root, TRACK, CAR).read_text())
        raw.pop("version")
        for c in raw["cues"]:
            c.pop("short")
        plan_path(root, TRACK, CAR).write_text(json.dumps(raw))
        assert load_plan(root, TRACK, CAR).version == 1
        session = SyntheticSource(n_laps=1).session
        again = plan_for(ws, session)
    finally:
        ws.close()
    assert again.version == 2 and again.cues[0].text == "Hairpin. Big stop." and again.cues[1].short


def test_feedback_notices_repeats_and_progress(root):
    from iagent.live.coach import CornerResult

    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        coach = LiveCoach(SyntheticSource(n_laps=1).session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id),
                          Arbiter(CapturedVoice()))
    finally:
        ws.close()
    c = coach.cmap.get(2)
    early = CornerResult(2, 0.2, "Turn 2: braked 20 metres early. Brake later.", "Brake later than last lap.", True,
                         cause="early_brake", amount=20.0, longer="Turn 2: you're braking about 20 metres...")
    assert coach._feedback_line(c, early)[0] == "Turn 2: braked 20 metres early. Brake later."
    coach._laps_done += 1
    assert coach._feedback_line(c, early)[0] == "Turn 2 again: braked 20 metres early. Brake later."
    coach._laps_done += 1
    fine = CornerResult(2, 0.01, None, None, False)
    text, longer = coach._feedback_line(c, fine)
    assert text == "Turn 2: better." and "keep that" in longer
    coach._laps_done += 1
    assert coach._feedback_line(c, fine) is None  # said once, not every lap after
