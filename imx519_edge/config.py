"""設定（~/.config/imx519_edge/config.toml）。書いていない項目は既定値を使う。"""

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

DEFAULT_PATH = Path("~/.config/imx519_edge/config.toml").expanduser()


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8519


@dataclass
class StorageConfig:
    captures_dir: str = "~/captures"
    reserve_gb: float = 5.0  # 撮影後にこれだけは空けておく（85pi はほかの用途と兼用）
    frame_mb: float = 36.0  # 1 フレーム（DNG + JPEG + JSON）の見積もり

    @property
    def captures_path(self) -> Path:
        return Path(self.captures_dir).expanduser()


@dataclass
class CameraConfig:
    rotate180: bool = False
    preview_size: tuple[int, int] = (
        640,
        480,
    )  # 幅は 64 の倍数にする（YUV420 の行の詰め物を避ける）
    preview_quality: int = 70


@dataclass
class SequenceConfig:
    """撮影アプリの「撮影」ボタンの既定の手順。"""

    ev: list[float] = field(default_factory=lambda: [-2.0, 0.0, 2.0])
    frames: int = 8
    positions: int = 1  # 微動の位置の数（2 以上はパンチルトが要る）
    jitter_ticks: tuple[int, int] = (5, 30)  # ランダムに動かす量（tick、最小と最大）


@dataclass
class CmsConfig:
    url: str = "https://cms.85-store.com"
    cache_s: float = 600.0  # 選択肢（ブランド・カテゴリ・品目）を覚えておく時間
    timeout_s: float = 20.0


@dataclass
class PanTiltConfig:
    enabled: bool = False
    bus: int = 1
    address: int = 0x40
    freq_hz: float = 50.0
    pan_channel: int = 0
    tilt_channel: int = 1
    # 指令値（tick）の可動範囲。50 Hz で 1 tick ≈ 4.88 µs（500〜2500 µs が 102〜512）
    pan_min: int = 150
    pan_max: int = 450
    tilt_min: int = 150
    tilt_max: int = 450
    home_pan: int = 307
    home_tilt: int = 307
    approach_ticks: int = 10  # 決まった方向から近づけるときに、手前で止める量
    move_wait_s: float = 0.3  # 1 回動かしたあとに待つ時間
    settle_s: float = 0.5  # 止めて（full-off）から撮るまでに待つ時間
    presets_file: str = "~/.config/imx519_edge/presets.json"


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    sequence: SequenceConfig = field(default_factory=SequenceConfig)
    cms: CmsConfig = field(default_factory=CmsConfig)
    pantilt: PanTiltConfig = field(default_factory=PanTiltConfig)


def _fill(cls, data: dict):
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"{cls.__name__} に無い項目: {', '.join(sorted(unknown))}")
    obj = cls()
    for name, value in data.items():
        current = getattr(obj, name)
        if is_dataclass(current):
            value = _fill(type(current), value)
        elif isinstance(current, tuple):
            value = tuple(value)
        setattr(obj, name, value)
    return obj


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        return _fill(Config, tomllib.load(f))
