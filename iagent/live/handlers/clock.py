"""The session's clock: its start, and a tick a second (schedules are rules on `tick`)."""

from iagent.live.events import Frame, SessionStart, Tick
from iagent.live.pipeline import EventHandler, on


class Clock(EventHandler):
    """Emits `SessionStart` on the first frame, then a `Tick` each whole second."""

    def start(self):
        self._start: float | None = None
        self._second = -1

    @on(Frame)
    def tick(self, e: Frame):
        """Keep `since_start_s` up to date; start the session, and tick each second."""
        if self._start is None:
            self._start = e.at
            self.emit(SessionStart())
        since = e.at - self._start
        self.state.since_start_s = since
        if int(since) != self._second:
            self._second = int(since)
            self.emit(Tick(since_start_s=self._second))
