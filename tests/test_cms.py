import io
import json
import urllib.error

import pytest

from imx519_edge.cms import Cms, CmsError, build_options


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        for (method, prefix), handler in self.routes.items():
            if req.get_method() == method and req.full_url.split("?")[0].endswith(prefix):
                status, body = handler(req)
                if status >= 400:
                    raise urllib.error.HTTPError(req.full_url, status, "", {}, io.BytesIO(b"{}"))
                return FakeResponse(json.dumps(body).encode())
        raise AssertionError(f"想定外の呼び出し: {req.get_method()} {req.full_url}")


def test_build_options_counts_and_disambiguates():
    products = [
        {"productType": "Shirts", "categoryId": "gid://x/aa-1-13-7", "categoryName": "Shirts"},
        {"productType": "Shirts", "categoryId": "gid://x/aa-1-13-7", "categoryName": "Shirts"},
        {
            "productType": "Sweatshirts",
            "categoryId": "gid://x/aa-1-13-14",
            "categoryName": "Sweatshirts",
        },
        {
            "productType": "Sweatshirts",
            "categoryId": "gid://x/aa-1-1-7-4",
            "categoryName": "Sweatshirts",
        },
        {"productType": None, "categoryId": None, "categoryName": None},
    ]
    options = build_options(products, [{"id": 1, "name": "River"}])
    assert options["product_types"][0] == {"name": "Shirts", "count": 2}
    shirts = options["categories"][0]
    assert (
        shirts["label"] == "Shirts" and shirts["count"] == 2 and shirts["product_type"] == "Shirts"
    )
    labels = {c["label"] for c in options["categories"]}
    assert "Sweatshirts（aa-1-13-14）" in labels and "Sweatshirts（aa-1-1-7-4）" in labels
    assert options["brands"] == [{"id": 1, "name": "River"}]


def test_create_draft_refuses_without_hold_field():
    opener = FakeOpener({("GET", "/api/products"): lambda req: (400, {})})
    cms = Cms("https://cms.example", opener=opener)
    with pytest.raises(CmsError, match="保留"):
        cms.create_draft("used", "Jacket", 1, None, None, None)
    assert all(r.get_method() == "GET" for r in opener.requests)


def test_create_draft_sends_held_product():
    created = {}

    def create(req):
        created.update(json.loads(req.data))
        return 201, {"doc": {"id": 7, "title": "[River] Jacket [USED]", "kind": "used"}}

    opener = FakeOpener(
        {
            ("GET", "/api/products"): lambda req: (200, {"docs": []}),
            ("POST", "/api/products"): create,
        }
    )
    cms = Cms("https://cms.example", opener=opener)
    product = cms.create_draft(
        "used", "Jacket", 3, "Coats & Jackets", "gid://x/aa-1-10-2", "Coats & Jackets"
    )
    assert product["id"] == 7 and product["title"] == "[River] Jacket [USED]"
    assert created["shopifyHold"] is True
    assert created["status"] == "draft"
    assert created["variants"] == [{"price": 0}]
    assert created["autoTitle"] is True and created["brand"] == 3
