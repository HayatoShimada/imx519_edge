"""85store-cms（Payload）の REST を呼ぶ。

85pi はタグなしの管理者の端末なので、CMS の Tailscale の whois 認証をそのまま通る
（API キーは要らない）。
撮影時に作る下書きは、CMS の「保留」（shopifyHold）で Shopify に送らずに止めておく。
保留の欄が無い CMS では、下書きを作るとすぐ Shopify に作られてしまうので、作らない。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable

KINDS = [
    {"value": "used", "label": "古着"},
    {"value": "new", "label": "新品"},
    {"value": "consignment", "label": "委託"},
]
PRODUCT_FIELDS = ["title", "kind", "productType", "categoryId", "categoryName", "status"]


class CmsError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Cms:
    def __init__(self, url: str, timeout_s: float = 20.0, cache_s: float = 600.0, opener=None):
        self.url = url.rstrip("/")
        self.timeout_s, self.cache_s = timeout_s, cache_s
        self.opener = opener or urllib.request.urlopen
        self._cache: dict[str, tuple[float, object]] = {}

    def _request(self, method: str, path: str, params: dict | None = None, body=None):
        url = f"{self.url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener(req, timeout=self.timeout_s) as res:
                return json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise CmsError(f"CMS {method} {path}: {e.code} {detail}", e.code) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise CmsError(f"CMS に届きません: {e}") from e

    def _cached(self, key: str, load: Callable[[], object], refresh: bool = False):
        hit = self._cache.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < self.cache_s:
            return hit[1]
        value = load()
        self._cache[key] = (time.monotonic(), value)
        return value

    def health(self) -> dict:
        """CMS に届くか、誰として認証されるか（30 秒覚えておく）。"""

        def load() -> dict:
            start = time.monotonic()
            try:
                me = self._request("GET", "/api/users/me") or {}
                user = (me.get("user") or {}).get("role")
                return {
                    "ok": user is not None,
                    "ms": _ms(start),
                    "role": user,
                    "error": None if user else "CMS に認証されませんでした",
                }
            except CmsError as e:
                return {"ok": False, "ms": _ms(start), "role": None, "error": str(e)}

        hit = self._cache.get("health")
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
        value = load()
        self._cache["health"] = (time.monotonic(), value)
        return value

    def hold_supported(self, refresh: bool = False) -> bool:
        """CMS に保留の欄（shopifyHold）があるか。無い欄で絞り込むと Payload は 400 を返す。"""

        def load() -> bool:
            try:
                self._request("GET", "/api/products", _held_params(limit=1))
                return True
            except CmsError as e:
                if e.status == 400:
                    return False
                raise

        return bool(self._cached("hold", load, refresh))

    def options(self, refresh: bool = False) -> dict:
        """撮影時に選ぶ値（区分・ブランド・Shopify のカテゴリ・品目）。既存の商品から作る。"""

        def load() -> dict:
            select = ["productType", "categoryId", "categoryName"]
            products = self._request(
                "GET",
                "/api/products",
                {"limit": 0, "depth": 0, **{f"select[{f}]": "true" for f in select}},
            )["docs"]
            brands = self._request("GET", "/api/brands", {"limit": 0, "depth": 0, "sort": "name"})[
                "docs"
            ]
            return build_options(products, brands)

        return self._cached("options", load, refresh)

    def held_products(self) -> list[dict]:
        docs = self._request("GET", "/api/products", _held_params(limit=50))["docs"]
        return [summary(d) for d in docs]

    def product(self, product_id: int) -> dict:
        return summary(self._request("GET", f"/api/products/{product_id}", {"depth": 0}))

    def create_brand(self, name: str) -> dict:
        doc = self._request("POST", "/api/brands", body={"name": name})["doc"]
        self._cache.pop("options", None)
        return {"id": doc["id"], "name": doc["name"]}

    def create_draft(
        self,
        kind: str,
        name: str,
        brand_id: int | None,
        product_type: str | None,
        category_id: str | None,
        category_name: str | None,
    ) -> dict:
        """保留の下書きを作る。価格・SKU・原価は、あとで CMS で入れてから保留を外す。"""
        if not self.hold_supported(refresh=True):
            raise CmsError("CMS に保留の欄がまだ無いため、下書きを作れません（CMS の更新待ち）")
        body = {
            "kind": kind,
            "brand": brand_id,
            "name": name,
            "autoTitle": True,
            "title": name,  # 必須の欄なので仮に入れる（保存時に CMS がブランドと品名から作り直す）
            "productType": product_type,
            "categoryId": category_id,
            "categoryName": category_name,
            "status": "draft",
            "variants": [{"price": 0}],
            "shopifyHold": True,
        }
        doc = self._request("POST", "/api/products", body=body)["doc"]
        return summary(doc)


def _ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def _held_params(limit: int) -> dict:
    return {
        "where[shopifyHold][equals]": "true",
        "limit": limit,
        "depth": 0,
        "sort": "-updatedAt",
        **{f"select[{f}]": "true" for f in PRODUCT_FIELDS},
    }


def summary(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "title": doc.get("title"),
        "kind": doc.get("kind"),
        "product_type": doc.get("productType"),
        "category_id": doc.get("categoryId"),
        "category_name": doc.get("categoryName"),
        "status": doc.get("status"),
    }


def build_options(products: list[dict], brands: list[dict]) -> dict:
    types = Counter(p["productType"] for p in products if p.get("productType"))
    cats = Counter(
        (p["categoryId"], p.get("categoryName") or "") for p in products if p.get("categoryId")
    )
    pairs = Counter(
        (p["categoryId"], p["productType"])
        for p in products
        if p.get("categoryId") and p.get("productType")
    )
    names = Counter(name for _, name in cats)
    categories = []
    for (cid, name), count in cats.most_common():
        typical = [t for (c, t), _ in pairs.most_common() if c == cid]
        # 同じ名前で ID の違うカテゴリは、ID の末尾を添えて区別する
        label = f"{name}（{cid.rsplit('/', 1)[-1]}）" if names[name] > 1 else name
        categories.append(
            {
                "id": cid,
                "name": name,
                "label": label,
                "count": count,
                "product_type": typical[0] if typical else None,
            }
        )
    return {
        "kinds": KINDS,
        "brands": [{"id": b["id"], "name": b["name"]} for b in brands],
        "categories": categories,
        "product_types": [{"name": t, "count": n} for t, n in types.most_common()],
    }
