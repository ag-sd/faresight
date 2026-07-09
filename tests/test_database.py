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


def _rules_unique_index_present(conn) -> bool:
    """True if transaction_classification_rules carries the UNIQUE
    (description, category, importer) constraint."""
    for idx in conn.execute(text(
        "PRAGMA index_list('transaction_classification_rules')"
    )).fetchall():
        if not idx[2]:  # unique flag
            continue
        cols = {r[2] for r in conn.execute(
            text(f"PRAGMA index_info('{idx[1]}')")
        ).fetchall()}
        if cols == {"description", "category", "importer"}:
            return True
    return False


def test_migrate_preserves_rules():
    """Rules on a correctly-shaped table survive migrate_db — the migration must
    be a no-op when the UNIQUE constraint is already present (regression: the
    table used to be dropped and recreated on every boot)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO transaction_classification_rules "
                "(description, category, importer) "
                "VALUES ('COFFEE SHOP', 'Food', 'Chase Credit Card')"
            ))
            rule_id = conn.execute(text(
                "SELECT id FROM transaction_classification_rules"
            )).scalar()

        migrate_db()
        migrate_db()  # idempotent — second run is a no-op

        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, description, category, importer "
                "FROM transaction_classification_rules"
            )).fetchall()
            assert rows == [(rule_id, "COFFEE SHOP", "Food", "Chase Credit Card")]
            assert _rules_unique_index_present(conn)
    finally:
        Base.metadata.drop_all(bind=engine)


def test_migrate_upgrades_legacy_rules_table():
    """A legacy table without the UNIQUE constraint is rebuilt in place: rows
    are preserved, duplicates collapse to the first occurrence, and the
    constraint lands. Second run is a no-op."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE transaction_classification_rules"))
            conn.execute(text(
                "CREATE TABLE transaction_classification_rules ("
                " id INTEGER PRIMARY KEY,"
                " description VARCHAR(255) NOT NULL,"
                " category VARCHAR(100) NOT NULL,"
                " importer VARCHAR(100) NOT NULL,"
                " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            ))
            for desc, cat in (("COFFEE SHOP", "Food"),
                              ("GYM", "Health"),
                              ("COFFEE SHOP", "Food")):  # exact duplicate
                conn.execute(text(
                    "INSERT INTO transaction_classification_rules "
                    "(description, category, importer) "
                    "VALUES (:d, :c, 'Chase Credit Card')"
                ), {"d": desc, "c": cat})

        migrate_db()
        migrate_db()  # idempotent — second run is a no-op

        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, description, category "
                "FROM transaction_classification_rules ORDER BY id"
            )).fetchall()
            # Duplicate collapsed to the first occurrence (lowest id).
            assert rows == [(1, "COFFEE SHOP", "Food"), (2, "GYM", "Health")]
            assert _rules_unique_index_present(conn)
            # Rebuild scaffolding cleaned up.
            leftover = conn.execute(text(
                "SELECT 1 FROM sqlite_master "
                "WHERE name = 'transaction_classification_rules_old'"
            )).scalar()
            assert leftover is None
    finally:
        Base.metadata.drop_all(bind=engine)


def test_migrate_creates_rules_table_when_absent():
    """A DB predating the rules feature gets the table with the constraint."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE transaction_classification_rules"))

        migrate_db()

        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text(
                "PRAGMA table_info(transaction_classification_rules)"
            ))}
            assert {"id", "description", "category", "importer", "created_at"} <= cols
            assert _rules_unique_index_present(conn)
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
