import json
import threading

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live import rulebook
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import build_plan
from iagent.live.narrator import Narrator, chunks, clean, debrief_prompt, final_text, wake_prompt
from iagent.live.rules import RuleEngine
from iagent.live.run import own_best
from iagent.live.session import LiveSessions
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.testing.ibt_writer import write_ibt
from iagent.testing.synthetic import LapKind, SyntheticSource
from iagent.workspace import Workspace

TRACK, CAR = "synthetic", "synthcar"
WORDS = "Nice laps. Turn 1 is the one: you're on the brakes a touch early. Try a few metres later next time."


@pytest.fixture
def root(tmp_path):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"


def test_replies_are_cleaned_for_speech_and_split_for_the_radio():
    assert clean('**"Brake later** at `T1`."') == "Brake later at T1."
    assert clean("SILENT") is None and clean("silent.") is None and clean("  ") is None
    pieces = chunks("One. Two is a bit longer than one. " + "Three goes on and on for quite a while, " * 3 + "end.", 60)
    assert pieces[0] == "One. Two is a bit longer than one." and all(p.endswith(".") for p in pieces)
    stream = [json.dumps(e) for e in ({"type": "text_start"}, {"type": "text", "text": "Let me check."},
                                       {"type": "tool", "command": "iagent laps list"}, {"type": "text_start"},
                                       {"type": "text", "text": "Brake "}, {"type": "text", "text": "later."})]
    assert final_text(stream) == "Brake later."


def test_prompts_give_the_facts_and_the_constraints():
    p = debrief_prompt({"topics": [{"name": "Turn 1"}], "crewchief": True, "fallback": ["Turn 1 is where the time is."]})
    assert "cool-down lap" in p and "CrewChief" in p and '"name": "Turn 1"' in p and "- Turn 1 is where" in p
    w = wake_prompt({"rule": "t1", "message": "early again", "values": {"brake_diff_m": -20}}, {"mode": "pushing"})
    assert "under 12 words" in w and "SILENT" in w and "brake_diff_m" in w


def test_one_request_of_a_kind_at_a_time_and_wakes_are_spaced():
    release = threading.Event()
    replies = []
    done = threading.Event()

    def ask(prompt, context):
        release.wait(5)
        return "Okay."
    n = Narrator(ask)
    assert n.request("debrief", "p", {}, lambda r: (replies.append(r), done.set()))
    assert not n.request("debrief", "p", {}, replies.append)  # still running
    release.set()
    done.wait(5)
    assert replies == ["Okay."]
    quick = Narrator(lambda p, c: None)
    assert quick.request("wake", "p", {}, lambda r: None, min_gap_s=30)
    assert not quick.request("wake", "p", {}, lambda r: None, min_gap_s=30)


def drive(root, reply_after_s: float | None, settings: Settings):
    """A session with a cool-down lap; the narrator answers REPLY_AFTER_S after being asked
    (None: never), delivered on the coach's thread like the live session does."""
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        kinds = [LapKind.CLEAN, LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW, LapKind.CLEAN]
        src = SyntheticSource(n_laps=len(kinds), kinds=kinds, seed=11, start_m=2900.0)
        coach = LiveCoach(src.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(CapturedVoice()),
                          settings, own_best=own_best(ws, TRACK, CAR), rules=RuleEngine([]))
    finally:
        ws.close()
    asked, said = [], []
    coach.on_narrate = lambda facts, stretch: asked.append((facts, stretch, None))
    coach.on_debrief = said.append
    pending = []
    for f in src.frames():
        if asked and asked[-1][2] is None:
            facts, stretch, _ = asked[-1]
            asked[-1] = (facts, stretch, f.session_time)
            if reply_after_s is not None:
                pending.append((f.session_time + reply_after_s, stretch))
        for due, stretch in [p for p in pending if p[0] <= f.session_time]:
            pending.remove((due, stretch))
            coach.narrated(WORDS, stretch)
        coach.push(f)
    return coach, asked, said


def test_the_cool_down_debrief_is_in_the_coachs_words_when_they_come_in_time(root):
    coach, asked, said = drive(root, 4.0, Settings(learning_laps=1))
    assert asked and asked[0][0]["fallback"] and "topics" in asked[0][0]
    assert said and said[0]["by"] == "coach" and said[0]["text"] == WORDS
    spoken = [s.text for s in coach.arbiter.voice.spoken if s.kind == "debrief"]
    assert " ".join(spoken) == WORDS and len(spoken) >= 1


def test_without_a_reply_in_time_the_coach_uses_its_own_words(root):
    coach, asked, said = drive(root, None, Settings(learning_laps=1, narrate_wait_s=5.0))
    assert asked and said and said[0]["by"] == "template"
    late = [s for s in coach.arbiter.voice.spoken if s.kind == "debrief"]
    slow_at = asked[0][2] - coach.settings.narrate_after_s
    assert late[0].at_s >= slow_at + coach.settings.debrief_after_s + 5.0 - 0.1  # it waited, then spoke
    assert coach.narrated(WORDS, asked[0][1]) is False  # and a reply after that isn't said too


def test_a_live_session_narrates_and_answers_rule_wake_ups(root, tmp_path):
    rulebook.add_rule(root, {"id": "t1-watch", "track": TRACK, "when": {"corner_exit": "T1"}, "pushing_only": False,
                             "action": {"wake": "T1 done, delta {delta_s}"}, "limits": {"once": True}})
    rulebook.set_status(root, "t1-watch", "active", force=True)
    src = SyntheticSource(n_laps=3, seed=9, start_m=2900.0)
    (tmp_path / "rec").mkdir()
    rec = write_ibt(tmp_path / "rec" / "synthcar_synthetic 2026-10-10 10-00-00.ibt", src.session, src.frames())
    prompts = []

    def ask(prompt, context):
        prompts.append(prompt)
        return "Turn one's coming together, keep at it." if "rule you set" in prompt else "SILENT"
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice(), narrator=Narrator(ask))
    live.start({"source": "replay", "file": str(rec), "speed": 20, "voice": "alba"})
    live.wait(timeout_s=120)
    events = live.events()
    assert [e["type"] for e in events].count("wake") == 1 and any("T1 done" in p for p in prompts)
    narr = [e for e in events if e["type"] == "narration" and e["kind"] == "wake"]
    assert narr and narr[0]["status"] == "said"
    coach_lines = [e for e in events if e["type"] == "line" and e["kind"] == "coach"]
    assert coach_lines and coach_lines[0]["text"] == "Turn one's coming together, keep at it."
    assert coach_lines[0]["rule"] == "t1-watch"
