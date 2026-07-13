"""Embedding-model management routes (admin only): status / download / delete."""

from fastapi import APIRouter, Depends

from app.services.auth import get_current_admin
from app.services.model_manager import model_manager

router = APIRouter(prefix="/api/embedding-model", tags=["EmbeddingModel"])


@router.get("/status")
async def get_embedding_model_status(current_user=Depends(get_current_admin)):
    """Return whether the local embedding model is installed + live download state."""
    state = model_manager.get_state()
    state["installed"] = model_manager.is_installed()
    return state


@router.post("/download")
async def download_embedding_model(current_user=Depends(get_current_admin)):
    """Trigger a background download of the configured embedding model."""
    return model_manager.start_download()


@router.delete("")
async def delete_embedding_model(current_user=Depends(get_current_admin)):
    """Remove the downloaded model to free space / force a re-download."""
    return model_manager.delete()
