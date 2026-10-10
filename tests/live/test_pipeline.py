"""The event pipeline: new events, components, rules and speech gates plug in as definitions."""

import threading

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live.coach import LiveCoach, Settings
from iagent.live.components import speaking
from iagent.live.components.speaking import Gate
from iagent.live.cues import build_plan
from iagent.live.events import EVENTS, define_event, define_state
from iagent.live.pipeline import Component, Pipeline
from iagent.live.rules import Rule, describe
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.telemetry.frames import Frame
from iagent.testing.synthetic import SyntheticSource
from iagent.workspace import Workspace

TRACK, CAR = "synthetic", "synthcar"

if "test_ping" not in EVENTS:
    define_event("test_ping", "A test event.", {"n": "a number"})
    define_event("test_pong", "Another.", {"n": "a number", "seen": "added by an enricher"})
if "kerb_strike" not in EVENTS:
    define_event("kerb_strike", "The car hit a kerb hard.", {"jolt": "vertical acceleration (m/s²)"}, judged=True)
    define_state(kerbs="kerb strikes this session")


@pytest.fixture
def root(tmp_path):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"


class Recorder(Component):
    def start(self):
        self.seen = []

    def enrich_test_pong(self, e):
        e["seen"] = True

    def on_test_ping(self, e):
        self.seen.append(("ping", e["n"]))
        self.emit("test_pong", n=e["n"] + 1)

    def on_test_pong(self, e):
        self.seen.append(("pong", e["n"], e["seen"]))


def test_events_are_handled_in_order_enriched_first_and_posted_from_any_thread():
    pipe = Pipeline(None, None, [Recorder])
    rec = pipe.get(Recorder)
    pipe.emit("test_ping", n=1)
    pipe.emit("test_ping", n=10)
    pipe.run()
    assert rec.seen == [("ping", 1), ("ping", 10), ("pong", 2, True), ("pong", 11, True)]  # first in, first out
    t = threading.Thread(target=lambda: pipe.post("test_ping", n=100))
    t.start()
    t.join()
    pipe.push(Frame(1.0, {"LapDistPct": 0.1}))
    assert rec.seen[-2:] == [("ping", 100), ("pong", 101, True)]
    with pytest.raises(ValueError, match="define_event"):
        pipe.emit("no_such_event")


def test_a_component_for_an_undefined_event_is_refused():
    class Typo(Component):
        def on_corner_exti(self, e):
            pass
    with pytest.raises(ValueError, match="corner_exti"):
        Pipeline(None, None, [Typo])


class KerbDetector(Component):
    """A new detector: defined, then passed in. Nothing else changes."""

    def start(self):
        self.state["kerbs"] = 0
        self._t = 0.0

    def on_frame(self, e):
        f = e["frame"]
        if self.state["on_track"] and (f.get("Speed") or 0) > 40 and e.at - self._t > 20:  # stands in for a jolt
            self._t = e.at
            self.state["kerbs"] += 1
            self.emit("kerb_strike", jolt=25.0)


def test_a_new_event_component_and_rule_need_nothing_but_their_definitions(root):
    rule = Rule.from_dict({"id": "kerbs", "when": {"event": "kerb_strike"}, "if": "jolt > 20",
                           "action": {"say": "Kerb, {kerbs} so far."}, "limits": {"max_per_lap": 1}})
    assert "kerb_strike" in describe()["events"] and "kerbs" in describe()["state"]  # documented by itself
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        src = SyntheticSource(n_laps=3, seed=7, start_m=2900.0)
        coach = LiveCoach(src.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(CapturedVoice()),
                          Settings(), rules=[rule], components=(KerbDetector,))
    finally:
        ws.close()
    for f in src.frames():
        coach.push(f)
    said = [s.text for s in coach.arbiter.voice.spoken if s.kind == "rule"]
    assert said and said[0] == "Kerb, 1 so far."
    assert coach.rules.stats["kerbs"].fired >= 2


def test_a_new_reason_to_keep_quiet_is_a_gate(root, monkeypatch):
    monkeypatch.setattr(speaking, "GATES", [*speaking.GATES, Gate("test hush", "lap >= 1", hold=True, why="hush")])
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        src = SyntheticSource(n_laps=3, seed=7, start_m=2900.0)
        coach = LiveCoach(src.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id), Arbiter(CapturedVoice()),
                          Settings())
    finally:
        ws.close()
    first_lap = []
    coach.on("lap", lambda e: first_lap.append(e.at))
    for f in src.frames():
        coach.push(f)
    assert all(s.at_s <= first_lap[0] for s in coach.arbiter.voice.spoken)  # nothing after the first counted lap
