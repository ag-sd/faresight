# verifier-server — Safe server launch for Faresight

**Always use this skill when verifying any server-side change in this project.**

## Critical rule

NEVER start the server without `FARESIGHT_DB` pointing at a temporary file.
The live DB at `~/.local/share/expense-tracker/local.db` contains real user data
and must never be touched during verification.

## Launch recipe

```bash
# 1. Create an isolated temp DB for this session
export FARESIGHT_DB=$(mktemp --suffix=.db)

# 2. Pick an unused port (avoids colliding with a running dev server)
export PORT=18765

# 3. Boot the server (venv must be active)
source .venv/bin/activate
FARESIGHT_DB=$FARESIGHT_DB uvicorn app.faresight:app --host 127.0.0.1 --port $PORT &
SERVER_PID=$!
sleep 2  # wait for startup

# 4. Run verifications against http://127.0.0.1:$PORT
# ... your curl / Playwright / CLI calls here ...

# 5. Tear down
kill $SERVER_PID 2>/dev/null
rm -f "$FARESIGHT_DB"
```

## Why this works

`app/config.py` checks `os.environ.get("FARESIGHT_DB")` first, before reading
`config.yaml`. Setting the env var routes the server to the temp file for its
entire lifetime — including the SQLAlchemy engine and all sync paths.

## Checklist before every verification session

- [ ] `FARESIGHT_DB` is set and points to a temp file (not the real path)
- [ ] Port is not 8000 (default uvicorn) — use 18765 or another non-default
- [ ] `kill` and `rm` are in the teardown, even if a step fails
