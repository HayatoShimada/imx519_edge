import json
from datetime import datetime, timedelta, timezone

import pytest

from imx519_edge.camera import MAX_EXPOSURE_US
from imx519_edge.fake import open_fake_camera
from imx519_edge.sequence import exposure_for, run_burst
from imx519_edge.session import SCHEMA_VERSION, Product

NOW = datetime(2026, 10, 9, 14, 30, 5, tzinfo=timezone(timedelta(hours=9)))


def test_exposure_for_scales_by_ev_and_clamps():
    assert exposure_for(10_000, 0) == 10_000
    assert exposure_for(10_000, -2) == 2_500
    assert exposure_for(10_000, 2) == 40_000
    assert exposure_for(10, -2) == 100
    assert exposure_for(MAX_EXPOSURE_US, 2) == MAX_EXPOSURE_US


def test_run_burst_writes_frames_and_session(tmp_path):
    camera = open_fake_camera(auto_exposure_us=20_000, auto_gain=2.0, af_lens=1.5)
    session = run_burst(camera, tmp_path, [-2.0, 0.0, 2.0], 2, product=Product(42), now=NOW)

    stems = [s.stem for s in session.shots]
    assert stems == [
        "p00_ev-2.0_00", "p00_ev-2.0_01",
        "p00_ev+0.0_00", "p00_ev+0.0_01",
        "p00_ev+2.0_00", "p00_ev+2.0_01",
    ]  # fmt: skip
    for stem in stems:
        for ext in ("dng", "jpg", "json"):
            assert (tmp_path / f"{stem}.{ext}").exists()

    # 自動露出の明るさ（20ms × ゲイン 2）を、ゲイン 1.0 のシャッター時間にして EV で振る
    exposures = {s.ev: s.meta["ExposureTime"] for s in session.shots}
    assert exposures == {-2.0: 10_000, 0.0: 40_000, 2.0: 160_000}
    assert all(s.meta["AnalogueGain"] == 1.0 for s in session.shots)
    assert all(s.meta["LensPosition"] == 1.5 for s in session.shots)

    data = json.loads((tmp_path / "session.json").read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["session_id"] == "2026-10-09_143005_c42"
    assert data["created_at"] == "2026-10-09T14:30:05+09:00"
    assert data["product"]["cms_id"] == 42
    assert data["camera"]["base_exposure_us"] == pytest.approx(40_000)
    assert data["sequence"] == {
        "ev": [-2.0, 0.0, 2.0],
        "frames": 2,
        "positions": 1,
        "jitter_ticks": None,
    }
    assert len(data["shots"]) == 6


def test_manual_lens_position_skips_af(tmp_path):
    camera = open_fake_camera(af_lens=7.0)
    session = run_burst(camera, tmp_path, [0.0], 1, lens_position=0.5, now=NOW)
    assert session.camera["lens_position"] == 0.5
    assert session.product is None
    assert session.session_id == "2026-10-09_143005"
    assert not any(c.get("AfMode") == "auto" for c in camera.picam2.controls_log)
