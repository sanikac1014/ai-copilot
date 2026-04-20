from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ConversationType = Literal["technical", "casual", "business"]
ConversationStage = Literal["problem", "solution", "tradeoff"]


class TranscriptEntry(BaseModel):
    timestamp: str
    text: str


class Suggestion(BaseModel):
    """type: free-form label from the model (e.g. debugging probe). intent_category: canonical lens for diversity."""
    type: str
    preview: str = Field(max_length=120)
    reason: str
    topic: Optional[str] = None
    intent_category: Optional[str] = None
    score: Optional[float] = None
    relevance: Optional[float] = None
    novelty: Optional[float] = None
    actionability: Optional[float] = None


class ContextPayload(BaseModel):
    recent_transcript: str
    summary: str
    conversation_type: ConversationType
    primary_focus: str
    secondary_topics: List[str]
    intent: str
    entities: List[str]
    uncertainties: List[str]
    stage: ConversationStage
    is_low_signal: bool = False
    topic_shift: bool = False
    focus_chunk_similarity: float = 1.0


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: Optional[str] = None


class SuggestionRequest(BaseModel):
    transcript_entries: List[TranscriptEntry]
    force_refresh: bool = False


class ChatRequest(BaseModel):
    transcript_entries: List[TranscriptEntry]
    message: str
    from_suggestion: bool = False


class ConfigUpdate(BaseModel):
    groq_api_key: Optional[str] = None
    model_primary: Optional[str] = None
    model_fallback: Optional[str] = None
    suggestion_context_chars: Optional[int] = None
    chat_context_chars: Optional[int] = None
    suggestion_prompt_extra: Optional[str] = None
    chat_prompt_extra: Optional[str] = None
