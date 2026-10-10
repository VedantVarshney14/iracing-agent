"""The event pipeline: new events, components, rules and speech gates plug in as definitions."""

import threading
from dataclasses import dataclass
from typing import ClassVar

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live.coach import LiveCoach, Settings
from iagent.live.components import speaking
from iagent.live.components.speaking import Gate
from iagent.live.cues import build_plan
from iagent.live.events import EVENTS, Event, Frame, Lap, doc
from iagent.live.pipeline import Component, Pipeline, enrich, on
from iagent.live.rules import Rule, describe
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.telemetry.frames import Frame as TelemetryFrame
from iagent.testing.synthetic import SyntheticSource
from iagent.workspace import Workspace

TRACK, CAR = "synthetic", "synthcar"


@dataclass(kw_only=True)
class Ping(Event):
    """A test event."""
    name: ClassVar[str] = "test_ping"
    n: int = doc("a number", 0)


@dataclass(kw_only=True)
class Pong(Event):
    """Another."""
    name: ClassVar[str] = "test_pong"
    n: int = doc("a number", 0)
    seen: bool = doc("set by an enricher", False)


@dataclass(kw_only=True)
class KerbStrike(Event):
    """The car hit a kerb hard: a new event, defined here, used by a rule below."""
    name: ClassVar[str] = "kerb_strike"
    judged: ClassVar[bool] = True
    jolt: float = doc("vertical acceleration (m/s²)", 0.0)
    count: int = doc("kerb strikes so far", 0)


@pytest.fixture
def root(tmp_path):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"


class Recorder(Component):
    def start(self):
        self.seen = []

    @enrich(Pong)
    def mark(self, e: Pong):
        e.seen = True

    @on(Ping)
    def ping(self, e: Ping):
        self.seen.append(("ping", e.n))
        self.emit(Pong(n=e.n + 1))

    @on(Pong)
    def pong(self, e: Pong):
        self.seen.append(("pong", e.n, e.seen))


def test_events_are_handled_in_order_enriched_first_and_posted_from_any_thread():
    pipe = Pipeline(None, None, [Recorder])
    rec = pipe.get(Recorder)
    pipe.emit(Ping(n=1))
    pipe.emit(Ping(n=10))
    pipe.run()
    assert rec.seen == [("ping", 1), ("ping", 10), ("pong", 2, True), ("pong", 11, True)]  # first in, first out
    t = threading.Thread(target=lambda: pipe.post(Ping(n=100)))
    t.start()
    t.join()
    pipe.push(TelemetryFrame(1.0, {"LapDistPct": 0.1}))
    assert rec.seen[-2:] == [("ping", 100), ("pong", 101, True)]


def test_events_are_typed_and_registered_by_name():
    assert EVENTS["test_ping"] is Ping and Lap.__mro__[1].name == "crossing"  # a counted lap is a crossing
    assert Ping(n=3).public() == {"n": 3}
    with pytest.raises(TypeError):
        Ping(m=3)  # a typo in a field is an error, not a silent new key


class KerbDetector(Component):
    """A new detector: defined, then passed in. Nothing else changes."""

    def start(self):
        self.kerbs = 0
        self._t = 0.0

    @on(Frame)
    def jolt(self, e: Frame):
        if self.state.on_track and (e.frame.get("Speed") or 0) > 40 and e.at - self._t > 20:  # stands in for a jolt
            self._t = e.at
            self.kerbs += 1
            self.emit(KerbStrike(jolt=25.0, count=self.kerbs))


def test_a_new_event_component_and_rule_need_nothing_but_their_definitions(root):
    rule = Rule.from_dict({"id": "kerbs", "when": {"event": "kerb_strike"}, "if": "jolt > 20",
                           "actions": [{"say": "Kerb, {count} so far."}], "limits": {"max_per_lap": 1}})
    assert describe()["events"]["kerb_strike"]["fields"]["jolt"].startswith("vertical")  # documented by itself
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
    coach.on(Lap, lambda e: first_lap.append(e.at))
    for f in src.frames():
        coach.push(f)
    assert all(s.at_s <= first_lap[0] for s in coach.arbiter.voice.spoken)  # nothing after the first counted lap
