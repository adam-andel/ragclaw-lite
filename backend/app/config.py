"""Application configuration — hardcoded defaults only.

Runtime overrides are stored in the encrypted config.enc file,
managed by config_manager. No .env file needed.
"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"extra": "ignore"}

    # --- Paths ---
    project_root: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = project_root / "data"
    upload_dir: Path = data_dir / "uploads"
    sqlite_path: Path = data_dir / "sqlite" / "ragclaw.db"
    chroma_path: Path = data_dir / "chroma"

    # --- Skills (shared volume: backend mounts rw, mcp-repl mounts ro) ---
    # Mount point of the shared skills volume. Both backend and mcp-repl mount
    # this EXACT path. The canonical skill store and the enable/* symlink set
    # live underneath it, so skill files are readable inside the REPL sandbox
    # via a per-user symlink that resolves (through enable/*) back to store/*.
    # Override the mount point with env RAGCLAW_SKILLS_DIR (e.g. local dev).
    shared_skills_dir: Path = Field(
        default=Path("/ragclaw_skills"), validation_alias="RAGCLAW_SKILLS_DIR"
    )

    # Derived layout (properties so env overrides propagate everywhere):
    #   <shared>/store/<folder>   canonical skill files (source of truth)
    #   <shared>/enable/<folder>  symlink -> ../store/<folder> (enabled == link exists)
    @property
    def skills_dir(self) -> Path:
        """Canonical skill store, kept as the historical attribute name so all
        existing call sites (get_skill_dir, scan_skills_dir, ...) keep working."""
        return self.shared_skills_dir / "store"

    @property
    def skills_enable_dir(self) -> Path:
        return self.shared_skills_dir / "enable"

    # --- Database ---
    # SQLAlchemy async database URL. Defaults to the local SQLite file so the
    # project still runs with a one-command `docker compose up` and no external
    # Postgres. Set via env (e.g. in .env) to use Postgres, e.g.:
    #   DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/ragclaw
    database_url: str = ""

    # --- Embedding ---
    embedding_model: str = ""  # empty = no embedding model (vector search disabled)
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

    # --- LLM ---
    # llm_api_key / llm_context_window default to .env (LLM_API_KEY / LLM_CONTEXT_WINDOW);
    # if the admin sets them in the Settings UI, the stored value overrides the .env default.
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    llm_context_window: int = 192000

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
    # itself is reserved for the bootstrap admin account (the first user
    # self-registered via POST /api/auth/register takes this UID)
    # so it is stable and never collides. UIDs are stored on the user row with a
    # unique constraint; collisions retry up to ALLOC_RETRIES.
    # Capacity = MAX - MIN (e.g. 10000..110000 => 100k isolated users).
    # Expand MAX later to grow capacity WITHOUT affecting existing users
    # (their stored UIDs stay fixed; only new allocations use the wider range).
    repl_uid_range_min: int = 10000
    repl_uid_range_max: int = 110000  # EXCLUSIVE upper bound
    repl_uid_alloc_retries: int = 10  # cap on random-allocation collision retries

    # --- Tenant ---
    # RAGClaw is deployed as a single private instance inside an organization.
    # Tenancy here is NOT multi-tenant SaaS isolation — all users belong to ONE
    # shared tenant so that resources like SKILLs are visible to every user.
    # New users are assigned this fixed tenant id instead of a random one.
    default_tenant_id: str = "ragclaw"

    # --- Conversation ---

settings = Settings()
