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
    sqlite_path: Path = data_dir / "sqlite" / "ragclaw.db"
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

    # --- REPL sandbox per-user isolation (shared HMAC secret with mcp/repl_mcp_server.py) ---
    # When set, every run_python/run_shell/run_javascript call is signed with the
    # authenticated user id so the sandbox can drop privileges to a per-user UID.
    # Auth + isolation are mandatory; ConfigManager auto-generates this on first
    # boot when empty, so it is never blank in practice.
    repl_auth_secret: str = ""
    repl_auth_exp_seconds: int = 0  # 0 = envelope never expires

    # --- REPL sandbox per-user UID allocation (random, DB-backed, unique) ---
    # Each user gets a dedicated Linux UID for their REPL sandbox isolation
    # account. Regular users get a UID randomly assigned from [MIN+1, MAX); MIN
    # itself is reserved for the bootstrap admin account (see database._seed_admin_user)
    # so it is stable and never collides. UIDs are stored on the user row with a
    # unique constraint; collisions retry up to ALLOC_RETRIES.
    # Capacity = MAX - MIN (e.g. 10000..110000 => 100k isolated users).
    # Expand MAX later to grow capacity WITHOUT affecting existing users
    # (their stored UIDs stay fixed; only new allocations use the wider range).
    repl_uid_range_min: int = 10000
    repl_uid_range_max: int = 110000  # EXCLUSIVE upper bound
    repl_uid_alloc_retries: int = 10  # cap on random-allocation collision retries

    # --- Conversation ---

    # --- Memory (mem0) ---
    # Max chars of query/answer sent to Mem0 for memory extraction per turn.
    # These only cap the INPUT to Mem0's LLM; the extracted memory output is
    # still bounded by memory.py's max_tokens (500). Larger values give the
    # extraction model more context but cost more tokens per conversation turn.
    mem0_query_max_chars: int = 500
    mem0_answer_max_chars: int = 1500
    mem0_llm_max_tokens: int = 500  # Output token cap for Mem0's extraction LLM


settings = Settings()
