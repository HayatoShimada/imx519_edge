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


def serve(cfg: config_mod.Config) -> None:
    import uvicorn

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

    service = EdgeService(
        cfg,
        lambda: Camera.open(cfg.camera.rotate180, cfg.camera.preview_size),
        pantilt,
    )
    cms = Cms(cfg.cms.url, cfg.cms.timeout_s, cfg.cms.cache_s)
    app = create_app(service, cms, presets)
    app.add_event_handler("startup", service.start)
    app.add_event_handler("shutdown", service.stop)
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
    sub.add_parser("serve", help="デーモン（撮影アプリと API）を起動する")
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
        serve(cfg)
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
