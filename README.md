# imx519_edge

Arducam IMX519（16MP / AF）で、合成用のフレームを撮る Raspberry Pi 側のコード。85pi（Raspberry Pi 5）で動かす。
設計は [super_imx519 の DESIGN.md](https://github.com/HayatoShimada/super_imx519/blob/main/DESIGN.md) にある。

今はコマンドラインで撮れる。カメラとパンチルトを独占する常駐デーモン、Web の撮影アプリ、
85store-cms との連携、サーバーへの転送は、これから足す（Phase 1）。

## 撮る

露出ブラケット × 連写で撮る。最初に AE / AWB / AF を一度だけ走らせて値を読み、そのあと全部固定して撮る。
ゲインは 1.0 に固定し、明るさはシャッター時間だけで変える。各フレームを DNG（RAW）・JPEG（ISP 出力）・
メタデータ JSON で保存し、最後に `session.json`（schema_version 1）を書く。

```sh
cd ~/imx519_edge
python3 -m imx519_edge burst --out ~/captures/test1 --frames 8 --ev -2 0 2
```

| オプション | 内容 |
| --- | --- |
| `--out` | 保存先（必須） |
| `--frames` | 露出ごとの枚数（既定 8） |
| `--ev` | 露出補正の段数（既定 `-2 0 2`） |
| `--rotate180` | カメラが逆さに付いているとき |
| `--lens-position` | 指定すると AF せず、このレンズ位置で撮る |
| `--cms-id` | 85store-cms の商品 ID（session.json の `product` に入る） |
| `--note` | メモ |

ファイル名は `p{位置}_ev{EV}_{連番}`（例: `p00_ev+0.0_03.dng`）。

## 開発

カメラの無いマシンでは、`imx519_edge/fake.py` の偽のカメラでテストする。

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

## 必要なもの（85pi）

- Raspberry Pi OS Trixie（64bit）、Python 3.13
- Arducam 版の libcamera / rpicam-apps（Raspberry Pi 版の tuning ファイルには `rpi.af` が無く、`LensPosition` が無視される）
- `picamera2`（Raspberry Pi OS の apt のもの）
