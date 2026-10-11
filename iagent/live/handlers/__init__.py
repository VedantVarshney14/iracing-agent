"""The live coach's event handlers, in pipeline order.

Order matters only where one event handler reads what another puts on an event or in the state in
the same step: line crossings are handled before the rest of the frame, track state before pace,
the focus before the summary, the rules after everything that enriches events, speech last.
A new event handler goes in `HANDLERS`.
"""

from iagent.live.handlers.laps import Laps
from iagent.live.handlers.track import Track
from iagent.live.handlers.clock import Clock
from iagent.live.handlers.pace import Pace
from iagent.live.handlers.corners import CornerResult, Corners
from iagent.live.handlers.positions import Positions
from iagent.live.handlers.cue_caller import CueCaller
from iagent.live.handlers.focus import Focus
from iagent.live.handlers.history import History
from iagent.live.handlers.summary import Summary
from iagent.live.handlers.debrief import Debrief
from iagent.live.handlers.speaking import GATES, Gate, Speaking
from iagent.live.handlers.engineer_link import EngineerLink

HANDLERS = [Laps, Track, Clock, Pace, Corners, Positions, CueCaller, Focus, History, Summary, Debrief, EngineerLink]
# The rule engine (iagent.live.rules) comes after these, and Speaking last.

__all__ = ["HANDLERS", "CornerResult", "CueCaller", "Corners", "Debrief", "Focus", "GATES", "Gate", "History",
           "EngineerLink", "Laps", "Pace", "Positions", "Speaking", "Summary", "Track", "Clock"]
