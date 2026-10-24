import json

from iagent.live.dash import DashVoice
from iagent.live.speech import CapturedVoice, Utterance


def line(text, kind):
    return Utterance(text, 0, kind, 0.0, 100.0)


def test_the_feed_keeps_the_latest_line_of_each_kind_and_the_voice_still_speaks(tmp_path):
    path = tmp_path / "dash.json"
    inner = CapturedVoice()
    voice = DashVoice(inner, path)
    voice.play(line("La Source. Big stop.", "approach"), 1.0)
    voice.play(line("Turn 5: brake later.", "feedback"), 2.0)
    voice.play(line("Pouhon. Lift.", "approach"), 3.0)

    feed = json.loads(path.read_text())
    assert feed["cue"] == "Pouhon. Lift."
    assert feed["feedback"] == "Turn 5: brake later."
    assert feed["seq"] == 3
    assert [s.text for s in inner.spoken] == [
        "La Source. Big stop.",
        "Turn 5: brake later.",
        "Pouhon. Lift.",
    ]
