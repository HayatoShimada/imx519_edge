"""撮影の手順（露出ブラケット × 連写。パンチルトがあれば、位置ごとに微動を入れる）。

最初に AE / AWB / AF を一度だけ走らせて値を読み、そのあと全部固定して撮る
（固定値を渡されたらそれを使う）。
ゲインは 1.0 に固定し、自動露出が決めた明るさをシャッター時間だけで出す（ノイズを最小にするため）。
"""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .camera import MAX_EXPOSURE_US, Camera, Metered
from .session import Product, Session, Shot, new_session_id, shot_stem, software_version


class Cancelled(Exception):
    pass


def exposure_for(base_us: float, ev: float) -> int:
    return int(min(max(base_us * 2**ev, 100), MAX_EXPOSURE_US))


def run_burst(
    camera: Camera,
    out_dir: Path,
    evs: list[float],
    frames: int,
    *,
    lens_position: float | None = None,
    metered: Metered | None = None,
    product: Product | None = None,
    note: str | None = None,
    operator: str | None = None,
    lighting: list[dict] | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
    positions: int = 1,
    jitter_ticks: tuple[int, int] | None = None,
    before_position: Callable[[int], dict | None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_shot: Callable[[Shot], None] | None = None,
) -> Session:
    """ブラケット撮影をして、session.json まで書く。

    before_position(位置の番号) はパンチルトを動かし、{pan, tilt, approach} を返す
    （渡されなければ動かさない）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now().astimezone()
    if metered is None:
        metered = camera.meter(lens_position)
    camera.lock(metered)

    session = Session(
        session_id=session_id or new_session_id(now, product.cms_id if product else None),
        created_at=now.isoformat(timespec="seconds"),
        camera={**asdict(metered), "rotate180": camera.rotate180},
        sequence={
            "ev": evs,
            "frames": frames,
            "positions": positions,
            "jitter_ticks": list(jitter_ticks) if jitter_ticks else None,
        },
        product=product,
        operator=operator,
        lighting=lighting or [],
        note=note,
        software=software_version(),
    )
    for position in range(positions):
        pose = (before_position(position) if before_position else None) or {}
        for ev in evs:
            camera.set_exposure(exposure_for(metered.base_exposure_us, ev))
            for i in range(frames):
                if cancelled and cancelled():
                    raise Cancelled
                stem = shot_stem(position, ev, i)
                shot = Shot(
                    stem=stem,
                    ev=ev,
                    position=position,
                    pan=pose.get("pan"),
                    tilt=pose.get("tilt"),
                    approach=pose.get("approach"),
                    meta=camera.capture(out_dir, stem),
                )
                session.shots.append(shot)
                if on_shot:
                    on_shot(shot)
    session.write(out_dir)
    return session
