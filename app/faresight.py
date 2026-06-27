from contextlib import asynccontextmanager, suppress
from pathlib import Path
import asyncio

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine, migrate_db
from app.routers import accounts, sync, transactions
import app.sync as sync_mod


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_db()
    sync_mod.sync_from_nas()
    task = asyncio.create_task(sync_mod._periodic_sync_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    sync_mod.sync_to_nas()
    sync_mod._release_lock()


app = FastAPI(title="Faresight — Expense Tracker", lifespan=lifespan)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(sync.router)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=FileResponse)
def root():
    return FileResponse(FRONTEND_DIR / "app" / "pages" / "index.html")


@app.get("/accounts", response_class=FileResponse)
def accounts_page():
    return FileResponse(FRONTEND_DIR / "app" / "pages" / "accounts.html")


@app.get("/upload", response_class=FileResponse)
def upload_page():
    return FileResponse(FRONTEND_DIR / "app" / "pages" / "upload.html")
