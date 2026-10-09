"""コマンドライン（85pi で実行する）。

usage:
    python3 -m imx519_edge serve   # デーモン（撮影アプリと API）。普段は systemd から起動する
    python3 -m imx519_edge burst --out ~/captures/test1 --frames 8 --ev -2 0 2

カメラを開けるのは 1 プロセスだけなので、デーモンが動いているあいだは burst を使えない
（`systemctl --user stop imx519-edge` で止めてから使う）。
"""

import argparse
from pathlib import Path

from . import config as config_mod
from .camera import Camera
from .sequence import run_burst
from .session import Product


def serve(cfg: config_mod.Config, fake: bool = False) -> None:
    import logging

    import uvicorn

    # 撮影・CMS・パンチルトの出来事は journald にも残す（journalctl --user -u imx519-edge）
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from .api import create_app
    from .cms import Cms
    from .pantilt import PCA9685, PanTilt, Presets
    from .service import EdgeService

    pantilt = presets = None
    pt = cfg.pantilt
    if pt.enabled:
        try:
            pantilt = PanTilt(PCA9685.open(pt.bus, pt.address, pt.freq_hz), pt)
            presets = Presets(Path(pt.presets_file).expanduser())
        except OSError as e:
            print(f"警告: パンチルトを開けません（i2c-{pt.bus}、0x{pt.address:02x}）: {e}")

    if fake:
        # カメラの無いマシンで画面を試す（偽のカメラ。CMS は設定どおり本物を読む）
        from .fake import open_fake_camera

        camera_factory = lambda: open_fake_camera(frame_interval_s=0.11)  # noqa: E731
    else:
        camera_factory = lambda: Camera.open(cfg.camera.rotate180, cfg.camera.preview_size)  # noqa: E731
    service = EdgeService(cfg, camera_factory, pantilt)
    cms = Cms(cfg.cms.url, cfg.cms.timeout_s, cfg.cms.cache_s)
    app = create_app(service, cms, presets, manage_service=True)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, workers=1)


def main() -> None:
    ap = argparse.ArgumentParser(prog="imx519_edge")
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help="設定ファイル（既定 ~/.config/imx519_edge/config.toml）",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    serve_p = sub.add_parser("serve", help="デーモン（撮影アプリと API）を起動する")
    serve_p.add_argument("--fake", action="store_true", help="偽のカメラで動かす（開発用）")
    serve_p.add_argument("--port", type=int, default=None)
    burst = sub.add_parser("burst", help="露出ブラケット × 連写で撮る")
    burst.add_argument("--out", type=Path, required=True, help="セッションの保存先")
    burst.add_argument("--frames", type=int, default=8, help="露出ごとの枚数")
    burst.add_argument("--ev", type=float, nargs="+", default=[-2.0, 0.0, 2.0])
    burst.add_argument("--rotate180", action="store_true", help="カメラが逆さに付いているとき")
    burst.add_argument("--lens-position", type=float, default=None, help="指定すると AF しない")
    burst.add_argument("--cms-id", type=int, default=None, help="85store-cms の商品 ID")
    burst.add_argument("--note", default=None)
    args = ap.parse_args()
    cfg = config_mod.load(args.config)

    if args.command == "serve":
        if args.port:
            cfg.server.port = args.port
        serve(cfg, fake=args.fake)
        return

    camera = Camera.open(rotate180=args.rotate180 or cfg.camera.rotate180)
    try:
        session = run_burst(
            camera,
            args.out,
            args.ev,
            args.frames,
            lens_position=args.lens_position,
            product=Product(args.cms_id) if args.cms_id is not None else None,
            note=args.note,
            on_shot=lambda s: print(
                f"{s.stem}: exposure={s.meta['ExposureTime']}us gain={s.meta['AnalogueGain']:.2f}"
            ),
        )
    finally:
        camera.close()
    print(f"done: {session.session_id} {len(session.shots)} frames -> {args.out}")
