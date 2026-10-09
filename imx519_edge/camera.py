"""Picamera2 の操作（設定、測光と固定、1 フレームの保存）。

picamera2 と libcamera は Raspberry Pi OS の apt のものを使う。ほかのマシンでも import できるよう、
読むのは Camera.open() のときだけにする（テストは fake.py の偽のカメラを使う）。
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

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
# 1 枚あたりの露光の上限（静物なので長くてよいが、待ち時間が延びすぎないように）
MAX_EXPOSURE_US = 4_000_000


@dataclass
class Metered:
    """AE / AWB / AF を一度だけ走らせて読んだ値。撮影中はこれで固定する。"""

    lens_position: float
    colour_gains: tuple[float, float]
    base_exposure_us: float  # 自動露出の明るさを、ゲイン 1.0 のシャッター時間に換算したもの


class Camera:
    def __init__(
        self,
        picam2,
        af_auto,
        af_manual,
        af_continuous=None,
        rotate180: bool = False,
        preview_size: tuple[int, int] = (640, 480),
    ):
        self.picam2 = picam2
        self.af_auto, self.af_manual = af_auto, af_manual
        self.af_continuous = af_continuous if af_continuous is not None else af_auto
        self.rotate180 = rotate180
        self.preview_size = preview_size

    @classmethod
    def open(cls, rotate180: bool = False, preview_size: tuple[int, int] = (640, 480)) -> "Camera":
        from libcamera import Transform, controls
        from picamera2 import Picamera2

        picam2 = Picamera2()
        transform = Transform(hflip=1, vflip=1) if rotate180 else Transform()
        af = controls.AfModeEnum
        camera = cls(picam2, af.Auto, af.Manual, af.Continuous, rotate180, preview_size)
        camera.start(transform)
        return camera

    def start(self, transform=None) -> None:
        # フル解像度のモードのまま、ライブビュー用の小さな lores ストリームも出す
        # （撮るたびに設定し直さない）
        picam2 = self.picam2
        config = picam2.create_still_configuration(
            main={"size": picam2.sensor_resolution, "format": "RGB888"},
            lores={"size": self.preview_size, "format": "YUV420"},
            raw={"size": picam2.sensor_resolution},
            buffer_count=2,
            **({"transform": transform} if transform is not None else {}),
        )
        picam2.configure(config)
        # 長秒露光を許すため、フレーム時間の上限を広げておく
        picam2.set_controls({"FrameDurationLimits": (33333, MAX_EXPOSURE_US + 100_000)})
        picam2.start()

    def close(self) -> None:
        self.picam2.stop()

    def meter(self, lens_position: float | None = None, settle_s: float = 2.0) -> Metered:
        """AE / AWB を落ち着かせ、lens_position が無ければ AF も走らせて、その値を読む。"""
        time.sleep(settle_s)
        if lens_position is None:
            self.picam2.set_controls({"AfMode": self.af_auto})
            if not self.picam2.autofocus_cycle():
                print("警告: AF が合焦しませんでした")
        md = self.picam2.capture_metadata()
        lens = lens_position if lens_position is not None else md["LensPosition"]
        base_us = md["ExposureTime"] * md["AnalogueGain"] * md.get("DigitalGain", 1.0)
        print(
            f"auto: exposure={md['ExposureTime']}us gain={md['AnalogueGain']:.2f} "
            f"lens={lens:.2f} gains={md['ColourGains']} -> base {base_us:.0f}us @gain1.0"
        )
        return Metered(lens, tuple(md["ColourGains"]), base_us)

    def lock(self, metered: Metered) -> None:
        """自動制御をすべて止め、ゲイン 1.0 で固定する（明るさはシャッター時間だけで変える）。"""
        self.picam2.set_controls(
            {
                "AeEnable": False,
                "AwbEnable": False,
                "ColourGains": metered.colour_gains,
                "AnalogueGain": 1.0,
                "ExposureTime": int(min(metered.base_exposure_us, MAX_EXPOSURE_US)),
                "AfMode": self.af_manual,
                "LensPosition": metered.lens_position,
                "NoiseReductionMode": 0,  # Off（ISP の JPEG にだけ効く。RAW には影響しない）
            }
        )

    def unlock(self) -> None:
        """自動制御に戻す（ライブビューで構図を見るとき）。"""
        self.picam2.set_controls(
            {"AeEnable": True, "AwbEnable": True, "AfMode": self.af_continuous}
        )

    def preview_jpeg(self, quality: int = 70) -> bytes:
        """lores（YUV420）の 1 フレームを JPEG にする。

        Pi 5 には JPEG のハードウェアのエンコーダが無いので、simplejpeg で作る。
        """
        import simplejpeg

        request = self.picam2.capture_request()
        try:
            yuv = request.make_array("lores")
        finally:
            request.release()
        w, h = self.preview_size
        u = yuv[h : h + h // 4].reshape(h // 2, w // 2)
        v = yuv[h + h // 4 : h * 3 // 2].reshape(h // 2, w // 2)
        return simplejpeg.encode_jpeg_yuv_planes(yuv[:h, :w], u, v, quality=quality)

    def set_exposure(self, exposure_us: int, tol: float = 0.02, max_frames: int = 30) -> None:
        """露光時間を設定し、フレームに反映されるまで読み捨てる（設定は数フレーム遅れて効く）。"""
        self.picam2.set_controls({"ExposureTime": exposure_us})
        for _ in range(max_frames):
            md = self.picam2.capture_metadata()
            if abs(md["ExposureTime"] - exposure_us) <= max(tol * exposure_us, 100):
                return
        raise RuntimeError(
            f"露出 {exposure_us}us が反映されませんでした（最後: {md['ExposureTime']}us）"
        )

    def capture(self, out_dir: Path, stem: str) -> dict:
        """1 フレームを DNG（RAW）・JPEG（ISP 出力）・メタデータ JSON で保存する。"""
        request = self.picam2.capture_request()
        try:
            request.save("main", str(out_dir / f"{stem}.jpg"))
            request.save_dng(str(out_dir / f"{stem}.dng"))
            meta = {k: request.get_metadata().get(k) for k in META_KEYS}
        finally:
            request.release()
        (out_dir / f"{stem}.json").write_text(json.dumps(meta, indent=1))
        return meta
