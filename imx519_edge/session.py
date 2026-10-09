"""session.json の形（schema_version 1）。

サーバー（super_imx519）との約束なので、変えるときは版を上げる。
v0（capture_burst.py の形。schema_version が無く、ファイル名は ev{EV}_{NN}）は
サーバー側で読み分ける。
"""

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1


@dataclass
class Product:
    """撮った商品（85store-cms の商品）。値は撮影時点の写し。handle は途中で変わるので持たない。"""

    cms_id: int
    title: str | None = None
    kind: str | None = None  # used / new / consignment
    product_type: str | None = None
    category_id: str | None = None


@dataclass
class Shot:
    stem: str
    ev: float
    position: int  # 微動の位置の番号（0 始まり）
    pan: int | None = None  # パンチルトの指令値（tick）。使わないときは None
    tilt: int | None = None
    approach: str | None = None  # 指令値に近づけた方向（"+" / "-"）
    meta: dict = field(default_factory=dict)


@dataclass
class Session:
    session_id: str
    created_at: str
    camera: dict  # lens_position, colour_gains, base_exposure_us, rotate180
    sequence: dict  # ev, frames, positions, jitter_ticks
    product: Product | None = None
    operator: str | None = None
    lighting: list[dict] = field(default_factory=list)  # 灯ごとの色温度・明るさ・距離（手入力）
    note: str | None = None
    shots: list[Shot] = field(default_factory=list)
    software: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def write(self, out_dir: Path) -> Path:
        path = out_dir / "session.json"
        path.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False))
        return path


def new_session_id(now: datetime, cms_id: int | None = None) -> str:
    """YYYY-MM-DD_HHMMSS（商品があれば _c{CMS の商品 ID} を付ける）。"""
    base = now.strftime("%Y-%m-%d_%H%M%S")
    return f"{base}_c{cms_id}" if cms_id is not None else base


def shot_stem(position: int, ev: float, index: int) -> str:
    return f"p{position:02d}_ev{ev:+.1f}_{index:02d}"


def software_version() -> dict:
    """このリポジトリの git の版（取れなければ None）。"""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = None
    return {"name": "imx519_edge", "git": sha}
