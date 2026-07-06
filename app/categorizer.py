"""AI transaction categorization via a local Ollama server.

Imported transactions are saved with model_confidence=-1 (pending). This module
runs as a standalone subprocess (`python -m app.categorizer`) that polls for
those rows, annotates them with a *suggested* category + confidence
(model_category, model_confidence), and writes the results back. The suggestion
never overwrites the human-facing `category` field.

Run independently for debugging:
    FARESIGHT_DB=/path/to/db.db python -m app.categorizer

The HTTP/subprocess touch-points are isolated as small module-level helpers
(`_get_tags`, `_start_ollama`, `_generate`) so tests can monkeypatch them
without a real Ollama server.

model_confidence sentinel values:
  None  — legacy only; not present in normal flow after the -1 default was introduced
  -1    — pending: queued for categorization, not yet processed (default for all new rows)
  0–10  — categorized (0 = fallback / low confidence, 10 = highest)
"""
import json
import logging
import subprocess
import time
from typing import TYPE_CHECKING, Optional

import httpx
from sqlalchemy import update

from app.config import CATEGORIZATION_POLL_INTERVAL_S, OLLAMA_HOST, OLLAMA_MODEL

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

BATCH_SIZE = 5

# Fallback used when the model output is invalid, missing, or unparseable.
# Kept as a constant: it's a sentinel, not a category from the DB list.
FALLBACK_CATEGORY = "Other"
FALLBACK_CONFIDENCE = 0

_STARTUP_TIMEOUT_S = 30
_CONFIDENCE_MIN = 0
_CONFIDENCE_MAX = 10

# Sentinel written to model_confidence when a transaction is queued for categorization.
PENDING_CONFIDENCE = -1


# ── Category data (loaded from DB each cycle) ──────────────────────────────────

def _load_category_data(db: "Session") -> dict:
    """Query the categories table and return the data the categorizer needs.

    Loaded once per poll cycle so user edits take effect without a restart.
    Returns a dict with:
      allowed  — list[str] of valid category names in sort_order
      canonical — {lower_name: canonical_name} for case-insensitive matching
      block     — formatted "- name: description" string for prompt injection
    """
    from app.models import Category
    rows = db.query(Category).order_by(Category.sort_order, Category.name).all()
    allowed = [r.name for r in rows]
    canonical = {r.name.lower(): r.name for r in rows}
    block = "\n".join(
        f"- {r.name}: {r.description or r.name}" for r in rows
    )
    return {"allowed": allowed, "canonical": canonical, "block": block}


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
    t0 = time.monotonic()
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
    logger.info("Ollama inference: %.2fs", time.monotonic() - t0)
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


# ── Prompt  ─────────────────────────

def build_prompt(batch: list[dict], category_block: str) -> str:
    """Build the inference prompt for a batch of transactions."""
    return (
        "You are a transaction categorization engine. Assign exactly ONE category to each\n"
        "transaction from the allowed list below, and rate your confidence in that choice.\n"
        "Use the merchant/description and the amount (negative = money out, positive =\n"
        "money in) as signals.\n"
        "\n"
        "ALLOWED CATEGORIES (use these labels exactly):\n"
        f"{category_block}\n"
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
        "- A positive amount is usually Income, Interest Income, or Transfers, not a spending category.\n"
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

def _coerce_confidence(raw) -> int:
    """Coerce a model confidence to an int clamped to [0, 10]; 0 on failure."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return FALLBACK_CONFIDENCE
    return max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, value))


def _apply_results(
    results: list[dict],
    index: dict[int, "TransactionCreate"],
    canonical_map: dict[str, str],
) -> int:
    """Write back model_category/model_confidence onto the batch's transactions.

    `index` maps run-level id → TransactionCreate. `canonical_map` is a
    {lower_name: canonical_name} lookup built from the categories table.
    Returns the number of rows that fell back to the FALLBACK_CATEGORY.
    """
    fell_back = 0
    seen: set[int] = set()

    for item in results:
        logger.info(f"Processing {item}")
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        tx = index.get(tid)
        if tx is None:  # unknown id
            continue
        seen.add(tid)

        raw_cat = item.get("category")
        canonical = canonical_map.get(str(raw_cat).strip().lower()) if raw_cat else None
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
    items: list[dict],
    index: dict[int, "TransactionCreate"],
    cat_data: dict,
) -> tuple[int, int, bool]:
    """Infer + validate one batch. Retries once on failure, then falls back whole.

    Returns (processed, fell_back, retried).
    """
    retried = False
    for attempt in range(2):
        try:
            prompt = build_prompt(items, cat_data["block"])
            logger.info(f"Making call to Ollama")
            raw = _generate(prompt)
            results = _parse_results(raw)
            logger.info(f"Ollama returned results {results}")
        except Exception as e:  # noqa: BLE001 — small models drift; be defensive
            if attempt == 0:
                retried = True
                logger.warning("Batch inference failed (%s); retrying once", e)
                continue
            logger.warning("Batch inference failed again (%s); marking needs-attention", e)
            _mark_batch_fallback(index)
            return len(index), len(index), retried
        fell_back = _apply_results(results, index, cat_data["canonical"])
        return len(index), fell_back, retried

    # Unreachable, but keeps type-checkers happy.
    return len(index), len(index), retried


# ── Background polling worker ───────────────────────────────────────────────────

def _categorize_pending(db) -> int:
    """Query rows with model_confidence=-1, categorize and commit them batch by batch.

    Processes BATCH_SIZE rows at a time, committing each batch to the DB before
    starting the next. A crash mid-cycle leaves already-committed batches intact;
    remaining rows stay at -1 and are picked up on the next poll cycle.

    Uses the provided SQLAlchemy session (caller manages lifecycle). Returns the
    number of rows processed. Skips rows with model_confidence=None (legacy rows
    that were never submitted for categorization).
    """
    from app.models import Transaction
    from app.schemas import TransactionCreate

    cat_data = _load_category_data(db)
    rows = db.query(Transaction).filter(
        Transaction.model_confidence == PENDING_CONFIDENCE
    ).all()
    if not rows:
        logger.info("Categorizer: no pending transactions")
        return 0

    logger.info("Categorizer: found %d pending transaction(s) — starting inference", len(rows))
    cycle_start = time.monotonic()

    total_processed = 0
    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[batch_start : batch_start + BATCH_SIZE]
        batch_no = batch_start // BATCH_SIZE + 1

        # Health-check per batch: Ollama may have exited between batches.
        ensure_ollama_running()

        txs = [
            TransactionCreate(
                date=row.date,
                description=row.description,
                amount=row.amount,
                category=row.category,
                model_confidence=PENDING_CONFIDENCE,
            )
            for row in batch_rows
        ]
        index = {i: tx for i, tx in enumerate(txs)}
        items = [
            {"id": i, "description": tx.description, "amount": tx.amount}
            for i, tx in enumerate(txs)
        ]

        try:
            processed, fell_back, retried = _categorize_batch(items, index, cat_data)
        except Exception as e:  # noqa: BLE001 — isolate unexpected per-batch errors
            logger.warning("Batch %d crashed (%s); marking fallback", batch_no, e)
            _mark_batch_fallback(index)
            processed, fell_back, retried = len(index), len(index), False

        skipped = 0
        for row, tx in zip(batch_rows, txs):
            result = db.execute(
                update(Transaction)
                .where(
                    Transaction.id == row.id,
                    Transaction.model_confidence == PENDING_CONFIDENCE,
                    Transaction.user_modified_category == False,
                )
                .values(
                    model_category=tx.model_category,
                    model_confidence=tx.model_confidence,
                )
            )
            if result.rowcount == 0:
                skipped += 1
                logger.info(
                    "Categorizer: skipped row %d — user-modified or no longer pending",
                    row.id,
                )

        db.commit()
        total_processed += len(batch_rows) - skipped
        logger.info(
            "Batch %d: processed=%d fell_back_to_%s=%d retried=%s skipped=%d (committed)",
            batch_no, processed, FALLBACK_CATEGORY, fell_back, retried, skipped,
        )

    logger.info(
        "Categorizer: cycle complete — %d written back in %.2fs",
        total_processed, time.monotonic() - cycle_start,
    )
    return total_processed


if __name__ == "__main__":
    import signal
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    from app.database import SessionLocal

    _shutdown = False

    def _handle_term(signum, frame):
        global _shutdown
        logger.info("Categorizer: SIGTERM received, shutting down")
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_term)

    logger.info("Categorizer worker started (poll interval: %ds)", CATEGORIZATION_POLL_INTERVAL_S)
    while not _shutdown:
        time.sleep(CATEGORIZATION_POLL_INTERVAL_S)
        if _shutdown:
            break
        logger.info("Categorizer: poll cycle waking up")
        db = SessionLocal()
        try:
            _categorize_pending(db)
        except Exception:
            logger.warning("Categorization poll cycle failed", exc_info=True)
        finally:
            db.close()
    logger.info("Categorizer: worker exiting")
