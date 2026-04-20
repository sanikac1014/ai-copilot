from typing import List

from backend.models.schemas import ChatMessage, ContextPayload, TranscriptEntry
from backend.services.groq_client import get_groq_client
from backend.services.model_config import chat_with_fallback


def build_chat_reply(
    user_message: str,
    transcript_entries: List[TranscriptEntry],
    context: ContextPayload,
    history: List[ChatMessage],
) -> str:
    from backend.services.session_store import CONFIG
    tail = transcript_entries[-2:] if transcript_entries else []
    full_transcript = "\n".join(f"[{t.timestamp}] {t.text}" for t in tail)[-CONFIG.chat_context_chars:]
    recent_history = history[-8:]
    history_text = "\n".join(f"{m.role}: {m.content}" for m in recent_history)
    extra = CONFIG.chat_prompt_extra.strip()

    prompt = f"""
Answer like a real-time assistant.

User message: "{user_message}"

Conversation type: {context.conversation_type}
Conversation stage: {context.stage}
Primary focus: {context.primary_focus}

Recent chat history:
{history_text}

Full conversation:
{full_transcript}

Constraints:
- Keep response under 120-150 words
- No tables
- No long explanations
- Be concise and actionable

Structure:
1. Direct answer (1-2 lines)
2. Key recommendation (2-3 bullets max)
3. One tradeoff (optional)

Avoid over-explaining.
{("Additional instructions: " + extra) if extra else ""}
"""

    client = get_groq_client()
    response, _used = chat_with_fallback(
        client,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content or ""

