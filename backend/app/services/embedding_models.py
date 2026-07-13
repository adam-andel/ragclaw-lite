"""Curated list of local Embedding models the admin can choose from.

This is the single source of truth, shared by the backend (status / switch
endpoints) and, via the status API, the frontend dropdown. Each entry carries
the model id (HuggingFace repo), a human label, the vector dimension, and the
approx download size so the UI can surface compatibility / dimension info and
guard against switching to an incompatible dimension.
"""

# id -> meta
EMBEDDING_MODEL_OPTIONS = [
    {
        "id": "BAAI/bge-small-zh-v1.5",
        "label": "BGE small · 中文 · 512维 · ~130MB（快）",
        "dimension": 512,
        "size": "130MB",
    },
    {
        "id": "BAAI/bge-base-zh-v1.5",
        "label": "BGE base · 中文 · 768维 · ~400MB",
        "dimension": 768,
        "size": "400MB",
    },
    {
        "id": "BAAI/bge-large-zh-v1.5",
        "label": "BGE large · 中文 · 1024维 · ~1.3GB（更准）",
        "dimension": 1024,
        "size": "1.3GB",
    },
    {
        "id": "BAAI/bge-small-en-v1.5",
        "label": "BGE small · 英文 · 512维 · ~130MB",
        "dimension": 512,
        "size": "130MB",
    },
    {
        "id": "BAAI/bge-large-en-v1.5",
        "label": "BGE large · 英文 · 1024维 · ~1.3GB",
        "dimension": 1024,
        "size": "1.3GB",
    },
]

_MODEL_INDEX = {m["id"]: m for m in EMBEDDING_MODEL_OPTIONS}


def get_model_option(model_id: str) -> dict | None:
    return _MODEL_INDEX.get(model_id)


def is_known_model(model_id: str) -> bool:
    return model_id in _MODEL_INDEX


def known_dimension(model_id: str) -> int | None:
    m = _MODEL_INDEX.get(model_id)
    return m["dimension"] if m else None
