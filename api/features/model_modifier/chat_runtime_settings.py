from __future__ import annotations

import os


def _env_flag(key: str, default: bool = False) -> bool:
    val = (os.getenv(key) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = (
    os.getenv("OPENAI_MODEL")
    or os.getenv("CHAT_MODEL")
    or os.getenv("LLM_MODEL")
    or "gpt-4o"
)

AI_AUDIT_LOG_ENABLED = _env_flag("AI_AUDIT_LOG_ENABLED", True)
AI_AUDIT_LOG_FULL_OUTPUT = _env_flag("AI_AUDIT_LOG_FULL_OUTPUT", False)


