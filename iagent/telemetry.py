import logging
import time
from datetime import datetime
from pathlib import Path
from types import NoneType

import irsdk
import pytz
from tinyflux import TinyFlux, Point

# Focus in on some specific telemetry items
TELEMETRY_KEYS = (
    "AirDensity", "AirPressure", "AirTemp", "Brake", "Throttle",
    "Clutch", "Gear", "IsOnTrack", "Lap", "LapBestLap",
    "LapDeltaToBestLap", "LRtempCL", "LRtempCM", "LRtempCR",
    "OilLevel", "OilPress", "OilTemp",
    "PlayerTireCompound", "RRtempCL", "RRtempCM", "RRtempCR", "IsInGarage", "LapDist",
    "LapDistPct", "Pitch", "PitchRate", "PlayerCarClass",
    "RelativeHumidity", "RPM", "Roll", "TrackTemp", "WaterTemp",
    "WindVel"
)

COLLECTION_RATE = 60

logger = logging.getLogger(__name__)


class TelemetryCollectionClient:
    def __init__(self, db_path: Path, ir: irsdk.IRSDK):
        assert db_path.suffix == ".csv"
        self.db = TinyFlux(db_path)
        self.ir = ir

    def insert_frame(self):
        self.ir.freeze_var_buffer_latest()
        data = {}
        for key in TELEMETRY_KEYS:
            val = self.ir[key]
            if isinstance(val, bool):
                val = int(val)
            data[key] = val
        point = Point(
            time=datetime.now(tz=pytz.utc),
            fields=data
        )
        self.db.insert(point)

    def collect(self):
        logger.info("Collecting telemetry")
        while True:
            self.insert_frame()
            time.sleep(1 / COLLECTION_RATE)
