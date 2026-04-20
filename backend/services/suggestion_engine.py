import json
import re
from collections import Counter
from typing import Any, List, Optional, Tuple, Union

from backend.models.schemas import ContextPayload, Suggestion
from backend.services.groq_client import get_groq_client
from backend.services.model_config import chat_with_fallback


def rank_topics(context: ContextPayload) -> List[dict]:
    topics = [context.primary_focus] + context.secondary_topics
    weighted = []
    for idx, topic in enumerate([t for t in topics if t]):
        base = 1.0 if idx == 0 else max(0.2, 0.7 - (idx * 0.2))
        stage_boost = 0.1 if context.stage in ("solution", "tradeoff") and idx == 0 else 0.0
        weighted.append({"name": topic, "score": round(base + stage_boost, 2)})
    return weighted[:2]


def _flatten_previous(previous_batches: List[Union[List[Suggestion], Any]]) -> List[str]:
    out: List[str] = []
    for batch in previous_batches:
        if isinstance(batch, dict):
            for s in batch.get("suggestions", []):
                if hasattr(s, "preview"):
                    out.append(str(s.preview))
                elif isinstance(s, dict):
                    out.append(str(s.get("preview", "")))
        else:
            for s in batch:
                out.append(str(s.preview))
    return out


# When transcript is vague, block hollow template lines unless we replace them.
_BANNED_GENERIC_LOW_SIGNAL = (
    "what's the next step",
    "what is the next step",
    "next step?",
    "implement additional features",
    "implement more features",
    "identify risks",
    "identify risk",
    "list risks",
)


def _is_banned_generic_preview(preview: str) -> bool:
    p = preview.lower().strip()
    return any(b in p for b in _BANNED_GENERIC_LOW_SIGNAL)


# Abstract "framework label" phrasing — reads templated, not like the user's words.
_BANNED_ABSTRACT_PHRASES = (
    "validate emotions",
    "validate feelings",
    "emotional validation",
    "define scope",
    "explore constraints",
    "surface concerns",
    "clarify expectations",
    "establish alignment",
    "reason for hesitation",
    "acknowledge feelings",
    "explore dynamics",
    "key considerations",
    "identify blockers",
    "map stakeholders",
    "align on priorities",
    "unpack tensions",
    "process feelings",
    "hold space",
    "name the emotion",
    "check assumptions",
    "frame the problem",
    "zoom out",
    "double down",
)

_STOPWORDS = frozenset(
    """
    the a an and or but if so to of in on for as at by is it we you he she they this that these those
    was were be been being have has had do does did will would could should may might not just like um
    uh okay ok yeah yep nope well really very much more most some any all can about into out up down
    so then than too also gonna wanna kinda sorta literally basically um uh hey ooh
    """.split()
)

# Filler / discourse — skip as grounding anchors (prefer content words).
_SKIP_ANCHOR = frozenset(
    {
        "okay",
        "ok",
        "yeah",
        "yep",
        "nope",
        "well",
        "like",
        "just",
        "really",
        "literally",
        "basically",
        "thinking",
        "wondering",
        "talking",
    }
)


def _is_banned_abstract_preview(preview: str) -> bool:
    p = preview.lower().strip()
    return any(b in p for b in _BANNED_ABSTRACT_PHRASES)


def _salient_transcript_tokens(transcript: str, limit: int = 18) -> List[str]:
    """Order-preserving salient words from transcript (for grounding checks and prompt hints)."""
    words = re.findall(r"[a-z0-9]+", (transcript or "").lower())
    out: List[str] = []
    seen: set[str] = set()
    for w in words:
        if len(w) < 3 or w in _STOPWORDS or w in _SKIP_ANCHOR:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= limit:
            break
    return out


def _top_weighted_tokens(transcript: str, limit: int = 14) -> List[str]:
    """Frequent content words, excluding stopwords — good anchors for grounding."""
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", (transcript or "").lower())
        if len(w) > 2 and w not in _STOPWORDS and w not in _SKIP_ANCHOR
    ]
    if not words:
        return []
    freq = Counter(words)
    first_at: dict[str, int] = {}
    for i, w in enumerate(words):
        first_at.setdefault(w, i)
    ranked = sorted(freq.keys(), key=lambda w: (-freq[w], first_at[w]))
    return ranked[:limit]


def _is_preview_grounded_in_transcript(preview: str, transcript: str, primary_focus: str) -> bool:
    """True if preview echoes transcript language or the stated primary focus (not generic labels)."""
    pv = (preview or "").lower()
    tr = (transcript or "").lower()
    if len(tr.strip()) < 12:
        return True
    if not pv.strip():
        return False
    # Substring: any salient token (4+) from transcript appears in preview
    salient4 = [w for w in _salient_transcript_tokens(tr, 24) if len(w) >= 4]
    for w in salient4[:16]:
        if w in pv:
            return True
    salient3 = [w for w in _salient_transcript_tokens(tr, 30) if len(w) == 3]
    for w in salient3[:12]:
        if w in pv:
            return True
    # Bigram from transcript appears inside preview
    tw = [
        t
        for t in re.findall(r"[a-z0-9]+", tr)
        if len(t) > 2 and t not in _STOPWORDS and t not in _SKIP_ANCHOR
    ]
    for i in range(len(tw) - 1):
        bigram = f"{tw[i]} {tw[i + 1]}"
        if len(bigram) >= 7 and bigram in pv:
            return True
    # Primary focus words
    for w in re.findall(r"[a-z0-9]+", (primary_focus or "").lower()):
        if len(w) > 3 and w in pv:
            return True
    return False


def _grounded_rewrite_preview(
    intent_category: str,
    transcript: str,
    primary_focus: str,
    salient: List[str],
) -> str:
    """Short conversational line that must use transcript vocabulary."""
    a = salient[0] if salient else (re.findall(r"[a-z0-9]+", (primary_focus or "that").lower()) or ["that"])[0]
    b = salient[1] if len(salient) > 1 else ""
    focus_short = (primary_focus or "this").strip()[:48]
    if intent_category == "root_cause":
        return f"When you said {a}{', ' + b if b else ''} — what feels like the real sticking point there?"
    if intent_category == "system_design":
        return f"Given you brought up {a}, how would you split ownership so {focus_short} stays maintainable?"
    if intent_category == "tradeoff":
        return f"Between keeping {a} simple and pushing further on {b or focus_short}, where are you leaning?"
    if intent_category == "validation":
        return f"What would convince you in the next week that {a} is actually working for you?"
    if intent_category == "constraint":
        return f"What hard limit (time, money, energy) is squeezing {a} the most right now?"
    if intent_category == "alternative":
        return f"If you parked {a} for a moment, what would you try instead for {b or focus_short}?"
    return f"What about {a} feels most unclear when you think about {focus_short}?"


_CANONICAL_INTENTS = frozenset(
    {
        "root_cause",
        "system_design",
        "tradeoff",
        "validation",
        "constraint",
        "alternative",
        "clarification",
        "scope",
    }
)

_INTENT_ALIASES = {
    "root_cause_probing": "root_cause",
    "debugging_probe": "root_cause",
    "debugging": "root_cause",
    "debug": "root_cause",
    "probe": "root_cause",
    "architectural_insight": "system_design",
    "architecture": "system_design",
    "design": "system_design",
    "system_design_improvement": "system_design",
    "tradeoff_analysis": "tradeoff",
    "risk": "tradeoff",
    "validation_testing_step": "validation",
    "testing": "validation",
    "test": "validation",
    "constraint_awareness": "constraint",
    "prioritization": "constraint",
    "alternative_approach": "alternative",
    "optimization": "alternative",
}


def _normalize_intent_category(raw: Optional[str], preview: str, type_str: str) -> str:
    if raw:
        s = re.sub(r"[^a-z0-9]+", "_", raw.lower().strip()).strip("_")
        s = _INTENT_ALIASES.get(s, s)
        if s in _CANONICAL_INTENTS:
            return s
    blob = f"{type_str} {preview}".lower()
    if any(x in blob for x in ("debug", "bottleneck", "cause", "why", "root")):
        return "root_cause"
    if any(x in blob for x in ("architect", "design", "component", "service boundary")):
        return "system_design"
    if any(x in blob for x in ("tradeoff", "risk", "versus", " vs ")):
        return "tradeoff"
    if any(x in blob for x in ("test", "validate", "verify", "experiment", "scenario")):
        return "validation"
    if any(x in blob for x in ("constraint", "budget", "priorit", "limit", "sla")):
        return "constraint"
    if any(x in blob for x in ("alternative", "instead", "another approach", "option")):
        return "alternative"
    return "alternative"


def _preview_similarity(p1: str, p2: str) -> float:
    raw1 = re.findall(r"[a-z0-9]+", p1.lower())
    raw2 = re.findall(r"[a-z0-9]+", p2.lower())
    w1 = [w for w in raw1 if len(w) > 2]
    w2 = [w for w in raw2 if len(w) > 2]
    if not w1 or not w2:
        return 0.0
    set1, set2 = set(w1), set(w2)
    union = len(set1 | set2)
    jaccard = len(set1 & set2) / union if union else 0.0
    pre1, pre2 = w1[:5], w2[:5]
    if pre1 and pre2:
        prefix_overlap = len(set(pre1) & set(pre2)) / max(len(pre1), len(pre2))
    else:
        prefix_overlap = 0.0
    out = max(jaccard, prefix_overlap * 0.95)
    # Same opening bigram on raw tokens (includes "is", "the", …) → structurally similar
    if len(raw1) >= 2 and len(raw2) >= 2 and raw1[0] == raw2[0] and raw1[1] == raw2[1]:
        out = max(out, 0.58)
    return out


def _apply_diversity_penalties(
    ranked: List[Tuple[float, Any, float, float, float]],
) -> List[Tuple[float, Any, float, float, float]]:
    """Lower score of later items when they are too similar to an earlier higher-scored item or share intent."""
    if len(ranked) <= 1:
        return ranked
    n = len(ranked)
    adjusted_totals: List[float] = [t[0] for t in ranked]
    previews = [
        str(r[1].get("preview", "")) if isinstance(r[1], dict) else "" for r in ranked
    ]
    intents = [
        _normalize_intent_category(
            r[1].get("intent_category") if isinstance(r[1], dict) else None,
            str(r[1].get("preview", "")) if isinstance(r[1], dict) else "",
            str(r[1].get("type", "")) if isinstance(r[1], dict) else "",
        )
        for r in ranked
    ]

    for i in range(n):
        for j in range(i + 1, n):
            sim = _preview_similarity(previews[i], previews[j])
            if sim >= 0.48:
                adjusted_totals[j] -= 0.18 + sim * 0.12
            if intents[i] and intents[j] and intents[i] == intents[j]:
                adjusted_totals[j] -= 0.14

    out: List[Tuple[float, Any, float, float, float]] = []
    for idx, row in enumerate(ranked):
        total, item, rel, nov, act = row
        out.append((round(adjusted_totals[idx], 4), item, rel, nov, act))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _apply_grounding_penalties(
    ranked: List[Tuple[float, Any, float, float, float]],
    transcript: str,
    primary_focus: str,
    is_low_signal: bool,
) -> List[Tuple[float, Any, float, float, float]]:
    """Down-rank abstract or ungrounded previews before diversity selection."""
    if is_low_signal:
        return ranked
    out: List[Tuple[float, Any, float, float, float]] = []
    for row in ranked:
        total, item, rel, nov, act = row
        if not isinstance(item, dict):
            out.append(row)
            continue
        pv = str(item.get("preview", ""))
        pen = 0.0
        if _is_banned_abstract_preview(pv) or _is_banned_generic_preview(pv):
            pen += 0.42
        if not _is_preview_grounded_in_transcript(pv, transcript, primary_focus):
            pen += 0.36
        out.append((round(total - pen, 4), item, rel, nov, act))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _enforce_grounding_quality(suggestions: List[Suggestion], context: ContextPayload) -> List[Suggestion]:
    """Replace abstract or ungrounded previews with conversational, transcript-anchored lines."""
    tr = context.recent_transcript or ""
    pf = context.primary_focus or ""
    salient = _top_weighted_tokens(tr) or _salient_transcript_tokens(tr)
    out: List[Suggestion] = []
    for sug in suggestions:
        p = sug.preview or ""
        if context.is_low_signal:
            out.append(sug)
            continue
        bad = _is_banned_abstract_preview(p) or _is_banned_generic_preview(p)
        grounded = _is_preview_grounded_in_transcript(p, tr, pf)
        if bad or not grounded:
            new_p = _grounded_rewrite_preview(sug.intent_category or "clarification", tr, pf, salient)
            reason = (sug.reason or "").strip()
            if reason:
                reason = f"{reason} Grounded in your words."
            else:
                reason = "Grounded in your transcript."
            out.append(
                Suggestion(
                    type=sug.type,
                    intent_category=sug.intent_category,
                    preview=_sharpen_preview(new_p),
                    reason=reason[:220],
                    topic=sug.topic,
                    score=sug.score,
                    relevance=sug.relevance,
                    novelty=sug.novelty,
                    actionability=sug.actionability,
                )
            )
        else:
            out.append(sug)
    return out


def _select_diverse_top3(
    ranked: List[Tuple[float, Any, float, float, float]],
) -> List[Tuple[float, Any, float, float, float]]:
    """Pick up to 3 with distinct intent_category and low pairwise preview similarity."""
    picked: List[Tuple[float, Any, float, float, float]] = []
    picked_intents: set = set()
    picked_previews: List[str] = []
    picked_indices: set = set()

    def intent_of(item: Any) -> str:
        if not isinstance(item, dict):
            return "alternative"
        return _normalize_intent_category(
            item.get("intent_category"),
            str(item.get("preview", "")),
            str(item.get("type", "")),
        )

    def preview_of(item: Any) -> str:
        return str(item.get("preview", "")) if isinstance(item, dict) else ""

    for i, row in enumerate(ranked):
        if len(picked) >= 3:
            break
        _, item, _, _, _ = row
        ic = intent_of(item)
        pv = preview_of(item)
        if ic in picked_intents:
            continue
        if any(_preview_similarity(pv, pp) >= 0.52 for pp in picked_previews):
            continue
        picked.append(row)
        picked_indices.add(i)
        picked_intents.add(ic)
        picked_previews.append(pv)

    if len(picked) < 3:
        for i, row in enumerate(ranked):
            if len(picked) >= 3:
                break
            if i in picked_indices:
                continue
            _, item, _, _, _ = row
            pv = preview_of(item)
            if any(_preview_similarity(pv, pp) >= 0.55 for pp in picked_previews):
                continue
            picked.append(row)
            picked_indices.add(i)
            picked_previews.append(pv)

    if len(picked) < 3:
        for i, row in enumerate(ranked):
            if len(picked) >= 3:
                break
            if i in picked_indices:
                continue
            picked.append(row)
            picked_indices.add(i)

    return picked[:3]


def _low_signal_template_suggestions(primary_focus: str, recent_transcript: str) -> List[Suggestion]:
    """Deterministic lines when the model drifts generic; still echo user wording when possible."""
    sal = _salient_transcript_tokens(recent_transcript or "", 6)
    hook = sal[0] if sal else "that"
    hook2 = sal[1] if len(sal) > 1 else ""
    return [
        Suggestion(
            type="clarification",
            intent_category="clarification",
            preview=_sharpen_preview(
                f"You mentioned {hook}{' and ' + hook2 if hook2 else ''} — what are you hoping to figure out next?"
            ),
            reason="Low-signal: clarify using their wording.",
            topic=primary_focus,
            score=0.72,
            relevance=0.75,
            novelty=0.65,
            actionability=0.7,
        ),
        Suggestion(
            type="validation step",
            intent_category="validation",
            preview=_sharpen_preview(
                f"If we stress-tested {hook} (latency, load, or failure), which scenario would tell you the most?"
            ),
            reason="Low-signal: steer toward a concrete check tied to their words.",
            topic=primary_focus,
            score=0.7,
            relevance=0.72,
            novelty=0.68,
            actionability=0.72,
        ),
        Suggestion(
            type="scope insight",
            intent_category="scope",
            preview=_sharpen_preview(
                f"I'm not hearing a sharp problem yet around {hook} — what would make this feel solved for you?"
            ),
            reason="Low-signal: honest scope without framework jargon.",
            topic=primary_focus,
            score=0.68,
            relevance=0.7,
            novelty=0.6,
            actionability=0.55,
        ),
    ]


def _merge_low_signal_suggestions(
    generated: List[Suggestion], context: ContextPayload
) -> List[Suggestion]:
    """Replace banned generic previews with curated low-signal lines."""
    templates = _low_signal_template_suggestions(context.primary_focus, context.recent_transcript or "")
    ti = 0
    out: List[Suggestion] = []
    for sug in generated:
        if _is_banned_generic_preview(sug.preview) and ti < len(templates):
            out.append(templates[ti])
            ti += 1
        else:
            out.append(sug)
    while len(out) < 3 and ti < len(templates):
        out.append(templates[ti])
        ti += 1
    return out[:3]


def _dedupe_intent_in_top3(suggestions: List[Suggestion], context: ContextPayload) -> List[Suggestion]:
    """Ensure final three use distinct intent lenses when possible."""
    if len(suggestions) <= 1:
        return suggestions
    seen: set = set()
    result: List[Suggestion] = []
    sal = _top_weighted_tokens(context.recent_transcript or "") or _salient_transcript_tokens(
        context.recent_transcript or "", 8
    )
    a0 = sal[0] if sal else "this"
    a1 = sal[1] if len(sal) > 1 else context.primary_focus or "that"
    pf = context.primary_focus or "this"
    alt_pool = [
        ("tradeoff note", "tradeoff", f"You brought up {a0} — what are you giving up if you push harder on {a1}?"),
        ("constraint check", "constraint", f"What real-world limit is squeezing {a0} the most for {pf}?"),
        ("alternative approach", "alternative", f"If {a0} stayed as-is, what would you try next for {pf}?"),
        ("validation step", "validation", f"What would prove to you in one week that {a0} is actually fixed?"),
    ]
    ai = 0
    for sug in suggestions:
        ic = sug.intent_category or "alternative"
        if ic not in seen:
            seen.add(ic)
            result.append(sug)
            continue
        while ai < len(alt_pool) and alt_pool[ai][1] in seen:
            ai += 1
        if ai >= len(alt_pool):
            result.append(sug)
            continue
        t, new_ic, pv = alt_pool[ai]
        ai += 1
        seen.add(new_ic)
        result.append(
            Suggestion(
                type=t,
                intent_category=new_ic,
                preview=pv[:110],
                reason="Diversity: distinct intent lens.",
                topic=sug.topic or context.primary_focus,
                score=sug.score,
                relevance=sug.relevance,
                novelty=sug.novelty,
                actionability=sug.actionability,
            )
        )
    return result[:3]


def _sharpen_preview(preview: str) -> str:
    refined = preview.strip()
    replacements = {
        "Consider ": "",
        "consider ": "",
        "You should ": "",
        "It may help to ": "",
        "You might want to ": "",
    }
    for old, new in replacements.items():
        if refined.startswith(old):
            refined = new + refined[len(old) :]
    return refined[:110]


def _diversity_prompt_block(is_low_signal: bool) -> str:
    if is_low_signal:
        return """
LOW-SIGNAL TRANSCRIPT: Acknowledge limited signal; no hollow PM templates.
Still output 6 candidates with DISTINCT intent_category values from the allowed set (use each lens at most once): clarification, scope, validation, root_cause, constraint, alternative.
GROUNDING: Each preview must repeat at least one substantive word or short phrase from recent_transcript (names, products, feelings, tech terms they used). Sound like a friend talking, not a framework.
"""
    return """
Generate exactly 6 suggestions with diverse thinking angles. Do not repeat the same structure or opening.
Each suggestion must use a different cognitive lens and a different intent_category (use each exactly once across the 6):
- root_cause — probing causes, bottlenecks, or failure modes
- system_design — structure, boundaries, scalability, components
- tradeoff — costs, risks, competing options
- validation — tests, experiments, verification, scenarios
- constraint — budgets, SLAs, priorities, limits
- alternative — different approach or path

TONE (critical): Write like a smart friend or senior engineer in real time — warm, direct, conversational. Never like a consulting slide or therapy worksheet label.

GROUNDING (critical): Every preview MUST visibly echo the user's language — include a concrete word, name, product, metric, or phrase copied from recent_transcript, or directly name their stated worry. If you cannot tie to their words, ask a question that quotes what they said.

BANNED STYLE: No abstract noun stacks such as "validate emotions", "define scope", "explore constraints", "reason for hesitation", "surface concerns", "clarify expectations". Use full spoken sentences ("What about dating feels overwhelming right now?") not label-speak.

Allowed suggestion "type" strings (examples — vary wording): debugging probe, architectural insight, constraint check, prioritization angle, alternative approach, validation step, tradeoff note.
Do NOT force a rigid question / solution / risk pattern. Prefer varied sentence shapes.
Do NOT use these hollow phrases unless the transcript clearly supports them: "What's the next step?", "Implement additional features", "Identify risks".
Return JSON only.
"""


def _json_instruction() -> str:
    return """Return JSON: {"candidates": [
  {"type": "short descriptive label", "intent_category": "one of: root_cause|system_design|tradeoff|validation|constraint|alternative",
   "preview": "one spoken line, <=20 words, must include a word/phrase from recent_transcript", "reason": "one short clause tying preview to transcript", "relevance": 0.0, "novelty": 0.0, "actionability": 0.0}
]}"""


def generate_suggestions(context: ContextPayload, previous_batches: List[List[Suggestion]]) -> List[Suggestion]:
    from backend.services.session_store import CONFIG
    previous_suggestions = _flatten_previous(previous_batches)
    top_topics = rank_topics(context)
    focus_text = ", ".join(f"{t['name']} ({t['score']})" for t in top_topics) or context.primary_focus

    rt = (context.recent_transcript or "")[-CONFIG.suggestion_context_chars:]
    echo_hints = _top_weighted_tokens(rt)[:14]
    minimal_context = {
        "recent_transcript": rt,
        "primary_focus": context.primary_focus,
        "conversation_type": context.conversation_type,
        "stage": context.stage,
        "is_low_signal": context.is_low_signal,
        "prefer_echo_these_terms": echo_hints,
    }
    prev_short = previous_suggestions[-12:]

    diversity = _diversity_prompt_block(context.is_low_signal)

    if context.is_low_signal:
        strategy = """
Rules: Acknowledge limited signal. Prefer clarification, honest scope-setting, concrete scenarios when relevant.
Each preview must still reuse at least one content word from recent_transcript when any exist in prefer_echo_these_terms.
Do NOT use: "What's the next step?", "Implement additional features", "Identify risks" unless substantive.
"""
    else:
        strategy = """
Each preview must sound like something you'd say out loud to the speaker after listening — never a framework label.
Use prefer_echo_these_terms where natural; every preview must clearly reference their actual words or situation.
"""

    extra = CONFIG.suggestion_prompt_extra.strip()
    prompt = f"""Meeting copilot. Context: {json.dumps(minimal_context)}
Prior previews (no repeat): {json.dumps(prev_short)}
Topics: {focus_text}
{diversity}
{strategy}
{("Additional instructions: " + extra) if extra else ""}
{_json_instruction()}
"""
    client = get_groq_client()
    response, used_model = chat_with_fallback(
        client,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.35,
        response_format={"type": "json_object"},
    )
    print(f"[SUGGESTIONS][MODEL] {used_model}")
    raw_text = response.choices[0].message.content or ""
    print("[SUGGESTIONS][RAW OUTPUT]", raw_text[:800] + ("..." if len(raw_text) > 800 else ""))
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print("[SUGGESTIONS][PARSE ERROR]", exc)
        raise
    items = payload if isinstance(payload, list) else payload.get("candidates", payload.get("suggestions", []))
    ranked = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        relevance = max(0.0, min(1.0, float(item.get("relevance", 0.5))))
        novelty = max(0.0, min(1.0, float(item.get("novelty", 0.5))))
        actionability = max(0.0, min(1.0, float(item.get("actionability", 0.5))))
        total = round(relevance + novelty + actionability, 3)
        ranked.append((total, item, relevance, novelty, actionability))

    ranked.sort(key=lambda tup: tup[0], reverse=True)
    ranked = _apply_grounding_penalties(
        ranked,
        context.recent_transcript or "",
        context.primary_focus or "",
        context.is_low_signal,
    )
    ranked = _apply_diversity_penalties(ranked)
    top_three_rows = _select_diverse_top3(ranked)

    suggestions = []
    for idx, (total, item, relevance, novelty, actionability) in enumerate(top_three_rows):
        topic = top_topics[min(idx, max(0, len(top_topics) - 1))]["name"] if top_topics else context.primary_focus
        topic_score = top_topics[min(idx, max(0, len(top_topics) - 1))]["score"] if top_topics else 0.5
        combined = round((total / 3.0) * 0.8 + (topic_score * 0.2), 3)
        preview_raw = item.get("preview", "") if isinstance(item, dict) else ""
        type_raw = item.get("type", "insight") if isinstance(item, dict) else "insight"
        ic = _normalize_intent_category(
            item.get("intent_category") if isinstance(item, dict) else None,
            str(preview_raw),
            str(type_raw),
        )
        suggestions.append(
            Suggestion(
                type=str(type_raw)[:80],
                intent_category=ic,
                preview=_sharpen_preview(str(preview_raw)),
                reason=str(item.get("reason", "")) if isinstance(item, dict) else "",
                topic=topic,
                score=combined,
                relevance=relevance,
                novelty=novelty,
                actionability=actionability,
            )
        )

    if not context.is_low_signal:
        suggestions = _enforce_grounding_quality(suggestions, context)

    if context.is_low_signal:
        suggestions = _merge_low_signal_suggestions(suggestions, context)

    if context.is_low_signal:
        templates_fb = _low_signal_template_suggestions(
            context.primary_focus, context.recent_transcript or ""
        )
        while len(suggestions) < 3:
            idx = len(suggestions)
            suggestions.append(templates_fb[idx])
    else:
        sal = _top_weighted_tokens(context.recent_transcript or "") or _salient_transcript_tokens(
            context.recent_transcript or "", 6
        )
        w0 = sal[0] if sal else "this"
        w1 = sal[1] if len(sal) > 1 else (context.primary_focus or "that").split()[0] if context.primary_focus else "that"
        pf = context.primary_focus or "this"
        fallback_specs = [
            (
                "debugging probe",
                "root_cause",
                f"When you said {w0}, what breaks first for {pf} if traffic spikes?",
            ),
            (
                "architectural insight",
                "system_design",
                f"How would you draw the boundary so {w0} and {w1} do not step on each other?",
            ),
            (
                "validation step",
                "validation",
                f"What single check around {w0} would convince you {pf} is actually fixed?",
            ),
        ]
        while len(suggestions) < 3:
            idx = len(suggestions)
            t, ic, prev = fallback_specs[idx % 3]
            suggestions.append(
                Suggestion(
                    type=t,
                    intent_category=ic,
                    preview=prev[:110],
                    reason="Fallback to keep consistent three-suggestion UI behavior.",
                    topic=context.primary_focus,
                    score=0.45,
                    relevance=0.45,
                    novelty=0.45,
                    actionability=0.45,
                )
            )

    suggestions = _dedupe_intent_in_top3(suggestions, context)
    return suggestions
