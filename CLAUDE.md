# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Local Development

### Backend (run from repo root — NOT from `backend/`)
```bash
# First-time setup
cd backend && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt && cd ..
cp backend/.env.example backend/.env   # then set GROQ_API_KEY

# Start server (always from repo root so `backend` package resolves)
uvicorn backend.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend && npm install
# .env is already configured for local dev (leave VITE_API_URL unset to use Vite proxy)
npm run dev        # http://localhost:5173
npm run build      # production build → frontend/dist/
```

### Environment variables
- `backend/.env`: `GROQ_API_KEY` (required). Optional model overrides: `GROQ_MODEL_PRIMARY` (default `llama-3.3-70b-versatile`), `GROQ_MODEL_FALLBACK` (default `llama-3.1-8b-instant`).
- `frontend/.env`: Leave `VITE_API_URL` unset for local dev (Vite proxies `/api` → `http://127.0.0.1:8000`). Set to `http://localhost:8000/api` only if bypassing the proxy.

There are no automated tests; verification is manual via the browser UI.

## Architecture

### Request flow
```
Browser → Vite proxy /api → FastAPI (backend.main:app, router prefix /api)
                                 ↓
              backend/routes/api.py  (single router, four endpoints)
                                 ↓
              backend/services/  (stateless pure functions)
                                 ↓
              backend/services/session_store.py  (in-memory singleton STATE)
```

On Vercel the Vite proxy is absent; `vercel.json` rewrites `/api/(.*)` → `api/index.py` (which simply imports `backend.main:app`).

### Backend services
| File | Role |
|---|---|
| `session_store.py` | Global `STATE` singleton (`SessionState` dataclass). Holds transcript, chat history, `suggestion_history` deque (max 80), `rolling_summary`, `session_segments`, `current_segment_id`, `latest_batch_id`. Never reset between requests except on explicit topic shift. |
| `context_engine.py` | Builds `ContextPayload` from transcript + rolling summary. Detects **topic shift** when chunk-to-focus cosine similarity < 0.6 or conversation type changes. Uses word-cosine + trigram-Jaccard (no embeddings). |
| `suggestion_engine.py` | Calls Groq to generate 6 candidates, scores each on relevance × novelty × actionability, selects diverse top 3 (distinct `intent_category`). Receives `previous_batches` from `STATE.suggestion_history` to suppress repetition. |
| `chat_engine.py` | Structured assistant answers. Receives full chat history + transcript context. |
| `transcription.py` | Groq Whisper Large V3 for audio chunks. |
| `groq_client.py` | Creates `Groq` client from env. |
| `model_config.py` | `chat_with_fallback()` — tries primary model, retries fallback on 429/TPD rate limit. |

### Suggestion history and segment isolation
`STATE.suggestion_history` is a deque of `{batch_id, segment_id, suggestions}` dicts. It is **never reset by the backend** on topic shift — only the frontend clears its view. `STATE.current_segment_id` increments on every topic shift via `_sync_session_segments()`. The API response includes the full `suggestion_history` list and `current_segment_id`; the frontend filters `batch.segment_id === currentSegmentId` before rendering. Frontend history is append-only (deduplicated by `batch_id`).

### Topic shift detection (context_engine.py)
`build_structured_context()` sets `topic_shift=True` when **all guards pass**:

| Signal | Condition | Guard |
|---|---|---|
| `low_chunk_sim` | chunk-to-focus blended similarity < 0.6 | primary trigger |
| `type_mismatch` | LLM conversation type changed **and** heuristic `_infer_conversation_type_from_chunk` also disagrees with the old type | prevents LLM phrasing artefacts from flipping the type alone |
| `focus_token_reset` | token Jaccard < 0.25 **and** `low_chunk_sim` is also true | AND-gated so a rephrased-but-same focus doesn't shift alone |
| `focus_same` override | new `primary_focus` token-overlap ≥ 0.5 with old → **no shift** | idempotency guard |

**Cooldown guard (api.py):** after any confirmed shift, the next shift is suppressed for 30 s (`STATE.last_topic_shift_time`). Applied in both `/suggestions` and `/chat` routes.

**Strong-shift override:** cooldown is bypassed when `focus_chunk_similarity < 0.3` AND conversation type changes. Because `topic_shift=True` already requires heuristic corroboration of the type change (from the detection logic), no additional import is needed — the override uses only fields already on `context`.

On topic shift: `api.py` clears `STATE.suggestion_history` (backend deque), opens a new session segment, and appends the rolling summary. The frontend never clears its `suggestionHistory` state — it accumulates across all segments and relies on `segment_id` filtering to isolate the current view.

**Debug log format:**
```
[TOPIC_SHIFT] prev_focus=... new_focus=... sim=0.xxx ... → shift=True/False   # from context_engine
[TOPIC_SHIFT][SUPPRESSED] sim=0.52 cooldown 4.2s < 30.0s                      # cooldown fired
[TOPIC_SHIFT][OVERRIDE] sim=0.18 type 'technical'→'casual' — bypassing cooldown (8.1s)
```

### Frontend state (App.jsx)
Key state variables and their roles:
- `displayedSuggestions` — latest batch (3 items) from the API, cleared to `[]` on topic shift before new state lands; passed to `SuggestionsPanel` as fallback only
- `suggestionHistory` — append-only accumulation of every `{batch_id, segment_id, suggestions}` entry ever received, deduplicated by `batch_id`; **never reset**, even on topic shift
- `currentSegmentId` — set from `response.current_segment_id` **before** the history merge so the filter in `SuggestionsPanel` sees the right segment on the same render
- `latestBatchId` / `newBatchPulse` — drive the "New" badge glow animation

`postSuggestions()` always sends `force_refresh: true`. Debounced 2500 ms after each transcript change. Also re-fires after 9 s when `early_signal_detected` is true.

On topic shift: `setDisplayedSuggestions([])` clears the visible suggestions. `setSuggestionHistory` is **not** called — history is preserved so older segments remain browsable.

### SuggestionsPanel rendering
`SuggestionsPanel` owns its own `stableBatches` state (internal, not derived on every render) and a `renderedBatchIdRef` guard. The update flow:

1. `useEffect([suggestionHistory, currentSegmentId, latestBatchId])` filters history to the current segment, checks `renderedBatchIdRef.current === latest.batch_id` to skip no-op updates, sorts newest-first (`b.batch_id - a.batch_id`), then calls `setStableBatches`.
2. The stagger slide-in effect depends on `[loading, latestBatchId]` only — **not** on the `suggestions` prop — to prevent animation resets on every API poll.
3. Render: `stableBatches[0]` = "Latest Suggestions" (full opacity, metrics, glow); `stableBatches.slice(1)` = "Previous Suggestions" (50% opacity, no metrics). Section dividers only appear when both groups exist.
4. Shimmer guard is `loading && !hasStable` — will not hide behind stale prop data.
5. Fallback to `suggestions` prop only when `suggestionHistory.length === 0` (first render before any API response).

### API endpoints (all under `/api`)
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/transcribe` | Audio chunk → transcript line (Groq Whisper) |
| `POST` | `/suggestions` | Returns `suggestions`, `suggestion_history`, `current_segment_id`, `context`, `meta` |
| `POST` | `/chat` | Chat turn with transcript context |
| `GET` | `/export` | Full session dump (transcript, suggestions, chat, segments) |

### Transcript signature cache
`api.py` caches `ContextPayload` keyed on `_signature(entries)` (joined text hash). Repeated calls with identical transcript skip `build_structured_context()`. `force_refresh=true` bypasses this.
