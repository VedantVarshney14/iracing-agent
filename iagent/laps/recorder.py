"""Run a telemetry source through segmentation and into a lap store."""

import logging

from iagent.laps.lap import Lap
from iagent.laps.segment import LapSegmenter
from iagent.laps.store import LapStore
from iagent.telemetry.source import TelemetrySource

logger = logging.getLogger(__name__)


def record(source: TelemetrySource, store: LapStore, source_label: str) -> list[Lap]:
    """Consume the whole source (as fast as it yields) and save every lap, including flagged ones.
    Returns the saved laps without their samples."""
    segmenter = LapSegmenter(source.session, source=source_label)
    laps: list[Lap] = []

    for frame in source.frames():
        lap = segmenter.push(frame)
        if lap is not None:
            laps.append(store.save(lap))
    tail = segmenter.flush()
    if tail is not None:
        laps.append(store.save(tail))

    logger.info("Recorded %d laps (%d valid)", len(laps), sum(lap.valid for lap in laps))
    return laps
