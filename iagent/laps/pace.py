"""Which laps are representative? Anything close to the best valid lap is real."""

from typing import Sequence

from iagent.laps.store import LapRecord

DEFAULT_WITHIN = 0.05  # 5% of the best lap

Group = tuple[str, str]  # (track_key, car_key)


def group_of(record: LapRecord) -> Group:
    return (record.track_key, record.car_key)


def best_times(records: Sequence[LapRecord]) -> dict[Group, float]:
    """Best valid lap time per track and car. Laps from different groups are never compared."""
    best: dict[Group, float] = {}
    for r in records:
        if r.valid and r.lap_time is not None:
            key = group_of(r)
            best[key] = min(best.get(key, r.lap_time), r.lap_time)
    return best


def best_time(records: Sequence[LapRecord]) -> float | None:
    """Best valid lap time, for records from a single track and car."""
    times = best_times(records)
    if len(times) > 1:
        raise ValueError(f"records span several track/car groups: {sorted(times)}")
    return next(iter(times.values()), None)


def representative(records: Sequence[LapRecord], within: float = DEFAULT_WITHIN) -> list[LapRecord]:
    """Valid laps whose time is within `within` (a fraction) of the best valid lap for the same
    track and car.

    Slow laps (spins, recoveries, traffic, cool-downs) fall outside; a brief kerb clip that costs
    no time stays in.
    """
    best = best_times(records)
    return [
        r for r in records
        if r.valid and r.lap_time is not None and r.lap_time <= best[group_of(r)] * (1.0 + within)
    ]
