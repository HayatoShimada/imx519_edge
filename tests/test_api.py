import json
import time

import pytest
from fastapi.testclient import TestClient

from imx519_edge.api import create_app
from imx519_edge.config import Config, SequenceConfig, StorageConfig
from imx519_edge.fake import open_fake_camera
from imx519_edge.manifest import sha256_file
from imx519_edge.service import EdgeService

PRODUCT = {
    "id": 42,
    "title": "[River] Jacket [USED]",
    "kind": "used",
    "product_type": "Coats & Jackets",
    "category_id": "gid://x/aa-1-10-2",
    "category_name": "Coats & Jackets",
    "status": "draft",
}


class FakeCms:
    def __init__(self):
        self.drafts = []

    def options(self, refresh=False):
        return {"kinds": [], "brands": [], "categories": [], "product_types": []}

    def hold_supported(self, refresh=False):
        return True

    def health(self):
        return {"ok": True, "ms": 5, "role": "admin", "error": None}

    def held_products(self):
        return [PRODUCT]

    def product(self, product_id):
        return {**PRODUCT, "id": product_id}

    def create_brand(self, name):
        return {"id": 99, "name": name}

    def create_draft(self, *args):
        self.drafts.append(args)
        return PRODUCT


class FakeStudio:
    def json(self, path, method="GET", params=None):
        if path == "/api/sessions":
            return [
                {"session_id": "2026-10-09_121401_c434", "state": "processing", "params": params}
            ]
        return {"ok": True, "path": path, "method": method}

    def image(self, path):
        return b"\xff\xd8" + path.encode()

    def health(self):
        return {"ok": True, "ms": 3, "error": None, "edge_ok": True}


@pytest.fixture
def client(tmp_path):
    config = Config(
        storage=StorageConfig(captures_dir=str(tmp_path), reserve_gb=1.0, frame_mb=1.0),
        sequence=SequenceConfig(ev=[-1.0, 1.0], frames=2),
    )
    free = {"bytes": 100 * 10**9}
    service = EdgeService(config, lambda: open_fake_camera(), free_bytes=lambda p: free["bytes"])
    service.start()
    cms = FakeCms()
    app = create_app(service, cms, whois=lambda ip: f"whois-{ip}", studio=FakeStudio())
    with TestClient(app) as c:
        c.service, c.cms, c.free = service, cms, free
        wait(lambda: service.state == "idle")
        yield c
    service.stop()


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("待ちきれませんでした")


def test_status_and_preview(client):
    status = client.get("/api/status").json()
    assert status["state"] == "idle" and status["pantilt"]["available"] is False
    wait(lambda: client.service.frame()[0] > 0)
    res = client.get("/api/preview.jpg")
    assert res.status_code == 200 and res.content[:2] == b"\xff\xd8"


def test_job_writes_session_then_transfer_and_delete(client):
    res = client.post(
        "/api/jobs", json={"cms_product_id": 42}, headers={"Tailscale-User-Login": "me@example.com"}
    )
    assert res.status_code == 200, res.text
    job = res.json()
    assert job["total"] == 4
    wait(lambda: client.get(f"/api/jobs/{job['id']}").json()["status"] == "done")

    sessions = client.get("/api/sessions", params={"cms_id": 42}).json()
    assert [s["session_id"] for s in sessions] == [job["session_id"]]
    assert job["session_id"].endswith("_c42")

    manifest = client.get(f"/api/sessions/{job['session_id']}/manifest").json()
    names = {f["name"] for f in manifest["files"]}
    assert "session.json" in names and "p00_ev-1.0_00.dng" in names
    session = json.loads(
        client.get(f"/api/sessions/{job['session_id']}/files/session.json").content
    )
    assert session["operator"] == "me@example.com"
    assert session["product"]["cms_id"] == 42 and session["product"]["title"] == PRODUCT["title"]

    path = client.service.captures / job["session_id"]
    assert all(sha256_file(path / f["name"]) == f["sha256"] for f in manifest["files"])

    assert (
        client.delete(
            f"/api/sessions/{job['session_id']}", params={"manifest_sha256": "x"}
        ).status_code
        == 409
    )
    res = client.delete(
        f"/api/sessions/{job['session_id']}", params={"manifest_sha256": manifest["sha256"]}
    )
    assert res.status_code == 200 and not path.exists()


def test_job_refused_when_disk_is_low(client):
    client.free["bytes"] = int(1.001e9)  # 予備 1 GB ぎりぎり
    res = client.post("/api/jobs", json={"cms_product_id": 42})
    assert res.status_code == 507 and "空き" in res.json()["detail"]


def test_positions_need_pantilt(client):
    res = client.post("/api/jobs", json={"cms_product_id": 42, "positions": 3})
    assert res.status_code == 409


def test_cancel_removes_partial(client):
    job = client.post("/api/jobs", json={"cms_product_id": 42, "frames": 30}).json()
    client.delete(f"/api/jobs/{job['id']}")
    wait(lambda: client.get(f"/api/jobs/{job['id']}").json()["status"] == "cancelled")
    assert not any(client.service.captures.iterdir())


def test_meter_locks_and_auto_unlocks(client):
    locked = client.post("/api/camera/meter", json={"lens_position": 2.0}).json()
    assert locked["lens_position"] == 2.0
    assert client.get("/api/status").json()["locked"]["lens_position"] == 2.0
    changed = client.put("/api/camera/settings", json={"lens_position": 3.5}).json()
    assert changed["lens_position"] == 3.5
    client.post("/api/camera/auto")
    assert client.get("/api/status").json()["locked"] is None


def test_create_draft_with_new_brand(client):
    res = client.post(
        "/api/cms/products", json={"kind": "used", "name": " Jacket ", "brand_name": "New Brand"}
    )
    assert res.status_code == 200
    assert client.cms.drafts == [("used", "Jacket", 99, None, None, None)]


def test_session_paths_are_checked(client):
    assert client.get("/api/sessions/../etc/manifest").status_code == 404
    assert client.get("/api/sessions/2026-10-09_000000/files/x").status_code == 404


def test_logs_ping_and_health(client):
    assert client.get("/api/ping").json()["t"] > 0
    assert client.get("/api/health").json()["cms"]["ok"] is True
    client.post("/api/camera/meter", json={"lens_position": 1.0})
    messages = [e["message"] for e in client.get("/api/logs").json()]
    assert "カメラを開きました" in messages
    assert any(m.startswith("測光して固定") for m in messages)


def test_operator_from_camera_entry(client):
    job = client.post(
        "/api/jobs",
        json={"cms_product_id": 42, "frames": 1},
        headers={"X-Camera-Client": "100.64.0.9"},
    ).json()
    wait(lambda: client.get(f"/api/jobs/{job['id']}").json()["status"] == "done")
    session = json.loads(
        client.get(f"/api/sessions/{job['session_id']}/files/session.json").content
    )
    assert session["operator"] == "whois-100.64.0.9"


def test_studio_relay(client):
    rows = client.get("/api/studio/sessions", params={"cms_id": 434}).json()
    assert rows[0]["state"] == "processing" and rows[0]["params"] == {"cms_id": 434}
    res = client.get("/api/studio/sessions/2026-10-09_121401_c434/preview.jpg")
    assert res.content == b"\xff\xd8/api/sessions/2026-10-09_121401_c434/preview.jpg"
    assert client.get("/api/studio/sessions/2026-10-09_121401_c434/session.json").status_code == 404
    assert (
        client.post("/api/studio/sessions/2026-10-09_121401_c434/reprocess").json()["method"]
        == "POST"
    )
    assert client.get("/api/health").json()["studio"]["ok"] is True
