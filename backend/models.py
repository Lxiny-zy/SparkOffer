"""Data models — LangGraph states (TypedDict) + API models (Pydantic)."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, TypedDict
from pydantic import BaseModel, Field
from langgraph.graph import add_messages


# ── Enums ──

class InterviewMode(str, Enum):
    RESUME = "resume"
    TOPIC_DRILL = "topic_drill"
    JD_PREP = "jd_prep"
    RECORDING = "recording"


class InterviewPhase(str, Enum):
    GREETING = "greeting"
    SELF_INTRO = "self_intro"
    TECHNICAL = "technical"
    PROJECT_DEEP_DIVE = "project_deep_dive"
    REVERSE_QA = "reverse_qa"
    END = "end"


# ── LangGraph States (TypedDict for max compatibility) ──

class ResumeInterviewState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    phase: str           # InterviewPhase value
    system_prompt: str   # frozen STABLE system prefix (built once at init) → prompt-prefix cache hits
    resume_context: str
    knowledge_context: str   # cached at init; reused every ask (query is fixed)
    questions_asked: list[str]
    phase_question_count: int
    is_finished: bool
    last_eval: dict          # Latest inline eval from interviewer {score, should_advance, brief}
    eval_history: list       # All evals accumulated across the interview


class TopicDrillState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    topic: str
    topic_name: str
    knowledge_context: str
    difficulty: int
    questions_asked: list[str]
    scores: list[dict]
    weak_points: list[str]
    total_questions: int
    is_finished: bool


# ── API Models (Pydantic) ──

class StartInterviewRequest(BaseModel):
    mode: InterviewMode
    topic: str | None = None


class JobPrepPreviewRequest(BaseModel):
    jd_text: str
    company: str | None = None
    position: str | None = None
    use_resume: bool = True


class JobPrepStartRequest(JobPrepPreviewRequest):
    preview_data: dict | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str


class EndDrillRequest(BaseModel):
    answers: list[dict] = Field(default_factory=list)  # [{question_id: int, answer: str}]


# ── Auth Models ──

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    email: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── Algorithm Solver Models ──

class AlgorithmSolveRequest(BaseModel):
    problem_text: str
    language: str = "python"
    source_url: str = ""


class AlgorithmChatRequest(BaseModel):
    session_id: str
    message: str


class AlgorithmSaveRequest(BaseModel):
    session_id: str
    title: str
    difficulty: str = ""
    tags: list[str] = Field(default_factory=list)
    note: str = ""


# ── AI Config Models ──

class LLMConfig(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None

class EmbeddingConfig(BaseModel):
    backend: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    api_model: str | None = None
    local_model: str | None = None
    local_path: str | None = None

class AIConfigUpdate(BaseModel):
    llm: LLMConfig | None = None
    embedding: EmbeddingConfig | None = None

class TestLLMRequest(BaseModel):
    api_base: str
    api_key: str
    model: str
    temperature: float = 0.7
    reasoning_effort: str = ""

class TestEmbeddingRequest(BaseModel):
    backend: str = "api"
    api_base: str = ""
    api_key: str = ""
    api_model: str = ""


# ── Multi-Channel Config Models ──

class LLMChannelConfig(BaseModel):
    id: str = ""
    name: str = "Default"
    api_base: str
    keys: list[str]
    model: str
    temperature: float = 0.7
    reasoning_effort: str = ""
    max_tokens: int = 32768  # 单次输出上限（OpenAI max_completion_tokens），非上下文窗口；推理模型含思考 token 故抬高
    context_window: int = 0  # 输入上下文窗口（token）；0 = 用 settings.default_context_window 兜底。被 context_assembler._resolve_window 读取
    timeout: int = 0  # read timeout 秒；0 = 用全局默认 _LLM_TIMEOUT（read 240s）
    tier: str = "large"  # "small" for cheap-fast eval, "large" for primary; default large for back-compat
    priority: int = 1
    enabled: bool = True
    proxy: str = ""

class EmbeddingChannelConfig(BaseModel):
    id: str = ""
    name: str = "Default"
    backend: str = "api"
    api_base: str = ""
    keys: list[str] = Field(default_factory=list)
    api_model: str = ""
    local_model: str = ""
    local_path: str = ""
    priority: int = 1
    enabled: bool = True
    proxy: str = ""

class RerankerChannelConfig(BaseModel):
    id: str = ""
    name: str = "Default"
    api_base: str = ""
    keys: list[str] = Field(default_factory=list)
    api_model: str = ""
    priority: int = 1
    enabled: bool = True
    proxy: str = ""

class ChannelsConfig(BaseModel):
    llm: list[LLMChannelConfig] = Field(default_factory=list)
    embedding: list[EmbeddingChannelConfig] = Field(default_factory=list)
    reranker: list[RerankerChannelConfig] = Field(default_factory=list)

class TestChannelRequest(BaseModel):
    section: str
    channel: dict


# ── Runtime tuning (context budget + retrieval) ──
# All fields optional: a None / omitted field means "revert to default" on save.
# The backend resolver (ai_config.get_tuning / get_retrieval_setting) owns the
# defaults + clamping; this model only validates shape for the PUT endpoint.
class RetrievalTuning(BaseModel):
    preset: str = "balanced"  # fast | balanced | thorough | custom (UI hint only)
    per_query_top_k: int | None = None
    final_top_n: int | None = None
    embed_concurrency: int | None = None
    dedup_threshold: float | None = None
    end_to_end_timeout: int | None = None
    per_query_timeout: int | None = None
    reranker_read_timeout: int | None = None

class TuningConfig(BaseModel):
    max_output_tokens: int | None = None
    default_context_window: int | None = None
    retrieval: RetrievalTuning = Field(default_factory=RetrievalTuning)
