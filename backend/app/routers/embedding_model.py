"""Embedding-model management routes (admin only): status / download / delete / switch."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth import get_current_admin
from app.services.config_manager import config_manager
from app.services.model_manager import model_manager
from app.services.embedding_models import (
    EMBEDDING_MODEL_OPTIONS,
    is_known_model,
    known_dimension,
)

router = APIRouter(prefix="/api/embedding-model", tags=["EmbeddingModel"])


class DownloadRequest(BaseModel):
    model: str | None = None


class SwitchRequest(BaseModel):
    model: str
    force: bool = False


@router.get("/status")
async def get_embedding_model_status(current_user=Depends(get_current_admin)):
    """Return the configured model, candidate options, install list, and live download state."""
    configured = config_manager.embedding_model
    state = model_manager.get_state()
    state["configured_model"] = configured
    state["installed"] = model_manager.is_installed(configured)
    state["installed_models"] = model_manager.list_installed_models()
    state["options"] = EMBEDDING_MODEL_OPTIONS
    return state


@router.post("/download")
async def download_embedding_model(
    body: DownloadRequest | None = None,
    current_user=Depends(get_current_admin),
):
    """Trigger a background download of the given (default: configured) model."""
    model = (body.model if body else None) or config_manager.embedding_model
    return model_manager.start_download(model)


@router.delete("")
async def delete_embedding_model(
    body: DownloadRequest | None = None,
    current_user=Depends(get_current_admin),
):
    """Remove the given (default: configured) model to free space / force re-download."""
    model = (body.model if body else None) or config_manager.embedding_model
    return model_manager.delete(model)


@router.post("/switch")
async def switch_embedding_model(
    body: SwitchRequest,
    current_user=Depends(get_current_admin),
):
    """Change the configured embedding model.

    If existing vectors exist and the new model's dimension differs, a 409 is
    returned (unless ``force=True``, which clears all vector collections first
    and then applies the switch). Vectors must be cleared because ChromaDB
    collections are built against a fixed embedding dimension.
    """
    target = body.model
    if not is_known_model(target):
        raise HTTPException(status_code=400, detail=f"未知模型：{target}")

    new_dim = known_dimension(target) or model_manager.model_dimension(target)
    if new_dim is None:
        raise HTTPException(
            status_code=400,
            detail=f"无法确定模型维度，请先安装 {target} 后再切换",
        )

    from app.services.vector_store import vector_store

    total = vector_store.total_vector_count()

    conflict = False
    existing_dim = None
    if total > 0:
        existing_dim = model_manager.model_dimension(config_manager.embedding_model)
        if existing_dim is not None and existing_dim != new_dim:
            conflict = True

    if conflict and not body.force:
        raise HTTPException(
            status_code=409,
            detail={
                "conflict": True,
                "existing_dim": existing_dim,
                "new_dim": new_dim,
                "vector_count": total,
                "message": "向量维度不兼容，切换将清除全部向量索引，需重新上传/重建知识库。",
            },
        )

    if conflict and body.force:
        vector_store.clear_all()

    # Apply the new model and reset the loaded instance so it reloads on next use.
    await config_manager.update({"embedding_model": target})
    try:
        from app.services.embedder import embedder_service

        embedder_service._model = None
    except Exception:
        pass

    installed = model_manager.is_installed(target)
    if installed:
        try:
            embedder_service._ensure_model()
        except Exception:
            pass

    return {
        "switched": True,
        "model": target,
        "installed": installed,
        "cleared_vectors": bool(conflict and body.force),
    }
