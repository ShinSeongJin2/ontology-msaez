"""
Ingestion AI Audit Flags

Business capability: traceability for LLM-driven ingestion steps.
Keeps feature-local toggles for prompt/output logging and performance metrics.
"""

from __future__ import annotations

import os


def _env_flag(key: str, default: bool = False) -> bool:
    val = (os.getenv(key) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


AI_AUDIT_LOG_ENABLED = _env_flag("AI_AUDIT_LOG_ENABLED", True)
AI_AUDIT_LOG_FULL_PROMPT = _env_flag("AI_AUDIT_LOG_FULL_PROMPT", False)
AI_AUDIT_LOG_FULL_OUTPUT = _env_flag("AI_AUDIT_LOG_FULL_OUTPUT", False)


