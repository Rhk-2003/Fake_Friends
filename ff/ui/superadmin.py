"""Super-admin: read-only window into every quiz on this deployment.

Only the first admin account (or SUPER_ADMIN_USERNAME from secrets) gets this
view. Ordinary admins never see it, and the check is repeated server-side on
every run. Nothing here can modify another admin's quiz.
"""
from __future__ import annotations

import streamlit as st

from .. import service
from ..runtime import quiz_link
from . import components as ui
from . import shared

TABS = ["Leaderboard", "Memories", "Questions", "Players & videos", "Results", "Link"]


def render(vault, username: str) -> None:
    if not service.is_super(vault, username):       # defence in depth
        st.error("Not allowed.")
        return
    ui.heading("All quizzes", kicker="👑 Super-admin",
               sub="Everything other admins have created on this site. View only.")
    rows = service.list_all_quizzes(vault)
    table = [{"Quiz": r["name"], "Created by": r["owner"], "Admin username": r["username"],
              "Status": "Live" if r["published"] else "Draft", "Players": r["players"],
              "Completed": r["completed"], "MCQs": r["mcqs"], "Situations": r["sas"],
              "Created": r["created_at"][:10]} for r in rows]
    st.dataframe(table, hide_index=True, width="stretch")

    others = [r for r in rows if r["username"] != service.normalize_username(username)]
    if not others:
        st.info("Nobody else has created a quiz yet. When they do, it shows up here.")
        return
    labels = {r["quiz_id"]: f'{r["name"]} — by {r["owner"]} (@{r["username"]})' for r in others}
    quiz_id = st.selectbox("Open a quiz", list(labels), format_func=lambda q: labels[q], key="super_quiz")
    quiz = vault.load_quiz(quiz_id)
    if quiz is None:
        st.warning("That quiz no longer exists.")
        return
    tab = st.segmented_control("View", TABS, default=TABS[0], key="super_tab",
                               label_visibility="collapsed") or TABS[0]
    st.write("")
    if tab == "Leaderboard":
        ui.leaderboard(vault, quiz, title=quiz["settings"]["name"])
    elif tab == "Memories":
        shared.memories_gallery(vault, quiz, key=f"super_{quiz_id}", can_delete=False)
    elif tab == "Questions":
        shared.questions_readonly(vault, quiz)
    elif tab == "Players & videos":
        if not quiz["players"]:
            st.info("No players yet.")
            return
        names = {p["id"]: p.get("display_name") or p["name"] for p in quiz["players"]}
        pid = st.selectbox("Player", list(names), format_func=lambda i: names[i], key=f"super_p_{quiz_id}")
        shared.player_media(vault, quiz, service.get_player(quiz, pid), key=f"super_{quiz_id}")
    elif tab == "Results":
        shared.stats_row(quiz)
        st.write("")
        shared.results_table(quiz)
        if quiz["players"]:
            names = {p["id"]: p.get("display_name") or p["name"] for p in quiz["players"]}
            pid = st.selectbox("Player details", list(names), format_func=lambda i: names[i],
                               key=f"super_r_{quiz_id}")
            shared.player_detail(vault, quiz, service.get_player(quiz, pid))
    else:
        st.caption("Player link for this quiz")
        st.code(quiz_link(quiz["id"]), language=None)
        st.caption(f'Created by {quiz["owner"]["name"]} (@{quiz["owner"]["username"]}) on '
                   f'{quiz["created_at"][:10]} · {"Live" if quiz["settings"]["published"] else "Draft"}')
