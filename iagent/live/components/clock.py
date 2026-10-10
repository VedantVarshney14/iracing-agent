"""The session's clock: its start, and a tick a second (schedules are rules on `clock`)."""

from iagent.live.pipeline import Component


class Clock(Component):
    def start(self):
        self._start: float | None = None
        self._second = -1
        self.state["since_start_s"] = 0.0

    def on_frame(self, e):
        if self._start is None:
            self._start = e.at
            self.emit("session_start")
        since = e.at - self._start
        self.state["since_start_s"] = since
        if int(since) != self._second:
            self._second = int(since)
            self.emit("clock", since_start_s=self._second)
