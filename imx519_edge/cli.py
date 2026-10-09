"""コマンドラインから撮る（85pi で実行する。デーモンが動いているときはカメラを開けない）。

usage:
    python3 -m imx519_edge burst --out ~/captures/test1 --frames 8 --ev -2 0 2
"""

import argparse
from pathlib import Path

from .camera import Camera
from .sequence import run_burst
from .session import Product


def main() -> None:
    ap = argparse.ArgumentParser(prog="imx519_edge")
    sub = ap.add_subparsers(dest="command", required=True)
    burst = sub.add_parser("burst", help="露出ブラケット × 連写で撮る")
    burst.add_argument("--out", type=Path, required=True, help="セッションの保存先")
    burst.add_argument("--frames", type=int, default=8, help="露出ごとの枚数")
    burst.add_argument("--ev", type=float, nargs="+", default=[-2.0, 0.0, 2.0])
    burst.add_argument("--rotate180", action="store_true", help="カメラが逆さに付いているとき")
    burst.add_argument("--lens-position", type=float, default=None, help="指定すると AF しない")
    burst.add_argument("--cms-id", type=int, default=None, help="85store-cms の商品 ID")
    burst.add_argument("--note", default=None)
    args = ap.parse_args()

    camera = Camera.open(rotate180=args.rotate180)
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
