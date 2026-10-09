"""カメラとパンチルトを独占する常駐の部分。

カメラ（Picamera2）は 1 本のワーカースレッドだけが触る。API からの操作はキューに入れて順に実行し、
手が空いているあいだはライブビューの JPEG を作る。撮影ジョブも同じスレッドで 1 本ずつ実行する。
"""

import queue
import random
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path

from .camera import Camera, Metered
from .config import Config
from .manifest import write_manifest
from .pantilt import PanTilt
from .sequence import Cancelled, run_burst
from .session import Product, new_session_id


class Busy(Exception):
    pass


class NoSpace(Exception):
    pass


@dataclass
class Job:
    id: str
    session_id: str
    product: dict
    ev: list[float]
    frames: int
    positions: int
    total: int
    status: str = "queued"  # queued / running / done / failed / cancelled
    done: int = 0
    error: str | None = None
    manifest_sha256: str | None = None
    created_at: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "cancel"}


class EdgeService:
    def __init__(
        self,
        config: Config,
        camera_factory: Callable[[], Camera],
        pantilt: PanTilt | None = None,
        free_bytes: Callable[[Path], int] | None = None,
        rng: random.Random | None = None,
    ):
        self.config = config
        self.camera_factory = camera_factory
        self.pantilt = pantilt
        self.free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self.rng = rng or random.Random()
        self.captures = config.storage.captures_path
        self.captures.mkdir(parents=True, exist_ok=True)

        self.state = "starting"  # starting / idle / moving / capturing / error
        self.error: str | None = None
        self.locked: Metered | None = None
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None

        self._commands: queue.Queue = queue.Queue()
        self._frame = (0, b"")
        self._frame_cond = threading.Condition()
        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self.camera: Camera | None = None

    # --- ワーカースレッド ---

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        if self.camera:
            self.camera.close()

    def _loop(self) -> None:
        try:
            self.camera = self.camera_factory()
            self.camera.unlock()
            self._set_state("idle")
        except Exception as e:  # カメラが開けない（別のプロセスが使っている など）
            self.error = f"カメラを開けません: {e}"
            self._set_state("error")
            return
        while self._running:
            try:
                fn, future = self._commands.get(timeout=0.01)
            except queue.Empty:
                self._preview()
                continue
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn())
                except BaseException as e:
                    future.set_exception(e)

    def _preview(self) -> None:
        try:
            jpeg = self.camera.preview_jpeg(self.config.camera.preview_quality)
        except Exception as e:
            self.error = f"ライブビュー: {e}"
            time.sleep(0.5)
            return
        with self._frame_cond:
            self._frame = (self._frame[0] + 1, jpeg)
            self._frame_cond.notify_all()

    def submit(self, fn: Callable):
        """カメラのスレッドで fn を実行する Future を返す。"""
        if self.state in ("starting", "error"):
            raise Busy(self.error or "カメラの準備中です")
        future: Future = Future()
        self._commands.put((fn, future))
        return future

    def call(self, fn: Callable, timeout: float = 30.0):
        return self.submit(fn).result(timeout=timeout)

    def frame(self) -> tuple[int, bytes]:
        with self._frame_cond:
            return self._frame

    # --- 通知（SSE） ---

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: str, data: dict) -> None:
        with self._sub_lock:
            for q in self._subscribers:
                try:
                    q.put_nowait((event, data))
                except queue.Full:
                    pass

    def _set_state(self, state: str) -> None:
        self.state = state
        self.publish("status", self.status())

    # --- 状態 ---

    def status(self) -> dict:
        free = self.free_bytes(self.captures)
        pt = self.pantilt
        return {
            "state": self.state,
            "error": self.error,
            "locked": asdict(self.locked) if self.locked else None,
            "pantilt": {
                "available": pt is not None,
                "pan": pt.pan if pt else None,
                "tilt": pt.tilt if pt else None,
            },
            "disk": {"free_gb": round(free / 1e9, 1), "reserve_gb": self.config.storage.reserve_gb},
            "job": self.current.public() if self.current else None,
            "defaults": asdict(self.config.sequence),
        }

    # --- カメラの操作 ---

    def meter(self, lens_position: float | None = None) -> Metered:
        def run():
            self.camera.unlock()
            metered = self.camera.meter(lens_position, settle_s=1.0)
            self.camera.lock(metered)
            self.locked = metered
            return metered

        result = self.call(run, timeout=60)
        self.publish("status", self.status())
        return result

    def update_locked(self, **changes) -> Metered:
        if self.locked is None:
            raise Busy("先に測光して固定してください")

        def run():
            self.locked = replace(
                self.locked, **{k: v for k, v in changes.items() if v is not None}
            )
            self.camera.lock(self.locked)
            return self.locked

        result = self.call(run)
        self.publish("status", self.status())
        return result

    def unlock(self) -> None:
        def run():
            self.locked = None
            self.camera.unlock()

        self.call(run)
        self.publish("status", self.status())

    # --- パンチルト ---

    def move(self, pan: int, tilt: int, approach: str | None = None) -> dict:
        if not self.pantilt:
            raise Busy("パンチルトが使えません（設定で無効か、I2C が有効になっていません）")

        def run():
            self._set_state("moving")
            try:
                return self.pantilt.move(pan, tilt, approach, settle=False)
            finally:
                self._set_state("idle")

        return self.call(run)

    def stop_pantilt(self) -> None:
        # 非常停止はキューを待たずに直接送る（I2C はカメラと別のバス）
        if self.pantilt:
            self.pantilt.stop()

    # --- 撮影ジョブ ---

    def estimate_bytes(self, ev: list[float], frames: int, positions: int) -> int:
        return int(len(ev) * frames * positions * self.config.storage.frame_mb * 1e6)

    def start_job(
        self,
        product: dict,
        ev: list[float] | None = None,
        frames: int | None = None,
        positions: int | None = None,
        note: str | None = None,
        lighting: list[dict] | None = None,
        operator: str | None = None,
    ) -> Job:
        if self.current and self.current.status in ("queued", "running"):
            raise Busy("撮影中です")
        seq = self.config.sequence
        ev = ev or seq.ev
        frames = frames or seq.frames
        positions = positions or seq.positions
        if positions > 1 and not self.pantilt:
            raise Busy("微動（位置が 2 以上）にはパンチルトが要ります")
        need = self.estimate_bytes(ev, frames, positions)
        free = self.free_bytes(self.captures)
        reserve = self.config.storage.reserve_gb * 1e9
        if free - need < reserve:
            raise NoSpace(
                f"空きが足りません（空き {free / 1e9:.1f} GB、見積もり {need / 1e9:.1f} GB、"
                f"予備 {reserve / 1e9:.0f} GB）。サーバーに取り込んでから撮ってください"
            )

        session_id = new_session_id(datetime.now().astimezone(), product["id"])
        job = Job(
            id=uuid.uuid4().hex[:12],
            session_id=session_id,
            product=product,
            ev=ev,
            frames=frames,
            positions=positions,
            total=len(ev) * frames * positions,
        )
        self.jobs[job.id] = job
        self.current = job
        self.submit(lambda: self._run_job(job, note, lighting, operator))
        self.publish("job", job.public())
        return job

    def cancel_job(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        job.cancel.set()
        return job

    def _run_job(self, job: Job, note, lighting, operator) -> None:
        partial = self.captures / f".partial-{job.session_id}"
        final = self.captures / job.session_id
        job.status = "running"
        self._set_state("capturing")
        target = None
        if self.pantilt and job.positions > 1:
            pt = self.pantilt
            target = (pt.pan or pt.config.home_pan, pt.tilt or pt.config.home_tilt)

        def before_position(position: int) -> dict | None:
            if target is None:
                return None
            return self.pantilt.jitter(*target, self.config.sequence.jitter_ticks, self.rng)

        def on_shot(shot) -> None:
            job.done += 1
            self.publish("job", job.public())

        p = job.product
        try:
            run_burst(
                self.camera,
                partial,
                job.ev,
                job.frames,
                metered=self.locked,
                product=Product(
                    cms_id=p["id"],
                    title=p.get("title"),
                    kind=p.get("kind"),
                    product_type=p.get("product_type"),
                    category_id=p.get("category_id"),
                ),
                note=note,
                operator=operator,
                lighting=lighting,
                session_id=job.session_id,
                positions=job.positions,
                jitter_ticks=self.config.sequence.jitter_ticks if target else None,
                before_position=before_position,
                cancelled=job.cancel.is_set,
                on_shot=on_shot,
            )
            job.manifest_sha256 = write_manifest(partial)
            partial.rename(final)
            job.status = "done"
        except Cancelled:
            shutil.rmtree(partial, ignore_errors=True)
            job.status = "cancelled"
        except Exception as e:
            shutil.rmtree(partial, ignore_errors=True)
            job.status, job.error = "failed", str(e)
        finally:
            # 測光して固定していなければ、ライブビューのために自動に戻す
            if self.locked is None:
                self.camera.unlock()
            else:
                self.camera.lock(self.locked)
            self._set_state("idle")
            self.publish("job", job.public())
