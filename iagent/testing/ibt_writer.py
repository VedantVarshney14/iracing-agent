"""Write a minimal but format-faithful `.ibt` file, so the real reader can be tested without a
recording from the sim. Layout follows the iRacing SDK: header, disk sub-header, variable
headers, session-info YAML, then fixed-size sample records.
"""

import struct
from pathlib import Path
from typing import Iterable

from iagent.telemetry.frames import CORE_CHANNELS, Frame
from iagent.telemetry.session import SessionInfo

# irsdk type ids and their struct codes/sizes.
_TYPES = {"?": (1, 1), "i": (2, 4), "f": (4, 4), "d": (5, 8)}
_CHANNEL_CODES = {
    "SessionTime": "d",
    "Lat": "d",
    "Lon": "d",
    "SessionTick": "i",
    "Lap": "i",
    "Gear": "i",
    "PlayerTrackSurface": "i",
    "OnPitRoad": "?",
    "IsOnTrack": "?",
}
_HEADER_LEN = 144  # main header (48) + 4 buffer slots (64) + disk sub-header (32)
_VAR_HEADER_LEN = 144


def session_yaml(session: SessionInfo) -> str:
    return (
        "---\n"
        "WeekendInfo:\n"
        f" TrackID: {session.track_id or 0}\n"
        f" TrackName: {session.track_code or session.track_key}\n"
        f" TrackDisplayName: {session.track_name}\n"
        f" TrackConfigName: {session.track_config or 'Full'}\n"
        f" TrackLength: {session.track_length_m / 1000.0:.3f} km\n"
        "DriverInfo:\n"
        " DriverCarIdx: 0\n"
        " Drivers:\n"
        " - CarIdx: 0\n"
        "   UserName: Test Driver\n"
        f"   CarScreenName: {session.car_name}\n"
        f"   CarPath: {session.car_path or session.car_key}\n"
        f"   CarID: {session.car_id or 0}\n"
        " - CarIdx: 1\n"
        "   UserName: Someone Else\n"
        "   CarScreenName: Other Car\n"
        "   CarPath: othercar\n"
        "...\n"
    )


def write_ibt(
    path: Path | str,
    session: SessionInfo,
    frames: Iterable[Frame],
    channels: Iterable[str] = CORE_CHANNELS,
    tick_rate: int = 60,
) -> Path:
    frames = list(frames)
    names = [c for c in channels if c in frames[0].values]
    codes = [_CHANNEL_CODES.get(n, "f") for n in names]

    offsets, offset = [], 0
    for code in codes:
        offsets.append(offset)
        offset += _TYPES[code][1]
    buf_len = offset

    var_header_offset = _HEADER_LEN
    session_offset = var_header_offset + len(names) * _VAR_HEADER_LEN
    yaml_bytes = session_yaml(session).encode("latin-1")
    data_offset = session_offset + len(yaml_bytes)

    out = bytearray(data_offset)
    # Main header.
    struct.pack_into(
        "9i", out, 0,
        2, 1, tick_rate, 1, len(yaml_bytes), session_offset, len(names), var_header_offset, 1,
    )
    struct.pack_into("i", out, 36, buf_len)
    struct.pack_into("2i", out, 48, 0, data_offset)  # var_buf[0]: tick_count, buf_offset
    # Disk sub-header at 112.
    struct.pack_into("Qddii", out, 112, 0, frames[0].session_time, frames[-1].session_time, 0, len(frames))
    # Variable headers.
    for i, (name, code) in enumerate(zip(names, codes)):
        struct.pack_into(
            "iii?x", out, var_header_offset + i * _VAR_HEADER_LEN,
            _TYPES[code][0], offsets[i], 1, False,
        )
        struct.pack_into("32s", out, var_header_offset + i * _VAR_HEADER_LEN + 16, name.encode())
    out[session_offset:data_offset] = yaml_bytes

    body = bytearray()
    fmt = "<" + "".join(codes)
    for f in frames:
        row = [bool(f.values[n]) if c == "?" else int(f.values[n]) if c == "i" else float(f.values[n])
               for n, c in zip(names, codes)]
        body += struct.pack(fmt, *row)
    Path(path).write_bytes(bytes(out) + bytes(body))
    return Path(path)
