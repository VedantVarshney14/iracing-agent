import uuid
from datetime import datetime

import pytz
from sqlalchemy import Column, Float, Integer, Boolean, TIMESTAMP, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Telemetry(Base):
    __tablename__ = "telemetry"

    telemetryID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    time = Column(
        TIMESTAMP, default=datetime.now(pytz.utc)
    )

    # All telemetry items are camelcase by convention
    AirDensity = Column(Float)
    AirPressure = Column(Float)
    AirTemp = Column(Float)
    Brake = Column(Float)
    Throttle = Column(Float)
    Clutch = Column(Float)
    Gear = Column(Integer)
    IsOnTrack = Column(Boolean)
    Lap = Column(Integer)
    LapBestLap = Column(Integer)
    LapDeltaToBestLap = Column(Float)
    LRtempCL = Column(Float)
    LRtempCM = Column(Float)
    LRtempCR = Column(Float)
    OilLevel = Column(Float)
    OilPress = Column(Float)
    OilTemp = Column(Float)
    PlayerTireCompound = Column(Integer)
    RRtempCL = Column(Float)
    RRtempCM = Column(Float)
    RRtempCR = Column(Float)
    IsInGarage = Column(Boolean)
    LapDist = Column(Float)
    LapDistPct = Column(Float)
    Pitch = Column(Float)
    PitchRate = Column(Float)
    PlayerCarClass = Column(Integer)
    RelativeHumidity = Column(Float)
    RPM = Column(Float)
    Roll = Column(Float)
    TrackTemp = Column(Float)
    WaterTemp = Column(Float)
    WindVel = Column(Float)
