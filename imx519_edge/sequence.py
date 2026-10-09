"""撮影の手順（露出ブラケット × 連写）。

最初に AE / AWB / AF を一度だけ走らせて値を読み、そのあと全部固定して撮る。
ゲインは 1.0 に固定し、自動露出が決めた明るさをシャッター時間だけで出す（ノイズを最小にするため）。
"""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .camera import MAX_EXPOSURE_US, Camera, Metered
from .session import Product, Session, Shot, new_session_id, shot_stem, software_version


def exposure_for(base_us: float, ev: float) -> int:
    return int(min(max(base_us * 2**ev, 100), MAX_EXPOSURE_US))


def run_burst(
    camera: Camera,
    out_dir: Path,
    evs: list[float],
    frames: int,
    lens_position: float | None = None,
    product: Product | None = None,
    note: str | None = None,
    now: datetime | None = None,
    on_shot: Callable[[Shot], None] | None = None,
) -> Session:
    """1 位置ぶんのブラケット撮影をして、session.json まで書く（微動は Phase 1 で足す）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now().astimezone()
    metered: Metered = camera.meter(lens_position)
    camera.lock(metered)

    session = Session(
        session_id=new_session_id(now, product.cms_id if product else None),
        created_at=now.isoformat(timespec="seconds"),
        camera={**asdict(metered), "rotate180": camera.rotate180},
        sequence={"ev": evs, "frames": frames, "positions": 1, "jitter_ticks": None},
        product=product,
        note=note,
        software=software_version(),
    )
    for ev in evs:
        camera.set_exposure(exposure_for(metered.base_exposure_us, ev))
        for i in range(frames):
            stem = shot_stem(0, ev, i)
            shot = Shot(stem=stem, ev=ev, position=0, meta=camera.capture(out_dir, stem))
            session.shots.append(shot)
            if on_shot:
                on_shot(shot)
    session.write(out_dir)
    return session
