from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from backend.models.schemas import ChatMessage, ContextPayload, Suggestion, TranscriptEntry


@dataclass
class Config:
    """Runtime-editable configuration. Persists for the server process lifetime."""
    groq_api_key: str = ""
    model_primary: str = ""       # empty → use model_config default
    model_fallback: str = ""      # empty → use model_config default
    suggestion_context_chars: int = 1200
    chat_context_chars: int = 4000
    suggestion_prompt_extra: str = ""
    chat_prompt_extra: str = ""


CONFIG = Config()


@dataclass
class SessionState:
    transcript_entries: List[TranscriptEntry] = field(default_factory=list)
    chat_history: List[ChatMessage] = field(default_factory=list)
    # Each item: {"batch_id": int, "segment_id": int, "suggestions": List[Suggestion]}
    suggestion_history: Deque[Dict] = field(default_factory=lambda: deque(maxlen=80))
    # Append-only log of every batch ever generated — never cleared on topic shift.
    # Used exclusively by /export so evaluators see every suggestion across all segments.
    suggestion_export_log: List[Dict] = field(default_factory=list)
    rolling_summary: str = ""
    last_primary_focus: str = ""
    last_conversation_type: str = ""
    current_segment_id: int = 0
    latest_batch_id: int = 0
    last_context: Optional[ContextPayload] = None
    last_transcript_signature: str = ""
    session_segments: List[dict] = field(default_factory=list)
    last_topic_shift_time: float = 0.0  # perf_counter timestamp of last confirmed shift
    # Index into transcript_entries where the current segment begins.
    # Used to pass only current-segment entries to context/suggestion engines.
    current_segment_start_idx: int = 0
    # Rolling summary scoped to the current segment only (no prior-topic content).
    current_segment_rolling_summary: str = ""


STATE = SessionState()
