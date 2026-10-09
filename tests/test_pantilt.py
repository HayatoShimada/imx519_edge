import random

from imx519_edge.config import PanTiltConfig
from imx519_edge.pantilt import ALL_LED_OFF_H, FULL_OFF, LED0_ON_L, PCA9685, FakeBus, PanTilt


def make(**overrides):
    bus = FakeBus()
    pt = PanTilt(PCA9685(bus), PanTiltConfig(enabled=True, **overrides), sleep=lambda s: None)
    bus.writes.clear()
    return bus, pt


def test_prescale_for_50hz():
    bus = FakeBus()
    PCA9685(bus, freq_hz=50)
    assert ("byte", 0xFE, 121) in bus.writes  # round(25e6 / (4096 * 50)) - 1


def test_move_clamps_and_releases():
    bus, pt = make(pan_min=200, pan_max=400)
    result = pt.move(1000, 300)
    assert result["pan"] == 400 and pt.pan == 400
    # パン（ch0）とチルト（ch1）に指令を送ったあと、両方を full-off にする
    assert ("block", LED0_ON_L, [0, 0, 400 & 0xFF, 400 >> 8]) in bus.writes
    assert bus.writes[-2:] == [("byte", LED0_ON_L + 3, FULL_OFF), ("byte", LED0_ON_L + 7, FULL_OFF)]


def test_approach_from_below_overshoots_first():
    bus, pt = make(approach_ticks=10)
    pt.move(300, 300, approach="+")
    pans = [w[2][2] | (w[2][3] << 8) for w in bus.writes if w[0] == "block" and w[1] == LED0_ON_L]
    assert pans == [290, 300]


def test_jitter_returns_to_target():
    bus, pt = make()
    result = pt.jitter(300, 310, (5, 30), random.Random(1))
    assert (result["pan"], result["tilt"]) == (300, 310)
    dp, dt = result["offset"]
    assert 5 <= abs(dp) <= 30 and 5 <= abs(dt) <= 30
    assert result["approach"] == ("-" if dp > 0 else "+")


def test_stop_turns_all_channels_off():
    bus, pt = make()
    pt.stop()
    assert bus.writes == [("byte", ALL_LED_OFF_H, FULL_OFF)]
