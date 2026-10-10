"""The live coach's components, in pipeline order.

Order matters only where one component reads what another puts on an event or in the state in
the same step: line crossings are handled before the rest of the frame, track state before pace,
the focus before the summary, the rules after everything that enriches events, speech last.
A new component goes in `COMPONENTS`.
"""

from iagent.live.components.laps import Laps
from iagent.live.components.track import Track
from iagent.live.components.clock import Clock
from iagent.live.components.pace import Pace
from iagent.live.components.corners import CornerResult, Corners
from iagent.live.components.positions import Positions
from iagent.live.components.cue_caller import CueCaller
from iagent.live.components.focus import Focus
from iagent.live.components.history import History
from iagent.live.components.summary import Summary
from iagent.live.components.debrief import Debrief
from iagent.live.components.speaking import GATES, Gate, Speaking
from iagent.live.narrator import Radio

COMPONENTS = [Laps, Track, Clock, Pace, Corners, Positions, CueCaller, Focus, History, Summary, Debrief, Radio]
# The rule engine (iagent.live.rules) comes after these, and Speaking last.

__all__ = ["COMPONENTS", "CornerResult", "CueCaller", "Corners", "Debrief", "Focus", "GATES", "Gate", "History",
           "Laps", "Pace", "Positions", "Radio", "Speaking", "Summary", "Track", "Clock"]
