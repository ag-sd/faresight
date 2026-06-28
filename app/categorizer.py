"""AI transaction categorization via a local Ollama server.

Imported transactions are saved with model_confidence=-1 (pending). A background
async task (_categorization_loop) polls for those rows, annotates them with a
*suggested* category + confidence (model_category, model_confidence), and writes
the results back. The suggestion never overwrites the human-facing `category` field.

The HTTP/subprocess touch-points are isolated as small module-level helpers
(`_get_tags`, `_start_ollama`, `_generate`) so tests can monkeypatch them
without a real Ollama server.

model_confidence sentinel values:
  None  — legacy only; not present in normal flow after the -1 default was introduced
  -1    — pending: queued for categorization, not yet processed (default for all new rows)
  0–10  — categorized (0 = fallback / low confidence, 10 = highest)
"""
import asyncio
import json
import logging
import subprocess
import time
from typing import TYPE_CHECKING, Optional

import httpx

from app.config import CATEGORIZATION_POLL_INTERVAL_S, OLLAMA_HOST, OLLAMA_MODEL

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

BATCH_SIZE = 5

# Single source of truth shared by the prompt builder and the validator.
ALLOWED_CATEGORIES = [
    "Groceries",
    "Dining & Takeout",
    "Transportation",
    "Housing & Utilities",
    "Shopping",
    "Health & Personal Care",
    "Entertainment & Subscriptions",
    "Travel",
    "Income",
    "Transfers & Fees",
    "Other",
]

# Fallback used when the model output is invalid, missing, or unparseable.
FALLBACK_CATEGORY = "Other"
FALLBACK_CONFIDENCE = 0

_STARTUP_TIMEOUT_S = 30
_CONFIDENCE_MIN = 0
_CONFIDENCE_MAX = 10

# Sentinel written to model_confidence when a transaction is queued for categorization.
PENDING_CONFIDENCE = -1

# ── Throughput tracking state ────────────────────────────────────────────────────
# Read by GET /api/categorizer/status to surface ETA to the upload page.

_cat_status: dict = {
    # Exponential moving average of categorization throughput in tx/sec.
    # Measured end-to-end between consecutive productive cycles (includes the
    # sleep between them), so it reflects the user-visible rate rather than
    # raw Ollama inference speed.  None until at least two productive cycles
    # have been observed (need a before/after pair to compute elapsed time).
    "throughput_ema": None,
    # time.monotonic() recorded at the end of the last cycle that processed
    # at least one row.  Used as the start reference for the next interval.
    "last_cycle_end": None,
}

# EMA smoothing factor.  0.3 means each new cycle contributes ~30%; after
# three cycles the most recent data carries ~66% of the total weight.
# Fast enough to track genuine speed changes; slow enough to ignore spikes.
_EMA_ALPHA = 0.3


# ── Ollama HTTP/process seams (monkeypatched in tests) ──────────────────────────

def _get_tags() -> list[str]:
    """Return the list of model names from {OLLAMA_HOST}/api/tags.

    Raises httpx.HTTPError / ConnectError if the server is unreachable.
    """
    resp = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
    resp.raise_for_status()
    return [m.get("name", "") for m in resp.json().get("models", [])]


def _start_ollama() -> None:
    """Launch `ollama serve` as a detached background process (never killed)."""
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _generate(prompt: str) -> str:
    """POST a prompt to {OLLAMA_HOST}/api/generate and return the raw response text."""
    resp = httpx.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
            "prompt": prompt,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["response"]


# ── Lifecycle ───────────────────────────────────────────────────────────────────

def ensure_ollama_running() -> None:
    """Make sure Ollama is up and the configured model (OLLAMA_MODEL) is available.

    Health-checks /api/tags; if the server is down, starts `ollama serve` and
    polls for up to 30s. Raises RuntimeError if the server never comes up or the
    required model is missing. Does NOT shut Ollama down afterwards.
    """
    try:
        models = _get_tags()
    except (httpx.HTTPError, OSError):
        logger.info("Ollama not reachable; starting `ollama serve`")
        _start_ollama()
        models = _wait_for_ollama()

    if OLLAMA_MODEL not in models:
        raise RuntimeError(
            f"Ollama is running but model {OLLAMA_MODEL!r} is not available. "
            f"Pull it with: ollama pull {OLLAMA_MODEL}"
        )


def _wait_for_ollama() -> list[str]:
    """Poll /api/tags every 1s for up to 30s; return models or raise RuntimeError."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            return _get_tags()
        except (httpx.HTTPError, OSError):
            time.sleep(1)
    raise RuntimeError(
        f"Ollama did not become reachable at {OLLAMA_HOST} within {_STARTUP_TIMEOUT_S}s"
    )


# ── Prompt (STUB — real prompt engineering lands later) ─────────────────────────

def build_prompt(batch: list[dict]) -> str:
    """Build the inference prompt for a batch of transactions."""
    return (
        "You are a transaction categorization engine. Assign exactly ONE category to each\n"
        "transaction from the allowed list below, and rate your confidence in that choice.\n"
        "Use the merchant/description and the amount (negative = money out, positive =\n"
        "money in) as signals.\n"
        "\n"
        "ALLOWED CATEGORIES (use these labels exactly):\n"
        "- Groceries: supermarkets, grocery and food markets\n"
        "- Dining & Takeout: restaurants, cafes, bars, coffee, food delivery\n"
        "- Transportation: gas, parking, rideshare, transit, tolls, auto maintenance\n"
        "- Housing & Utilities: rent, mortgage, electric, water, gas, internet, phone\n"
        "- Shopping: retail, online marketplaces, clothing, electronics, household goods\n"
        "- Health & Personal Care: pharmacy, doctors, dental, gym, salons\n"
        "- Entertainment & Subscriptions: streaming, games, events, movies, memberships\n"
        "- Travel: flights, hotels, car rentals, vacation spend\n"
        "- Income: paychecks, deposits, refunds, interest, dividends\n"
        "- Transfers & Fees: account transfers, ATM, bank fees, card payments, taxes, loans\n"
        "- Other: use ONLY when no category above clearly fits\n"
        "\n"
        "CONFIDENCE SCORE (integer 0-10):\n"
        "- 10 = the description names a known merchant that maps cleanly to one category.\n"
        "- 7-9 = strong signal, very likely correct.\n"
        "- 4-6 = plausible but ambiguous; description is vague or could fit two categories.\n"
        "- 1-3 = weak guess; little to go on.\n"
        "- 0 = no basis to categorize (assign \"Other\").\n"
        "Score honestly. A wrong high-confidence score is worse than an honest low one.\n"
        "\n"
        "RULES:\n"
        "- Pick the single best category. Do not invent new labels.\n"
        "- A positive amount is usually Income or Transfers & Fees, not a spending category.\n"
        "- If genuinely unsure, use \"Other\" and a low confidence score.\n"
        "\n"
        "EXAMPLES:\n"
        "Input: {\"id\": 1, \"description\": \"TRADER JOE'S #542\", \"amount\": -48.30}\n"
        "Output: {\"id\": 1, \"category\": \"Groceries\", \"confidence\": 10}\n"
        "Input: {\"id\": 2, \"description\": \"SQ *MERCHANT\", \"amount\": -12.00}\n"
        "Output: {\"id\": 2, \"category\": \"Other\", \"confidence\": 2}\n"
        "Input: {\"id\": 3, \"description\": \"PAYROLL DIRECT DEP ACME CORP\", \"amount\": 3200.00}\n"
        "Output: {\"id\": 3, \"category\": \"Income\", \"confidence\": 10}\n"
        "\n"
        "Categorize these transactions. Respond with ONLY a JSON object in this exact shape,\n"
        "no prose, no markdown:\n"
        "{\"results\": [{\"id\": <id>, \"category\": \"<category>\", \"confidence\": <0-10>}, ...]}\n"
        "\n"
        "TRANSACTIONS:\n"
        + json.dumps(batch)
    )


# ── Parse + validate ────────────────────────────────────────────────────────────

# Case-insensitive lookup → canonical casing.
_CANONICAL = {c.lower(): c for c in ALLOWED_CATEGORIES}


def _coerce_confidence(raw) -> int:
    """Coerce a model confidence to an int clamped to [0, 10]; 0 on failure."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return FALLBACK_CONFIDENCE
    return max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, value))


def _apply_results(results: list[dict], index: dict[int, "TransactionCreate"]) -> int:
    """Write back model_category/model_confidence onto the batch's transactions.

    `index` maps run-level id → TransactionCreate. Returns the number of rows
    that fell back to the FALLBACK_CATEGORY.
    """
    fell_back = 0
    seen: set[int] = set()

    for item in results:
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        tx = index.get(tid)
        if tx is None:  # unknown id
            continue
        seen.add(tid)

        raw_cat = item.get("category")
        canonical = _CANONICAL.get(str(raw_cat).strip().lower()) if raw_cat else None
        if canonical is None:  # invalid / missing category
            tx.model_category = FALLBACK_CATEGORY
            tx.model_confidence = FALLBACK_CONFIDENCE
            fell_back += 1
        else:
            tx.model_category = canonical
            tx.model_confidence = _coerce_confidence(item.get("confidence"))

    # Any transaction the model never spoke to falls back too.
    for tid, tx in index.items():
        if tid not in seen:
            tx.model_category = FALLBACK_CATEGORY
            tx.model_confidence = FALLBACK_CONFIDENCE
            fell_back += 1

    return fell_back


def _parse_results(raw: str) -> list[dict]:
    """Parse the model's JSON output into a list of result dicts; raise on drift."""
    data = json.loads(raw)
    results = data["results"]
    if not isinstance(results, list):
        raise ValueError("`results` is not a list")
    return results


def _mark_batch_fallback(index: dict[int, "TransactionCreate"]) -> None:
    for tx in index.values():
        tx.model_category = FALLBACK_CATEGORY
        tx.model_confidence = FALLBACK_CONFIDENCE


# ── Batch orchestration ─────────────────────────────────────────────────────────

def _categorize_batch(
    items: list[dict], index: dict[int, "TransactionCreate"]
) -> tuple[int, int, bool]:
    """Infer + validate one batch. Retries once on failure, then falls back whole.

    Returns (processed, fell_back, retried).
    """
    retried = False
    for attempt in range(2):
        try:
            raw = _generate(build_prompt(items))
            results = _parse_results(raw)
        except Exception as e:  # noqa: BLE001 — small models drift; be defensive
            if attempt == 0:
                retried = True
                logger.warning("Batch inference failed (%s); retrying once", e)
                continue
            logger.warning("Batch inference failed again (%s); marking needs-attention", e)
            _mark_batch_fallback(index)
            return len(index), len(index), retried
        fell_back = _apply_results(results, index)
        return len(index), fell_back, retried

    # Unreachable, but keeps type-checkers happy.
    return len(index), len(index), retried


def categorize_transactions(
    transactions: list["TransactionCreate"],
) -> list["TransactionCreate"]:
    """Annotate each transaction with a suggested model_category/model_confidence.

    Processes in batches of BATCH_SIZE. A failure in one batch never aborts the
    whole run. Mutates and returns the same list.
    """
    if not transactions:
        return transactions

    ensure_ollama_running()

    for start in range(0, len(transactions), BATCH_SIZE):
        batch = transactions[start:start + BATCH_SIZE]
        index = {start + i: tx for i, tx in enumerate(batch)}
        items = [
            {"id": start + i, "description": tx.description, "amount": tx.amount}
            for i, tx in enumerate(batch)
        ]
        batch_no = start // BATCH_SIZE + 1
        try:
            processed, fell_back, retried = _categorize_batch(items, index)
        except Exception as e:  # noqa: BLE001 — isolate unexpected per-batch errors
            logger.warning("Batch %d crashed (%s); marking needs-attention", batch_no, e)
            _mark_batch_fallback(index)
            processed, fell_back, retried = len(index), len(index), False
        logger.info(
            "Batch %d: processed=%d fell_back_to_%s=%d retried=%s",
            batch_no, processed, FALLBACK_CATEGORY, fell_back, retried,
        )

    return transactions


# ── Background polling worker ───────────────────────────────────────────────────

def _categorize_pending(db) -> int:
    """Query rows with model_confidence=-1, categorize them, write results back.

    Uses the provided SQLAlchemy session (caller manages lifecycle). Returns the
    number of rows processed. Skips rows with model_confidence=None (manually
    created transactions that were never submitted for categorization).
    """
    from app.models import Transaction
    from app.schemas import TransactionCreate

    rows = db.query(Transaction).filter(
        Transaction.model_confidence == PENDING_CONFIDENCE
    ).all()
    if not rows:
        logger.info("Categorizer: no pending transactions")
        return 0

    logger.info("Categorizer: found %d pending transaction(s) — starting inference", len(rows))

    txs = [
        TransactionCreate(
            date=row.date,
            description=row.description,
            amount=row.amount,
            category=row.category,
            model_confidence=PENDING_CONFIDENCE,
        )
        for row in rows
    ]

    categorize_transactions(txs)

    for row, tx in zip(rows, txs):
        row.model_category = tx.model_category
        row.model_confidence = tx.model_confidence

    db.commit()
    logger.info("Categorizer: finished — %d transaction(s) written back", len(rows))

    # Update throughput EMA — only on productive cycles.
    # Elapsed time is measured end-to-end between consecutive productive cycles
    # (wall time including the sleep), giving the user-visible categorization rate.
    now = time.monotonic()
    prev_end = _cat_status["last_cycle_end"]
    if prev_end is not None:
        elapsed = now - prev_end
        if elapsed > 0:
            cycle_tput = len(rows) / elapsed
            prev_ema = _cat_status["throughput_ema"]
            _cat_status["throughput_ema"] = (
                cycle_tput if prev_ema is None        # first observation — no prior EMA
                else _EMA_ALPHA * cycle_tput + (1 - _EMA_ALPHA) * prev_ema
            )
    # Record end-of-cycle timestamp as the baseline for the next interval.
    _cat_status["last_cycle_end"] = now

    return len(rows)


async def _categorization_loop() -> None:
    """Background async task: polls every CATEGORIZATION_POLL_INTERVAL_S seconds
    for transactions with model_confidence=-1 and categorizes them."""
    from app.database import SessionLocal

    logger.info(
        "Categorization loop started (poll interval: %ds)", CATEGORIZATION_POLL_INTERVAL_S
    )
    while True:
        await asyncio.sleep(CATEGORIZATION_POLL_INTERVAL_S)
        logger.info("Categorizer: poll cycle waking up")
        db = SessionLocal()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _categorize_pending, db)
        except Exception:
            logger.warning("Categorization poll cycle failed", exc_info=True)
        finally:
            db.close()
