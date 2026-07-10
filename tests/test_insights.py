"""Insights: recurring-charge detection (unit + API), category trends, top merchants."""
from datetime import date

from app.insights import detect_recurring
from tests.conftest import make_tx


# ── detect_recurring — pure unit tests, no DB ─────────────────────────────────

AS_OF = date(2026, 7, 1)


def _monthly_dates(n, day=15, start_month=1, year=2026):
    return [date(year + (start_month + i - 1) // 12, (start_month + i - 1) % 12 + 1, day)
            for i in range(n)]


def test_monthly_cadence_detected():
    dates = [date(2026, 3, 15), date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)]
    hit = detect_recurring(dates, [-15.49] * 4, AS_OF)
    assert hit is not None
    assert hit["cadence"] == "monthly"
    assert hit["occurrences"] == 4
    assert hit["amount"] == -15.49
    assert hit["last_date"] == date(2026, 6, 15)


def test_weekly_cadence_detected():
    dates = [date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 22), date(2026, 6, 29)]
    hit = detect_recurring(dates, [-5.00] * 5, AS_OF)
    assert hit["cadence"] == "weekly"


def test_yearly_cadence_needs_only_two_occurrences():
    hit = detect_recurring([date(2025, 6, 20), date(2026, 6, 20)], [-99.00, -99.00], AS_OF)
    assert hit["cadence"] == "yearly"
    assert hit["occurrences"] == 2


def test_two_occurrences_not_enough_for_monthly():
    hit = detect_recurring([date(2026, 5, 15), date(2026, 6, 15)], [-15.49, -15.49], AS_OF)
    assert hit is None


def test_irregular_gaps_rejected():
    # 30-day then 10-day gap — median lands in the monthly band but gaps are inconsistent.
    dates = [date(2026, 4, 15), date(2026, 5, 15), date(2026, 5, 25), date(2026, 6, 24)]
    assert detect_recurring(dates, [-15.49] * 4, AS_OF) is None


def test_stale_subscription_excluded():
    # Last charge in January; as_of July — cancelled.
    dates = [date(2025, 11, 15), date(2025, 12, 15), date(2026, 1, 15)]
    assert detect_recurring(dates, [-15.49] * 3, AS_OF) is None


def test_next_expected_is_last_plus_median_gap():
    dates = [date(2026, 4, 10), date(2026, 5, 10), date(2026, 6, 10)]  # 30-day gaps
    hit = detect_recurring(dates, [-9.99] * 3, AS_OF)
    assert hit["next_expected"] == date(2026, 7, 10)


def test_price_change_flagged():
    dates = [date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)]
    hit = detect_recurring(dates, [-15.49, -15.49, -17.99], AS_OF)
    assert hit["price_changed"] is True
    assert hit["previous_amount"] == -15.49
    assert hit["amount"] == -17.99


def test_stable_price_not_flagged():
    dates = [date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)]
    hit = detect_recurring(dates, [-15.49] * 3, AS_OF)
    assert hit["price_changed"] is False
    assert hit["previous_amount"] is None


def test_unsorted_input_handled():
    dates = [date(2026, 6, 15), date(2026, 4, 15), date(2026, 5, 15)]
    hit = detect_recurring(dates, [-17.99, -15.49, -15.49], AS_OF)
    assert hit["amount"] == -17.99  # latest by date, not by input order


# ── /api/insights/recurring ───────────────────────────────────────────────────

def _seed_netflix(client, account_id=None, months=(4, 5, 6), amounts=None):
    amounts = amounts or [-15.49] * len(months)
    for m, a in zip(months, amounts):
        make_tx(client, date=f"2026-{m:02d}-15", amount=a,
                description="NETFLIX.COM", account_id=account_id)


def _recurring(client, as_of="2026-07-01"):
    r = client.get(f"/api/insights/recurring?as_of={as_of}")
    assert r.status_code == 200, r.text
    return r.json()


def test_recurring_detects_uncategorized_subscription(client):
    _seed_netflix(client)  # model_category stays NULL — still detected
    body = _recurring(client)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["description"] == "NETFLIX.COM"
    assert item["cadence"] == "monthly"
    assert body["monthly_total"] == -15.49


def test_recurring_excludes_internal_card_payments(client):
    for m in (4, 5, 6):
        make_tx(client, date=f"2026-{m:02d}-01", amount=-500.00,
                description="CAPITAL ONE AUTOPAY",
                model_category="Payments", model_confidence=10)
    assert _recurring(client)["items"] == []


def test_recurring_excludes_positive_amounts(client):
    for m in (4, 5, 6):
        make_tx(client, date=f"2026-{m:02d}-01", amount=3000.00,
                description="PAYROLL", model_category="Income", model_confidence=9)
    assert _recurring(client)["items"] == []


def test_recurring_groups_per_account(client):
    a1 = client.post("/api/accounts", json={"bank": "B", "name": "One",
        "account_number": "1", "account_type": "credit_card",
        "default_importer": "Capital One Credit Card"}).json()["id"]
    a2 = client.post("/api/accounts", json={"bank": "B", "name": "Two",
        "account_number": "2", "account_type": "credit_card",
        "default_importer": "Capital One Credit Card"}).json()["id"]
    _seed_netflix(client, account_id=a1)
    _seed_netflix(client, account_id=a2)
    items = _recurring(client)["items"]
    assert len(items) == 2
    assert {i["account_id"] for i in items} == {a1, a2}


def test_recurring_price_change_surfaces(client):
    _seed_netflix(client, amounts=[-15.49, -15.49, -17.99])
    item = _recurring(client)["items"][0]
    assert item["price_changed"] is True
    assert item["previous_amount"] == -15.49


def test_recurring_monthly_total_mixes_cadences(client):
    _seed_netflix(client)  # monthly −15.49
    for d in ("2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29"):
        make_tx(client, date=d, amount=-3.00, description="COFFEE CLUB")
    total = _recurring(client)["monthly_total"]
    assert total == round(-15.49 - 3.00 * 52 / 12, 2)


def test_recurring_sorted_by_next_expected(client):
    _seed_netflix(client)  # next ~Jul 15
    for d in ("2026-06-13", "2026-06-20", "2026-06-27"):  # next ~Jul 4
        make_tx(client, date=d, amount=-3.00, description="COFFEE CLUB")
    items = _recurring(client)["items"]
    assert [i["description"] for i in items] == ["COFFEE CLUB", "NETFLIX.COM"]


def test_recurring_one_offs_not_detected(client):
    make_tx(client, date="2026-06-10", amount=-42.00, description="HARDWARE STORE")
    assert _recurring(client)["items"] == []


# ── /api/insights/category-trends ─────────────────────────────────────────────

def _trends(client, year=2026, month=6, limit=10):
    r = client.get(f"/api/insights/category-trends?year={year}&month={month}&limit={limit}")
    assert r.status_code == 200, r.text
    return r.json()


def test_trends_delta_vs_previous_month(client):
    make_tx(client, date="2026-05-10", amount=-380.00,
            model_category="Groceries", model_confidence=8, description="May")
    make_tx(client, date="2026-06-10", amount=-450.00,
            model_category="Groceries", model_confidence=8, description="Jun")
    row = _trends(client)[0]
    assert row["category"] == "Groceries"
    assert row["current"] == -450.00
    assert row["previous"] == -380.00
    assert row["delta"] == -70.00  # more negative = spent more


def test_trends_january_rollover(client):
    make_tx(client, date="2025-12-10", amount=-100.00,
            model_category="Groceries", model_confidence=8)
    rows = _trends(client, year=2026, month=1)
    assert rows[0]["previous"] == -100.00


def test_trends_avg_3mo_full_window(client):
    for m, amt in ((3, -300.00), (4, -400.00), (5, -500.00)):
        make_tx(client, date=f"2026-{m:02d}-10", amount=amt,
                model_category="Groceries", model_confidence=8, description=f"m{m}")
    make_tx(client, date="2026-06-10", amount=-450.00,
            model_category="Groceries", model_confidence=8, description="jun")
    row = _trends(client)[0]
    assert row["avg_3mo"] == -400.00  # (−300 −400 −500) / 3


def test_trends_avg_divisor_shrinks_with_coverage(client):
    # Only one covered month in the window → divide by 1, not 3.
    make_tx(client, date="2026-05-10", amount=-300.00,
            model_category="Groceries", model_confidence=8)
    make_tx(client, date="2026-06-10", amount=-450.00,
            model_category="Groceries", model_confidence=8, description="jun")
    assert _trends(client)[0]["avg_3mo"] == -300.00


def test_trends_avg_none_without_prior_data(client):
    make_tx(client, date="2026-06-10", amount=-450.00,
            model_category="Groceries", model_confidence=8)
    assert _trends(client)[0]["avg_3mo"] is None


def test_trends_skips_uncategorized_and_nonspend(client):
    make_tx(client, date="2026-06-01", amount=-50.00, description="Uncat")
    make_tx(client, date="2026-06-02", amount=500.00,
            model_category="Payments", model_confidence=10, description="Pay")
    make_tx(client, date="2026-06-03", amount=3000.00,
            model_category="Income", model_confidence=9, description="Salary")
    assert _trends(client) == []


def test_trends_sorted_by_abs_delta_and_limited(client):
    make_tx(client, date="2026-06-01", amount=-10.00,
            model_category="Groceries", model_confidence=8, description="small")
    make_tx(client, date="2026-06-02", amount=-500.00,
            model_category="Travel", model_confidence=8, description="big")
    rows = _trends(client)
    assert [r["category"] for r in rows] == ["Travel", "Groceries"]
    assert len(_trends(client, limit=1)) == 1


def test_trends_invalid_month_422(client):
    assert client.get("/api/insights/category-trends?year=2026&month=0").status_code == 422


# ── /api/insights/top-merchants ───────────────────────────────────────────────

def test_top_merchants_groups_and_orders(client):
    for i in range(3):
        make_tx(client, date=f"2026-06-0{i + 1}", amount=-20.00, description="DOORDASH")
    make_tx(client, date="2026-06-05", amount=-45.00, description="MARKET")
    rows = client.get("/api/insights/top-merchants?year=2026&month=6").json()
    assert [(r["description"], r["total"], r["count"]) for r in rows] == [
        ("DOORDASH", -60.00, 3),
        ("MARKET", -45.00, 1),
    ]


def test_top_merchants_excludes_internal_includes_uncategorized(client):
    make_tx(client, date="2026-06-01", amount=-500.00, description="AUTOPAY",
            model_category="Payments", model_confidence=10)
    make_tx(client, date="2026-06-02", amount=-25.00, description="MYSTERY SHOP")
    rows = client.get("/api/insights/top-merchants?year=2026&month=6").json()
    assert [r["description"] for r in rows] == ["MYSTERY SHOP"]


def test_top_merchants_limit(client):
    for i in range(5):
        make_tx(client, date="2026-06-10", amount=-(i + 1.0), description=f"SHOP {i}")
    rows = client.get("/api/insights/top-merchants?year=2026&month=6&limit=2").json()
    assert len(rows) == 2
    assert rows[0]["description"] == "SHOP 4"  # biggest spend first


def test_top_merchants_full_year_when_month_omitted(client):
    make_tx(client, date="2026-01-10", amount=-10.00, description="SHOP")
    make_tx(client, date="2026-06-10", amount=-30.00, description="SHOP")
    rows = client.get("/api/insights/top-merchants?year=2026").json()
    assert rows == [{"description": "SHOP", "total": -40.00, "count": 2}]
