"""The session's clock: its start, and a tick a second (schedules are rules on `clock`)."""

from iagent.live.events import Clock as Tick
from iagent.live.events import Frame, SessionStart
from iagent.live.pipeline import Component, on


class Clock(Component):
    def start(self):
        self._start: float | None = None
        self._second = -1

    @on(Frame)
    def tick(self, e: Frame):
        if self._start is None:
            self._start = e.at
            self.emit(SessionStart())
        since = e.at - self._start
        self.state.since_start_s = since
        if int(since) != self._second:
            self._second = int(since)
            self.emit(Tick(since_start_s=self._second))
