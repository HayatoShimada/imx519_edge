# imx519_burst_edge

Arducam IMX519（16MP / AF）で、合成用のフレームを撮る Raspberry Pi 側のスクリプト。

## capture_burst.py

露出ブラケット × 連写で撮る。最初に AE / AWB / AF を一度だけ走らせて値を読み、そのあと全部固定して撮る。
ゲインは 1.0 に固定し、明るさはシャッター時間だけで変える。各フレームを DNG（RAW）・JPEG（ISP 出力）・メタデータ JSON で保存し、最後に `session.json` を書く。

```sh
python3 capture_burst.py --out ~/captures/test1 --frames 8 --ev -2 0 2
```

| オプション | 内容 |
| --- | --- |
| `--out` | 保存先（必須） |
| `--frames` | 露出ごとの枚数（既定 8） |
| `--ev` | 露出補正の段数（既定 `-2 0 2`） |
| `--rotate180` | カメラが逆さに付いているとき |
| `--lens-position` | 指定すると AF せず、このレンズ位置で撮る |

## 必要なもの

- Raspberry Pi 5（Pi 4 でも動く想定）
- Arducam 版の libcamera / rpicam-apps（Raspberry Pi 版の tuning ファイルには `rpi.af` が無く、`LensPosition` が無視される）
- `picamera2`（Raspberry Pi OS のシステムパッケージ）
