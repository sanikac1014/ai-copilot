from typing import Generator, List

from backend.models.schemas import ChatMessage, ContextPayload, TranscriptEntry
from backend.services.groq_client import get_groq_client
from backend.services.model_config import chat_with_fallback, stream_with_fallback


def build_chat_reply(
    user_message: str,
    transcript_entries: List[TranscriptEntry],
    context: ContextPayload,
    history: List[ChatMessage],
    from_suggestion: bool = False,
) -> str:
    from backend.services.session_store import CONFIG

    # Use more transcript for suggestion expansions; last 2 chunks for free-form chat.
    entry_limit = 8 if from_suggestion else 4
    tail = transcript_entries[-entry_limit:] if transcript_entries else []
    full_transcript = "\n".join(f"[{t.timestamp}] {t.text}" for t in tail)[-CONFIG.chat_context_chars:]

    recent_history = history[-8:]
    history_text = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in recent_history if m.content
    )
    extra = CONFIG.chat_prompt_extra.strip()

    if from_suggestion:
        prompt = f"""You are a real-time meeting copilot. The user clicked a suggestion card during an active conversation.
Expand on it with a detailed, specific answer grounded entirely in what they are actually discussing.

Suggestion clicked: "{user_message}"

Conversation context:
- Primary topic: {context.primary_focus}
- Conversation type: {context.conversation_type}
- Stage: {context.stage}
- Key uncertainties: {", ".join(context.uncertainties[:3]) or "none noted"}

Recent conversation (use this as your source of truth):
{full_transcript or "(no transcript yet)"}

Respond with this structure:
**Direct answer** — 2-3 sentences addressing the suggestion head-on, using their exact situation.
**Why it matters here** — 1-2 sentences tying the answer to something specific they said or are wrestling with.
**Concrete steps** — 2-4 tight bullets the speaker could act on immediately.
**Key tradeoff** — 1-2 sentences on the main risk or cost to consider.

Rules:
- Reference specific things from the transcript (names, numbers, decisions mentioned).
- Never give a generic answer that could apply to any conversation.
- 180-250 words total.
- No padding, no filler phrases like "Great question" or "Certainly".
{("Additional instructions: " + extra) if extra else ""}
"""
    else:
        prompt = f"""You are a real-time meeting copilot. Answer the user's question based on their ongoing conversation.

User question: "{user_message}"

Conversation context:
- Topic: {context.primary_focus}
- Type: {context.conversation_type}
- Stage: {context.stage}

Chat history:
{history_text or "(none)"}

Recent conversation:
{full_transcript or "(no transcript yet)"}

Give a focused, useful answer:
1. Direct answer (2-3 lines)
2. Recommendation or insight (2-3 bullets)
3. One tradeoff or caveat if relevant

Be specific to their conversation. 120-180 words max.
{("Additional instructions: " + extra) if extra else ""}
"""

    client = get_groq_client()
    response, _used = chat_with_fallback(
        client,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content or ""


def stream_chat_reply(
    user_message: str,
    transcript_entries: List[TranscriptEntry],
    context: ContextPayload,
    history: List[ChatMessage],
    from_suggestion: bool = False,
) -> Generator[str, None, None]:
    """Identical prompt logic to build_chat_reply but yields text deltas as they stream."""
    from backend.services.session_store import CONFIG

    entry_limit = 8 if from_suggestion else 4
    tail = transcript_entries[-entry_limit:] if transcript_entries else []
    full_transcript = "\n".join(f"[{t.timestamp}] {t.text}" for t in tail)[-CONFIG.chat_context_chars:]

    recent_history = history[-8:]
    history_text = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in recent_history if m.content
    )
    extra = CONFIG.chat_prompt_extra.strip()

    if from_suggestion:
        prompt = f"""You are a real-time meeting copilot. The user clicked a suggestion card during an active conversation.
Expand on it with a detailed, specific answer grounded entirely in what they are actually discussing.

Suggestion clicked: "{user_message}"

Conversation context:
- Primary topic: {context.primary_focus}
- Conversation type: {context.conversation_type}
- Stage: {context.stage}
- Key uncertainties: {", ".join(context.uncertainties[:3]) or "none noted"}

Recent conversation (use this as your source of truth):
{full_transcript or "(no transcript yet)"}

Respond with this structure:
**Direct answer** — 2-3 sentences addressing the suggestion head-on, using their exact situation.
**Why it matters here** — 1-2 sentences tying the answer to something specific they said or are wrestling with.
**Concrete steps** — 2-4 tight bullets the speaker could act on immediately.
**Key tradeoff** — 1-2 sentences on the main risk or cost to consider.

Rules:
- Reference specific things from the transcript (names, numbers, decisions mentioned).
- Never give a generic answer that could apply to any conversation.
- 180-250 words total.
- No padding, no filler phrases like "Great question" or "Certainly".
{("Additional instructions: " + extra) if extra else ""}
"""
    else:
        prompt = f"""You are a real-time meeting copilot. Answer the user's question based on their ongoing conversation.

User question: "{user_message}"

Conversation context:
- Topic: {context.primary_focus}
- Type: {context.conversation_type}
- Stage: {context.stage}

Chat history:
{history_text or "(none)"}

Recent conversation:
{full_transcript or "(no transcript yet)"}

Give a focused, useful answer:
1. Direct answer (2-3 lines)
2. Recommendation or insight (2-3 bullets)
3. One tradeoff or caveat if relevant

Be specific to their conversation. 120-180 words max.
{("Additional instructions: " + extra) if extra else ""}
"""

    client = get_groq_client()
    stream, _used = stream_with_fallback(
        client,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
