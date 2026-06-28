from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import LOCAL_DB_PATH

LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{LOCAL_DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _set_wal_mode(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
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

        # Backfill pre-default-change NULL rows so they are queued for categorization.
        conn.execute(text(
            "UPDATE transactions SET model_confidence = -1 WHERE model_confidence IS NULL"
        ))

        conn.commit()
