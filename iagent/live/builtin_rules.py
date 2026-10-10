"""The coach's own behaviour, as rules: what it says, on which event, and when.

The components decide what's worth saying (the cue that's wanted, the feedback for a corner, the
summary's words) and put it on the event; these rules say it. They're ordinary rules in the
`coach` group (no shared line budget, not reported in the log), so a new built-in behaviour is a
new rule here, in the same format the agent uses.
"""

from iagent.live.settings import Settings


def builtin_rules(s: Settings) -> list[dict]:
    lap_wait = "0.6 * (ref_lap_time or lap_time or 60)"  # wait for a straight with room, up to most of a lap
    return [{**r, "group": "coach"} for r in [
        {"id": "coach-cue", "description": "The corner cue on the approach (if it's wanted).",
         "when": {"event": "approach", "source": "cue"}, "if": "wanted",
         "action": {"say": "{text}", "priority": "cue", "kind": "approach"}},
        {"id": "coach-feedback", "description": "What went wrong in a corner (or that it's better), on the straight after.",
         "when": {"event": "corner_exit"}, "if": "feedback",
         "action": {"say": "{feedback}", "long": "{feedback_long}", "priority": "feedback", "kind": "feedback",
                    "expires_s": s.feedback_expires_s},
         "limits": {"max_per_lap": s.feedback_per_lap}},
        {"id": "coach-summary", "description": "The lap summary at the line.", "enabled_by": "summary",
         "when": {"event": "lap"}, "if": "summary_text and pace != 'tranquille'",
         "action": {"say": "{summary_text}", "priority": "summary", "kind": "summary", "expires_s": lap_wait}},
        {"id": "coach-focus", "description": "What changed about the focus, at the line.", "pushing_only": False,
         "when": {"event": "lap"}, "if": "focus_news",
         "action": {"say": "{focus_news}", "priority": "feedback", "kind": "focus", "expires_s": lap_wait}},
        {"id": "coach-debrief", "description": "The cool-down debrief, a piece at a time.", "pushing_only": False,
         "when": {"event": "debrief_line"},
         "action": {"say": "{text}", "priority": "feedback", "kind": "debrief", "expires_s": 90}},
        {"id": "coach-say", "description": "Something to say (an answer to the driver).", "pushing_only": False,
         "when": {"event": "say"},
         "action": {"say": "{text}", "priority": "answer", "kind": "{kind}", "expires_s": 90}},
        {"id": "coach-words", "description": "The coach's reply when a rule woke it.", "pushing_only": False,
         "when": {"event": "coach_words"}, "if": "text",
         "action": {"say": "{text}", "priority": "answer", "kind": "coach", "expires_s": 90}},
    ]]

