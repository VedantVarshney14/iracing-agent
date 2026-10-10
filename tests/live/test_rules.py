import json

import pytest
from click.testing import CliRunner

from iagent.cli import cli
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.live import rulebook
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import build_plan
from iagent.live.expr import Expr, ExprError, Template
from iagent.live.rules import Rule, RuleError, describe
from iagent.live.session import LiveSessions
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.testing.ibt_writer import write_ibt
from iagent.testing.synthetic import LapKind, SyntheticSource
from iagent.workspace import Workspace

TRACK, CAR = "synthetic", "synthcar"


@pytest.fixture
def root(tmp_path):
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"


def rule(**raw) -> Rule:
    return Rule.from_dict({"id": "r", "track": TRACK, "action": {"say": "Hello."}, **raw})


def drive(root, rules, source=None, settings=None):
    """Replay synthetic laps through a coach with RULES; returns the coach and its engine."""
    source = source or SyntheticSource(n_laps=4, seed=7, start_m=2900.0)
    ws = Workspace(root)
    try:
        plan = build_plan(ws, TRACK, CAR)
        coach = LiveCoach(source.session, plan, ws.corner_map(TRACK), ws.load(plan.ref_lap_id),
                          Arbiter(CapturedVoice()), settings or Settings(learning_laps=1), rules=rules)
    finally:
        ws.close()
    engine = coach.rules
    frames = list(source.frames())
    coach._frames_seen = []
    for f in frames:
        coach._frames_seen.append(f)
        coach.push(f)
    coach.finish(frames[-1].session_time)
    return coach, engine


def fired(engine, rule_id="r"):
    return [f for f in engine.firings if f["rule"] == rule_id and f["result"] == "fired"]


# --- the expression language --------------------------------------------------------------------

def test_expressions_compute_and_missing_values_are_false_not_errors():
    env = {"brake_m": 480.0, "ref_brake_m": 500.0, "off": None, "name": "Pouhon"}
    assert Expr("brake_m < ref_brake_m - 10")(env) is True
    assert Expr("off > 5")(env) is False and Expr("not off > 5")(env) is True
    assert Expr("off == none")(env) is True and Expr("off + 3")(env) is None
    assert Expr("400 < brake_m < 490 and name in ['Pouhon', 'Eau Rouge']")(env) is True
    assert Expr("round5(ref_brake_m - brake_m)")(env) == 20
    assert Expr("'late' if brake_m > ref_brake_m else 'early'")(env) == "early"
    assert Expr("name > 3")(env) is False  # a type mix-up is a non-match, not a crash
    assert Expr("true and not false")({}) is True


@pytest.mark.parametrize("text", ["__import__('os')", "brake_m.real", "open('x')", "lambda: 1", "x[0]", "a = 1", ""])
def test_expressions_refuse_anything_but_the_small_language(text):
    with pytest.raises(ExprError):
        Expr(text)


def test_templates_format_holes_and_stay_silent_on_missing_values():
    t = Template("{name}: braked {round5(-brake_diff_m)} metres early ({delta_s:+.2f} s). {{literal}}")
    assert t.render({"name": "T1", "brake_diff_m": -22.0, "delta_s": 0.153}) == "T1: braked 20 metres early (+0.15 s). {literal}"
    assert t.render({"name": "T1", "brake_diff_m": None, "delta_s": 0.1}) is None
    assert t.missing({"name": "T1", "delta_s": 0.1}) == ["round5(-brake_diff_m)"]
    assert Template("{say_time(lap_time)}, {say_gap(gap_s)} down.").render({"lap_time": 104.21, "gap_s": 0.52}) == "1 44.2, 5 tenths down."
    with pytest.raises(ExprError):
        Template("unclosed {name")


# --- rules ---------------------------------------------------------------------------------------

def test_rules_are_checked_when_added_with_messages_that_say_what_to_fix():
    with pytest.raises(RuleError, match="unknown variable.*brake_dif_m"):
        rule(when={"corner_exit": 1}, **{"if": "brake_dif_m < -10"})
    with pytest.raises(RuleError, match="exactly one trigger"):
        rule(when={"corner_exit": 1, "lap": "complete"})
    with pytest.raises(RuleError, match="priority"):
        Rule.from_dict({"id": "r", "when": {"lap": "complete"}, "action": {"say": "x", "priority": "loud"}})
    with pytest.raises(RuleError, match="Unknown field"):
        rule(when={"lap": "complete"}, then={})
    with pytest.raises(RuleError, match="lap_time"):  # a lap variable isn't there at a corner exit
        rule(when={"corner_exit": 1}, action={"say": "{lap_time}"})
    r = rule(when={"at": "T2 apex", "offset_m": -50}, **{"if": "speed_kph > 100"}, limits={"cooldown_laps": 1})
    assert Rule.from_dict(r.to_dict()).fingerprint == r.fingerprint
    assert r.fingerprint != rule(when={"at": "T2 apex", "offset_m": -40}, **{"if": "speed_kph > 100"},
                                 limits={"cooldown_laps": 1}).fingerprint
    assert set(describe()["events"]) >= {"approach", "corner_exit", "lap", "pit", "frame", "clock", "pace", "focus"}


def test_a_corner_exit_rule_sees_the_same_numbers_as_the_coach(root):
    r = rule(when={"corner_exit": "T1"}, action={"log": "{name} {brake_m} vs {ref_brake_m}: {brake_diff_m}"},
             pushing_only=False)
    coach, engine = drive(root, [r])
    fires = fired(engine)
    assert len(fires) >= 3  # once per lap past T1
    assert all(f["values"]["brake_diff_m"] == pytest.approx(f["values"]["brake_m"] - f["values"]["ref_brake_m"], abs=0.11)
               for f in fires)
    assert {f["lap_dist"] for f in fires} <= set(range(700, 800))  # just after T1's exit
    assert fires[0]["actions"][0]["log"].startswith("Turn 1 ")


def test_conditions_in_a_row_and_limits(root):
    always = {"corner_exit": "any"}
    _, engine = drive(root, [rule(when=always, pushing_only=False, **{"if": "corner == 2"})])
    assert {f["values"]["corner"] for f in fired(engine)} == {2}
    _, engine = drive(root, [rule(when={"corner_exit": 2}, pushing_only=False, in_a_row=2)])
    n_exits = engine.stats["r"].occurrences
    assert len(fired(engine)) == n_exits // 2  # every second time: the streak restarts after firing
    _, engine = drive(root, [rule(when=always, pushing_only=False, limits={"max_per_lap": 1})])
    laps = [f["lap_no"] for f in fired(engine)]
    assert len(laps) == len(set(laps)) and engine.stats["r"].limited > 0
    _, engine = drive(root, [rule(when=always, pushing_only=False, limits={"once": True})])
    assert len(fired(engine)) == 1


def test_a_position_rule_finishes_before_its_point_once_per_pass(root):
    r = rule(when={"at": "T3", "lead_s": 0.5}, action={"say": "Three. Stay off the kerb."})
    coach, engine = drive(root, [r], settings=Settings(learning_laps=0, summary=False))
    target = coach.plan.cue_for(3).target_m
    fires = fired(engine)
    assert len(fires) == len({f["lap_no"] for f in fires}) >= 3
    said = [s for s in coach.arbiter.voice.spoken if s.text == "Three. Stay off the kerb."]
    assert said
    for s in said:
        f = next(f for f in coach._frames_seen if f.session_time >= s.at_s)
        to_target = (target - f.get("LapDist")) % coach.length
        assert to_target / f.get("Speed") >= len(s.text) / 14.0 + 0.25  # it can be said in time


def test_lap_pit_and_schedule_triggers(root):
    rules = [
        Rule.from_dict({"id": "best", "when": {"lap": "complete"}, "if": "new_best",
                        "action": {"wake": "new best {lap_time:.3f}"}}),
        Rule.from_dict({"id": "pit-in", "when": {"pit": "entry"}, "action": {"log": "in"}}),
        Rule.from_dict({"id": "pit-out", "when": {"pit": "exit"}, "action": {"log": "out"}}),
        Rule.from_dict({"id": "every2", "when": {"every_laps": 2}, "action": {"log": "lap {lap}"}}),
        Rule.from_dict({"id": "hello", "when": {"session_start": True}, "action": {"say": "Let's go."}}),
        Rule.from_dict({"id": "fast", "when": {"condition": "speed_kph > 150", "for_s": 1.0, "rearm_s": 3.0},
                        "pushing_only": False, "action": {"log": "fast"}, "limits": {"max_per_lap": 1}}),
    ]
    kinds = [LapKind.OUT_LAP, LapKind.CLEAN, LapKind.CLEAN, LapKind.CLEAN, LapKind.PIT_IN]
    coach, engine = drive(root, rules, SyntheticSource(n_laps=5, kinds=kinds, seed=3))
    assert len(fired(engine, "pit-in")) == 1
    assert fired(engine, "pit-out")[0]["lap_no"] == 0  # leaving the pits on the out lap
    assert [f["values"]["lap"] for f in fired(engine, "every2")] == [2]  # three counted laps: the out lap doesn't count
    assert len(fired(engine, "hello")) == 1 and "Let's go." in [s.text for s in coach.arbiter.voice.spoken]
    assert fired(engine, "best") and all(f["actions"][0]["wake"].startswith("new best") for f in fired(engine, "best"))
    fast = fired(engine, "fast")
    assert 1 <= len(fast) <= 5 and all(f["values"]["speed_kph"] > 150 for f in fast)


def test_rules_ignore_laps_and_corners_when_not_pushing(root):
    r = rule(when={"corner_exit": "any"}, action={"log": "x"})
    _, engine = drive(root, [r], SyntheticSource(n_laps=3, kinds=[LapKind.OUT_LAP, LapKind.CLEAN, LapKind.CLEAN], seed=5))
    st = engine.stats["r"]
    assert st.skipped > 0 and st.fired == st.occurrences - st.skipped


def test_rules_for_another_track_or_unknown_corners_are_left_out(root):
    other = Rule.from_dict({"id": "other", "track": "spa", "when": {"lap": "complete"}, "action": {"log": "x"}})
    ghost = rule(id="ghost", when={"corner_exit": "T12"})
    coach, engine = drive(root, [other, ghost, rule(when={"lap": "complete"})],
                          SyntheticSource(n_laps=2, seed=7, start_m=2900.0))
    assert [r.id for r in engine.rules] == ["r"] and "ghost" in engine.errors[0]


# --- the rulebook --------------------------------------------------------------------------------

def test_rules_are_drafts_until_backtested_and_activated(root):
    raw = {"id": "t1-early", "track": TRACK, "when": {"corner_exit": "Turn 1"}, "if": "brake_diff_m < -3",
           "action": {"say": "{name}: braked {round5(-brake_diff_m)} metres early."}}
    r, path = rulebook.add_rule(root, raw)
    assert path == root / "rules" / TRACK / "t1-early.json" and r.status == "draft"
    with pytest.raises(RuleError, match="exists"):
        rulebook.add_rule(root, raw)
    with pytest.raises(RuleError, match="Backtest"):
        rulebook.set_status(root, "t1-early", "active")
    report = rulebook.backtest(root, [r], TRACK, CAR)
    result = report["rules"]["t1-early"]
    assert report["sessions"] == ["synthetic-1"] and result["laps"] >= 3
    rulebook.record_backtest(root, r, result)
    assert rulebook.set_status(root, "t1-early", "active").status == "active"
    assert [x.id for x in rulebook.load_rules(root, TRACK, CAR, "active")] == ["t1-early"]
    edited, _ = rulebook.add_rule(root, {**raw, "if": "brake_diff_m < -8"}, replace=True)
    assert edited.status == "draft"  # changed: off until backtested again
    with pytest.raises(RuleError, match="Backtest"):
        rulebook.set_status(root, "t1-early", "active")
    with pytest.raises(RuleError, match="No corner"):
        rulebook.add_rule(root, {**raw, "id": "nope", "when": {"corner_exit": "Eau Rouge"}})


def test_rules_for_every_track_live_apart_and_load_everywhere(root):
    rulebook.add_rule(root, {"id": "pb", "when": {"lap": "complete"}, "if": "new_best", "action": {"say": "New best."}})
    assert (root / "rules" / "_any" / "pb.json").exists()
    assert [r.id for r in rulebook.load_rules(root, "spa")] == ["pb"]
    with pytest.raises(RuleError, match="needs a track"):
        rulebook.add_rule(root, {"id": "c", "when": {"corner_exit": "T1"}, "action": {"say": "x"}})


def test_rules_cli(root):
    runner = CliRunner()
    args = ["--workspace", str(root), "rules"]
    rule_json = json.dumps({"id": "lap-call", "when": {"lap": "complete"},
                            "action": {"say": "{say_time(lap_time)}.", "priority": "summary"}})
    out = runner.invoke(cli, [*args, "add", rule_json, "--track", TRACK])
    assert out.exit_code == 0, out.output
    bad = runner.invoke(cli, [*args, "add", '{"id": "x", "when": {"lap": "complete"}, "if": "lapp > 1", "action": {"log": "x"}}'])
    assert bad.exit_code != 0 and "unknown variable" in bad.output
    out = runner.invoke(cli, [*args, "activate", "lap-call"])
    assert out.exit_code != 0 and "Backtest" in out.output
    out = runner.invoke(cli, [*args, "backtest", "lap-call", "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["rules"]["lap-call"]["totals"]["fired"] >= 2
    assert runner.invoke(cli, [*args, "activate", "lap-call"]).exit_code == 0
    listed = runner.invoke(cli, [*args, "list"])
    assert "lap-call" in listed.output and "active" in listed.output
    assert runner.invoke(cli, [*args, "vars", "--trigger", "corner_exit"]).output.count("brake_diff_m") == 1
    assert runner.invoke(cli, [*args, "remove", "lap-call"]).exit_code == 0


def test_a_live_session_uses_active_rules_and_logs_them(root, tmp_path):
    rulebook.add_rule(root, {"id": "lap-call", "track": TRACK, "when": {"lap": "complete"},
                             "action": [{"say": "Rule says {say_time(lap_time)}."}, {"wake": "lap {lap} done"}]})
    rulebook.set_status(root, "lap-call", "active", force=True)
    src = SyntheticSource(n_laps=3, seed=9, start_m=2900.0)
    (tmp_path / "rec").mkdir()
    rec = write_ibt(tmp_path / "rec" / "synthcar_synthetic 2026-10-10 10-00-00.ibt", src.session, src.frames())
    live = LiveSessions(root, voice_factory=lambda name: CapturedVoice())
    live.start({"source": "replay", "file": str(rec), "speed": 500, "voice": "alba"})
    live.wait(timeout_s=60)
    events = live.events()
    running = next(e for e in events if e["type"] == "status" and e["state"] == "running")
    assert running["rules"] == ["lap-call"]
    assert [e for e in events if e["type"] == "rule" and e["result"] == "fired"]
    assert [e["message"] for e in events if e["type"] == "wake"][:1] == ["lap 1 done"]
    lines = [e for e in events if e["type"] == "line" and e.get("rule") == "lap-call"]
    assert lines and lines[0]["kind"] == "rule"


def test_rules_changed_mid_session_are_picked_up(root):
    from iagent.live.run import reload_rules

    coach, engine = drive(root, [], SyntheticSource(n_laps=1, seed=7, start_m=2900.0))
    rulebook.add_rule(root, {"id": "pb", "when": {"lap": "complete"}, "action": {"log": "x"}})
    before = rulebook.signature(root)
    reload_rules(root, coach)
    assert engine.rules == []  # a draft isn't live
    rulebook.set_status(root, "pb", "active", force=True)
    assert rulebook.signature(root) != before
    reload_rules(root, coach)
    assert [r.id for r in engine.rules] == ["pb"]
