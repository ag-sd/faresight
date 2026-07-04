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
        assert f"- {category}:" in prompt  # rendered as a described allow-list line
    assert "Score honestly" in prompt  # rubric present — not the old stub


def test_allowed_categories_derives_from_descriptions():
    # CATEGORY_DESCRIPTIONS is the single source of truth; the allow-list and the
    # prompt block are both derived from it and can never drift.
    assert list(cz.CATEGORY_DESCRIPTIONS) == cz.ALLOWED_CATEGORIES
    for cat, desc in cz.CATEGORY_DESCRIPTIONS.items():
        assert isinstance(desc, str) and desc.strip(), f"{cat} missing a description"

    prompt = cz.build_prompt([{"id": 0, "description": "x", "amount": -1.0}])
    for cat, desc in cz.CATEGORY_DESCRIPTIONS.items():
        assert f"- {cat}: {desc}" in prompt


def test_build_prompt_uses_split_transfer_categories():
    # "Transfers & Fees" was split into four distinct labels.
    prompt = cz.build_prompt([{"id": 0, "description": "x", "amount": -1.0}])
    assert "Transfers & Fees" not in prompt
    for category in ("Transfers", "Fees", "Interest Income", "Interest Paid"):
        assert category in cz.ALLOWED_CATEGORIES
        assert category in prompt


def test_apply_other_from_model_preserves_confidence():
    t = tx()
    fb = cz._apply_results([{"id": 0, "category": "Other", "confidence": 3}], {0: t})
    assert fb == 0
    assert t.model_category == "Other"
    assert t.model_confidence == 3


# ── _categorize_pending ──────────────────────────────────────────────────────────

def _good_generate(prompt):
    batch = json.loads(prompt.split("TRANSACTIONS:\n", 1)[1])
    return json.dumps({
        "results": [{"id": it["id"], "category": "Shopping", "confidence": 6} for it in batch]
    })

def _make_row(db, description="Coffee", amount=-5.0, confidence=PENDING_CONFIDENCE):
    from app.models import FileImport
    fi = FileImport(filename="test.csv", rows_seen=1, rows_persisted=0)
    db.add(fi)
    db.flush()
    row = Transaction(
        date=date(2026, 1, 1),
        description=description,
        amount=amount,
        category="Uncategorized",
        model_confidence=confidence,
        file_id=fi.id,
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
    from app.models import FileImport
    db = TestingSession()
    try:
        fi = FileImport(filename="test.csv", rows_seen=1, rows_persisted=0)
        db.add(fi)
        db.flush()
        db.execute(text(
            "INSERT INTO transactions (date, description, amount, category, model_confidence, user_modified_category, file_id)"
            f" VALUES ('2026-01-01', 'Coffee', -5.0, 'Uncategorized', NULL, 0, {fi.id})"
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


def test_categorize_pending_commits_each_batch_independently(monkeypatch):
    """First batch committed before Ollama dies on the second batch check."""
    call_count = {"n": 0}

    def ollama_dies_on_second_call():
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise RuntimeError("Ollama died")

    monkeypatch.setattr(cz, "ensure_ollama_running", ollama_dies_on_second_call)
    monkeypatch.setattr(cz, "_generate", _good_generate)

    db = TestingSession()
    try:
        for i in range(cz.BATCH_SIZE + 1):
            _make_row(db, description=f"tx{i}")

        with pytest.raises(RuntimeError, match="Ollama died"):
            cz._categorize_pending(db)

        committed = db.query(Transaction).filter(
            Transaction.model_confidence != PENDING_CONFIDENCE
        ).count()
        assert committed == cz.BATCH_SIZE
    finally:
        db.close()


def test_categorize_pending_user_modified_row_is_skipped(monkeypatch):
    """Row queried as pending but user-modified during Ollama inference is not overwritten."""
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)

    db = TestingSession()
    try:
        row = _make_row(db)

        def interleave_user_edit(prompt):
            # Simulate user patching the row while Ollama is processing
            db.query(Transaction).filter(Transaction.id == row.id).update({
                "model_confidence": 10,
                "model_category": "Income",
                "user_modified_category": True,
            })
            return _good_generate(prompt)

        monkeypatch.setattr(cz, "_generate", interleave_user_edit)

        count = cz._categorize_pending(db)

        assert count == 0  # skipped rows are not counted as processed
        db.refresh(row)
        assert row.model_confidence == 10
        assert row.model_category == "Income"
        assert row.user_modified_category is True
    finally:
        db.close()


def test_categorize_pending_unmodified_row_in_same_batch_gets_model_result(monkeypatch):
    """In a mixed batch, modified row is skipped and unmodified row gets the model's result."""
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)

    db = TestingSession()
    try:
        user_row = _make_row(db, description="User edited")
        model_row = _make_row(db, description="Untouched")

        def interleave_user_edit(prompt):
            db.query(Transaction).filter(Transaction.id == user_row.id).update({
                "model_confidence": 10,
                "model_category": "Income",
                "user_modified_category": True,
            })
            return _good_generate(prompt)

        monkeypatch.setattr(cz, "_generate", interleave_user_edit)

        count = cz._categorize_pending(db)

        assert count == 1  # only model_row was written back

        db.refresh(user_row)
        assert user_row.model_confidence == 10
        assert user_row.model_category == "Income"
        assert user_row.user_modified_category is True

        db.refresh(model_row)
        assert model_row.model_category == "Shopping"
        assert model_row.model_confidence == 6
        assert model_row.user_modified_category is False
    finally:
        db.close()


def test_categorize_pending_normal_cycle_no_interleaving_regression(monkeypatch):
    """Regression: without interleaving all pending rows are categorized and counted."""
    monkeypatch.setattr(cz, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(cz, "_generate", _good_generate)

    db = TestingSession()
    try:
        row1 = _make_row(db, description="tx1")
        row2 = _make_row(db, description="tx2")

        count = cz._categorize_pending(db)

        assert count == 2
        for row in (row1, row2):
            db.refresh(row)
            assert row.model_category == "Shopping"
            assert row.model_confidence == 6
    finally:
        db.close()


def test_categorize_pending_checks_ollama_per_batch(monkeypatch):
    """ensure_ollama_running is called once per batch, not once per cycle."""
    calls = {"n": 0}
    monkeypatch.setattr(cz, "ensure_ollama_running",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(cz, "_generate", _good_generate)

    db = TestingSession()
    try:
        for i in range(cz.BATCH_SIZE + 1):  # two batches
            _make_row(db, description=f"tx{i}")
        cz._categorize_pending(db)
        assert calls["n"] == 2
    finally:
        db.close()


# ── TransactionOut sentinel mapping ─────────────────────────────────────────────

def _out(model_confidence):
    return TransactionOut(
        id=1,
        file_id=1,
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


