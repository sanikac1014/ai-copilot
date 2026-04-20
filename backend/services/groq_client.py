import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

# Load env reliably whether uvicorn starts from repo root or backend directory.
_BACKEND_ENV = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_BACKEND_ENV, override=False)
load_dotenv(override=False)


def get_groq_client() -> Groq:
    from backend.services.session_store import CONFIG  # late import avoids circular deps
    api_key = CONFIG.groq_api_key or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Groq API key is not configured. Open Settings and paste your key.")
    return Groq(api_key=api_key)

