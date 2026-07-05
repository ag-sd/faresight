from contextlib import asynccontextmanager, suppress
import asyncio
import logging
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import jinja2
from fastapi.templating import Jinja2Templates

from app.config import TOP_CARD_PAGE_LIMIT
from app.database import Base, engine, migrate_db
from app.routers import accounts, rules, sync, transactions
import app.sync as sync_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")


def _spawn_categorizer() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "app.categorizer"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_db()
    sync_mod.sync_from_nas()
    # Pool connections opened before the sync still hold the pre-replacement
    # file descriptor; dispose so the next request opens a fresh connection
    # to the NAS-pulled file.
    engine.dispose()
    sync_task = asyncio.create_task(sync_mod._periodic_sync_loop())
    app.state.cat_proc = _spawn_categorizer()
    yield
    sync_task.cancel()
    with suppress(asyncio.CancelledError):
        await sync_task
    app.state.cat_proc.terminate()
    try:
        app.state.cat_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        app.state.cat_proc.kill()
    sync_mod.sync_to_nas()
    sync_mod._release_lock()


app = FastAPI(title="Faresight — Expense Tracker", lifespan=lifespan)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(rules.router)
app.include_router(sync.router)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(FRONTEND_DIR),
    autoescape=jinja2.select_autoescape(),
    cache_size=0,
)
templates = Jinja2Templates(env=_jinja_env)


@app.get("/api/config")
def frontend_config():
    """Config values the frontend needs at boot."""
    return {"top_card_page_limit": TOP_CARD_PAGE_LIMIT}


@app.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "app/pages/index.html")


@app.get("/accounts")
def accounts_page(request: Request):
    return templates.TemplateResponse(request, "app/pages/accounts.html")


@app.get("/upload")
def upload_page(request: Request):
    return templates.TemplateResponse(request, "app/pages/upload.html")
