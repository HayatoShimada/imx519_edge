# imx519_edge

Arducam IMX519（16MP / AF）で服を撮る、Raspberry Pi 側のコード。85pi（Raspberry Pi 5）で常駐する。
設計は [super_imx519 の DESIGN.md](https://github.com/HayatoShimada/super_imx519/blob/main/DESIGN.md) にある。

- **カメラデーモン**: Picamera2 とパンチルト（PCA9685）を独占し、ライブビューと撮影ジョブを 1 本ずつ実行する
- **撮影アプリ**（スマホのブラウザ向け）: 区分・ブランド・品名・カテゴリ・品目を既存の値から選び、85store-cms に「保留」の下書きを作って、その商品で撮る
- **取り込み用の API**: サーバー（super_imx519）がセッションを取りに来て、sha256 で照合してから消す

## 使う

`https://85pi.taila713c8.ts.net:12443`（tailnet の中だけ）をスマホで開く。

1. 「新しい商品」で入力して「下書きを作って撮影へ」。続きを撮るときは「保留中の下書き」から選ぶ
2. 「ライブビューに接続」で構図を見て「撮影」。必要なら「カメラ」で先に測光して固定する。画面を閉じるとライブビューは切れる
3. 撮ったセッションは `~/captures/<session_id>/` に置かれ、サーバーが取り込むと消える
4. 価格・SKU・原価は CMS で入れ、「保留」を外して保存すると Shopify に作られる

CMS に保留の欄が無いあいだ（85store-cms が古いあいだ）は、下書きを作らない。

画面の上の帯に通信状態を出す。

| 項目 | 内容 |
| --- | --- |
| 通知 | 状態と進捗の通知（SSE）。サーバーは 5 秒ごとに ping を送るので、12 秒来なければ黄色、切れたら赤 |
| API | 85pi の API の応答時間（5 秒ごと） |
| ライブ | ライブビューの fps（下に fps・通信量・最後のフレームからの時間）。撮影中は止まる |
| CMS | 85pi から CMS に届くか、何 ms か（30 秒ごと） |

画面の下のログには、サーバーの出来事（撮影・測光・CMS・取り込み・エラー）と、画面の出来事（接続・切断）が出る。

## 入れる・更新する（85pi）

```sh
git clone https://github.com/HayatoShimada/imx519_edge.git ~/imx519_edge
~/imx519_edge/deploy/install.sh     # venv、設定ファイル、systemd のユーザーサービス、tailscale serve
```

- 更新: `cd ~/imx519_edge && git pull && systemctl --user restart imx519-edge`
- 状態: `systemctl --user status imx519-edge`（出来事は画面のログと `GET /api/logs` で見る）
- 設定: `~/.config/imx519_edge/config.toml`（例は `deploy/config.example.toml`、全項目は `imx519_edge/config.py`）
- パンチルトは、I2C を有効にし（`/boot/firmware/config.txt` に `dtparam=i2c_arm=on`、要 sudo と再起動）、
  `i2cdetect -y 1` で 0x40 が見えてから、設定の `[pantilt] enabled = true` にする

## コマンドラインで撮る

カメラを開けるのは 1 プロセスだけなので、先にデーモンを止める（`systemctl --user stop imx519-edge`）。

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

ファイル名は `p{位置}_ev{EV}_{連番}`（例: `p00_ev+0.0_03.dng`）。`session.json` は schema_version 1（`imx519_edge/session.py`）。

## API

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/status` | 状態、固定値、パンチルト、空き容量、実行中のジョブ |
| GET | `/api/ping`・`/api/health`・`/api/logs` | 応答時間の計測・CMS に届くか・最近のログ |
| GET | `/api/events` | 状態と進捗（Server-Sent Events） |
| GET | `/api/preview.mjpg`・`/api/preview.jpg` | ライブビュー |
| POST | `/api/camera/meter`・`/api/camera/auto` | 測光して固定 / 自動に戻す |
| PUT | `/api/camera/settings` | 固定値を変える |
| POST | `/api/pantilt/move`・`/api/pantilt/stop` | 移動（後で full-off）/ 非常停止 |
| GET・PUT | `/api/pantilt/presets` | 構図のプリセット |
| GET | `/api/cms/options`・`/api/cms/products`・`/api/cms/products/{id}` | CMS の選択肢・保留中の下書き・商品 1 件 |
| POST | `/api/cms/products` | 保留の下書きを作る |
| POST | `/api/jobs` | 撮影（`cms_product_id` 必須） |
| GET・DELETE | `/api/jobs/{id}` | 進捗 / 中止 |
| GET | `/api/sessions`・`/api/sessions/{id}/manifest`・`/api/sessions/{id}/files/{name}` | 取り込み用 |
| DELETE | `/api/sessions/{id}?manifest_sha256=…` | 照合したあとに消す |

## 開発

カメラ・パンチルト・CMS の無いマシンでは、偽物（`imx519_edge/fake.py`、`pantilt.FakeBus`、テストの `FakeCms`）で試す。
画面は `uv run python -m imx519_edge serve --fake --port 8601` で、偽のカメラのまま開ける（CMS は設定どおり本物を読む。
撮影も偽のカメラで動き、`captures_dir` に書く）。

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

## 必要なもの（85pi）

- Raspberry Pi OS Trixie（64bit）、Python 3.13
- Arducam 版の libcamera / rpicam-apps（Raspberry Pi 版の tuning ファイルには `rpi.af` が無く、`LensPosition` が無視される）
- apt の `python3-picamera2`（numpy・simplejpeg も一緒に入る）と `python3-smbus2`
