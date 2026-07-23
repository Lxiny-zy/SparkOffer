"""Data models — LangGraph states (TypedDict) + API models (Pydantic)."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypedDict
from pydantic import BaseModel, Field, field_validator
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
    topic: str | None = Field(default=None, max_length=200)


class JobPrepPreviewRequest(BaseModel):
    jd_text: str = Field(max_length=200_000)
    company: str | None = Field(default=None, max_length=200)
    position: str | None = Field(default=None, max_length=200)
    use_resume: bool = True


class JobPrepStartRequest(JobPrepPreviewRequest):
    preview_data: dict | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=100_000)


class ReferenceAnswerRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=100_000)
    session_id: str | None = Field(default=None, max_length=200)
    question_id: str | int | None = None
    force: bool = False
    mode: Literal["full", "hint"] = "full"

    @field_validator("topic", "question")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("session_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class DrillProgressRequest(BaseModel):
    current_index: int = Field(default=0, ge=0, le=100_000)
    partial_answers: dict[str, str] = Field(default_factory=dict)
    hints: dict[str, Any] = Field(default_factory=dict)


class EndDrillRequest(BaseModel):
    answers: list[dict] = Field(default_factory=list)  # [{question_id: int, answer: str}]

    @field_validator("answers", mode="before")
    @classmethod
    def normalize_answers(cls, value):
        """Accept both the UI list form and legacy question-id keyed maps."""
        if value is None:
            return []
        if isinstance(value, dict):
            normalized = []
            for question_id, answer in value.items():
                if isinstance(answer, dict):
                    answer = answer.get("answer", "")
                normalized.append({"question_id": question_id, "answer": "" if answer is None else str(answer)})
            return normalized
        if not isinstance(value, list):
            raise ValueError("answers must be a list or question-id map")
        normalized = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("each answer must be an object")
            question_id = item.get("question_id", item.get("id"))
            if question_id is None:
                raise ValueError("each answer requires question_id")
            answer = item.get("answer", "")
            normalized.append({
                **item,
                "question_id": question_id,
                "answer": "" if answer is None else str(answer),
            })
        return normalized


# ── Auth Models ──

class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=72)
    name: str = Field(default="", max_length=50)
    invite_code: str = Field(default="", max_length=512)


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    # Preserve the generic invalid-credentials response for legacy passwords
    # beyond bcrypt's limit while still bounding hostile request bodies.
    password: str = Field(min_length=1, max_length=1024)


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=72)


# ── Algorithm Solver Models ──

class AlgorithmSolveRequest(BaseModel):
    problem_text: str = Field(min_length=1, max_length=200_000)
    language: str = Field(default="python", min_length=1, max_length=64)
    source_url: str = Field(default="", max_length=2048)


class AlgorithmChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=100_000)


class AlgorithmSaveRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    difficulty: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=1_000_000)


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
    # Empty values are allowed for disabled channels so the settings UI can
    # keep an incomplete draft without making it runtime-active.
    api_base: str = ""
    keys: list[str] = Field(default_factory=list)
    model: str = ""
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
