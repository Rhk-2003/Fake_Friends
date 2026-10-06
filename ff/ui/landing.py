"""Landing page, join-by-code, quiz creation and admin login."""
from __future__ import annotations

import re

import streamlit as st

from .. import service, throttle
from ..runtime import quiz_link
from ..storage import StorageError
from . import components as ui


def render_landing() -> None:
    ui.hero()
    st.write("")
    left, right = st.columns(2)
    with left:
        if st.button("🎮  PLAY A QUIZ", type="primary", width="stretch"):
            ui.go("join")
    with right:
        if st.button("✨  CREATE YOUR OWN QUIZ", width="stretch"):
            ui.go("create")
    st.markdown(
        '<div class="ff-card" style="margin-top:1.4rem">'
        "<b>How it works</b><br>"
        "1 · Someone writes questions about themselves and shares a link.<br>"
        "2 · Friends pick their name and answer — multiple choice first, then real situations.<br>"
        "3 · Scores land on a podium. The truth comes out. 👀"
        "</div>", unsafe_allow_html=True)
    if st.button("Admin login", width="stretch"):
        ui.go("admin_login")
    ui.privacy_note()


def _back_home() -> None:
    if st.button("← Home", key="back_home"):
        ui.go("landing")


def render_join() -> None:
    _back_home()
    ui.heading("Play a quiz", kicker="Fake Friends", sub="Paste the link or code your friend sent you.")
    with st.form("join"):
        raw = st.text_input("Quiz link or code", placeholder="e.g. K7M2XQ9P")
        if st.form_submit_button("Find quiz", type="primary", width="stretch"):
            match = re.search(r"quiz=([A-Za-z0-9]{4,16})", raw or "")
            code = (match.group(1) if match else (raw or "").strip()).upper()
            if not re.fullmatch(r"[A-Z0-9]{4,16}", code):
                st.error("That doesn't look like a quiz code.")
            else:
                st.query_params["quiz"] = code
                ui.go("player")


def render_create(vault) -> None:
    _back_home()
    ui.heading("Create your own quiz", kicker="Step 1 of 8 · Your admin account",
               sub="You'll use this login to add players, questions and videos.")
    with st.form("create_quiz"):
        name = st.text_input("Your name", max_chars=60)
        quiz_name = st.text_input("Quiz name", max_chars=80, placeholder="How well do you know Rahul?")
        username = st.text_input("Admin username", max_chars=24,
                                 help="3-24 characters: letters, numbers, dot, dash, underscore.")
        password = st.text_input("Admin password", type="password", help="At least 6 characters.")
        confirm = st.text_input("Repeat password", type="password")
        submitted = st.form_submit_button("Create my quiz", type="primary", width="stretch")
    if submitted:
        if password != confirm:
            st.error("The two passwords don't match.")
            return
        try:
            with st.spinner("Creating your quiz…"):
                made = service.create_quiz(vault, name, username, password, quiz_name)
        except (service.ServiceError, StorageError) as exc:
            st.error(str(exc))
            return
        st.session_state["admin"] = {"username": made["username"], "quiz_id": made["quiz_id"]}
        ui.go("created", created=made)
    ui.privacy_note()


def render_created() -> None:
    made = st.session_state.get("created")
    if not made:
        ui.go("landing")
    ui.heading("Your Fake Friends quiz is ready!", kicker="Saved",
               sub="Keep these safe. Next: add players and questions, then publish.")
    if made.get("is_super"):
        st.success("👑 You are the first admin on this site, so this account is the super-admin: "
                   "it can also view every quiz other people create here.")
    st.caption("Quiz link — share with your friends once the quiz is published")
    st.code(quiz_link(made["quiz_id"]), language=None)
    st.caption("Quiz password — friends need it to enter")
    st.code(made["quiz_password"], language=None)
    st.caption("Admin username — log in from the home page → Admin login")
    st.code(made["username"], language=None)
    st.caption("Your admin password is not shown or stored anywhere readable — remember it.")
    if st.button("Open my dashboard →", type="primary", width="stretch"):
        st.session_state.pop("created", None)
        ui.go("admin")


def render_admin_login(vault) -> None:
    _back_home()
    ui.heading("Admin login", kicker="Fake Friends", sub="Manage your own quiz.")
    with st.form("admin_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary", width="stretch")
    if submitted:
        key = "admin:" + service.normalize_username(username)
        wait = throttle.blocked_for(key)
        if wait:
            st.error(f"Too many wrong tries. Try again in {wait} seconds.")
            return
        quiz_id = service.authenticate_admin(vault, username, password)
        if quiz_id is None:
            throttle.record_failure(key)
            st.error("Wrong username or password.")
            return
        throttle.reset(key)
        st.session_state["admin"] = {"username": service.normalize_username(username), "quiz_id": quiz_id}
        ui.go("admin")
