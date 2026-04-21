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
    resume_context: str
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


class RecordingAnalyzeRequest(BaseModel):
    transcript: str
    recording_mode: str = "dual"  # "dual" | "solo"
    company: str | None = None
    position: str | None = None


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

class ASRConfig(BaseModel):
    dashscope_api_key: str | None = None
    model: str | None = None

class QiniuConfig(BaseModel):
    access_key: str | None = None
    secret_key: str | None = None
    bucket: str | None = None
    domain: str | None = None

class AIConfigUpdate(BaseModel):
    llm: LLMConfig | None = None
    embedding: EmbeddingConfig | None = None
    asr: ASRConfig | None = None
    qiniu: QiniuConfig | None = None

class TestLLMRequest(BaseModel):
    api_base: str
    api_key: str
    model: str
    temperature: float = 0.7

class TestEmbeddingRequest(BaseModel):
    backend: str = "api"
    api_base: str = ""
    api_key: str = ""
    api_model: str = ""

class TestASRRequest(BaseModel):
    dashscope_api_key: str

class TestQiniuRequest(BaseModel):
    access_key: str
    secret_key: str
    bucket: str
    domain: str = ""


# ── Multi-Channel Config Models ──

class LLMChannelConfig(BaseModel):
    id: str = ""
    name: str = "Default"
    api_base: str
    keys: list[str]
    model: str
    temperature: float = 0.7
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

class ASRChannelConfig(BaseModel):
    id: str = ""
    name: str = "Default"
    keys: list[str] = Field(default_factory=list)
    model: str = "qwen3-asr-flash-filetrans"
    priority: int = 1
    enabled: bool = True
    proxy: str = ""

class ChannelsConfig(BaseModel):
    llm: list[LLMChannelConfig] = Field(default_factory=list)
    embedding: list[EmbeddingChannelConfig] = Field(default_factory=list)
    asr: list[ASRChannelConfig] = Field(default_factory=list)

class TestChannelRequest(BaseModel):
    section: str
    channel: dict
