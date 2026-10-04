"""Replay a recorded iRacing `.ibt` telemetry file as a `TelemetrySource`."""

import re
from pathlib import Path
from typing import Iterator, Sequence

import irsdk
import numpy as np

from iagent.telemetry.frames import CORE_CHANNELS, Frame
from iagent.telemetry.session import SessionInfo, parse_session_yaml, slug

_REQUIRED = ("SessionTime", "LapDistPct")


def _read_session_text(ibt: "irsdk.IBT") -> str:
    """The session-info YAML block. pyirsdk's IBT class doesn't expose it, so read it from the
    header offsets directly."""
    header = ibt._header
    start = header.session_info_offset
    raw = bytes(ibt._shared_mem[start : start + header.session_info_len])
    return raw.rstrip(b"\x00").decode("latin-1")


# iRacing names recordings "<car>_<track> <config> YYYY-MM-DD HH-MM-SS.ibt".
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})$")


def session_id_from_filename(path: Path) -> str:
    """A short, shell-safe session id: "20250723-202727" from iRacing's file name, otherwise a
    slug of the file stem. Lap ids build on it (e.g. "20250723-202727-L002")."""
    m = _STAMP.search(path.stem)
    if m:
        y, mo, d, h, mi, sec = m.groups()
        return f"{y}{mo}{d}-{h}{mi}{sec}"
    return slug(path.stem)


class IbtSource:
    """Yields the file's samples in order as fast as they can be read.

    Args:
        path: `.ibt` file.
        channels: channels to stream. Those missing from the file are skipped; `SessionTime` and
            `LapDistPct` are mandatory.
        session_id: label used in lap ids (defaults to one derived from the file name).
    """

    def __init__(
        self,
        path: Path | str,
        channels: Sequence[str] = CORE_CHANNELS,
        session_id: str | None = None,
    ):
        self._path = Path(path)
        if not self._path.is_file():
            raise FileNotFoundError(self._path)
        self._channels = tuple(channels)
        self._session_id = session_id or session_id_from_filename(self._path)
        self._session: SessionInfo | None = None

    @property
    def session(self) -> SessionInfo:
        if self._session is None:
            ibt = irsdk.IBT()
            ibt.open(str(self._path))
            try:
                self._session = parse_session_yaml(_read_session_text(ibt), self._session_id)
            finally:
                ibt.close()
        return self._session

    def frames(self) -> Iterator[Frame]:
        ibt = irsdk.IBT()
        ibt.open(str(self._path))
        try:
            available = set(ibt.var_headers_names or [])
            missing = [c for c in _REQUIRED if c not in available]
            if missing:
                raise ValueError(f"{self._path.name} lacks required channels: {missing}")
            names = [c for c in self._channels if c in available]
            columns = {n: np.asarray(ibt.get_all(n), dtype=float) for n in names}
        finally:
            ibt.close()

        n_samples = len(columns["SessionTime"])
        for i in range(n_samples):
            values = {n: float(col[i]) for n, col in columns.items()}
            yield Frame(session_time=values["SessionTime"], values=values)
