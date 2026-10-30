import numpy as np

from iagent.live.radio import HIGH_HZ, LEVEL, crackle, key_down, key_up


def test_a_crackle_is_short_and_fades_out():
    c = crackle(24_000, 0.15)
    assert c.dtype == np.float32 and len(c) == 3600
    assert np.isclose(np.abs(c).max(), LEVEL, atol=1e-6)
    assert abs(c[-1]) < 0.01  # no click as it stops
    assert np.array_equal(crackle(24_000, 0.15), c)  # the same every time


def test_a_crackle_is_low_not_a_hiss():
    c = crackle(24_000, 1.0)
    power = np.abs(np.fft.rfft(c)) ** 2
    hz = np.fft.rfftfreq(len(c), 1 / 24_000)
    assert power[hz > 2 * HIGH_HZ].sum() < 0.1 * power.sum()


def test_key_up_and_key_down_differ():
    up, down = key_up(16_000), key_down(16_000)
    assert len(up) > len(down) and not np.array_equal(up[: len(down)], down)
