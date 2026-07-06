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


def test_migrate_backfills_dedup_hash():
    """Legacy rows (dedup_hash IS NULL) get their identity hash backfilled so
    pre-existing history participates in re-import dedupe. Running migrate_db
    twice must not change the result."""
    from app.models import dedup_hash_for
    from datetime import date

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO file_imports (filename, rows_seen, rows_persisted) "
                "VALUES ('t.csv', 1, 1)"
            ))
            fid = conn.execute(text("SELECT id FROM file_imports LIMIT 1")).scalar()
            conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(date, description, amount, category, file_id, "
                    " model_confidence, user_modified_category, dedup_hash) "
                    "VALUES ('2026-01-15', 'Legacy row', -12.5, 'Food', :fid, -1, 0, NULL)"
                ),
                {"fid": fid},
            )

        migrate_db()
        migrate_db()  # idempotent — second run is a no-op

        with engine.connect() as conn:
            got = conn.execute(text("SELECT dedup_hash FROM transactions")).scalar()
        assert got == dedup_hash_for(None, date(2026, 1, 15), "Legacy row", -12.5)
    finally:
        Base.metadata.drop_all(bind=engine)


def test_migrate_adds_file_import_columns():
    """content_hash / rows_skipped land on a legacy file_imports table."""
    # migrate_db expects the other tables to exist; only file_imports is
    # rebuilt in its legacy (pre-dedupe) shape.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE file_imports"))
            conn.execute(text(
                "CREATE TABLE file_imports ("
                " id INTEGER PRIMARY KEY, filename VARCHAR(255) NOT NULL,"
                " rows_seen INTEGER NOT NULL, rows_persisted INTEGER NOT NULL,"
                " loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            ))
            conn.execute(text(
                "INSERT INTO file_imports (filename, rows_seen, rows_persisted) "
                "VALUES ('old.csv', 3, 3)"
            ))

        migrate_db()

        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(file_imports)"))}
            assert {"content_hash", "rows_skipped"} <= cols
            row = conn.execute(text(
                "SELECT content_hash, rows_skipped FROM file_imports"
            )).one()
        assert row[0] is None
        assert row[1] == 0
    finally:
        Base.metadata.drop_all(bind=engine)


def test_migrate_adds_reference_number():
    """reference_number lands on a legacy transactions table that predates it,
    idempotently, and existing content-based dedup_hashes are left untouched."""
    from app.models import dedup_hash_for
    from datetime import date

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            # Simulate a pre-feature DB: drop the column, seed a legacy row whose
            # identity is content-based.
            conn.execute(text("ALTER TABLE transactions DROP COLUMN reference_number"))
            conn.execute(text(
                "INSERT INTO file_imports (filename, rows_seen, rows_persisted) "
                "VALUES ('t.csv', 1, 1)"
            ))
            fid = conn.execute(text("SELECT id FROM file_imports LIMIT 1")).scalar()
            legacy_hash = dedup_hash_for(None, date(2026, 1, 15), "Legacy row", -12.5)
            conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(date, description, amount, category, file_id, "
                    " model_confidence, user_modified_category, dedup_hash) "
                    "VALUES ('2026-01-15', 'Legacy row', -12.5, 'Food', :fid, -1, 0, :h)"
                ),
                {"fid": fid, "h": legacy_hash},
            )

        migrate_db()
        migrate_db()  # idempotent — second run is a no-op

        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(transactions)"))}
            assert "reference_number" in cols
            row = conn.execute(text(
                "SELECT reference_number, dedup_hash FROM transactions"
            )).one()
        assert row[0] is None            # legacy rows have no reference number
        assert row[1] == legacy_hash     # content-based identity untouched
    finally:
        Base.metadata.drop_all(bind=engine)


def test_migrate_creates_balance_history():
    """balance_history is created on a legacy DB that predates it, and the
    migration is idempotent (safe to run on every boot)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE balance_history"))

        migrate_db()
        migrate_db()  # idempotent — second run is a no-op

        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(balance_history)"))}
            assert {"id", "account_id", "balance", "as_of", "created_at"} <= cols
            indexes = {r[1] for r in conn.execute(text("PRAGMA index_list(balance_history)"))}
            assert "ix_balance_history_account_id" in indexes
    finally:
        Base.metadata.drop_all(bind=engine)
