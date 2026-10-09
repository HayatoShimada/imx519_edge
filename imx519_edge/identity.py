"""操作した人（Tailscale のログイン名）を調べる。session.json の operator に入れる（記録用）。

- 85pi の tailscale serve から直接来たとき: tailscale serve が付ける Tailscale-User-Login を使う
- camera.85-store.com（camera 端末の Caddy）から来たとき: camera 端末はタグ付きで名前が届かない。
  そこで、Caddy が渡す接続元のアドレス（X-Camera-Client）を tailscaled に問い合わせる（whois）

X-Camera-Client は tailnet の中からなら誰でも付けられる。
記録の参考にとどめ、権限の判断には使わない。
"""

import ipaddress
import json
import subprocess
import time
from collections.abc import Callable, Mapping

_cache: dict[str, tuple[float, str | None]] = {}


def tailscale_whois(ip: str) -> str | None:
    """tailnet のアドレスの持ち主のログイン名。タグ付きの端末や、分からないときは None。"""
    hit = _cache.get(ip)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    login = None
    try:
        out = subprocess.run(
            ["tailscale", "whois", "--json", ip],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        data = json.loads(out)
        if not (data.get("Node") or {}).get("Tags"):
            login = (data.get("UserProfile") or {}).get("LoginName")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    _cache[ip] = (time.monotonic(), login)
    return login


def operator_from(
    headers: Mapping[str, str], whois: Callable[[str], str | None] = tailscale_whois
) -> str | None:
    login = headers.get("Tailscale-User-Login")
    if login:
        return login
    client = headers.get("X-Camera-Client")
    if not client:
        return None
    try:
        ip = str(ipaddress.ip_address(client.strip()))
    except ValueError:
        return None
    return whois(ip)
