from datetime import datetime

import pydantic
from pydantic import Field


class Lap(pydantic.BaseModel):
    id: str
    car_id: int
    car_name: str
    track_id: int
    track_name: str
    track_variant: str | None = None
    track_name: str
    driver_slug: str
    driverRating: float
    event: str
    session: int
    sessionType: int
    run: int
    startTime: datetime
    lapNumber: int
    lapTime: float
    clean: bool
    joker: bool
    discontinuity: bool
    missing: bool
    incomplete: bool
    offtrack: bool
    pitlane: bool
    pitIn: bool
    pitOut: bool
    trackTemp: float = Field(None, alias="track_temp")
    trackUsage: float = Field(None, alias="track_usage")
    trackWetness: float = Field(None, alias="track_wetness")
    airTemp: float = Field(None, alias="air_temp")
    clouds: int = None
    airDensity: float = Field(None, alias="air_density")
    airPressure: float = Field(None, alias="air_pressure")
    windVel: float = Field(None, alias="wind_vel")
    windDir: float = Field(None, alias="wind_dir")
    relativeHumidity: float = Field(None, alias="relative_humidity")
    fogLevel: float = Field(None, alias="fog_level")
    precipitation: float = None
    sectorTimes: list[float] | None = None  # Assuming sectors is a list of floats or None
    fuelLevel: float | None = None  # Assuming fuelLevel can be None
    fuelUsed: float | None = None  # Assuming fuelUsed can be None
    fuelAdded: float | None = None  # Assuming fuelAdded can be None
    tireCompound: int | None = None  # Assuming tireCompound can be None
    ghostAvailable: bool | None = None  # Assuming ghostAvailable can be None
    canViewTelemetry: bool | None = Field(None, alias="can_view_telemetry")
    canViewSetup: bool | None = Field(None, alias="can_view_setup")
