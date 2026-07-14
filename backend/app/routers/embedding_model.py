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


def _dimension_conflict(target: str):
    """Return ``(conflict, existing_dim, new_dim, total)`` for switching to ``target``.

    A conflict means vectors already exist and the target model's embedding
    dimension differs from the currently configured model, so existing vector
    collections would have to be wiped before the switch.
    """
    new_dim = known_dimension(target) or model_manager.model_dimension(target)
    from app.services.vector_store import vector_store

    total = vector_store.total_vector_count()
    conflict = False
    existing_dim = None
    if total > 0:
        existing_dim = model_manager.model_dimension(config_manager.embedding_model)
        if existing_dim is not None and existing_dim != new_dim:
            conflict = True
    return conflict, existing_dim, new_dim, total


@router.post("/switch")
async def switch_embedding_model(
    body: SwitchRequest,
    current_user=Depends(get_current_admin),
):
    """Change the configured embedding model.

    When ``force=True`` every existing vector collection is cleared first and a
    re-index against the new model is started (immediately if the target model is
    already installed, otherwise queued until its download completes). This is the
    explicit "clear + switch + rebuild" action triggered from the UI.

    When ``force=False`` and a dimension conflict exists (existing vectors with a
    different dimension), a 409 is returned and nothing is mutated.
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

    conflict, existing_dim, new_dim, total = _dimension_conflict(target)

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

    # force=True means the user explicitly wants "clear + switch + rebuild":
    # wipe existing collections (if any) and re-index against the new model.
    will_clear = bool(body.force) and total > 0
    if will_clear:
        from app.services.vector_store import vector_store

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

    # Rebuild collections against the new model — immediately if it's already
    # installed, otherwise queue it so the download worker starts it on completion.
    from app.services.reindex_service import reindex_service

    reindex_started = False
    if will_clear:
        if installed:
            reindex_service.start()
            reindex_started = True
        else:
            model_manager.set_pending_reindex()

    return {
        "switched": True,
        "model": target,
        "installed": installed,
        "cleared_vectors": will_clear,
        "reindex_started": reindex_started,
    }


class CheckRequest(BaseModel):
    model: str


@router.post("/check")
async def check_embedding_model(
    body: CheckRequest,
    current_user=Depends(get_current_admin),
):
    """Dry-run dimension check (no mutation).

    Returns 409 when switching to ``body.model`` would require wiping existing
    vector collections because the dimensions differ. Returns 200 otherwise.
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

    conflict, existing_dim, new_dim, total = _dimension_conflict(target)
    if conflict:
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
    return {
        "conflict": False,
        "existing_dim": existing_dim,
        "new_dim": new_dim,
        "vector_count": total,
    }
