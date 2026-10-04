"""Which laps are representative? Anything close to the best valid lap is real."""

from typing import Sequence

from iagent.brain.store import LapRecord

DEFAULT_WITHIN = 0.05  # 5% of the best lap


def best_time(records: Sequence[LapRecord]) -> float | None:
    times = [r.lap_time for r in records if r.valid and r.lap_time is not None]
    return min(times) if times else None


def representative(records: Sequence[LapRecord], within: float = DEFAULT_WITHIN) -> list[LapRecord]:
    """Valid laps whose time is within `within` (a fraction) of the best valid lap.

    Slow laps (spins, recoveries, traffic, cool-downs) fall outside; a brief kerb clip that costs
    no time stays in. Compare like with like: pass records for one track and car.
    """
    best = best_time(records)
    if best is None:
        return []
    limit = best * (1.0 + within)
    return [r for r in records if r.valid and r.lap_time is not None and r.lap_time <= limit]
