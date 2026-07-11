"""Application configuration — hardcoded defaults only.

Runtime overrides are stored in the encrypted config.enc file,
managed by config_manager. No .env file needed.
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"extra": "ignore"}

    # --- Paths ---
    project_root: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = project_root / "data"
    upload_dir: Path = data_dir / "uploads"
    sqlite_path: Path = data_dir / "sqlite" / "erag.db"
    chroma_path: Path = data_dir / "chroma"
    skills_dir: Path = data_dir / "skills"

    # --- Embedding ---
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"  # "cuda" if GPU available

    # --- Chunking ---
    chunk_min_tokens: int = 300
    chunk_max_tokens: int = 800
    chunk_overlap_tokens: int = 50

    # --- Retrieval ---
    retrieval_vector_top_k: int = 20
    retrieval_bm25_top_k: int = 20
    retrieval_final_top_k: int = 10
    retrieval_rrf_k: int = 60
    retrieval_similarity_threshold: float = 0.5

    # --- LLM (non-sensitive defaults; api_key configured via web UI, not .env) ---
    llm_provider: str = "openai"       # openai | anthropic | qwen | tencent | ollama (falls back to base_url domain if unspecified)
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    agent_max_tokens: int = 8192  # Agent 工具决策节点专用上限（独立于全局 llm_max_tokens）

    # --- Cache ---
    cache_enabled: bool = True
    cache_max_size: int = 1000
    cache_ttl_seconds: int = 3600  # 60 min (default), runtime override via config_manager

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:8000", "http://localhost:5173"]

    # --- Download Proxy ---
    public_url: str = ""  # External URL for download links; empty = use relative URLs
    mcp_repl_internal_url: str = "http://mcp-repl:9200"  # Docker internal network URL for MCP REPL

    # --- Conversation ---
    conversation_max_history: int = 10


settings = Settings()
