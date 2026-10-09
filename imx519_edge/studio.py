"""サーバー（home-linux の super_imx519 のスタジオ）を呼ぶ。

撮影アプリの画面は 85pi（camera.85-store.com）からしか読まないので、完成画像と処理の進み具合は
このエッジが中継する。スタジオは home-linux の tailnet のアドレスだけで待ち受けている。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class StudioError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Studio:
    def __init__(self, url: str, timeout_s: float = 10.0, opener=None):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.opener = opener or urllib.request.urlopen

    def _open(self, path: str, method: str = "GET", params: dict | None = None):
        url = f"{self.url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
        try:
            return self.opener(req, timeout=self.timeout_s)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise StudioError(f"サーバー {method} {path}: {e.code} {detail}", e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise StudioError(f"サーバーに届きません: {e}") from e

    def json(self, path: str, method: str = "GET", params: dict | None = None):
        with self._open(path, method, params) as res:
            return json.loads(res.read())

    def image(self, path: str) -> bytes:
        with self._open(path) as res:
            return res.read()

    def health(self) -> dict:
        start = time.monotonic()
        try:
            h = self.json("/api/health")
            return {
                "ok": True,
                "ms": round((time.monotonic() - start) * 1000),
                "error": None,
                "edge_ok": h.get("edge", {}).get("ok"),
            }
        except StudioError as e:
            return {"ok": False, "ms": None, "error": str(e), "edge_ok": None}

    def wake(self) -> None:
        """撮影が終わったことを知らせる（次の確認を待たずに取り込んでもらう）。届かなくてもよい。"""
        try:
            self.json("/api/wake", method="POST")
        except StudioError:
            pass
