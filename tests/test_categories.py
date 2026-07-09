"""Tests for the categories CRUD API and bucketing behaviour."""
from app.category_defaults import DEFAULT_CATEGORIES

INCOME_NAMES = {n for n, _, b, _ in DEFAULT_CATEGORIES if b == "income"}
INTERNAL_NAMES = {n for n, _, b, _ in DEFAULT_CATEGORIES if b == "internal"}
SPEND_NAMES = {n for n, _, b, _ in DEFAULT_CATEGORIES if b == "spend"}


# ── GET /api/categories ───────────────────────────────────────────────────────

def test_list_categories_returns_15_defaults(client):
    r = client.get("/api/categories")
    assert r.status_code == 200
    assert len(r.json()) == 15


def test_list_categories_schema(client):
    first = client.get("/api/categories").json()[0]
    for field in ("id", "name", "color", "bucket", "description", "sort_order"):
        assert field in first


def test_list_categories_correct_buckets(client):
    data = client.get("/api/categories").json()
    buckets = {d["name"]: d["bucket"] for d in data}
    for name in INCOME_NAMES:
        assert buckets[name] == "income"
    for name in INTERNAL_NAMES:
        assert buckets[name] == "internal"
    for name in SPEND_NAMES:
        assert buckets[name] == "spend"


def test_list_categories_sorted_by_sort_order(client):
    data = client.get("/api/categories").json()
    orders = [d["sort_order"] for d in data]
    assert orders == sorted(orders)


def test_list_categories_colors_are_hex(client):
    data = client.get("/api/categories").json()
    for cat in data:
        assert cat["color"].startswith("#"), f"{cat['name']} color {cat['color']!r} not hex"
        assert len(cat["color"]) == 7


# ── POST /api/categories ──────────────────────────────────────────────────────

def test_create_category(client):
    r = client.post("/api/categories", json={
        "name": "Gifts", "color": "#ffaacc", "bucket": "spend",
        "description": "Birthday and holiday gifts",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Gifts"
    assert body["color"] == "#ffaacc"
    assert body["bucket"] == "spend"


def test_create_category_appears_in_list(client):
    client.post("/api/categories", json={"name": "Gifts", "color": "#aabb00", "bucket": "spend"})
    names = [c["name"] for c in client.get("/api/categories").json()]
    assert "Gifts" in names


def test_create_category_sort_order_appended(client):
    data_before = client.get("/api/categories").json()
    max_order = max(c["sort_order"] for c in data_before)
    r = client.post("/api/categories", json={"name": "Gifts", "color": "#000000", "bucket": "spend"})
    assert r.json()["sort_order"] == max_order + 1


def test_create_category_duplicate_name_409(client):
    r = client.post("/api/categories", json={"name": "Groceries", "color": "#aabbcc", "bucket": "spend"})
    assert r.status_code == 409


def test_create_category_invalid_bucket_422(client):
    r = client.post("/api/categories", json={"name": "X", "color": "#aabbcc", "bucket": "invalid"})
    assert r.status_code == 422


def test_create_category_all_valid_buckets(client):
    for bucket in ("income", "spend", "internal"):
        r = client.post("/api/categories", json={
            "name": f"Test {bucket}", "color": "#aabbcc", "bucket": bucket,
        })
        assert r.status_code == 201, f"bucket={bucket!r} failed: {r.text}"


# ── PATCH /api/categories/{name} ─────────────────────────────────────────────

def test_patch_category_color(client):
    r = client.patch("/api/categories/Groceries", json={"color": "#123456"})
    assert r.status_code == 200
    assert r.json()["color"] == "#123456"
    assert r.json()["name"] == "Groceries"  # name unchanged


def test_patch_category_bucket(client):
    r = client.patch("/api/categories/Groceries", json={"bucket": "income"})
    assert r.status_code == 200
    assert r.json()["bucket"] == "income"


def test_patch_category_description(client):
    r = client.patch("/api/categories/Groceries", json={"description": "Updated!"})
    assert r.status_code == 200
    assert r.json()["description"] == "Updated!"


def test_patch_category_invalid_bucket_422(client):
    r = client.patch("/api/categories/Groceries", json={"bucket": "bogus"})
    assert r.status_code == 422


def test_patch_category_not_found_404(client):
    r = client.patch("/api/categories/Nonexistent", json={"color": "#aabbcc"})
    assert r.status_code == 404


# ── DELETE /api/categories/{name} ────────────────────────────────────────────

def test_delete_category(client):
    r = client.delete("/api/categories/Groceries")
    assert r.status_code == 204
    names = [c["name"] for c in client.get("/api/categories").json()]
    assert "Groceries" not in names


def test_delete_category_not_found_404(client):
    r = client.delete("/api/categories/Nonexistent")
    assert r.status_code == 404


# ── Integration: rules validation uses DB categories ─────────────────────────

def test_rule_accepts_custom_category(client):
    """A user-created category is immediately valid for rules."""
    client.post("/api/categories", json={"name": "Gifts", "color": "#aabbcc", "bucket": "spend"})
    r = client.post("/api/rules", json={
        "description": "Gift Shop", "category": "Gifts", "importer": "Capital One Credit Card",
    })
    assert r.status_code == 201


def test_rule_rejects_deleted_category(client):
    """Once a category is deleted, new rules for it are rejected."""
    client.delete("/api/categories/Groceries")
    r = client.post("/api/rules", json={
        "description": "Supermarket", "category": "Groceries",
        "importer": "Capital One Credit Card",
    })
    assert r.status_code == 422


# ── Integration: _exclude_internal uses bucket column ────────────────────────

def test_summary_excludes_internal_bucket(client):
    """Categories with bucket='internal' are excluded from spend summaries."""
    from tests.conftest import make_tx

    make_tx(client, description="Groceries txn", amount=-50.0, date="2026-05-01",
            model_category="Groceries", model_confidence=8)
    # Internal transaction: excluded because model_category is in the internal bucket.
    make_tx(client, description="Card payment", amount=100.0, date="2026-05-02",
            model_category="Payments", model_confidence=8)

    totals = {r["category"]: r["total"]
              for r in client.get("/api/summary/by-category-for-period?year=2026").json()}
    assert "Groceries" in totals
    assert "Payments" not in totals


def test_summary_excludes_custom_internal_category(client):
    """A user-created internal-bucket category is also excluded from summaries."""
    from tests.conftest import make_tx

    client.post("/api/categories", json={"name": "Sweep", "color": "#aabbcc", "bucket": "internal"})
    make_tx(client, description="Sweep txn", amount=-200.0, date="2026-05-01",
            model_category="Sweep", model_confidence=8)
    totals = {r["category"]: r["total"]
              for r in client.get("/api/summary/by-category-for-period?year=2026").json()}
    assert "Sweep" not in totals
