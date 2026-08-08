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
        "label": "BGE small · Chinese · 512-dim · ~130MB (fast)",
        "dimension": 512,
        "size": "130MB",
    },
    {
        "id": "BAAI/bge-base-zh-v1.5",
        "label": "BGE base · Chinese · 768-dim · ~400MB",
        "dimension": 768,
        "size": "400MB",
    },
    {
        "id": "BAAI/bge-large-zh-v1.5",
        "label": "BGE large · Chinese · 1024-dim · ~1.3GB (accurate)",
        "dimension": 1024,
        "size": "1.3GB",
    },
    {
        "id": "BAAI/bge-small-en-v1.5",
        "label": "BGE small · English · 512-dim · ~130MB",
        "dimension": 512,
        "size": "130MB",
    },
    {
        "id": "BAAI/bge-base-en-v1.5",
        "label": "BGE base · English · 768-dim · ~400MB",
        "dimension": 768,
        "size": "400MB",
    },
    {
        "id": "BAAI/bge-large-en-v1.5",
        "label": "BGE large · English · 1024-dim · ~1.3GB",
        "dimension": 1024,
        "size": "1.3GB",
    },
    {
        "id": "",
        "label": "None · disable vector search",
        "dimension": None,
        "size": "0",
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
