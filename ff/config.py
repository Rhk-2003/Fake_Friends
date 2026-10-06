"""Configuration: game constants + secrets/env lookup.

Every setting is read from Streamlit secrets first, then environment
variables, so the same code runs on Streamlit Community Cloud and locally.
"""
from __future__ import annotations

import os

# ---- Game rules (fixed by design) -----------------------------------------
MCQ_REQUIRED = 8      # first 8 answered MCQs count
SA_REQUIRED = 4       # first 4 answered situations count
MCQ_POINTS = 1
SA_POINTS = 2
MAX_SCORE = MCQ_REQUIRED * MCQ_POINTS + SA_REQUIRED * SA_POINTS  # 16
# 60% of 16 = 9.6, so the integer threshold is 10. 10-16 = HIGH, 0-9 = LOW.
HIGH_SCORE_MIN = 10

MIN_OPTIONS, MAX_OPTIONS, DEFAULT_OPTIONS = 2, 6, 4
MAX_PLAYERS = 40
MAX_MCQ_POOL = 60
MAX_SA_POOL = 30
MAX_ANSWER_CHARS = 600
MAX_MEMORY_CHARS = 600

MAX_IMAGE_MB = 5
DEFAULT_MAX_VIDEO_MB = 20

PRIVACY_NOTE = (
    "All quiz data — names, photos, videos, answers and memories — is encrypted. "
    "Only people with this quiz's password, the quiz creator and the site "
    "admin can view your details."
)


class ConfigError(RuntimeError):
    """Raised when the app is misconfigured (shown to the operator)."""


def get(name: str, default: str | None = None) -> str | None:
    """Look up a setting in st.secrets, then os.environ."""
    try:
        import streamlit as st

        if name in st.secrets:
            value = st.secrets[name]
            if value not in (None, ""):
                return str(value)
    except Exception:  # no secrets file, or running outside Streamlit
        pass
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def max_video_bytes() -> int:
    try:
        mb = float(get("FF_MAX_VIDEO_MB", str(DEFAULT_MAX_VIDEO_MB)))
    except ValueError:
        mb = DEFAULT_MAX_VIDEO_MB
    return int(mb * 1024 * 1024)
