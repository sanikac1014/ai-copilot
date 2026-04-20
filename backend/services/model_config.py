"""Groq model selection. Defaults to gpt-oss-120b with llama-3.3-70b as rate-limit fallback."""

import os


def primary_model() -> str:
    from backend.services.session_store import CONFIG
    return CONFIG.model_primary or os.getenv("GROQ_MODEL_PRIMARY", "openai/gpt-oss-120b")


def fallback_model() -> str:
    from backend.services.session_store import CONFIG
    return CONFIG.model_fallback or os.getenv("GROQ_MODEL_FALLBACK", "llama-3.3-70b-versatile")


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate_limit" in msg or "tokens per day" in msg


def chat_with_fallback(client, messages: list, **kwargs):
    """Try primary model, then fallback on rate limit (429 / TPD)."""
    models = [primary_model(), fallback_model()]
    last_exc: BaseException | None = None
    for idx, model in enumerate(models):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
            if idx > 0:
                print(f"[GROQ] using fallback model {model}")
            return resp, model
        except BaseException as exc:
            last_exc = exc
            if _is_rate_limit(exc) and idx < len(models) - 1:
                print(f"[GROQ] {model} rate limited, retrying with {models[idx + 1]}")
                continue
            raise
    assert last_exc is not None
    raise last_exc
