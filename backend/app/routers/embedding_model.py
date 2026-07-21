"""Embedding-model management routes (admin only): status / download / delete / switch."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth import get_current_admin
from app.services.config_manager import config_manager
from app.services.model_manager import model_manager
from app.services.embedder import embedder_service
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


@router.post("/pause")
async def pause_embedding_download(current_user=Depends(get_current_admin)):
    """Pause an in-flight download (takes effect between files)."""
    return {"paused": model_manager.pause_download()}


@router.post("/resume")
async def resume_embedding_download(current_user=Depends(get_current_admin)):
    """Resume a paused download."""
    return {"resumed": model_manager.resume_download()}


@router.post("/cancel")
async def cancel_embedding_download(current_user=Depends(get_current_admin)):
    """Cancel an in-flight or paused download; partial cache is kept for resume."""
    return {"cancelled": model_manager.cancel_download()}


def _embedding_conflict(target: str) -> dict:
    """Decide whether switching to ``target`` would invalidate existing vectors.

    A conflict (which forces a full wipe + reindex) occurs when stored vectors
    exist and EITHER:
      * their dimension differs from ``target`` (the original check), OR
      * their stamped model id / backend differs from ``target`` / the current
        backend (e.g. torch vs onnx, or bge-small-zh -> bge-small-en at the same
        512-dim — previously this slipped through and silently corrupted retrieval).

    Relies on the ``embed_model`` / ``embed_backend`` stamps written by
    ``VectorStore.add_chunks*``. Legacy vectors without the stamp are ignored
    for the source check (the dimension check still applies to them).
    """
    new_dim = known_dimension(target) or model_manager.model_dimension(target)
    from app.services.vector_store import vector_store

    total = vector_store.total_vector_count()
    existing_dim = None
    dim_conflict = False
    if total > 0:
        existing_dim = model_manager.model_dimension(config_manager.embedding_model)
        if existing_dim is not None and existing_dim != new_dim:
            dim_conflict = True

    stored_model, stored_backend = vector_store.stored_embed_info()
    source_conflict = bool(
        total > 0
        and stored_model is not None
        and (stored_model != target or stored_backend != embedder_service.BACKEND)
    )
    return {
        "total": total,
        "existing_dim": existing_dim,
        "new_dim": new_dim,
        "dim_conflict": dim_conflict,
        "source_conflict": source_conflict,
        "stored_model": stored_model,
        "stored_backend": stored_backend,
        "conflict": dim_conflict or source_conflict,
    }


def _conflict_detail(target: str, info: dict) -> dict:
    """Build the 409 response body for a switch/check conflict."""
    if info["dim_conflict"]:
        reason = f"向量维度不兼容（现有 {info['existing_dim']} 维，目标 {info['new_dim']} 维）"
    elif info["source_conflict"]:
        reason = (
            f"向量来源不兼容（现有 {info['stored_model']}/{info['stored_backend']} "
            f"→ 目标 {target}/{embedder_service.BACKEND}）"
        )
    else:
        reason = "向量不兼容"
    return {
        "conflict": True,
        "existing_dim": info["existing_dim"],
        "new_dim": info["new_dim"],
        "existing_model": info["stored_model"],
        "existing_backend": info["stored_backend"],
        "new_model": target,
        "new_backend": embedder_service.BACKEND,
        "vector_count": info["total"],
        "dim_conflict": info["dim_conflict"],
        "source_conflict": info["source_conflict"],
        "message": reason + "，切换将清除全部向量索引，需重新上传/重建知识库。",
    }


@router.post("/switch")
async def switch_embedding_model(
    body: SwitchRequest,
    current_user=Depends(get_current_admin),
):
    """Change the configured embedding model.

    ``force=True`` is the explicit "clear + switch + rebuild" action from the UI.
    We only ever activate a model we are sure is present:

    * target already installed → clear existing vectors (if any), switch the
      active config immediately and start the re-index right away.
    * target not installed → the switch is refused (400). We never auto-start a
      download here; switching only ever activates an already-installed model, so
      the user must install the target via the dedicated download action first.
      Nothing is mutated.

    When ``force=False`` and a conflict exists (existing vectors whose dimension
    or stamped model/backend identity differs from ``target``), a 409 is returned
    and nothing is mutated.
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

    info = _embedding_conflict(target)

    if info["conflict"] and not body.force:
        raise HTTPException(status_code=409, detail=_conflict_detail(target, info))

    # force=True means the user explicitly wants "clear + switch + rebuild".
    will_clear = bool(body.force) and info["total"] > 0
    installed = model_manager.is_installed(target)

    # We only ever activate a model that is already present. Switching to a
    # not-yet-installed model is refused — the user must download & install it
    # first through the dedicated download action. No config is written, no
    # vectors are cleared, and no download is started here.
    if not installed:
        raise HTTPException(
            status_code=400,
            detail=f"模型 {target} 尚未安装，请先下载安装后再切换。",
        )

    # Target already installed → switch synchronously now.
    if will_clear:
        from app.services.vector_store import vector_store

        vector_store.clear_all()

    # Apply the new model and reset the loaded instance so it reloads on next use.
    await config_manager.update({"embedding_model": target})
    try:
        from app.services.embedder import embedder_service

        embedder_service._model = None
        embedder_service._ensure_model()
    except Exception:
        pass

    from app.services.reindex_service import reindex_service

    reindex_started = False
    if will_clear:
        reindex_service.start()
        reindex_started = True

    return {
        "switched": True,
        "model": target,
        "installed": True,
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
    """Dry-run conflict check (no mutation).

    Returns 409 when switching to ``body.model`` would require wiping existing
    vector collections because the dimension OR the stamped model/backend
    identity differs. Returns 200 otherwise.
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

    info = _embedding_conflict(target)
    if info["conflict"]:
        raise HTTPException(status_code=409, detail=_conflict_detail(target, info))
    return {
        "conflict": False,
        "existing_dim": info["existing_dim"],
        "new_dim": info["new_dim"],
        "existing_model": info["stored_model"],
        "existing_backend": info["stored_backend"],
        "new_model": target,
        "new_backend": embedder_service.BACKEND,
        "vector_count": info["total"],
        "dim_conflict": info["dim_conflict"],
        "source_conflict": info["source_conflict"],
    }
