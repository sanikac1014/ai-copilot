import json
import math
import re
from collections import Counter
from typing import List

from backend.models.schemas import ContextPayload, TranscriptEntry
from backend.services.groq_client import get_groq_client
from backend.services.model_config import chat_with_fallback


def _join_recent_entries(entries: List[TranscriptEntry], max_chars: int = 3500) -> str:
    blob = "\n".join(f"[{e.timestamp}] {e.text}" for e in entries)
    return blob[-max_chars:]


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2]


def _word_cosine(a: str, b: str) -> float:
    """Cosine similarity on word count vectors (0–1). Lightweight proxy for embedding similarity."""
    ca, cb = Counter(_tokens(a)), Counter(_tokens(b))
    vocab = set(ca) | set(cb)
    if not vocab:
        return 1.0
    dot = sum(ca[w] * cb[w] for w in vocab)
    na = math.sqrt(sum(c * c for c in ca.values()))
    nb = math.sqrt(sum(c * c for c in cb.values()))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _trigram_jaccard(a: str, b: str) -> float:
    s1 = re.sub(r"[^a-z0-9]+", " ", a.lower()).strip()
    s2 = re.sub(r"[^a-z0-9]+", " ", b.lower()).strip()

    def grams(s: str) -> set[str]:
        if len(s) < 3:
            return set()
        return {s[i : i + 3] for i in range(len(s) - 2)}

    g1, g2 = grams(s1), grams(s2)
    if not g1 and not g2:
        return 1.0
    if not g1 or not g2:
        return 0.0
    return len(g1 & g2) / len(g1 | g2)


def focus_chunk_semantic_similarity(focus: str, chunk: str) -> float:
    """
    Semantic overlap between prior primary focus and the latest transcript chunk(s).
    Blends word-level cosine + character trigram Jaccard (no external embedding API).
    Returns 0–1; values below ~0.6 indicate likely topic drift.
    """
    focus = (focus or "").strip()
    chunk = (chunk or "").strip()
    if not focus or not chunk:
        return 1.0
    if len(_tokens(chunk)) < 4:
        return 1.0
    wc = _word_cosine(focus, chunk)
    tg = _trigram_jaccard(focus, chunk)
    return max(0.0, min(1.0, 0.55 * wc + 0.45 * tg))


_STOP = frozenset(
    """
    the a an and or but if so to of in on for as at by is it we you he she they this that these those
    was were be been being have has had do does did will would could should may might not just like um
    uh okay ok yeah yep nope well really very much more most some any all can about into out up down
    then than too also gonna wanna kinda sorta literally basically um uh hey ooh wasnt werent dont
    """.split()
)

_BUSINESS_MARKERS = frozenset(
    {
        "revenue",
        "kpi",
        "mrr",
        "growth",
        "stakeholder",
        "roadmap",
        "deck",
        "pitch",
        "invest",
        "customer",
        "market",
        "sales",
        "quota",
        "okr",
        "pilot",
        "b2b",
        "pricing",
    }
)

_CASUAL_MARKERS = frozenset(
    {
        "dating",
        "relationship",
        "feel",
        "feeling",
        "worried",
        "anxious",
        "friend",
        "family",
        "therapy",
        "personal",
        "life",
        "love",
        "marriage",
        "breakup",
    }
)


def _infer_conversation_type_from_chunk(chunk: str) -> str:
    """Heuristic: technical vs casual vs business from latest text."""
    words = set(re.findall(r"[a-z0-9]+", chunk.lower()))
    tech_hits = len(words.intersection(_TECH_KEYWORDS))
    bus = sum(1 for m in _BUSINESS_MARKERS if m in chunk.lower())
    cas = sum(1 for m in _CASUAL_MARKERS if m in chunk.lower())
    if bus >= 2 and tech_hits < 2:
        return "business"
    if cas >= 1 and tech_hits < 2:
        return "casual"
    if tech_hits >= 2:
        return "technical"
    if bus >= 1:
        return "business"
    if cas >= 1:
        return "casual"
    return "technical" if tech_hits else "casual"


_FORBIDDEN_FOCUS_SUBSTRINGS = (
    "current discussion",
    "general discussion",
    "the discussion",
    "ongoing discussion",
    "main discussion",
    "broader discussion",
    "overall discussion",
)


def _derive_primary_focus_title(chunk: str, max_words: int = 6) -> str:
    """3–6 content words from transcript, title-style — never generic placeholder."""
    raw = re.findall(r"[a-z0-9]+", (chunk or "").lower())
    picked: List[str] = []
    seen: set[str] = set()
    for w in raw:
        if len(w) < 3 or w in _STOP:
            continue
        if w in seen:
            continue
        seen.add(w)
        picked.append(w)
        if len(picked) >= max_words:
            break
    if not picked:
        snippet = re.sub(r"\s+", " ", (chunk or "").strip())[:42]
        return (snippet + "…") if len((chunk or "").strip()) > 42 else (snippet or "Recent transcript")
    title = " ".join(picked[:max_words])
    return title[:72].strip()


def _sanitize_primary_focus(focus: str, chunk_only: str) -> str:
    f = (focus or "").strip()
    low = f.lower()
    if not f or any(b in low for b in _FORBIDDEN_FOCUS_SUBSTRINGS) or len(f) < 4:
        return _derive_primary_focus_title(chunk_only)
    if len(f) > 90:
        return f[:87].rsplit(" ", 1)[0] + "…" if " " in f else f[:87] + "…"
    return f


def _sanitize_conversation_type(ctype: str, chunk_only: str) -> str:
    inferred = _infer_conversation_type_from_chunk(chunk_only)
    c = (ctype or "technical").strip().lower()
    if c not in ("technical", "casual", "business"):
        c = inferred
    if c not in ("technical", "casual", "business"):
        c = "casual"
    # If model says technical but text is clearly casual/personal, trust chunk.
    if c == "technical" and inferred == "casual" and len(_tokens(chunk_only)) >= 5:
        return "casual"
    if c == "casual" and inferred == "technical" and len([w for w in _tokens(chunk_only) if w in _TECH_KEYWORDS]) >= 2:
        return "technical"
    return c


def _non_empty_summary(
    model_summary: str,
    effective_prior: str,
    chunk_only: str,
    primary_focus: str,
    conversation_type: str,
) -> str:
    s = (model_summary or "").strip()
    if len(s) >= 24:
        return s[:900]
    prior = (effective_prior or "").strip()
    snippet = " ".join((chunk_only or "").split())[:220]
    line = f"{conversation_type.title()} focus «{primary_focus}»: {snippet}".strip()
    if prior and len(prior) > 20 and prior.lower() not in line.lower():
        return (prior[:400] + " " + line).strip()[:900]
    return line[:900]


def segment_opening_summary(context: ContextPayload) -> str:
    """
    Non-empty summary for a segment row (initial session or new segment after topic_shift).
    Uses context.summary when substantive; otherwise opener + recent chunk (never empty).
    """
    model_s = (context.summary or "").strip()
    if len(model_s) >= 24:
        return model_s[:900]
    pf = (context.primary_focus or "").strip() or "the current topic"
    ct = context.conversation_type
    chunk = " ".join((context.recent_transcript or "").split())[:220]
    opener = f"User started discussing {pf} ({ct})."
    if chunk and chunk.lower() not in opener.lower():
        return f"{opener} {chunk[:200]}".strip()[:900]
    return opener[:900]


def _post_process_payload(
    payload: dict,
    chunk_only: str,
    recent_text: str,
    effective_summary: str,
    rolling_summary: str,
) -> None:
    """In-place: concrete primary_focus, aligned conversation_type, non-empty summary."""
    payload["primary_focus"] = _sanitize_primary_focus(str(payload.get("primary_focus", "")), chunk_only)
    payload["conversation_type"] = _sanitize_conversation_type(
        str(payload.get("conversation_type", "technical")),
        chunk_only,
    )
    payload["summary"] = _non_empty_summary(
        str(payload.get("summary", "")),
        effective_summary or rolling_summary,
        chunk_only,
        payload["primary_focus"],
        payload["conversation_type"],
    )


def _fallback_context(
    recent: str,
    summary: str,
    *,
    topic_shift: bool = False,
    focus_chunk_similarity: float = 1.0,
) -> ContextPayload:
    chunk = " ".join(re.findall(r"\S+", recent))[:2000]
    pf = _derive_primary_focus_title(chunk)
    ct = _infer_conversation_type_from_chunk(chunk)
    summ = _non_empty_summary("", summary, chunk, pf, ct)
    return ContextPayload(
        recent_transcript=recent,
        summary=summ,
        conversation_type=ct,  # type: ignore[arg-type]
        primary_focus=pf,
        secondary_topics=[],
        intent="discussion",
        entities=[],
        uncertainties=[],
        stage="problem",
        is_low_signal=detect_low_signal(recent),
        topic_shift=topic_shift,
        focus_chunk_similarity=focus_chunk_similarity,
    )


# Filler / low-information phrases (normalized matching)
_FILLER_PATTERNS = [
    "everything is fine",
    "everything is good",
    "everything's fine",
    "its working fine",
    "it's working fine",
    "all good",
    "sounds good",
    "okay yeah",
    "ok yeah",
    "that's nice",
    "thats nice",
    "no issues",
    "nothing wrong",
    "going well",
    "works fine",
    "seems fine",
    "looks good",
    "all fine",
    "pretty good",
    "not much to say",
    "nothing much",
]

# If transcript contains enough of these, treat as low-signal unless strong tech terms appear.
_TECH_KEYWORDS = frozenset(
    {
        "api",
        "latency",
        "scale",
        "scaling",
        "database",
        "redis",
        "kafka",
        "deploy",
        "deployment",
        "kubernetes",
        "docker",
        "websocket",
        "load",
        "throughput",
        "memory",
        "cpu",
        "error",
        "bug",
        "crash",
        "timeout",
        "queue",
        "cache",
        "microservice",
        "backend",
        "frontend",
        "infrastructure",
        "cost",
        "budget",
        "sla",
        "monitoring",
        "logging",
        "security",
        "auth",
        "oauth",
        "postgres",
        "sql",
        "distributed",
    }
)


def detect_low_signal(text: str) -> bool:
    """Heuristic: vague / filler-heavy transcript without clear technical substance."""
    if not text or not text.strip():
        return True
    lowered = text.lower()
    # Strong filler presence
    if any(p in lowered for p in _FILLER_PATTERNS):
        # Unless clearly technical in same blob, still low
        words = set(re.findall(r"[a-z0-9]+", lowered))
        tech_hits = len(words.intersection(_TECH_KEYWORDS))
        if tech_hits >= 2:
            return False
        return True
    words = set(re.findall(r"[a-z0-9]+", lowered))
    tech_hits = len(words.intersection(_TECH_KEYWORDS))
    # Short and no tech vocabulary
    if len(lowered) < 120 and tech_hits == 0:
        return True
    if tech_hits == 0 and len(lowered.split()) < 25:
        return True
    return False


def _tokenize_topic(value: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", value.lower()) if len(w) > 2}


def should_reset_context_memory(previous_focus: str, new_focus: str, threshold: float = 0.25) -> bool:
    if not previous_focus or not new_focus:
        return False
    prev_tokens = _tokenize_topic(previous_focus)
    next_tokens = _tokenize_topic(new_focus)
    if not prev_tokens or not next_tokens:
        return False
    overlap = len(prev_tokens.intersection(next_tokens))
    union = len(prev_tokens.union(next_tokens))
    similarity = overlap / union if union else 0.0
    return similarity < threshold


def _focus_is_same(a: str, b: str, threshold: float = 0.5) -> bool:
    """True when new primary_focus overlaps enough with previous that no shift is warranted."""
    if not a or not b:
        return False
    ta = _tokenize_topic(a)
    tb = _tokenize_topic(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    union = len(ta | tb)
    return (overlap / union if union else 0.0) >= threshold


def detect_strong_signal_for_early_suggestion(entries: List[TranscriptEntry]) -> bool:
    if not entries:
        return False
    recent = " ".join(e.text.lower() for e in entries[-4:])
    uncertainty_markers = ["not sure", "unsure", "i don't know", "unknown", "unclear", "confused"]
    decision_markers = ["should we", "let's decide", "decision", "go with", "choose", "tradeoff"]
    if "?" in recent:
        return True
    if any(token in recent for token in uncertainty_markers):
        return True
    if any(token in recent for token in decision_markers):
        return True
    return False


def build_structured_context(
    entries: List[TranscriptEntry],
    rolling_summary: str,
    *,
    last_primary_focus: str = "",
    last_conversation_type: str = "",
) -> ContextPayload:
    """
    Build context from the **latest 1–2 transcript chunks only** (no full session in the prompt).
    Detects topic_shift when chunk is dissimilar to last_primary_focus or conversation_type changes.
    """
    if not entries:
        return ContextPayload(
            recent_transcript="",
            summary=rolling_summary or "No transcript yet.",
            conversation_type="casual",
            primary_focus="Waiting for transcript",
            secondary_topics=[],
            intent="idle",
            entities=[],
            uncertainties=[],
            stage="problem",
            is_low_signal=True,
            topic_shift=False,
            focus_chunk_similarity=1.0,
        )

    recent_entries = entries[-2:]
    recent_text = _join_recent_entries(recent_entries, max_chars=2500)
    chunk_only = " ".join(e.text for e in recent_entries).strip()

    sim = focus_chunk_semantic_similarity(last_primary_focus, chunk_only)
    use_prior_summary = bool(not last_primary_focus.strip() or sim >= 0.6)
    effective_summary = (rolling_summary or "").strip() if use_prior_summary else ""

    client = get_groq_client()
    prompt = f"""Context engine. Return ONLY JSON with keys: recent_transcript, summary, conversation_type, primary_focus, secondary_topics, intent, entities, uncertainties, stage.

PRIMARY_FOCUS (critical):
- Exactly 3–6 words, drawn from phrases the user actually said in Recent (e.g. "Postgres vs distributed database", "Dating decision uncertainty").
- NEVER use placeholders: "Current discussion", "General discussion", "Ongoing topic", or similar.

CONVERSATION_TYPE (critical) — pick one:
- technical: systems, infra, APIs, databases, engineering tradeoffs
- casual: personal life, relationships, feelings, social situations
- business: metrics, growth, revenue, product strategy, stakeholders

SUMMARY (critical):
- 1–2 complete sentences describing ONLY what appears in Recent (and how it connects to Prior rolling summary if that prior text still applies).
- Must be non-empty and concrete (who/what/worry).

STAGE: problem | solution | tradeoff

Recent (latest 1–2 segments only — sole source of truth for focus and type):
{recent_text}

Prior rolling summary (ignore if unrelated to Recent): {effective_summary or "(none)"}
"""
    try:
        response, _used = chat_with_fallback(
            client,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        payload["recent_transcript"] = payload.get("recent_transcript") or recent_text
        payload.setdefault("summary", "")
        payload.setdefault("conversation_type", "technical")
        payload.setdefault("primary_focus", "")

        _post_process_payload(payload, chunk_only, recent_text, effective_summary, rolling_summary)

        payload.setdefault("is_low_signal", False)
        payload["is_low_signal"] = bool(payload.get("is_low_signal")) or detect_low_signal(
            payload["recent_transcript"] or recent_text
        )

        new_type = payload.get("conversation_type", "technical")
        new_focus = (payload.get("primary_focus") or "").strip()

        # Require heuristic to corroborate LLM type flips — prevents model phrasing artefacts.
        heuristic_type = _infer_conversation_type_from_chunk(chunk_only)
        type_mismatch = (
            bool(last_conversation_type)
            and last_conversation_type != new_type
            and heuristic_type != last_conversation_type
        )

        low_chunk_sim = bool(last_primary_focus.strip()) and sim < 0.6

        # focus_token_reset only counts when chunk similarity is also low — avoids shift on
        # rephrased-but-same topics where the LLM just chose different wording for primary_focus.
        focus_token_reset = low_chunk_sim and should_reset_context_memory(last_primary_focus, new_focus)

        # Idempotency: if the new focus overlaps strongly with the previous one, suppress shift.
        focus_same = _focus_is_same(last_primary_focus, new_focus)

        topic_shift = not focus_same and bool(type_mismatch or low_chunk_sim or focus_token_reset)

        print(
            f"[TOPIC_SHIFT] prev_focus={last_primary_focus!r} new_focus={new_focus!r} "
            f"sim={sim:.3f} heuristic_type={heuristic_type!r} llm_type={new_type!r} "
            f"type_mismatch={type_mismatch} low_chunk_sim={low_chunk_sim} "
            f"focus_token_reset={focus_token_reset} focus_same={focus_same} → shift={topic_shift}"
        )

        payload["focus_chunk_similarity"] = round(float(sim), 4)
        payload["topic_shift"] = topic_shift

        if topic_shift:
            payload["secondary_topics"] = []
            payload["stage"] = "problem"
            if not use_prior_summary:
                payload["summary"] = _non_empty_summary(
                    str(payload.get("summary", "")),
                    "",
                    chunk_only,
                    payload["primary_focus"],
                    payload["conversation_type"],
                )

        return ContextPayload(**payload)
    except Exception:
        ts = bool(last_primary_focus.strip()) and sim < 0.6
        return _fallback_context(recent_text, rolling_summary or "", topic_shift=ts, focus_chunk_similarity=float(sim))
