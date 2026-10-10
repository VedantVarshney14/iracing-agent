import threading
import time

import pytest

from iagent.agent import final_text
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live import engineer as eng
from iagent.live import rulebook
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import build_plan
from iagent.live.engineer import Engineer, debrief_prompt, wake_prompt
from iagent.live.narrator import chunks, clean
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
    stream = [{"type": "text_start"}, {"type": "text", "text": "Let me check."},
              {"type": "tool", "command": "iagent laps list"}, {"type": "text_start"},
              {"type": "text", "text": "Brake "}, {"type": "text", "text": "later."}]
    assert final_text(stream) == "Brake later."


def test_prompts_give_the_facts_and_the_constraints():
    p = debrief_prompt({"topics": [{"name": "Turn 1"}], "crewchief": True, "fallback": ["Turn 1 is where the time is."]})
    assert "cool-down lap" in p and "CrewChief" in p and '"name": "Turn 1"' in p and "- Turn 1 is where" in p
    w = wake_prompt({"rule": "t1", "message": "early again", "values": {"brake_diff_m": -20}}, {"mode": "pushing"})
    assert "under 12 words" in w and "SILENT" in w and "brake_diff_m" in w


class Turns:
    """A fake engineer's model: records each turn; answers after DELAY_S."""

    def __init__(self, delay_s=0.0, answer="Copy."):
        self.calls = []  # (prompt, session id, resume)
        self.delay_s, self.answer = delay_s, answer
        self.running = 0
        self.overlapped = False

    def __call__(self, prompt, session_id, resume):
        self.running += 1
        self.overlapped |= self.running > 1
        time.sleep(self.delay_s)
        self.calls.append((prompt, session_id, resume))
        self.running -= 1
        return (self.answer(prompt) if callable(self.answer) else self.answer), True


def ask(e: Engineer, kind, prompt="go", **kw):
    done = threading.Event()
    out = {}

    def reply(text):
        out["text"] = text
        done.set()
    assert e.request(kind, prompt, {}, reply, **kw)
    return done, out


def test_one_conversation_for_the_session_one_turn_at_a_time():
    turns = Turns(delay_s=0.05)
    e = Engineer(turns)
    first = ask(e, "briefing")
    e.heard("[00:05.0] coach (approach): Turn 1, right.")
    waits = [ask(e, "wake", "a rule"), ask(e, "question", "where am I slow?")]
    for done, _ in [first, *waits]:
        assert done.wait(5)
    sids = {sid for _, sid, _ in turns.calls}
    assert sids == {e.session_id} and [r for _, _, r in turns.calls] == [False, True, True]  # started once, then resumed
    assert not turns.overlapped  # never two turns at once
    assert "where am I slow?" in turns.calls[1][0]  # the driver's question goes before the rule's wake-up
    heard = [p for p, _, _ in turns.calls if "Turn 1, right." in p]
    assert len(heard) == 1 and "<radio since your last turn>" in heard[0]  # in the next turn, and only that one


def test_a_request_too_late_to_be_useful_is_dropped(monkeypatch):
    monkeypatch.setitem(eng.KINDS, "debrief", (1, 0.05))
    turns = Turns(delay_s=0.2)
    e = Engineer(turns)
    busy = ask(e, "question")
    late = ask(e, "debrief")
    assert busy[0].wait(5) and late[0].wait(5)
    assert late[1]["text"] is None and len(turns.calls) == 1  # the cool-down was over by then
    assert e.request("wake", "x", {}, lambda t: None)
    assert not e.request("wake", "y", {}, lambda t: None)  # one wake-up at a time
    assert e.request("question", "a", {}, lambda t: None) and e.request("question", "b", {}, lambda t: None)


def test_turns_through_claude_code_start_then_resume_the_same_session():
    class Runner:
        def __init__(self):
            self.args = []

        def turn(self, prompt, system, session_id, new_session=False):
            self.args.append((session_id, new_session, system))
            yield {"type": "session", "session_id": session_id}
            yield {"type": "text_start"}
            yield {"type": "text", "text": "Copy that."}
            yield {"type": "done", "session_id": session_id, "is_error": False}
    runner = Runner()
    e = Engineer.over(runner)
    for _ in range(2):
        done, out = ask(e, "question")
        assert done.wait(5) and out["text"] == "Copy that."
    assert [(sid, new) for sid, new, _ in runner.args] == [(e.session_id, True), (e.session_id, False)]
    assert "race engineer" in runner.args[0][2]


class SlowEngineer:
    """Answers REPLY_AFTER_S (session time) after being asked, or never (None)."""

    def __init__(self, reply_after_s):
        self.reply_after_s = reply_after_s
        self.asked = []  # [kind, prompt, on_reply, asked at]
        self.now = 0.0

    def request(self, kind, prompt, context, on_reply, min_gap_s=0.0):
        if kind == "debrief":
            self.asked.append([kind, prompt, on_reply, self.now])
        return True

    def heard(self, line):
        pass

    def tick(self, now):
        self.now = now
        for a in self.asked:
            if self.reply_after_s is not None and a[2] is not None and now >= a[3] + self.reply_after_s:
                a[2](WORDS)
                a[2] = None


def drive(root, reply_after_s: float | None, settings: Settings):
    """A session with a cool-down lap; the narrator answers REPLY_AFTER_S after being asked
    (None: never), through the pipeline's inbox like the live session's."""
    narrator = SlowEngineer(reply_after_s)
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        kinds = [LapKind.CLEAN, LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW, LapKind.CLEAN]
        src = SyntheticSource(n_laps=len(kinds), kinds=kinds, seed=11, start_m=2900.0)
        coach = LiveCoach(src.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(CapturedVoice()),
                          settings, own_best=own_best(ws, TRACK, CAR), engineer=narrator)
    finally:
        ws.close()
    asked, said, words = [], [], []
    coach.on("narrate", lambda e: asked.append((e["facts"], e["stretch"], e.at)))
    coach.on("debrief", lambda e: said.append(e.public()))
    coach.on("debrief_words", lambda e: words.append(e.public()))
    for f in src.frames():
        narrator.tick(f.session_time)
        coach.push(f)
    return coach, asked, said, words


def test_the_cool_down_debrief_is_in_the_coachs_words_when_they_come_in_time(root):
    coach, asked, said, words = drive(root, 4.0, Settings(learning_laps=1))
    assert asked and asked[0][0]["fallback"] and "topics" in asked[0][0]
    assert said and said[0]["by"] == "coach" and said[0]["text"] == WORDS
    assert words[0]["status"] == "used"
    spoken = [s.text for s in coach.arbiter.voice.spoken if s.kind == "debrief"]
    assert " ".join(spoken) == WORDS and len(spoken) >= 1


def test_without_a_reply_in_time_the_coach_uses_its_own_words(root):
    coach, asked, said, _ = drive(root, None, Settings(learning_laps=1, narrate_wait_s=5.0))
    assert asked and said and said[0]["by"] == "template"
    late = [s for s in coach.arbiter.voice.spoken if s.kind == "debrief"]
    slow_at = asked[0][2] - coach.settings.narrate_after_s
    assert late[0].at_s >= slow_at + coach.settings.debrief_after_s + 5.0 - 0.1  # it waited, then spoke


def test_a_reply_after_the_coachs_own_words_is_not_said_too(root):
    coach, asked, said, words = drive(root, 30.0, Settings(learning_laps=1, narrate_wait_s=5.0))
    assert said and said[0]["by"] == "template"
    assert words and words[0]["status"] == "too late"
    assert WORDS not in [s.text for s in coach.arbiter.voice.spoken]


def test_a_live_session_has_one_engineer_from_radio_check_to_notes(root, tmp_path):
    rulebook.add_rule(root, {"id": "t1-watch", "track": TRACK, "when": {"corner_exit": "T1"}, "pushing_only": False,
                             "action": {"wake": "T1 done, delta {delta_s}"}, "limits": {"once": True}})
    rulebook.set_status(root, "t1-watch", "active", force=True)
    src = SyntheticSource(n_laps=3, seed=9, start_m=2900.0)
    (tmp_path / "rec").mkdir()
    rec = write_ibt(tmp_path / "rec" / "synthcar_synthetic 2026-10-10 10-00-00.ibt", src.session, src.frames())

    def answer(prompt):
        if "rule you set" in prompt:
            return "Turn one's coming together, keep at it."
        if "session is starting" in prompt:
            return "Radio check. Easy out lap, then build into it."
        if "session is over" in prompt:
            return "Noted the Turn 1 work."
        return "SILENT"
    turns = Turns(answer=answer)
    made = []
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice(),
                        engineer=lambda: made.append(Engineer(turns)) or made[-1])
    live.start({"source": "replay", "file": str(rec), "speed": 20, "voice": "alba"})
    live.wait(timeout_s=120)
    events = live.events()
    running = next(e for e in events if e["type"] == "status" and e["state"] == "running")
    assert len(made) == 1 and running["engineer"] == made[0].session_id
    assert {sid for _, sid, _ in turns.calls} == {made[0].session_id}  # one conversation, every turn
    assert [r for _, _, r in turns.calls][0] is False and all(r for _, _, r in turns.calls[1:])
    said = [e["text"] for e in events if e["type"] == "line" and e["kind"] == "coach"]
    assert said[:2] == ["Radio check. Easy out lap, then build into it.", "Turn one's coming together, keep at it."]
    assert any("<radio since your last turn>" in p and "lap 1:" in p for p, _, _ in turns.calls)  # it hears the radio
    notes = [e for e in events if e["type"] == "notes_written"]
    assert notes and notes[0]["text"] == "Noted the Turn 1 work."
    assert [e["type"] for e in events].count("wake") == 1
