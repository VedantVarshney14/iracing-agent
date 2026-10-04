import json
import sys

import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from iagent.cli import cli
from iagent.ui.coach import CoachRuns, StreamParser, screen_context
from iagent.ui.server import create_app

SESSION = "99190549-5e16-467e-855e-6f8b5b9107da"


def stream(*events: dict) -> list[str]:
    return [json.dumps(e) + "\n" for e in events]


def text_delta(text: str) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}


def tool_use(tool_id: str, command: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}]}}


def tool_result(tool_id: str, content: str, is_error: bool = False) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}]}}


SHOW_OUTPUT = json.dumps({"ui_action": {"corners": [9], "view": "corner"}}, indent=2)

# Shaped like real `claude -p --output-format stream-json --verbose --include-partial-messages` output.
TURN = stream(
    {"type": "system", "subtype": "init", "session_id": SESSION},
    {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use"}}},
    tool_use("t1", "iagent corners compare 20250723-202727-L002 --json"),
    tool_result("t1", '{"total_delta_s": 0.532}'),
    tool_use("t2", "iagent ui show --corner 9 --view corner"),
    tool_result("t2", SHOW_OUTPUT),
    {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}},
    text_delta("Pouhon costs "),
    text_delta("0.48 s."),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Pouhon costs 0.48 s."}]}},
    {"type": "result", "subtype": "success", "session_id": SESSION, "is_error": False, "result": "Pouhon costs 0.48 s."},
)


def parse(lines: list[str]) -> list[dict]:
    parser = StreamParser()
    return [e for line in lines for e in parser.feed(line)]


def test_stream_becomes_browser_events():
    events = parse(TURN + ["not json\n", "\n"])
    assert events == [
        {"type": "session", "session_id": SESSION},
        {"type": "tool", "id": "t1", "command": "iagent corners compare 20250723-202727-L002 --json"},
        {"type": "tool", "id": "t2", "command": "iagent ui show --corner 9 --view corner"},
        {"type": "ui", "action": {"corners": [9], "view": "corner"}},
        {"type": "text_start"},
        {"type": "text", "text": "Pouhon costs "},
        {"type": "text", "text": "0.48 s."},
        {"type": "done", "session_id": SESSION, "is_error": False},
    ]


def test_ui_actions_need_a_successful_ui_show():
    failed = parse(stream(tool_use("t1", "iagent ui show --corner 99"), tool_result("t1", SHOW_OUTPUT, is_error=True)))
    assert [e["type"] for e in failed] == ["tool"]
    other = parse(stream(tool_use("t1", "iagent laps list"), tool_result("t1", SHOW_OUTPUT)))
    assert [e["type"] for e in other] == ["tool"]  # only `iagent ui show` can move the screen


def test_screen_context():
    text = screen_context({"track": "spa-2024-up", "car": "formulair04", "lap": "L2", "ref": "g61-x",
                           "ref_driver": "Teammate", "corners": [9, 16], "range": [3550.4, 4300]})
    assert "ghost: g61-x (Teammate)" in text and "selected corners: T9, T16" in text
    assert "zoomed to: 3550-4300 m" in text and text.startswith("<screen>")


@pytest.fixture
def fake_claude(tmp_path):
    """A stand-in for `claude` that records how it was called and replays TURN."""
    if sys.platform == "win32":
        pytest.skip("shebang scripts")
    record = tmp_path / "call.json"
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'workspace': os.environ.get('IAGENT_WORKSPACE')}}, open({str(record)!r}, 'w'))\n"
        f"sys.stdout.write({''.join(TURN)!r})\n"
    )
    script.chmod(0o755)
    return script, record


def test_chat_streams_a_coach_turn(tmp_path, fake_claude):
    script, record = fake_claude
    app = create_app(tmp_path, static_dir=tmp_path / "no-build", coach=CoachRuns(tmp_path, claude=str(script)))
    body = {"message": "Where do I lose time?", "session_id": SESSION, "context": {"lap": "L2", "corners": [9]}}
    with TestClient(app).stream("POST", "/api/chat", json=body) as res:
        events = [json.loads(line) for line in res.iter_lines() if line]
    assert events[0]["type"] == "run"
    assert [e["type"] for e in events[1:]] == ["session", "tool", "tool", "ui", "text_start", "text", "text", "done"]

    call = json.loads(record.read_text())
    assert call["argv"][:3] == ["-p", "--output-format", "stream-json"]
    assert call["argv"][call["argv"].index("--resume") + 1] == SESSION
    assert "Bash(iagent *)" in call["argv"]
    assert call["stdin"].startswith("<screen>") and call["stdin"].endswith("Where do I lose time?")
    assert call["workspace"] == str(tmp_path.resolve())


def test_chat_explains_a_missing_claude(tmp_path):
    app = create_app(tmp_path, static_dir=tmp_path / "no-build", coach=CoachRuns(tmp_path, claude="no-such-claude"))
    with TestClient(app).stream("POST", "/api/chat", json={"message": "hi"}) as res:
        events = [json.loads(line) for line in res.iter_lines() if line]
    assert events[-1]["type"] == "error" and "IAGENT_CLAUDE" in events[-1]["message"]
    assert TestClient(app).post("/api/chat", json={"message": " "}).status_code == 400


def test_ui_show_prints_an_action(tmp_path):
    run = lambda *args: CliRunner().invoke(cli, ["--workspace", str(tmp_path), "ui", "show", *args])
    out = run("--corner", "9", "--corner", "10", "--from", "3550", "--to", "4300", "--view", "corner")
    assert out.exit_code == 0
    assert json.loads(out.output) == {"ui_action": {"corners": [9, 10], "range": [3550.0, 4300.0], "view": "corner"}}
    assert run("--from", "10").exit_code != 0
    assert run("--from", "10", "--to", "5").exit_code != 0
    assert run().exit_code != 0
