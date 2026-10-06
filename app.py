"""Fake Friends - entry point and router.

Run locally:  streamlit run app.py
"""
import logging

import streamlit as st

_view = st.session_state.get("view")
st.set_page_config(page_title="Fake Friends", page_icon="🎭",
                   layout="wide" if _view == "admin" else "centered",
                   initial_sidebar_state="collapsed")

from ff import config  # noqa: E402
from ff.runtime import get_vault  # noqa: E402
from ff.ui import admin, landing, player, theme  # noqa: E402
from ff.ui import components as ui  # noqa: E402

log = logging.getLogger("fakefriends")


def route(vault) -> None:
    quiz_param = st.query_params.get("quiz")
    view = st.session_state.get("view") or ("player" if quiz_param else "landing")
    if view == "player" and not quiz_param:
        view = "landing"
    st.session_state["view"] = view

    if view == "player":
        player.render(vault, quiz_param)
    elif view == "join":
        landing.render_join()
    elif view == "create":
        landing.render_create(vault)
    elif view == "created":
        landing.render_created()
    elif view == "admin_login":
        landing.render_admin_login(vault)
    elif view == "admin":
        admin.render(vault)
    elif view == "preview":
        auth = st.session_state.get("admin")
        if not auth:
            ui.go("admin_login")
        player.render(vault, auth["quiz_id"], preview=True)
    else:
        landing.render_landing()


def main() -> None:
    theme.inject()
    try:
        vault = get_vault()
    except config.ConfigError as exc:
        ui.hero()
        st.error(f"Setup problem: {exc}")
        st.caption("See the README for the secrets this app needs.")
        return
    try:
        vault.flush_stale()          # write batched progress to permanent storage
        ui.show_flash()
        route(vault)
    except Exception:                # never show a raw stack trace to players
        log.exception("unhandled error")
        st.error("Something went wrong on our side. Please refresh the page and try again.")


main()
