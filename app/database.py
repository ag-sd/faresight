from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import LOCAL_DB_PATH

LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{LOCAL_DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_db() -> None:
    """Non-destructively evolve the accounts table schema on existing SQLite DBs."""
    with engine.connect() as conn:
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

        conn.commit()
