import threading

import numpy as np
import pytest

from iagent.live.push_to_talk import SAMPLE_RATE, PushToTalk, parse_key


def talk(words: str = "where am I slow"):
    """A push-to-talk with no microphone or model: it hears what the test feeds it."""
    heard, done = [], threading.Event()
    audio_seen = []

    def transcribe(audio):
        audio_seen.append(len(audio))
        return words

    def on_text(text):
        heard.append(text)
        done.set()

    return PushToTalk("f9", on_text, transcribe), heard, done, audio_seen


def seconds(n: float) -> np.ndarray:
    return np.zeros(int(n * SAMPLE_RATE), "float32")


def test_what_is_said_while_the_key_is_held_becomes_the_question():
    ptt, heard, done, audio_seen = talk()
    ptt.hear(seconds(1))  # before the press: not kept
    ptt.press()
    ptt.press()  # key repeat while held doesn't start over
    ptt.hear(seconds(1))
    ptt.press()
    ptt.hear(seconds(0.5))
    ptt.release()
    ptt.hear(seconds(1))  # after the release: not kept
    assert done.wait(2)
    assert heard == ["where am I slow"] and audio_seen == [int(1.5 * SAMPLE_RATE)]


def test_a_knock_on_the_button_or_silence_asks_nothing():
    ptt, heard, done, _ = talk()
    ptt.press()
    ptt.hear(seconds(0.1))
    ptt.release()
    assert not done.wait(0.2) and heard == []
    ptt, heard, done, _ = talk(words="")  # held long enough, nothing recognised
    ptt.press()
    ptt.hear(seconds(1))
    ptt.release()
    assert not done.wait(0.2) and heard == []


def test_an_unknown_key_is_an_error_that_says_what_is_allowed():
    assert parse_key("f9") and parse_key("t")
    with pytest.raises(ValueError, match="f9, scroll_lock or pause"):
        parse_key("banana")
