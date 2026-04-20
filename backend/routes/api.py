import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatMessage, ChatRequest, ConfigUpdate, Suggestion, SuggestionRequest, TranscriptEntry
from backend.services.chat_engine import stream_chat_reply
from backend.services.context_engine import (
    build_structured_context,
    detect_strong_signal_for_early_suggestion,
    segment_opening_summary,
)
from backend.services.session_store import CONFIG, STATE
from backend.services.suggestion_engine import generate_suggestions
from backend.services.transcription import transcribe_audio

router = APIRouter()


def _sync_session_segments(context, topic_shift: bool) -> None:
    """Maintain session_segments; on topic_shift close prior segment and open a new one."""
    prev_focus = STATE.last_primary_focus
    prev_type = STATE.last_conversation_type
    opening = segment_opening_summary(context)[:650]

    if not STATE.session_segments:
        STATE.session_segments.append(
            {
                "id": 0,
                "topic": context.primary_focus,
                "conversation_type": context.conversation_type,
                "summary": opening,
            }
        )
        STATE.current_segment_id = 0
        STATE.current_segment_start_idx = 0
        STATE.current_segment_rolling_summary = opening
        return

    if topic_shift:
        last = STATE.session_segments[-1]
        if prev_focus.strip():
            last["summary"] = (
                f"User discussed «{prev_focus}» ({prev_type}), then shifted to "
                f"«{context.primary_focus}» ({context.conversation_type})."
            )[:650]
        else:
            last["summary"] = (last.get("summary") or (context.summary or "")).strip()[:650]
        new_id = int(last["id"]) + 1
        STATE.session_segments.append(
            {
                "id": new_id,
                "topic": context.primary_focus,
                "conversation_type": context.conversation_type,
                "summary": opening,
            }
        )
        STATE.current_segment_id = new_id
        # New segment starts at the 2 entries that triggered the shift (they belong to new topic).
        STATE.current_segment_start_idx = max(0, len(STATE.transcript_entries) - 2)
        STATE.current_segment_rolling_summary = opening
        if len(STATE.session_segments) > 50:
            STATE.session_segments = STATE.session_segments[-50:]
    else:
        cur = STATE.session_segments[-1]
        cur["topic"] = context.primary_focus
        cur["conversation_type"] = context.conversation_type
        cur["summary"] = opening
        # Keep segment summary fresh with current topic only — no prior-segment bleed.
        STATE.current_segment_rolling_summary = (context.summary or opening).strip()[:650]


def _rollup_rolling_summary(context, topic_shift: bool, prev_focus: str, prev_type: str) -> None:
    """Global rolling line for export; non-empty, includes shift narrative when applicable."""
    base = (context.summary or "").strip()
    if topic_shift and prev_focus.strip():
        tail = (
            f" User discussed «{prev_focus}» ({prev_type}), then shifted to "
            f"«{context.primary_focus}» ({context.conversation_type})."
        ).strip()
        merged = f"{base} {tail}".strip() if base else tail
        STATE.rolling_summary = merged[-2400:]
    elif base:
        STATE.rolling_summary = base[-2400:]
    elif not (STATE.rolling_summary or "").strip():
        STATE.rolling_summary = (context.primary_focus or "Session started.")[:500]


def _signature(entries: list[TranscriptEntry]) -> str:
    if not entries:
        return ""
    tail = entries[-8:]
    return "|".join(f"{e.timestamp}:{e.text[:80]}" for e in tail)


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    started_at = time.perf_counter()
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Missing audio filename.")
        text = await transcribe_audio(file)
        entry = TranscriptEntry(timestamp=datetime.now().strftime("%H:%M:%S"), text=text)
        STATE.transcript_entries.append(entry)
        elapsed = time.perf_counter() - started_at
        print(f"[TRANSCRIBE] {elapsed:.2f}s")
        return {"text": text, "timestamp": entry.timestamp, "entry": entry.model_dump()}
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        print(f"[TRANSCRIBE][ERROR] {elapsed:.2f}s :: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/suggestions")
async def suggestions(payload: SuggestionRequest):
    started_at = time.perf_counter()
    STATE.transcript_entries = payload.transcript_entries or STATE.transcript_entries

    # Isolate to current segment: ignore entries that pre-date the last topic shift.
    segment_entries = STATE.transcript_entries[STATE.current_segment_start_idx:]
    if not segment_entries:
        segment_entries = STATE.transcript_entries[-4:]

    segment_sig = _signature(segment_entries)
    bypass_cache = bool(payload.force_refresh)
    if (
        STATE.last_context
        and segment_sig
        and segment_sig == STATE.last_transcript_signature
        and not bypass_cache
    ):
        context = STATE.last_context
    else:
        context = build_structured_context(
            segment_entries,
            STATE.current_segment_rolling_summary,
            last_primary_focus=STATE.last_primary_focus,
            last_conversation_type=STATE.last_conversation_type,
        )
        STATE.last_context = context
        STATE.last_transcript_signature = segment_sig

    topic_shift = bool(context.topic_shift)
    prev_focus = STATE.last_primary_focus
    prev_type = STATE.last_conversation_type

    # Cooldown guard: suppress consecutive shifts within 30 s to prevent rapid flipping.
    # Exception: very strong signals (sim < 0.3 + type change) bypass the cooldown.
    _SHIFT_COOLDOWN = 30.0
    sim = context.focus_chunk_similarity
    type_changed = bool(prev_type) and prev_type != context.conversation_type
    strong_shift_override = sim < 0.3 and type_changed
    if topic_shift and STATE.last_topic_shift_time:
        elapsed_since_shift = time.perf_counter() - STATE.last_topic_shift_time
        if elapsed_since_shift < _SHIFT_COOLDOWN:
            if strong_shift_override:
                print(
                    f"[TOPIC_SHIFT][OVERRIDE] sim={sim:.3f} type {prev_type!r}→{context.conversation_type!r}"
                    f" — bypassing cooldown ({elapsed_since_shift:.1f}s)"
                )
            else:
                print(
                    f"[TOPIC_SHIFT][SUPPRESSED] sim={sim:.3f} cooldown {elapsed_since_shift:.1f}s"
                    f" < {_SHIFT_COOLDOWN}s"
                )
                topic_shift = False

    if topic_shift:
        STATE.last_topic_shift_time = time.perf_counter()
        STATE.suggestion_history.clear()

    _sync_session_segments(context, topic_shift)
    _rollup_rolling_summary(context, topic_shift, prev_focus, prev_type)

    STATE.last_primary_focus = context.primary_focus
    STATE.last_conversation_type = context.conversation_type

    suggestion_source = "llm"
    try:
        suggestion_batch = await asyncio.to_thread(
            generate_suggestions, context, list(STATE.suggestion_history)
        )
        print("[SUGGESTIONS][PARSED]", len(suggestion_batch), "items")
        for s in suggestion_batch:
            print("  -", s.preview[:60] if s.preview else "")
    except Exception as exc:
        suggestion_source = "error_fallback"
        print(f"[SUGGESTIONS][ERROR] {exc}")
        suggestion_batch = [
            Suggestion(
                type="debugging probe",
                intent_category="root_cause",
                preview=f"What is the first failure signal for {context.primary_focus} under stress?",
                reason="Fallback after suggestion generation failed.",
                topic=context.primary_focus,
                score=0.5,
                relevance=0.5,
                novelty=0.5,
                actionability=0.5,
            ),
            Suggestion(
                type="architectural insight",
                intent_category="system_design",
                preview=f"What boundary or contract is most likely to break as {context.primary_focus} grows?",
                reason="Fallback after suggestion generation failed.",
                topic=context.primary_focus,
                score=0.5,
                relevance=0.5,
                novelty=0.5,
                actionability=0.5,
            ),
            Suggestion(
                type="validation step",
                intent_category="validation",
                preview=f"What single experiment would most reduce uncertainty about {context.primary_focus}?",
                reason="Fallback after suggestion generation failed.",
                topic=context.primary_focus,
                score=0.5,
                relevance=0.5,
                novelty=0.5,
                actionability=0.5,
            ),
        ]

    STATE.latest_batch_id += 1
    print(
        f"[SUGGESTIONS][BATCH_CREATED] batch_id={STATE.latest_batch_id}"
        f" segment_id={STATE.current_segment_id} count={len(suggestion_batch)}"
    )
    STATE.suggestion_history.append(
        {
            "batch_id": STATE.latest_batch_id,
            "segment_id": STATE.current_segment_id,
            "suggestions": suggestion_batch,
        }
    )
    print(f"[SUGGESTIONS][HISTORY_APPENDED] history_length={len(STATE.suggestion_history)}")

    early_signal = detect_strong_signal_for_early_suggestion(STATE.transcript_entries)
    elapsed = time.perf_counter() - started_at
    print(f"[SUGGESTIONS] {elapsed:.2f}s")

    serialized_history = [
        {
            "batch_id": batch["batch_id"],
            "segment_id": batch["segment_id"],
            "suggestions": [s.model_dump() for s in batch["suggestions"]],
        }
        for batch in STATE.suggestion_history
    ]
    print(f"[SUGGESTIONS][API_RETURN] suggestion_history length={len(serialized_history)}")

    return {
        "context": context.model_dump(),
        "suggestions": [s.model_dump() for s in suggestion_batch],
        "current_batch": {
            "batch_id": STATE.latest_batch_id,
            "segment_id": STATE.current_segment_id,
            "suggestions": [s.model_dump() for s in suggestion_batch],
        },
        "suggestion_history": serialized_history,
        "current_segment_id": STATE.current_segment_id,
        "meta": {
            "batch_id": STATE.latest_batch_id,
            "segment_id": STATE.current_segment_id,
            "reset_triggered": topic_shift,
            "topic_shift": topic_shift,
            "focus_chunk_similarity": context.focus_chunk_similarity,
            "early_signal_detected": early_signal,
            "suggestion_source": suggestion_source,
        },
    }


@router.post("/chat")
async def chat(payload: ChatRequest):
    STATE.transcript_entries = payload.transcript_entries or STATE.transcript_entries
    segment_entries = STATE.transcript_entries[STATE.current_segment_start_idx:]
    if not segment_entries:
        segment_entries = STATE.transcript_entries[-4:]
    segment_sig = _signature(segment_entries)
    context = build_structured_context(
        segment_entries,
        STATE.current_segment_rolling_summary,
        last_primary_focus=STATE.last_primary_focus,
        last_conversation_type=STATE.last_conversation_type,
    )
    STATE.last_context = context
    STATE.last_transcript_signature = segment_sig

    topic_shift = bool(context.topic_shift)
    prev_focus = STATE.last_primary_focus
    prev_type = STATE.last_conversation_type

    _SHIFT_COOLDOWN = 30.0
    sim = context.focus_chunk_similarity
    type_changed = bool(prev_type) and prev_type != context.conversation_type
    strong_shift_override = sim < 0.3 and type_changed
    if topic_shift and STATE.last_topic_shift_time:
        elapsed_since_shift = time.perf_counter() - STATE.last_topic_shift_time
        if elapsed_since_shift < _SHIFT_COOLDOWN:
            if strong_shift_override:
                print(
                    f"[TOPIC_SHIFT][OVERRIDE] sim={sim:.3f} type {prev_type!r}→{context.conversation_type!r}"
                    f" — bypassing cooldown ({elapsed_since_shift:.1f}s)"
                )
            else:
                print(
                    f"[TOPIC_SHIFT][SUPPRESSED] sim={sim:.3f} cooldown {elapsed_since_shift:.1f}s"
                    f" < {_SHIFT_COOLDOWN}s"
                )
                topic_shift = False

    if topic_shift:
        STATE.last_topic_shift_time = time.perf_counter()
        STATE.suggestion_history.clear()

    _sync_session_segments(context, topic_shift)
    _rollup_rolling_summary(context, topic_shift, prev_focus, prev_type)

    STATE.last_primary_focus = context.primary_focus
    STATE.last_conversation_type = context.conversation_type

    ts = datetime.now().strftime("%H:%M:%S")
    user_msg = ChatMessage(role="user", content=payload.message, timestamp=ts)
    STATE.chat_history.append(user_msg)

    # Capture loop-local references for the generator closure.
    # Use only current-segment entries so chat answers don't bleed prior topic context.
    _transcript = list(segment_entries)
    _context = context
    _history = list(STATE.chat_history)
    _from_suggestion = bool(payload.from_suggestion)
    _topic_shift = topic_shift
    _segment_id = STATE.current_segment_id

    def generate():
        # First event: metadata so the frontend can update context immediately.
        yield (
            "data: "
            + json.dumps({
                "type": "meta",
                "context": _context.model_dump(),
                "meta": {
                    "topic_shift": _topic_shift,
                    "segment_id": _segment_id,
                    "focus_chunk_similarity": _context.focus_chunk_similarity,
                },
            })
            + "\n\n"
        )

        collected: list[str] = []
        assistant_ts = datetime.now().strftime("%H:%M:%S")
        try:
            for delta in stream_chat_reply(
                payload.message, _transcript, _context, _history, from_suggestion=_from_suggestion
            ):
                collected.append(delta)
                yield "data: " + json.dumps({"type": "delta", "text": delta}) + "\n\n"
        finally:
            content = "".join(collected)
            assistant_msg = ChatMessage(role="assistant", content=content, timestamp=assistant_ts)
            STATE.chat_history.append(assistant_msg)
            yield "data: " + json.dumps({"type": "done", "timestamp": assistant_ts}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/export")
async def export_state():
    """Export transcript, rolling summary, segments with linked suggestion batches, and flat suggestion log."""
    flat_batches = []
    for b in list(STATE.suggestion_history):
        flat_batches.append([s.model_dump() for s in b.get("suggestions", [])])

    segments_out = []
    for seg in STATE.session_segments:
        sid = seg["id"]
        batches = [
            [s.model_dump() for s in b.get("suggestions", [])]
            for b in STATE.suggestion_history
            if int(b.get("segment_id", -1)) == int(sid)
        ]
        segments_out.append(
            {
                "id": seg["id"],
                "topic": seg.get("topic"),
                "conversation_type": seg.get("conversation_type"),
                "summary": seg.get("summary") or "",
                "suggestion_batches": batches,
            }
        )

    return {
        "transcript": [t.model_dump() for t in STATE.transcript_entries],
        "suggestions": flat_batches,
        "suggestions_with_meta": [
            {
                "batch_id": b.get("batch_id"),
                "segment_id": b.get("segment_id"),
                "suggestions": [s.model_dump() for s in b.get("suggestions", [])],
            }
            for b in STATE.suggestion_history
        ],
        "chat": [c.model_dump() for c in STATE.chat_history],
        "summary": (
            (STATE.rolling_summary or "").strip()
            or (getattr(STATE.last_context, "summary", None) or "").strip()
            or "No summary yet."
        ),
        "last_primary_focus": STATE.last_primary_focus,
        "last_conversation_type": STATE.last_conversation_type,
        "current_segment_id": STATE.current_segment_id,
        "session_segments": segments_out,
        "latest_batch_id": STATE.latest_batch_id,
    }


@router.get("/config")
async def get_config():
    return {
        "groq_api_key_set": bool(CONFIG.groq_api_key),
        "model_primary": CONFIG.model_primary,
        "model_fallback": CONFIG.model_fallback,
        "suggestion_context_chars": CONFIG.suggestion_context_chars,
        "chat_context_chars": CONFIG.chat_context_chars,
        "suggestion_prompt_extra": CONFIG.suggestion_prompt_extra,
        "chat_prompt_extra": CONFIG.chat_prompt_extra,
    }


@router.post("/config")
async def set_config(payload: ConfigUpdate):
    if payload.groq_api_key is not None:
        CONFIG.groq_api_key = payload.groq_api_key.strip()
    if payload.model_primary is not None:
        CONFIG.model_primary = payload.model_primary.strip()
    if payload.model_fallback is not None:
        CONFIG.model_fallback = payload.model_fallback.strip()
    if payload.suggestion_context_chars is not None:
        CONFIG.suggestion_context_chars = max(200, min(8000, payload.suggestion_context_chars))
    if payload.chat_context_chars is not None:
        CONFIG.chat_context_chars = max(500, min(16000, payload.chat_context_chars))
    if payload.suggestion_prompt_extra is not None:
        CONFIG.suggestion_prompt_extra = payload.suggestion_prompt_extra
    if payload.chat_prompt_extra is not None:
        CONFIG.chat_prompt_extra = payload.chat_prompt_extra
    print(f"[CONFIG] updated: key_set={bool(CONFIG.groq_api_key)} model={CONFIG.model_primary or '(default)'}")
    return {"ok": True, "groq_api_key_set": bool(CONFIG.groq_api_key)}
