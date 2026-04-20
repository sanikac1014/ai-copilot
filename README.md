# TwinMind Live Suggestions

A real-time AI meeting copilot. Listens to your mic, continuously surfaces 3 contextual suggestions based on what is being said, and lets you click any suggestion for a detailed answer in a chat panel.

**Live demo:** [https://ai-copilot-chi.vercel.app/](https://ai-copilot-chi.vercel.app/)

---

## Setup

### Backend
```bash
# From repo root (not inside backend/)
cd backend && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt && cd ..
cp backend/.env.example backend/.env   # no key needed — users paste via UI
uvicorn backend.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend && npm install && npm run dev
# Open http://localhost:5173
# Leave VITE_API_URL unset — Vite proxies /api → backend automatically
```

### First run
Paste your Groq API key in the **Settings** modal that opens automatically. The key is stored in `localStorage` and sent to the backend on each page load — it is never stored server-side between sessions.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + Vite + Tailwind | Fast iteration, no build complexity |
| Audio capture | `MediaRecorder` (WebM/Opus) + browser `SpeechRecognition` | MediaRecorder gives clean 30s chunks for Whisper; SpeechRecognition adds a live interim line without extra API calls |
| Backend | FastAPI (Python) | Clean async, easy Vercel serverless via ASGI adapter |
| Transcription | Groq Whisper Large V3 | Fastest Whisper inference available |
| Suggestions + Chat | `openai/gpt-oss-120b` via Groq (fallback: `llama-3.3-70b-versatile`) | Required by spec; fallback protects against 429/rate-limit |
| Deployment | Vercel (SPA + Python serverless) | Zero-config, free tier |

---

## Prompt Strategy

### Live Suggestions

The goal is 3 suggestions that are **useful even before clicking** — each one should make the speaker think, not just prompt them to ask for more.

**What I pass as context:**
- Recent transcript window (last ~1200 chars, configurable) with `[HH:MM:SS]` timestamps
- `primary_focus` and `secondary_topics` derived by the context engine
- `conversation_type` (technical / planning / personal / casual) and `stage` (problem / solution / tradeoff / open)
- `prefer_echo_these_terms` — top content-word frequencies extracted from the transcript, so the model knows which vocabulary the speaker is using
- Last 12 suggestion previews (to block repetition across batches)
- `is_low_signal` flag so the model knows when to acknowledge limited transcript

**Generation pipeline:**
1. Ask the model for **6 candidates**, each with a distinct `intent_category` (root_cause, system_design, tradeoff, validation, constraint, alternative). This forces cognitive diversity up front.
2. Score each on `relevance × novelty × actionability` (model self-scores 0–1).
3. Apply grounding penalties: down-rank any preview that doesn't echo at least one salient word from the transcript. Down-rank banned abstract phrases ("define scope", "validate emotions") that read like consulting templates.
4. Apply diversity penalties: down-rank later items that share an intent category or have Jaccard similarity ≥ 0.48 with a higher-ranked item.
5. `_select_diverse_top3`: greedy pick that guarantees distinct intent categories and low pairwise similarity.
6. Post-pass: any preview that still fails grounding gets rewritten using transcript vocabulary in a deterministic template.

**Why this matters:** If you just ask a model for 3 suggestions it often produces near-duplicates or hollow labels. The 6→3 pipeline with explicit penalty signals consistently produces distinct, actionable suggestions.

### Detailed Chat Answers (suggestion click)

When a suggestion is clicked, the chat prompt changes mode:

- **Suggestion click** (`from_suggestion=True`): structured 180-250 word answer — Direct answer, Why it matters here, Concrete steps, Key tradeoff. Uses 8 recent transcript entries for context.
- **Typed question** (`from_suggestion=False`): focused 120-180 word Q&A with 3-part structure. Uses 4 recent transcript entries.

The distinction matters because a clicked suggestion should expand into a thorough, actionable brief — not a short conversational reply. Both modes receive full chat history (last 8 turns) plus transcript context.

### Topic Shift Detection

Context engine detects topic changes using word-cosine + trigram-Jaccard similarity (no embeddings). A shift is confirmed when any of these fire (and `focus_same` override does not suppress):
- Chunk-to-focus similarity < 0.6 (`low_chunk_sim`)
- Conversation type changes, corroborated by a local heuristic (`type_mismatch`) — prevents LLM phrasing artefacts from flipping the type alone
- Token Jaccard < 0.25 AND low_chunk_sim (`focus_token_reset`) — AND-gated to avoid rephrasing shifts
- **Entity override:** key-noun Jaccard between old and new `primary_focus` < 0.30 (`entity_override`) — catches clearly different topics like "coffee vs matcha" → "best friend vs boyfriend" even when chunk-sim is borderline

Similarity is computed on the **single latest entry only** (not the last 2 combined) so an older entry from the previous topic can't dilute the signal.

A 30-second cooldown prevents rapid flipping. A strong-signal override (sim < 0.3 + type change) bypasses the cooldown.

**Segment isolation:** on every confirmed shift, `current_segment_start_idx` is updated to the triggering entries. All subsequent suggestion and chat context calls use only entries from that index onwards, paired with a `current_segment_rolling_summary` that contains no prior-topic content. This prevents suggestions from drifting back to an older topic on refresh.

---

## Architecture

```
Browser → Vite proxy /api → FastAPI (backend.main:app)
                                  ↓
             backend/routes/api.py  (four endpoints)
                                  ↓
             backend/services/     (stateless pure functions)
                                  ↓
             backend/services/session_store.py  (in-memory STATE singleton)
```

**Key design decisions:**
- `suggestion_history` is append-only and never cleared — the frontend accumulates batches across topic shifts and uses `segment_id` filtering to show only the current segment's batches. This lets users scroll back through earlier topic suggestions.
- Transcript chunking: `MediaRecorder` stops every 30 seconds, uploads the blob, and immediately restarts. This gives Whisper clean complete utterances rather than mid-sentence fragments.
- True SSE streaming: `/chat` returns `text/event-stream`. The frontend consumes deltas via `ReadableStream` + `TextDecoder`, so the first token appears in ~300ms. A `meta` event is sent before any text so context/topic-shift state updates instantly. The assistant bubble is created on the first delta (not at send time), so the typing indicator and the message never overlap.
- Segment isolation: `current_segment_start_idx` + `current_segment_rolling_summary` ensure suggestions and chat always use only current-segment transcript, preventing cross-topic drift on refresh.
- Stale closure prevention: the 30-second auto-refresh interval uses `useRef` for `postSuggestions` and `transcript` so it always calls with current values.
- Reset Chat: clears chat messages only — transcript, suggestions, and settings are unaffected.

---

## Tradeoffs

**In-memory state:** `SessionState` is a process-level singleton. Fine for one session; would break under concurrent users. For this assignment scope, it is the right call — no DB setup, instant reset on reload.

**Word-cosine topic detection over embeddings:** Keeps the backend free of external calls for context detection. Word cosine + trigram Jaccard is less robust than sentence embeddings on short ambiguous text, but good enough for meeting transcripts where topic shifts are usually vocabulary-obvious.

**6-candidate generation over iterative refinement:** Generating 6 and picking 3 in one call is cheaper than multiple sequential calls for refinement. The grounding/diversity post-processing compensates for single-call quality variance.

**Suggestion preview ≤ 20 words:** Forces the model to be specific. A 20-word line that quotes transcript vocabulary delivers more value to the speaker at a glance than a vague 40-word description.
