"""露出ブラケット × 連写で、合成用のフレームを撮る（Raspberry Pi 上で実行する）。

最初に AE / AWB / AF を一度だけ走らせて値を読み、そのあと全部固定して撮る。
ゲインは 1.0 に固定し、自動露出が決めた明るさをシャッター時間だけで出す（ノイズを最小にするため）。
各フレームを DNG（RAW）・JPEG（ISP 出力）・メタデータ JSON で保存する。

usage (85pi):
    python3 capture_burst.py --out ~/captures/test1 --frames 8 --ev -2 0 2
"""

import argparse
import json
import time
from pathlib import Path

from libcamera import Transform, controls
from picamera2 import Picamera2

META_KEYS = [
    "ExposureTime",
    "AnalogueGain",
    "DigitalGain",
    "ColourGains",
    "ColourTemperature",
    "LensPosition",
    "AfState",
    "Lux",
    "FocusFoM",
    "SensorTimestamp",
    "SensorTemperature",
]
MAX_EXPOSURE_US = 4_000_000  # 1 枚あたりの上限（静物なので長くてよいが、待ち時間が延びすぎないように）


def wait_for(picam2: Picamera2, exposure_us: int, tol: float = 0.02, max_frames: int = 30) -> None:
    """設定した露出がフレームに反映されるまで読み捨てる（設定は数フレーム遅れて効く）。"""
    for _ in range(max_frames):
        md = picam2.capture_metadata()
        if abs(md["ExposureTime"] - exposure_us) <= max(tol * exposure_us, 100):
            return
    raise RuntimeError(f"露出 {exposure_us}us が反映されませんでした（最後: {md['ExposureTime']}us）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--frames", type=int, default=8, help="露出ごとの枚数")
    ap.add_argument("--ev", type=float, nargs="+", default=[-2.0, 0.0, 2.0])
    ap.add_argument("--rotate180", action="store_true", help="カメラが逆さに付いているとき")
    ap.add_argument("--lens-position", type=float, default=None, help="指定すると AF しない")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"size": picam2.sensor_resolution, "format": "RGB888"},
        raw={"size": picam2.sensor_resolution},
        buffer_count=2,
        transform=Transform(hflip=1, vflip=1) if args.rotate180 else Transform(),
    )
    picam2.configure(config)
    # 長秒露光を許すため、フレーム時間の上限を広げておく
    picam2.set_controls({"FrameDurationLimits": (33333, MAX_EXPOSURE_US + 100_000)})
    picam2.start()
    time.sleep(2)  # AE / AWB を落ち着かせる

    if args.lens_position is None:
        picam2.set_controls({"AfMode": controls.AfModeEnum.Auto})
        if not picam2.autofocus_cycle():
            print("警告: AF が合焦しませんでした")
    md = picam2.capture_metadata()
    lens = args.lens_position if args.lens_position is not None else md["LensPosition"]
    gains = md["ColourGains"]
    # 自動露出の明るさ（露光時間 × ゲイン）を、ゲイン 1.0 のシャッター時間に換算する
    base_us = md["ExposureTime"] * md["AnalogueGain"] * md.get("DigitalGain", 1.0)
    print(f"auto: exposure={md['ExposureTime']}us gain={md['AnalogueGain']:.2f} "
          f"lens={lens:.2f} gains={gains} -> base {base_us:.0f}us @gain1.0")

    picam2.set_controls({
        "AeEnable": False,
        "AwbEnable": False,
        "ColourGains": gains,
        "AnalogueGain": 1.0,
        "AfMode": controls.AfModeEnum.Manual,
        "LensPosition": lens,
        "NoiseReductionMode": 0,  # Off（ISP の JPEG にだけ効く。RAW には影響しない）
    })

    session = {"lens_position": lens, "colour_gains": gains, "base_exposure_us": base_us,
               "ev": args.ev, "frames": args.frames, "rotate180": args.rotate180, "shots": []}
    for ev in args.ev:
        exposure = int(min(max(base_us * 2**ev, 100), MAX_EXPOSURE_US))
        picam2.set_controls({"ExposureTime": exposure})
        wait_for(picam2, exposure)
        for i in range(args.frames):
            stem = f"ev{ev:+.1f}_{i:02d}"
            request = picam2.capture_request()
            try:
                request.save("main", str(args.out / f"{stem}.jpg"))
                request.save_dng(str(args.out / f"{stem}.dng"))
                meta = {k: request.get_metadata().get(k) for k in META_KEYS}
            finally:
                request.release()
            (args.out / f"{stem}.json").write_text(json.dumps(meta, indent=1))
            session["shots"].append({"stem": stem, "ev": ev, **meta})
            print(f"{stem}: exposure={meta['ExposureTime']}us gain={meta['AnalogueGain']:.2f}")

    picam2.stop()
    (args.out / "session.json").write_text(json.dumps(session, indent=1))
    print(f"done: {len(session['shots'])} frames -> {args.out}")


if __name__ == "__main__":
    main()
