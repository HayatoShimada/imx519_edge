"""HTTP API と撮影アプリの画面。127.0.0.1 で待ち受け、tailscale serve（tailnet only）で出す。"""

import asyncio
import contextlib
import json
import queue
import re
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cms import Cms, CmsError
from .identity import operator_from, tailscale_whois
from .manifest import MANIFEST, manifest_digest
from .pantilt import Presets
from .service import Busy, EdgeService, NoSpace

STATIC = Path(__file__).parent / "static"
SESSION_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}(_c[0-9]+)?$")


class MeterIn(BaseModel):
    lens_position: float | None = None


class SettingsIn(BaseModel):
    lens_position: float | None = None
    colour_gains: tuple[float, float] | None = None
    base_exposure_us: float | None = None


class MoveIn(BaseModel):
    pan: int
    tilt: int
    approach: str | None = None


class DraftIn(BaseModel):
    kind: str
    name: str
    brand_id: int | None = None
    brand_name: str | None = None  # 既存に無いブランドは、この名前で作る
    product_type: str | None = None
    category_id: str | None = None
    category_name: str | None = None


class JobIn(BaseModel):
    cms_product_id: int
    ev: list[float] | None = None
    frames: int | None = None
    positions: int | None = None
    note: str | None = None
    lighting: list[dict] | None = None


def create_app(
    service: EdgeService,
    cms: Cms,
    presets: Presets | None = None,
    manage_service: bool = False,
    whois=tailscale_whois,
) -> FastAPI:
    """manage_service が True なら、アプリの起動と終了に合わせてカメラのスレッドを動かす。"""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if manage_service:
            service.start()
        try:
            yield
        finally:
            if manage_service:
                service.stop()

    app = FastAPI(title="imx519_edge", lifespan=lifespan)

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Busy as e:
            raise HTTPException(409, str(e)) from e
        except NoSpace as e:
            raise HTTPException(507, str(e)) from e
        except CmsError as e:
            service.log("error", str(e))
            raise HTTPException(502, str(e)) from e
        except TimeoutError as e:
            raise HTTPException(504, "カメラが応答しません") from e

    def session_dir(session_id: str) -> Path:
        if not SESSION_ID.match(session_id):
            raise HTTPException(404, "セッションがありません")
        path = service.captures / session_id
        if not (path / MANIFEST).exists():
            raise HTTPException(404, "セッションがありません")
        return path

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/status")
    def status():
        return service.status()

    @app.get("/api/ping")
    def ping():
        return {"t": time.time()}

    @app.get("/api/health")
    def health():
        """エッジから先（CMS）に届くか。画面の通信状態に出す。"""
        return {"cms": cms.health(), "state": service.state}

    @app.get("/api/logs")
    def logs():
        return list(service.logs)

    @app.get("/api/preview.mjpg")
    async def preview():
        async def frames():
            last = -1
            while True:
                seq, jpeg = service.frame()
                if seq != last and jpeg:
                    last = seq
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                await asyncio.sleep(0.05)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/preview.jpg")
    def preview_still():
        _, jpeg = service.frame()
        if not jpeg:
            raise HTTPException(503, "ライブビューの準備中です")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    async def events(request: Request):
        q = service.subscribe()

        async def stream():
            try:
                yield f"event: status\ndata: {json.dumps(service.status())}\n\n"
                last = time.monotonic()
                while not await request.is_disconnected():
                    try:
                        event, data = q.get_nowait()
                    except queue.Empty:
                        # 何も無くても 5 秒ごとに送り、画面がつながっているかを判断できるようにする
                        if time.monotonic() - last > 5:
                            last = time.monotonic()
                            yield f"event: ping\ndata: {json.dumps({'t': time.time()})}\n\n"
                        await asyncio.sleep(0.2)
                        continue
                    last = time.monotonic()
                    yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            finally:
                service.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # --- カメラ ---

    @app.post("/api/camera/meter")
    def meter(body: MeterIn):
        return asdict(guard(service.meter, body.lens_position))

    @app.put("/api/camera/settings")
    def settings(body: SettingsIn):
        return asdict(guard(service.update_locked, **body.model_dump()))

    @app.post("/api/camera/auto")
    def auto():
        guard(service.unlock)
        return {"ok": True}

    # --- パンチルト ---

    @app.post("/api/pantilt/move")
    def move(body: MoveIn):
        return guard(service.move, body.pan, body.tilt, body.approach)

    @app.post("/api/pantilt/stop")
    def stop():
        service.stop_pantilt()
        return {"ok": True}

    @app.get("/api/pantilt/presets")
    def get_presets():
        return presets.load() if presets else {}

    @app.put("/api/pantilt/presets")
    def put_presets(body: dict[str, MoveIn]):
        if not presets:
            raise HTTPException(409, "パンチルトが使えません")
        data = {name: p.model_dump() for name, p in body.items()}
        presets.save(data)
        return data

    # --- CMS ---

    @app.get("/api/cms/options")
    def cms_options(refresh: bool = False):
        options = guard(cms.options, refresh)
        return {**options, "hold_supported": guard(cms.hold_supported, refresh)}

    @app.get("/api/cms/products")
    def cms_products():
        if not guard(cms.hold_supported):
            return []
        return guard(cms.held_products)

    @app.get("/api/cms/products/{product_id}")
    def cms_product(product_id: int):
        return guard(cms.product, product_id)

    @app.post("/api/cms/products")
    def cms_create(body: DraftIn):
        brand_id = body.brand_id
        if brand_id is None and body.brand_name:
            brand_id = guard(cms.create_brand, body.brand_name.strip())["id"]
        product = guard(
            cms.create_draft,
            body.kind,
            body.name.strip(),
            brand_id,
            body.product_type,
            body.category_id,
            body.category_name,
        )
        service.log("info", f"CMS に保留の下書きを作りました: #{product['id']} {product['title']}")
        return product

    # --- 撮影ジョブ ---

    @app.post("/api/jobs")
    def create_job(body: JobIn, request: Request):
        product = guard(cms.product, body.cms_product_id)
        job = guard(
            service.start_job,
            product,
            ev=body.ev,
            frames=body.frames,
            positions=body.positions,
            note=body.note,
            lighting=body.lighting,
            operator=operator_from(request.headers, whois),
        )
        return job.public()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in service.jobs:
            raise HTTPException(404, "ジョブがありません")
        return service.jobs[job_id].public()

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str):
        if job_id not in service.jobs:
            raise HTTPException(404, "ジョブがありません")
        return service.cancel_job(job_id).public()

    # --- セッション（サーバーが取り込む） ---

    @app.get("/api/sessions")
    def sessions(cms_id: int | None = None):
        out = []
        for path in sorted(service.captures.iterdir(), reverse=True):
            if not (path / MANIFEST).exists() or not SESSION_ID.match(path.name):
                continue
            session = json.loads((path / "session.json").read_text())
            product = session.get("product") or {}
            if cms_id is not None and product.get("cms_id") != cms_id:
                continue
            manifest = json.loads((path / MANIFEST).read_text())
            out.append(
                {
                    "session_id": path.name,
                    "created_at": session.get("created_at"),
                    "product": product,
                    "shots": len(session.get("shots", [])),
                    "bytes": sum(f["size"] for f in manifest["files"]),
                }
            )
        return out

    @app.get("/api/sessions/{session_id}/manifest")
    def manifest(session_id: str):
        path = session_dir(session_id)
        return {**json.loads((path / MANIFEST).read_text()), "sha256": manifest_digest(path)}

    @app.get("/api/sessions/{session_id}/files/{name}")
    def file(session_id: str, name: str):
        path = session_dir(session_id)
        target = path / name
        if "/" in name or name.startswith(".") or not target.is_file():
            raise HTTPException(404, "ファイルがありません")
        return FileResponse(target)

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, manifest_sha256: str):
        """取り込み側が全ファイルを照合したあとに呼ぶ。

        manifest の sha256 が一致するときだけ消す。
        """
        path = session_dir(session_id)
        if manifest_sha256 != manifest_digest(path):
            raise HTTPException(409, "manifest が一致しません")
        shutil.rmtree(path)
        service.log("info", f"取り込み済みのセッションを消しました: {session_id}")
        return {"deleted": session_id}

    return app
