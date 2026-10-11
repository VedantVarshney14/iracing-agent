"""The coach's own behaviour, as rules: what it says, on which event, and when.

The event handlers decide what's worth saying (the cue that's wanted, the feedback for a corner, the
summary's words) and put it on the event; these rules say it. They're ordinary rules in the
`builtin` group (no shared line budget, not reported in the log), so a new built-in behaviour is a
new rule here, in the same format the agent uses.
"""

from iagent.live.settings import Settings


def builtin_rules(s: Settings) -> list[dict]:
    lap_wait = "0.6 * (ref_lap_time or lap_time or 60)"  # wait for a straight with room, up to most of a lap
    return [{**r, "group": "builtin"} for r in [
        {"id": "builtin-cue", "description": "The corner cue on the approach (if it's wanted).",
         "when": {"event": "approach", "source": "cue"}, "if": "wanted",
         "actions": [{"say": "{text}", "priority": "cue", "kind": "approach"}]},
        {"id": "builtin-feedback", "description": "What went wrong in a corner (or that it's better), on the straight after.",
         "when": {"event": "corner_exit"}, "if": "feedback",
         "actions": [{"say": "{feedback}", "long": "{feedback_long}", "priority": "feedback", "kind": "feedback",
                     "expires_s": s.feedback_expires_s}],
         "limits": {"max_per_lap": s.feedback_per_lap}},
        {"id": "builtin-summary", "description": "The lap summary at the line.", "enabled_by": "summary",
         "when": {"event": "lap"}, "if": "summary_text and pace != 'tranquille'",
         "actions": [{"say": "{summary_text}", "priority": "summary", "kind": "summary", "expires_s": lap_wait}]},
        {"id": "builtin-focus", "description": "What changed about the focus, at the line.", "pushing_only": False,
         "when": {"event": "lap"}, "if": "focus_news",
         "actions": [{"say": "{focus_news}", "priority": "feedback", "kind": "focus", "expires_s": lap_wait}]},
        {"id": "builtin-debrief", "description": "The cool-down debrief, a piece at a time.", "pushing_only": False,
         "when": {"event": "debrief_line"},
         "actions": [{"say": "{text}", "priority": "feedback", "kind": "debrief", "expires_s": 90}]},
        {"id": "builtin-say", "description": "Something to say (an answer to the driver).", "pushing_only": False,
         "when": {"event": "say"},
         "actions": [{"say": "{text}", "priority": "answer", "kind": "{kind}", "expires_s": 90}]},
        {"id": "builtin-engineer", "description": "The engineer's words: the radio check, answers to the driver and to rules.",
         "pushing_only": False, "when": {"event": "engineer_reply", "kind": ["briefing", "question", "rule"]}, "if": "text",
         "actions": [{"say": "{text}", "priority": "answer", "kind": "engineer", "expires_s": 90}]},
    ]]

