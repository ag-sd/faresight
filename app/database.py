from datetime import date

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import LOCAL_DB_PATH

LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{LOCAL_DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # The categorizer runs as a separate process against the same file; wait
    # for its write locks instead of failing with "database is locked".
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_db() -> None:
    """Non-destructively evolve table schemas on existing SQLite DBs."""
    with engine.connect() as conn:
        # ── accounts ──────────────────────────────────────────────────────────
        rows = conn.execute(text("PRAGMA table_info(accounts)")).fetchall()
        existing = {row[1] for row in rows}

        # Add columns introduced in the source-account linking feature.
        new_columns = [
            ("source_account_id", "INTEGER REFERENCES accounts(id)"),
            ("source_amount",     "REAL"),
            ("source_frequency",  "VARCHAR(10)"),
            ("current_balance",   "REAL"),
            ("default_importer",  "VARCHAR(100)"),
        ]
        for col_name, col_def in new_columns:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}"))

        # Rename name→bank, nickname→name (SQLite 3.25+).
        for old, new in [("name", "bank"), ("nickname", "name")]:
            if old in existing and new not in existing:
                conn.execute(text(f"ALTER TABLE accounts RENAME COLUMN {old} TO {new}"))

        # ── transactions ──────────────────────────────────────────────────────
        rows = conn.execute(text("PRAGMA table_info(transactions)")).fetchall()
        tx_existing = {row[1] for row in rows}

        # Category unification: the old human-facing `category` column becomes
        # `bank_category` (raw bank label, LLM hint only); model_category is the
        # canonical display category.
        if "category" in tx_existing and "bank_category" not in tx_existing:
            conn.execute(text("ALTER TABLE transactions RENAME COLUMN category TO bank_category"))

        # Drop legacy free-text source column if still present.
        if "source" in tx_existing:
            conn.execute(text("ALTER TABLE transactions DROP COLUMN source"))

        # source_account_id was briefly used; rename to the canonical account_id.
        if "source_account_id" in tx_existing and "account_id" not in tx_existing:
            conn.execute(text("ALTER TABLE transactions RENAME COLUMN source_account_id TO account_id"))
        elif "account_id" not in tx_existing and "source_account_id" not in tx_existing:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN account_id INTEGER REFERENCES accounts(id)"
            ))

        # AI categorization fields (suggested category + confidence for human review).
        tx_new_columns = [
            ("model_category",   "VARCHAR(100)"),
            ("model_confidence", "INTEGER"),
        ]
        for col_name, col_def in tx_new_columns:
            if col_name not in tx_existing:
                conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_def}"))

        if "user_modified_category" not in tx_existing:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN user_modified_category INTEGER NOT NULL DEFAULT 0"
            ))

        # Backfill pre-default-change NULL rows so they are queued for categorization.
        conn.execute(text(
            "UPDATE transactions SET model_confidence = -1 WHERE model_confidence IS NULL"
        ))

        # Category split: "Transfers & Fees" → Transfers / Fees / Interest Income /
        # Interest Paid. Re-queue model-suggested rows so the worker reassigns them;
        # skip user-edited rows (their write-back is guarded on user_modified_category).
        # Idempotent: once the worker rewrites model_category, the label no longer matches.
        conn.execute(text(
            "UPDATE transactions SET model_confidence = -1 "
            "WHERE model_category = 'Transfers & Fees' AND user_modified_category = 0"
        ))

        # Drop hash_code (first idempotency attempt, removed) and its unique
        # index — a UNIQUE hash rejects legitimate duplicate transactions.
        if "hash_code" in tx_existing:
            conn.execute(text("DROP INDEX IF EXISTS ix_transactions_hash_code"))
            conn.execute(text("ALTER TABLE transactions DROP COLUMN hash_code"))

        # Re-import dedupe identity (occurrence-counting, so NOT unique).
        # Deliberately named dedup_hash: the hash_code block above deletes that
        # name on every boot.
        if "dedup_hash" not in tx_existing:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN dedup_hash VARCHAR(64)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_transactions_dedup_hash ON transactions(dedup_hash)"
        ))

        # Stable bank transaction ID (e.g. BofA Reference Number). Nullable; legacy
        # rows keep their content-based dedup_hash (backfilled below) untouched.
        if "reference_number" not in tx_existing:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN reference_number VARCHAR(64)"))

        # Backfill legacy rows so pre-existing history participates in dedupe.
        # Idempotent via the IS NULL filter.
        from app.models import dedup_hash_for  # deferred: models imports Base from here

        legacy = conn.execute(text(
            "SELECT id, account_id, date, description, amount FROM transactions "
            "WHERE dedup_hash IS NULL"
        )).fetchall()
        for row_id, account_id, tx_date, description, amount in legacy:
            conn.execute(
                text("UPDATE transactions SET dedup_hash = :h WHERE id = :id"),
                {"h": dedup_hash_for(account_id, date.fromisoformat(str(tx_date)[:10]), description, amount),
                 "id": row_id},
            )

        if "note" in tx_existing:
            conn.execute(text("ALTER TABLE transactions DROP COLUMN note"))

        if "file_id" not in tx_existing:
            conn.execute(text("DELETE FROM transactions"))
            # Schema drift: fresh DBs (models.py) declare file_id NOT NULL; migrated DBs
            # get it as nullable because SQLite ALTER TABLE cannot add NOT NULL without a
            # default. Do not rewrite the table — both shapes satisfy our insert path.
            conn.execute(text("ALTER TABLE transactions ADD COLUMN file_id INTEGER"))

        # ── file_imports ──────────────────────────────────────────────────────
        rows = conn.execute(text("PRAGMA table_info(file_imports)")).fetchall()
        fi_existing = {row[1] for row in rows}

        if "account_id" not in fi_existing:
            conn.execute(text(
                "ALTER TABLE file_imports ADD COLUMN account_id INTEGER REFERENCES accounts(id)"
            ))

        if "importer" not in fi_existing:
            conn.execute(text("ALTER TABLE file_imports ADD COLUMN importer VARCHAR(100)"))

        if "content_hash" not in fi_existing:
            conn.execute(text("ALTER TABLE file_imports ADD COLUMN content_hash VARCHAR(64)"))

        if "rows_skipped" not in fi_existing:
            conn.execute(text(
                "ALTER TABLE file_imports ADD COLUMN rows_skipped INTEGER NOT NULL DEFAULT 0"
            ))

        # ── transaction_classification_rules ──────────────────────────────────
        # Schema: UNIQUE on (description, category) only — importer dropped.
        # SQLite cannot DROP COLUMN when a UNIQUE index covers it, so any table
        # that still has the importer column (or lacks the constraint entirely)
        # is rebuilt via rename → create → copy → drop.
        rules_ddl = """
            CREATE TABLE transaction_classification_rules (
                id          INTEGER PRIMARY KEY,
                description VARCHAR(255) NOT NULL,
                category    VARCHAR(100) NOT NULL,
                created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (description, category)
            )
        """
        rules_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'transaction_classification_rules'"
        )).scalar()
        if not rules_exists:
            conn.execute(text(rules_ddl))
        else:
            existing_rule_cols = {r[1] for r in conn.execute(text(
                "PRAGMA table_info(transaction_classification_rules)"
            )).fetchall()}
            # Rebuild if importer column still present or constraint is missing.
            needs_rebuild = "importer" in existing_rule_cols
            if not needs_rebuild:
                needs_rebuild = not any(
                    idx[2] and
                    {r[2] for r in conn.execute(
                        text(f"PRAGMA index_info('{idx[1]}')")
                    ).fetchall()} == {"description", "category"}
                    for idx in conn.execute(text(
                        "PRAGMA index_list('transaction_classification_rules')"
                    )).fetchall()
                )
            if needs_rebuild:
                conn.execute(text(
                    "ALTER TABLE transaction_classification_rules "
                    "RENAME TO transaction_classification_rules_old"
                ))
                conn.execute(text(rules_ddl))
                # OR IGNORE deduplicates rows that share (description, category)
                # after the importer column is dropped; oldest row (lowest id) wins.
                conn.execute(text(
                    "INSERT OR IGNORE INTO transaction_classification_rules "
                    "(id, description, category, created_at) "
                    "SELECT id, description, category, created_at "
                    "FROM transaction_classification_rules_old "
                    "ORDER BY created_at ASC"
                ))
                conn.execute(text("DROP TABLE transaction_classification_rules_old"))

        # ── categories ────────────────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY,
                name        VARCHAR(100) UNIQUE NOT NULL,
                color       VARCHAR(7)   NOT NULL DEFAULT '#6c757d',
                bucket      VARCHAR(20)  NOT NULL DEFAULT 'spend',
                description VARCHAR(500),
                sort_order  INTEGER      NOT NULL DEFAULT 0
            )
        """))
        # Seed defaults only on first creation; INSERT OR IGNORE is idempotent.
        seed_count = conn.execute(text("SELECT COUNT(*) FROM categories")).scalar()
        if seed_count == 0:
            from app.category_defaults import DEFAULT_CATEGORIES
            for i, (name, color, bucket, desc) in enumerate(DEFAULT_CATEGORIES):
                conn.execute(text(
                    "INSERT OR IGNORE INTO categories "
                    "(name, color, bucket, description, sort_order) "
                    "VALUES (:n, :c, :b, :d, :s)"
                ), {"n": name, "c": color, "b": bucket, "d": desc, "s": i})

        # ── balance_history ─────────────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS balance_history (
                id         INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                balance    REAL     NOT NULL,
                as_of      DATE     NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_balance_history_account_id ON balance_history(account_id)"
        ))

        conn.commit()
