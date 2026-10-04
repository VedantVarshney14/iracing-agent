"""Regression tests against real iRacing recordings in data/telemetry/ (git-ignored).

These are skipped when a file is absent. The oracle is iRacing's own `LapLastLapTime`: every lap
time the sim reported should be reproduced by one of our segmented laps.
"""

import glob
from pathlib import Path

import irsdk
import numpy as np
import pytest

from iagent.laps.segment import LapSegmenter
from iagent.laps.pace import representative
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.telemetry.ibt import IbtSource

DATA = Path(__file__).parents[2] / "data" / "telemetry"


def find(pattern: str) -> Path:
    matches = sorted(glob.glob(str(DATA / pattern)))
    if not matches:
        pytest.skip(f"no recording matching {pattern!r} in {DATA}")
    return Path(matches[0])


def sim_lap_times(path: Path) -> list[float]:
    """Lap times the sim itself reported (distinct positive `LapLastLapTime` values)."""
    ibt = irsdk.IBT()
    ibt.open(str(path))
    try:
        last = np.array(ibt.get_all("LapLastLapTime"))
    finally:
        ibt.close()
    changes = np.where(np.diff(last) != 0)[0] + 1
    return [round(float(last[i]), 3) for i in changes if last[i] > 0]


def segment(path: Path):
    source = IbtSource(path)
    seg = LapSegmenter(source.session)
    laps = [lap for f in source.frames() if (lap := seg.push(f)) is not None]
    tail = seg.flush()
    return laps + ([tail] if tail else [])


# (file pattern, sim lap times we know we don't reproduce, why)
CASES = [
    ("formulair04_okayama*", []),
    ("formulair04_spa*", []),
    ("ferrari296gt3_watkinsglen*", [125.1]),  # first lap after a pit exit: sim clock starts earlier
]


@pytest.mark.parametrize("pattern,known_misses", CASES)
def test_our_lap_times_reproduce_the_sims(pattern, known_misses):
    path = find(pattern)
    ours = [lap.lap_time for lap in segment(path) if lap.lap_time is not None]
    for expected in sim_lap_times(path):
        if any(abs(expected - m) < 0.1 for m in known_misses):
            continue
        # One frame at 60 Hz is 17 ms.
        assert min(abs(expected - t) for t in ours) < 0.03, f"sim reported {expected}, we have {ours}"


def test_okayama_pace_filter_picks_the_clean_laps(tmp_path):
    path = find("formulair04_okayama*")
    source = IbtSource(path)
    store = ParquetLapStore(tmp_path)
    try:
        records = record(source, store, "okayama")
        assert source.session.track_name == "Okayama International Circuit"
        assert source.session.car_name == "FIA F4"
        assert (source.session.track_key, source.session.car_key) == ("okayama-full", "formulair04")
        assert len(records) == 10
        # Laps 2, 3, 5, 6 are the clean ones (6 has a 4-frame kerb clip that cost nothing);
        # 1 is an out-lap, 4/7/8 are long excursions ~11-13% slower, 0/9 are partial.
        assert [r.seq for r in representative(records)] == [2, 3, 5, 6]
        assert records[6].off_track_s < 0.5
        assert min(records[i].off_track_s for i in (4, 7, 8)) > 3.0
    finally:
        store.close()


def test_spa_best_lap_is_kept_even_with_an_off_track_flag(tmp_path):
    path = find("formulair04_spa*")
    source = IbtSource(path)
    store = ParquetLapStore(tmp_path)
    try:
        records = record(source, store, "spa")
        assert source.session.track_name == "Circuit de Spa-Francorchamps"
        assert source.session.track_length_m == pytest.approx(6929, abs=2)
        assert source.session.track_id == 523
        assert (source.session.track_key, source.session.car_key) == ("spa-2024-up", "formulair04")
        kept = representative(records)
        assert [r.seq for r in kept] == [2, 5]  # 147.0 and 146.5; the 161/280 s laps are out
        assert min(r.lap_time for r in kept) == pytest.approx(146.472, abs=0.03)
    finally:
        store.close()


def test_watkins_glen_slow_and_untimed_laps_are_excluded(tmp_path):
    path = find("ferrari296gt3_watkinsglen*")
    store = ParquetLapStore(tmp_path)
    try:
        records = record(IbtSource(path), store, "wg")
        kept = {r.seq for r in representative(records)}
        assert {1, 2} & kept == set()  # 183 s (untimed by the sim) and 116 s (pit-exit lap)
        assert kept == set(range(3, 11))
    finally:
        store.close()
