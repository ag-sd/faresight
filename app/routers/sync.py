from fastapi import APIRouter

import app.sync as sync_mod

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/status")
def sync_status():
    return sync_mod.get_status()


@router.post("")
def push_sync():
    """Push local DB to NAS immediately. Also used for 'Proceed anyway' on lock conflict."""
    sync_mod.sync_to_nas()
    return sync_mod.get_status()


@router.post("/go-offline")
def go_offline():
    """Disable NAS sync for this session ('Work offline' on lock conflict)."""
    sync_mod.disable_sync()
    return sync_mod.get_status()
