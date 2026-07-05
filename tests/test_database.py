"""Engine pragma + migration tests — the web app and the categorizer subprocess
share this engine configuration against one SQLite file."""
from sqlalchemy import text

from app.database import Base, engine, migrate_db


def test_engine_pragmas():
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        # Cross-process writes must wait, not fail with "database is locked".
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_migrate_requeues_split_transfer_rows():
    """The "Transfers & Fees" split re-queues model-suggested rows (skipping
    user-edited ones) so the worker reassigns them to the new categories."""
    # Rebuild the file-backed engine schema for an isolated migration check;
    # the next TestClient lifespan recreates it, so leaving it dropped is fine.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO file_imports (filename, rows_seen, rows_persisted) "
                "VALUES ('t.csv', 1, 1)"
            ))
            fid = conn.execute(text("SELECT id FROM file_imports LIMIT 1")).scalar()
            for desc, edited in (("auto", 0), ("edited", 1)):
                conn.execute(
                    text(
                        "INSERT INTO transactions "
                        "(date, description, amount, category, file_id, "
                        " model_category, model_confidence, user_modified_category) "
                        "VALUES ('2026-01-01', :desc, -1.0, 'Food', :fid, "
                        " 'Transfers & Fees', 10, :edited)"
                    ),
                    {"desc": desc, "fid": fid, "edited": edited},
                )

        migrate_db()

        with engine.connect() as conn:
            conf = dict(conn.execute(text(
                "SELECT description, model_confidence FROM transactions"
            )).fetchall())
        assert conf["auto"] == -1    # re-queued for re-categorization
        assert conf["edited"] == 10  # user-edited row left untouched
    finally:
        Base.metadata.drop_all(bind=engine)
