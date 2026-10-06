"""Process-wide singletons for the Streamlit app."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from . import config, crypto
from .storage import GitHubBackend, LocalBackend, Vault


@st.cache_resource(show_spinner=False)
def get_vault() -> Vault:
    token, repo = config.get("GITHUB_TOKEN"), config.get("GITHUB_REPO")
    mode = (config.get("FF_STORAGE") or ("github" if token and repo else "local")).lower()
    key = config.get("FF_MASTER_KEY")
    if mode == "github":
        if not (token and repo):
            raise config.ConfigError("FF_STORAGE is 'github' but GITHUB_TOKEN / GITHUB_REPO are not set.")
        if not key:
            raise config.ConfigError("FF_MASTER_KEY must be set in secrets when using GitHub storage.")
        backend = GitHubBackend(token, repo, config.get("GITHUB_DATA_BRANCH", "ff-data"),
                                config.get("GITHUB_DATA_PATH", "vault"))
    elif mode == "local":
        root = Path(config.get("FF_DATA_DIR", "data"))
        root.mkdir(parents=True, exist_ok=True)
        if not key:   # development convenience: a key file that is git-ignored
            key_file = root / ".dev_master_key"
            if not key_file.exists():
                key_file.write_text(crypto.generate_master_key())
            key = key_file.read_text().strip()
        backend = LocalBackend(root / "vault")
    else:
        raise config.ConfigError("FF_STORAGE must be 'github' or 'local'.")
    try:
        return Vault(backend, crypto.normalize_master_key(key))
    except crypto.CryptoError as exc:
        raise config.ConfigError(str(exc)) from exc


def base_url() -> str:
    """Public URL of the app, for share links."""
    configured = config.get("APP_BASE_URL")
    if configured:
        return configured.rstrip("/")
    try:
        url = st.context.url
        if url:
            return url.split("?")[0].rstrip("/")
    except Exception:
        pass
    return "http://localhost:8501"


def quiz_link(quiz_id: str) -> str:
    return f"{base_url()}/?quiz={quiz_id}"
