"""Run a telemetry source through segmentation and into a lap store."""

import logging

from iagent.laps.segment import LapSegmenter
from iagent.laps.store import LapRecord, LapStore
from iagent.telemetry.source import TelemetrySource

logger = logging.getLogger(__name__)


def record(source: TelemetrySource, store: LapStore, source_label: str) -> list[LapRecord]:
    """Consume the whole source (as fast as it yields) and save every lap, including flagged ones."""
    segmenter = LapSegmenter(source.session)
    records: list[LapRecord] = []

    for frame in source.frames():
        lap = segmenter.push(frame)
        if lap is not None:
            records.append(store.save(lap, source_label))
    tail = segmenter.flush()
    if tail is not None:
        records.append(store.save(tail, source_label))

    logger.info("Recorded %d laps (%d valid)", len(records), sum(r.valid for r in records))
    return records
