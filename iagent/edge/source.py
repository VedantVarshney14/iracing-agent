"""The telemetry seam: everything downstream reads frames from a `TelemetrySource`."""

from typing import Iterator, Protocol

from iagent.common.frames import Frame
from iagent.common.session import SessionInfo


class TelemetrySource(Protocol):
    """A finite or endless stream of frames plus the session it belongs to.

    Implementations: `IbtSource` (recorded file), `SyntheticSource` (generated laps), and later a
    live irsdk source on the sim PC.
    """

    @property
    def session(self) -> SessionInfo: ...

    def frames(self) -> Iterator[Frame]: ...
