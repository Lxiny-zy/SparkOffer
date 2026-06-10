from pathlib import Path
from pydantic_settings import BaseSettings


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"


class Settings(BaseSettings):
    # LLM (OpenAI-compatible proxy)
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7

    # Embedding — explicit backend + separate config for API/local modes
    embedding_backend: str = ""  # api | local; empty keeps legacy inference
    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_api_model: str = ""
    local_embedding_model: str = ""
    local_embedding_path: str = ""
    embedding_model: str = ""  # deprecated fallback for EMBEDDING_MODEL

    # Reranker (Cross-Encoder re-ranking API)
    reranker_api_base: str = ""
    reranker_api_key: str = ""
    reranker_api_model: str = ""

    # DashScope ASR (speech-to-text)
    dashscope_api_key: str = ""
    asr_model: str = "qwen3-asr-flash-filetrans"

    # Qiniu OSS (for uploading audio to get public URL)
    qiniu_access_key: str = ""
    qiniu_secret_key: str = ""
    qiniu_bucket: str = ""
    qiniu_domain: str = ""

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    resume_path: Path = Path(__file__).resolve().parent.parent / "data" / "resume"
    knowledge_path: Path = Path(__file__).resolve().parent.parent / "data" / "knowledge"
    high_freq_path: Path = Path(__file__).resolve().parent.parent / "data" / "high_freq"
    db_path: Path = Path(__file__).resolve().parent.parent / "data" / "interviews.db"
    # LangGraph checkpoint store — separate file so its connection doesn't
    # contend with interviews.db's WAL. Holds resume-interview graph state so an
    # in-flight interview survives restarts / multiple workers.
    checkpoint_db_path: Path = Path(__file__).resolve().parent.parent / "data" / "checkpoints.db"

    # Auth
    jwt_secret: str = "change-me-in-production"
    default_email: str = "legend@sparkoffer.local"
    default_password: str = "legend"
    default_name: str = "Legend"
    allow_registration: bool = False

    # Interview settings
    max_questions_per_phase: int = 5
    max_drill_questions: int = 15

    # Context budgeting (context_assembler.py) — fallback input window in tokens
    # when no LLM channel declares an explicit `context_window`. 32k is a safe
    # floor across modern OpenAI-compatible models.
    default_context_window: int = 32768

    # Redis (optional — empty string disables; falls back to in-memory LRU)
    redis_url: str = ""

    # Vector store backend (记忆库) — numpy（默认，SQLite+numpy）| qdrant
    vector_backend: str = ""                            # 空 → 按 qdrant_url 推断
    qdrant_url: str = ""                                # e.g. http://localhost:6333；空则禁用
    qdrant_api_key: str = ""
    qdrant_memory_collection: str = "sparkoffer_memory"

    def user_data_dir(self, user_id: str) -> Path:
        return self.base_dir / "data" / "users" / user_id

    def user_profile_dir(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / "profile"

    def user_resume_path(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / "resume"

    def user_knowledge_path(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / "knowledge"

    def user_high_freq_path(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / "high_freq"

    def user_topics_path(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / "topics.json"

    def user_index_cache_path(self, user_id: str) -> Path:
        return self.user_data_dir(user_id) / ".index_cache"

    def embedding_backend_mode(self) -> str:
        if self.embedding_backend:
            backend = self.embedding_backend.strip().lower()
            if backend in {"api", "local"}:
                return backend
            raise ValueError("EMBEDDING_BACKEND must be 'api' or 'local'")
        if self.embedding_api_base or self.embedding_api_key:
            return "api"
        return "local"

    def vector_backend_mode(self) -> str:
        """记忆库向量后端：'numpy'（默认）或 'qdrant'。

        与 embedding_backend_mode 同构：显式 VECTOR_BACKEND 优先；否则当
        QDRANT_URL 已设时推断为 'qdrant'，再否则 'numpy'。
        """
        if self.vector_backend:
            backend = self.vector_backend.strip().lower()
            if backend in {"numpy", "qdrant"}:
                return backend
            raise ValueError("VECTOR_BACKEND must be 'numpy' or 'qdrant'")
        return "qdrant" if self.qdrant_url else "numpy"

    def embedding_api_model_name(self) -> str:
        return self.embedding_api_model or self.embedding_model or DEFAULT_EMBEDDING_MODEL

    def local_embedding_model_name(self) -> str:
        return self.local_embedding_model or self.embedding_model or DEFAULT_EMBEDDING_MODEL

    def local_embedding_model_path(self) -> Path | None:
        if self.local_embedding_path:
            return Path(self.local_embedding_path).expanduser()

        bundled_path = self.base_dir / "data" / "models" / "bge-m3"
        if self.local_embedding_model_name() == DEFAULT_EMBEDDING_MODEL and bundled_path.exists():
            return bundled_path
        return None

    def active_embedding_target(self) -> str:
        if self.embedding_backend_mode() == "api":
            return self.embedding_api_model_name()

        model_path = self.local_embedding_model_path()
        if model_path is not None:
            return str(model_path)
        return self.local_embedding_model_name()

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
