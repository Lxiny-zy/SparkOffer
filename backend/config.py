import ipaddress
from pathlib import Path
from urllib.parse import urlsplit
from pydantic_settings import BaseSettings


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
_DEVELOPMENT_ENVS = {"dev", "development", "local", "test"}
_DEFAULT_JWT_SECRET = "change-me-in-production"
_DEFAULT_PASSWORD = "legend"


def _is_loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    folded = hostname.rstrip(".").casefold()
    if folded == "localhost":
        return True
    try:
        return ipaddress.ip_address(folded).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    # Runtime environment. Insecure bootstrap defaults are allowed only when
    # development is explicitly selected.
    app_env: str = "production"

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
    jwt_secret: str = _DEFAULT_JWT_SECRET
    default_email: str = "legend@sparkoffer.local"
    default_password: str = _DEFAULT_PASSWORD
    default_name: str = "Legend"
    allow_registration: bool = False
    # Non-empty → registration additionally requires this invite code.
    invite_code: str = ""
    # Comma-separated allowed CORS origins. Default "*" keeps current behavior;
    # set to your frontend origin(s) in production (e.g. "https://app.example.com").
    cors_allow_origins: str = "*"
    # Comma-separated IP addresses or CIDRs whose forwarding headers may be
    # trusted. Empty means the direct TCP peer is always used.
    trusted_proxy_cidrs: str = ""
    max_request_body_bytes: int = 40 * 1024 * 1024

    # Interview settings
    max_questions_per_phase: int = 5
    max_drill_questions: int = 15

    # Context budgeting (context_assembler.py) — fallback input window in tokens
    # when no LLM channel declares an explicit `context_window`. Set to 200k:
    # the channels in use are large-window models (≥258k), so the old 32k floor
    # silently starved the drill context budget down to its 1000-token floor.
    default_context_window: int = 200000

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

    def is_development(self) -> bool:
        return self.app_env.strip().lower() in _DEVELOPMENT_ENVS

    def trusted_proxy_networks(self) -> tuple:
        networks = []
        for raw in self.trusted_proxy_cidrs.split(","):
            value = raw.strip()
            if not value:
                continue
            try:
                networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError as exc:
                raise ValueError(f"Invalid TRUSTED_PROXY_CIDRS entry: {value!r}") from exc
        return tuple(networks)

    def validate_security_settings(self) -> list[str]:
        """Validate deployment-critical settings and return dev-only warnings."""
        issues = []
        jwt_secret = self.jwt_secret.strip()
        if not jwt_secret or jwt_secret == _DEFAULT_JWT_SECRET:
            issues.append("JWT_SECRET is empty or still uses the public default")
        elif len(jwt_secret.encode("utf-8")) < 32:
            issues.append("JWT_SECRET must contain at least 32 UTF-8 bytes")
        if not self.default_password.strip() or self.default_password.strip() == _DEFAULT_PASSWORD:
            issues.append("DEFAULT_PASSWORD is empty or still uses the public default")
        elif len(self.default_password) < 12:
            issues.append("DEFAULT_PASSWORD must contain at least 12 characters in production")
        elif len(self.default_password.encode("utf-8")) > 72:
            issues.append("DEFAULT_PASSWORD exceeds bcrypt's 72-byte UTF-8 limit")
        origins = {origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()}
        if not origins or "*" in origins:
            issues.append("CORS_ALLOW_ORIGINS must not be '*' outside development")
        else:
            invalid_origins = []
            insecure_origins = []
            for origin in origins:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.netloc
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                ):
                    invalid_origins.append(origin)
                elif (
                    parsed.scheme == "http"
                    and not _is_loopback_hostname(parsed.hostname)
                ):
                    insecure_origins.append(origin)
            if invalid_origins:
                issues.append(
                    "CORS_ALLOW_ORIGINS contains invalid browser origins: "
                    + ", ".join(sorted(invalid_origins))
                )
            if insecure_origins:
                issues.append(
                    "CORS_ALLOW_ORIGINS must use HTTPS for non-loopback origins: "
                    + ", ".join(sorted(insecure_origins))
                )
        try:
            proxy_networks = self.trusted_proxy_networks()
            vector_backend = self.vector_backend_mode()
        except ValueError as exc:
            raise RuntimeError(f"Invalid security configuration: {exc}") from exc
        if any(network.prefixlen == 0 for network in proxy_networks):
            issues.append("TRUSTED_PROXY_CIDRS must not trust the entire IPv4 or IPv6 internet")
        if vector_backend == "qdrant" and not self.qdrant_api_key.strip():
            issues.append("QDRANT_API_KEY is required when VECTOR_BACKEND=qdrant")
        elif (
            vector_backend == "qdrant"
            and len(self.qdrant_api_key.encode("utf-8")) < 32
        ):
            issues.append("QDRANT_API_KEY must contain at least 32 UTF-8 bytes")

        if issues and not self.is_development():
            details = "; ".join(issues)
            raise RuntimeError(
                f"Refusing to start with insecure {self.app_env!r} configuration: {details}. "
                "Set APP_ENV=development only for an isolated local environment."
            )
        return issues

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
