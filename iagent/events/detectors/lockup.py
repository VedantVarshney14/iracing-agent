from typing import Optional

import irsdk

from iagent.events.detectors.base import DetectorBase
from iagent.events.events import EventStamp, Event, EventPriority


class LockUpDetector(DetectorBase):
    def __init__(
            self,
            slip_threshold: float = 0.95,
            min_speed_threshold: float = 20.0,  # km/h
            min_duration_frames: int = 5
    ):
        """
        Initialize lock up detector

        Args:
            slip_threshold: Slip ratio threshold to detect lock up (0.95 = 95% slip)
            min_speed_threshold: Minimum speed in km/h to detect lock-ups
            min_duration_frames: Minimum frames a wheel must be locked to count as lock up
        """
        self.slip_threshold = slip_threshold
        self.min_speed_threshold = min_speed_threshold
        self.min_duration_frames = min_duration_frames

        self._is_in_lock: bool = False

    def detect(self, ir: irsdk.IRSDK) -> Optional[EventStamp]:
        """
        Approximate detection of *new* lock-up events.
        """
        vehicle_speed = ir["Speed"] * 3.6  # km/h
        brake_input = ir["Brake"]
        longitudinal_accel = ir["LongAccel"]

        event = EventStamp(
            session_tick=ir["SessionTick"],
            lap_dist=ir["LapDist"],
            event=Event.POSSIBLE_LOCK_UP,
            priority=EventPriority.LOW,
        )

        # Only detect during significant braking and above minimum speed
        if brake_input < 0.3 or vehicle_speed < self.min_speed_threshold:
            self._is_in_lock = False
            return None

        if self._is_in_lock:
            return None

        # Look for signs of lock up:
        # 1. High brake input with low deceleration (wheels not gripping)
        # 2. Sudden changes in deceleration during braking

        expected_decel = brake_input * 12.0  # Rough approximation (m/s²)
        actual_decel = abs(longitudinal_accel)

        # If actual deceleration is significantly less than expected, possible lock up
        decel_ratio = actual_decel / max(expected_decel, 0.1)

        if decel_ratio < 0.6 and brake_input > 0.7:  # Strong braking but poor deceleration
            self._is_in_lock = True
            return event

        return None
