"""セッションの manifest（ファイル名・サイズ・sha256）。サーバーが取り込んだあと照合する。"""

import hashlib
import json
from pathlib import Path

MANIFEST = "manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(session_dir: Path) -> str:
    """manifest.json を書き、その sha256（取り込み側が消すときに示す値）を返す。"""
    files = [
        {"name": p.name, "size": p.stat().st_size, "sha256": sha256_file(p)}
        for p in sorted(session_dir.iterdir())
        if p.is_file() and p.name != MANIFEST
    ]
    data = json.dumps({"session_id": session_dir.name, "files": files}, indent=1).encode()
    (session_dir / MANIFEST).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def manifest_digest(session_dir: Path) -> str:
    return hashlib.sha256((session_dir / MANIFEST).read_bytes()).hexdigest()
