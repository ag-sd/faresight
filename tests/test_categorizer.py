"""Tests for the Ollama categorizer — never touches a real Ollama server."""
import json
from datetime import date

import httpx
import pytest

import app.categorizer as cz
from app.categorizer import OLLAMA_MODEL, PENDING_CONFIDENCE
from app.models import Transaction
from app.schemas import TransactionCreate, TransactionOut
from tests.conftest import TestingSession


def tx(description="Thing", amount=-10.0):
    return TransactionCreate(
        date="2026-01-01", description=description, amount=amount, category="Uncategorized"
    )


# ── ensure_ollama_running ───────────────────────────────────────────────────────

def test_ensure_passes_when_already_up(monkeypatch):
    monkeypatch.setattr(cz, "_get_tags", lambda: [OLLAMA_MODEL, "other:latest"])
    cz.ensure_ollama_running()  # must not raise


def test_ensure_raises_when_model_missing(monkeypatch):
    monkeypatch.setattr(cz, "_get_tags", lambda: ["some-other-model:latest"])
    with pytest.raises(RuntimeError, match="not available"):
        cz.ensure_ollama_running()


def test_ensure_starts_ollama_when_down_then_succeeds(monkeypatch):
    calls = {"tags": 0, "start": 0}

    def fake_get_tags():
        calls["tags"] += 1
        if calls["tags"] == 1:
            raise httpx.ConnectError("connection refused")
        return [OLLAMA_MODEL]

    monkeypatch.setattr(cz, "_get_tags", fake_get_tags)
    monkeypatch.setattr(cz, "_start_ollama", lambda: calls.__setitem__("start", calls["start"] + 1))
    monkeypatch.setattr(cz.time, "sleep", lambda _s: None)

    cz.ensure_ollama_running()

    assert calls["start"] == 1
    assert calls["tags"] == 2  # first fails, polled call succeeds


def test_ensure_raises_when_never_comes_up(monkeypatch):
    monkeypatch.setattr(cz, "_get_tags", lambda: (_ for _ in ()).throw(httpx.ConnectError("x")))
    monkeypatch.setattr(cz, "_start_ollama", lambda: None)
    monkeypatch.setattr(cz.time, "sleep", lambda _s: None)

    ticks = [0.0]

    def fake_monotonic():
        ticks[0] += 10
        return ticks[0]

    monkeypatch.setattr(cz.time, "monotonic", fake_monotonic)

    with pytest.raises(RuntimeError, match="did not become reachable"):
        cz.ensure_ollama_running()


# ── confidence coercion ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (8, 8), (0, 0), (10, 10),
    (99, 10), (-5, 0),       # clamped
    ("7", 7),                # numeric string coerced
    ("high", 0), (None, 0),  # unparseable → 0
])
def test_coerce_confidence(raw, expected):
    assert cz._coerce_confidence(raw) == expected


# ── _apply_results ──────────────────────────────────────────────────────────────

def test_apply_valid_results_with_canonical_casing():
    txs = [tx(), tx()]
    index = {0: txs[0], 1: txs[1]}
    results = [
        {"id": 0, "category": "groceries", "confidence": 8},        # lowercase
        {"id": 1, "category": "DINING & TAKEOUT", "confidence": 5},  # uppercase
    ]
    assert cz._apply_results(results, index) == 0
    assert txs[0].model_category == "Groceries"
    assert txs[0].model_confidence == 8
    assert txs[1].model_category == "Dining & Takeout"
    assert txs[1].model_confidence == 5


def test_apply_clamps_confidence():
    t = tx()
    cz._apply_results([{"id": 0, "category": "Travel", "confidence": 42}], {0: t})
    assert t.model_confidence == 10


def test_apply_invalid_category_falls_back():
    t = tx()
    fb = cz._apply_results([{"id": 0, "category": "Nonsense", "confidence": 9}], {0: t})
    assert fb == 1
    assert t.model_category == "Other"
    assert t.model_confidence == 0


def test_apply_unknown_id_ignored_and_row_falls_back():
    t = tx()
    fb = cz._apply_results([{"id": 99, "category": "Travel", "confidence": 9}], {0: t})
    assert fb == 1
    assert t.model_category == "Other"
    assert t.model_confidence == 0


def test_apply_missing_row_falls_back():
    txs = [tx(), tx()]
    index = {0: txs[0], 1: txs[1]}
    fb = cz._apply_results([{"id": 0, "category": "Travel", "confidence": 6}], index)
    assert fb == 1
    assert txs[0].model_category == "Travel"
    assert txs[1].model_category == "Other"
    assert txs[1].model_confidence == 0


# ── build_prompt (stub sanity) ──────────────────────────────────────────────────

def test_build_prompt_includes_batch_and_categories():
    prompt = cz.build_prompt([{"id": 0, "description": "Blue Bottle Coffee", "amount": -5.0}])
    assert "Blue Bottle Coffee" in prompt
    for category in cz.ALLOWED_CATEGORIES:
        assert category in prompt
    assert "Score honestly" in prompt  # rubric present — not the old stub


def test_apply_other_from_model_preserves_confidence():
    t = tx()
    fb = cz._apply_results([{"id": 0, "category": "Other", "confidence": 3}], {0: t})
    assert fb == 0
    assert t.model_category == "Other"
    assert t.model_confidence == 3


# ── categorize_transactions (full pipeline) ─────────────────────────────────────

def _good_generate(prompt):
    batch = json.loads(prompt.split("TRANSACTIONS:\n", 1)[1])
    return json.dumps({
        "results": [{"id": it["id"], "category": "Shopping", "confidence": 6} for it in batch]
    })


def test_categorize_empty_is_noop_and_skips_ollama(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running",
                        lambda: pytest.fail("should not be called for empty input"))
    assert cz.categorize_transactions([]) == []


def test_categorize_batches_by_batch_size(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    calls = []

    def spy_generate(prompt):
        calls.append(prompt)
        return _good_generate(prompt)

    monkeypatch.setattr(cz, "_generate", spy_generate)
    txs = [tx(f"t{i}") for i in range(cz.BATCH_SIZE * 4 + 3)]
    cz.categorize_transactions(txs)

    assert len(calls) == 5  # 4 full batches + 1 partial
    assert all(t.model_category == "Shopping" and t.model_confidence == 6 for t in txs)


def test_categorize_isolates_per_batch_failure(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)

    def flaky_generate(prompt):
        batch = json.loads(prompt.split("TRANSACTIONS:\n", 1)[1])
        if batch[0]["id"] >= 20:  # second batch always fails
            raise RuntimeError("boom")
        return _good_generate(prompt)

    monkeypatch.setattr(cz, "_generate", flaky_generate)
    txs = [tx(f"t{i}") for i in range(25)]
    cz.categorize_transactions(txs)

    assert all(t.model_category == "Shopping" for t in txs[:20])
    assert all(t.model_category == "Other" and t.model_confidence == 0 for t in txs[20:])


def test_categorize_retries_once_then_falls_back_on_parse_failure(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    calls = []

    def bad_generate(prompt):
        calls.append(prompt)
        return "this is not json"

    monkeypatch.setattr(cz, "_generate", bad_generate)
    txs = [tx("a"), tx("b"), tx("c")]
    cz.categorize_transactions(txs)

    assert len(calls) == 2  # original + one retry
    assert all(t.model_category == "Other" and t.model_confidence == 0 for t in txs)


def test_categorize_isolates_unexpected_batch_crash(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(cz, "_categorize_batch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom")))
    txs = [tx("a"), tx("b")]
    cz.categorize_transactions(txs)  # must not raise
    assert all(t.model_category == "Other" and t.model_confidence == 0 for t in txs)


# ── _categorize_pending ──────────────────────────────────────────────────────────

def _make_row(db, description="Coffee", amount=-5.0, confidence=PENDING_CONFIDENCE):
    row = Transaction(
        date=date(2026, 1, 1),
        description=description,
        amount=amount,
        category="Uncategorized",
        model_confidence=confidence,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_categorize_pending_noop_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running",
                        lambda: pytest.fail("should not call Ollama"))
    db = TestingSession()
    try:
        count = cz._categorize_pending(db)
        assert count == 0
    finally:
        db.close()


def test_categorize_pending_categorizes_minus_one_rows(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(cz, "_generate", _good_generate)

    db = TestingSession()
    try:
        row = _make_row(db)
        count = cz._categorize_pending(db)
        assert count == 1
        db.refresh(row)
        assert row.model_category == "Shopping"
        assert row.model_confidence == 6
    finally:
        db.close()


def test_categorize_pending_ignores_null_confidence(monkeypatch):
    # Use raw SQL to bypass the ORM default (-1) and force a legacy NULL row.
    from sqlalchemy import text
    monkeypatch.setattr(cz, "ensure_ollama_running",
                        lambda: pytest.fail("should not call Ollama"))
    db = TestingSession()
    try:
        db.execute(text(
            "INSERT INTO transactions (date, description, amount, category, model_confidence)"
            " VALUES ('2026-01-01', 'Coffee', -5.0, 'Uncategorized', NULL)"
        ))
        db.commit()
        count = cz._categorize_pending(db)
        assert count == 0
    finally:
        db.close()


def test_categorize_pending_ignores_already_categorized(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running",
                        lambda: pytest.fail("should not call Ollama"))
    db = TestingSession()
    try:
        _make_row(db, confidence=8)
        count = cz._categorize_pending(db)
        assert count == 0
    finally:
        db.close()


def test_categorize_pending_rows_stay_pending_when_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(cz, "ensure_ollama_running",
                        lambda: (_ for _ in ()).throw(RuntimeError("Ollama down")))
    db = TestingSession()
    try:
        row = _make_row(db)
        with pytest.raises(RuntimeError, match="Ollama down"):
            cz._categorize_pending(db)
        db.refresh(row)
        assert row.model_confidence == PENDING_CONFIDENCE
    finally:
        db.close()


# ── TransactionOut sentinel mapping ─────────────────────────────────────────────

def _out(model_confidence):
    return TransactionOut(
        id=1,
        date=date(2026, 1, 1),
        description="Test",
        amount=-5.0,
        category="Uncategorized",
        created_at=date(2026, 1, 1),
        model_confidence=model_confidence,
    )


def test_transaction_out_maps_pending_to_null():
    assert _out(PENDING_CONFIDENCE).model_confidence is None


def test_transaction_out_preserves_categorized_confidence():
    assert _out(7).model_confidence == 7


def test_transaction_out_preserves_zero_confidence():
    assert _out(0).model_confidence == 0


def test_transaction_out_preserves_null_confidence():
    assert _out(None).model_confidence is None


# ── Throughput EMA ──────────────────────────────────────────────────────────────

@pytest.fixture()
def reset_cat_status():
    """Reset module-level EMA state before and after each EMA test."""
    cz._cat_status["throughput_ema"] = None
    cz._cat_status["last_cycle_end"] = None
    yield
    cz._cat_status["throughput_ema"] = None
    cz._cat_status["last_cycle_end"] = None


def test_throughput_ema_none_after_first_cycle(monkeypatch, reset_cat_status):
    """First productive cycle sets last_cycle_end but cannot compute EMA yet (no baseline)."""
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(cz, "_generate", _good_generate)
    ticks = iter([10.0])
    monkeypatch.setattr(cz.time, "monotonic", lambda: next(ticks))

    db = TestingSession()
    try:
        _make_row(db)
        cz._categorize_pending(db)
    finally:
        db.close()

    assert cz._cat_status["throughput_ema"] is None   # no prior baseline
    assert cz._cat_status["last_cycle_end"] == 10.0


def test_throughput_ema_computed_after_second_cycle(monkeypatch, reset_cat_status):
    """Second productive cycle has a baseline — EMA is set to the first observation."""
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(cz, "_generate", _good_generate)
    # Cycle 1 ends at t=10; cycle 2 ends at t=20 → elapsed=10s, 1 tx → 0.1 tx/s.
    ticks = iter([10.0, 20.0])
    monkeypatch.setattr(cz.time, "monotonic", lambda: next(ticks))

    db = TestingSession()
    try:
        _make_row(db, description="tx1")
        cz._categorize_pending(db)   # cycle 1: sets baseline, no EMA yet

        _make_row(db, description="tx2")
        cz._categorize_pending(db)   # cycle 2: elapsed=10s, EMA = 0.1
    finally:
        db.close()

    assert cz._cat_status["throughput_ema"] == pytest.approx(0.1)


def test_throughput_ema_smoothing(monkeypatch, reset_cat_status):
    """Third cycle with a different rate produces a blended EMA value."""
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(cz, "_generate", _good_generate)
    # Cycle 1: t=10 (baseline). Cycle 2: t=20 (10s, 0.1 tx/s). Cycle 3: t=25 (5s, 0.2 tx/s).
    ticks = iter([10.0, 20.0, 25.0])
    monkeypatch.setattr(cz.time, "monotonic", lambda: next(ticks))

    db = TestingSession()
    try:
        _make_row(db, description="tx1")
        cz._categorize_pending(db)   # cycle 1: baseline

        _make_row(db, description="tx2")
        cz._categorize_pending(db)   # cycle 2: EMA = 0.1

        _make_row(db, description="tx3")
        cz._categorize_pending(db)   # cycle 3: rate=0.2, EMA = 0.3*0.2 + 0.7*0.1
    finally:
        db.close()

    expected = cz._EMA_ALPHA * 0.2 + (1 - cz._EMA_ALPHA) * 0.1
    assert cz._cat_status["throughput_ema"] == pytest.approx(expected)
