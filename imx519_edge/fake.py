"""Picamera2 と同じ形の偽のカメラ（カメラの無いマシンで開発・テストするため）。

露光時間などの設定は、実機と同じく数フレーム遅れて反映される。保存するファイルは中身の無い小さなもの。
"""

from .camera import Camera

SENSOR_RESOLUTION = (4656, 3496)
DELAY_FRAMES = 2


class FakeRequest:
    def __init__(self, metadata: dict, lores_size: tuple[int, int] = (640, 480)):
        self.metadata = metadata
        self.lores_size = lores_size

    def make_array(self, name: str):
        import numpy as np

        w, h = self.lores_size
        yuv = np.full((h * 3 // 2, w), 128, dtype=np.uint8)
        yuv[:h] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
        return yuv

    def save(self, name: str, path: str) -> None:
        with open(path, "wb") as f:
            f.write(b"\xff\xd8fake-jpeg\xff\xd9")

    def save_dng(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(b"II*\x00fake-dng")

    def get_metadata(self) -> dict:
        return dict(self.metadata)

    def release(self) -> None:
        pass


class FakePicamera2:
    sensor_resolution = SENSOR_RESOLUTION

    def __init__(
        self, auto_exposure_us: int = 20_000, auto_gain: float = 2.0, af_lens: float = 1.5
    ):
        self.state = {
            "ExposureTime": auto_exposure_us,
            "AnalogueGain": auto_gain,
            "DigitalGain": 1.0,
            "ColourGains": (2.0, 1.6),
            "ColourTemperature": 5000,
            "LensPosition": 1.0,
            "AfState": 0,
            "Lux": 400.0,
            "FocusFoM": 1000,
            "SensorTemperature": 40.0,
        }
        self.af_lens = af_lens
        self.pending: list[tuple[int, dict]] = []  # (反映されるまでのフレーム数, 設定)
        self.controls_log: list[dict] = []
        self.frame = 0
        self.started = False

    def create_still_configuration(self, **kwargs) -> dict:
        return kwargs

    def configure(self, config: dict) -> None:
        self.config = config

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def set_controls(self, controls: dict) -> None:
        self.controls_log.append(dict(controls))
        self.pending.append((DELAY_FRAMES, dict(controls)))

    def autofocus_cycle(self) -> bool:
        self.state["LensPosition"] = self.af_lens
        return True

    def _next_frame(self) -> dict:
        self.frame += 1
        still = []
        for left, controls in self.pending:
            if left <= 1:
                self.state.update({k: v for k, v in controls.items() if k in self.state})
            else:
                still.append((left - 1, controls))
        self.pending = still
        return {**self.state, "SensorTimestamp": self.frame * 111_000_000}

    def capture_metadata(self) -> dict:
        return self._next_frame()

    def capture_request(self) -> FakeRequest:
        lores = self.config.get("lores", {}).get("size", (640, 480))
        return FakeRequest(self._next_frame(), lores)


def open_fake_camera(**kwargs) -> Camera:
    camera = Camera(FakePicamera2(**kwargs), "auto", "manual", "continuous")
    camera.start()
    return camera
