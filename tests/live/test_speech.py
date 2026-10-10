from iagent.live.speech import APPROACH, FEEDBACK, SUMMARY, Arbiter, CapturedVoice, Utterance


def utt(text, priority, created=0.0, expires=100.0, duration=1.0):
    return Utterance(text, priority, {0: "approach", 1: "feedback", 2: "summary"}[priority], created, expires,
                     duration_s=duration)


def test_one_at_a_time_most_important_first_with_a_quiet_gap():
    voice = CapturedVoice()
    arb = Arbiter(voice, quiet_gap_s=0.5)
    arb.say(utt("summary", SUMMARY))
    arb.say(utt("feedback", FEEDBACK))
    arb.say(utt("cue", APPROACH, created=0.5))
    for t in [0.0, 0.5, 1.0, 1.4, 1.5, 3.0, 4.5]:
        arb.tick(t)
    assert [(s.at_s, s.text) for s in voice.spoken] == [(0.0, "cue"), (1.5, "feedback"), (3.0, "summary")]


def test_expired_lines_are_dropped_not_said_late():
    voice = CapturedVoice()
    arb = Arbiter(voice)
    arb.say(utt("Turn 1 cue", APPROACH, expires=2.0))
    arb.tick(0.0, hold=True)  # a car alongside
    arb.tick(2.5)
    assert voice.spoken == [] and [u.text for u in arb.dropped] == ["Turn 1 cue"]


def test_less_important_lines_wait_for_room_before_a_cue():
    voice = CapturedVoice()
    arb = Arbiter(voice)
    arb.say(utt("long feedback", FEEDBACK, duration=3.0))
    arb.tick(0.0, free_for_s=2.0)  # a corner cue is due in 2 s: no room
    assert voice.spoken == []
    arb.say(utt("cue", APPROACH))
    arb.tick(0.1, free_for_s=0.0)  # cues always go
    assert [s.text for s in voice.spoken] == ["cue"]


def test_a_corner_cue_cuts_off_a_summary():
    voice = CapturedVoice()
    arb = Arbiter(voice)
    arb.say(Utterance("Lap summary that goes on for a while.", SUMMARY, "summary", 0.0, 20.0, duration_s=5.0))
    arb.tick(0.0)
    arb.say(Utterance("Turn 1, hard brake.", APPROACH, "approach", 1.0, 3.0, duration_s=1.5))
    arb.tick(1.0)
    assert [(s.text, s.cut) for s in voice.spoken] == [("Lap summary that goes on for a while.", True),
                                                       ("Turn 1, hard brake.", False)]
