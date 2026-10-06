"""Admin dashboard: an admin manages exactly one quiz - their own.

Authorisation is re-checked on every run (session -> index -> quiz owner), so
a tampered session can never open someone else's quiz.
"""
from __future__ import annotations

import streamlit as st

from .. import config, game, grading, media, qr, service
from ..runtime import quiz_link
from ..storage import StorageError
from . import components as ui
from . import shared

h = ui.h
LETTERS = shared.LETTERS
ERRORS = (service.ServiceError, game.GameError, media.MediaError, StorageError)

SECTIONS = ["Overview", "Settings", "Players", "Questions", "Review answers", "Results",
            "Leaderboard", "Memories", "Share", "Preview & publish"]
SUPER_SECTION = "👑 All quizzes"


def _run(fn, message: str | None = None) -> None:
    """Run a mutation; show a friendly error or toast + rerun."""
    try:
        fn()
    except ERRORS as exc:
        st.error(str(exc))
        return
    if message:
        ui.flash(message)
    st.rerun()


def _logout() -> None:
    for key in ("admin", "preview_state", "mcq_edit", "sa_edit", "created", "flash", "previewed"):
        st.session_state.pop(key, None)
    ui.go("landing")


def render(vault) -> None:
    auth = st.session_state.get("admin")
    if not auth or not service.owns_quiz(vault, auth["username"], auth["quiz_id"]):
        st.session_state.pop("admin", None)
        ui.go("admin_login")
    quiz = vault.load_quiz(auth["quiz_id"])
    if quiz is None:
        st.session_state.pop("admin", None)
        ui.go("admin_login")
    is_super = service.is_super(vault, auth["username"])

    top = st.columns([5, 1.2, 1])
    with top[0]:
        s = quiz["settings"]
        ui.heading(f'{s.get("emoji") or "🎭"} {s["name"]}',
                   kicker="Super-admin dashboard" if is_super else "Admin dashboard")
        ui.chips(["● LIVE" if s["published"] else "● DRAFT"], css="live" if s["published"] else "draft")
    with top[1]:
        st.caption(f'Signed in as **{auth["username"]}**')
    with top[2]:
        if st.button("Log out", width="stretch"):
            _logout()
    ui.flash_banner()
    if vault.pending_sync() and vault.last_error:
        st.warning("Some changes are waiting to sync to permanent storage and will be retried "
                   f"automatically. Last error: {vault.last_error}")

    options = SECTIONS + ([SUPER_SECTION] if is_super else [])
    section = st.segmented_control("Section", options, default="Overview", key="admin_section",
                                   label_visibility="collapsed") or "Overview"
    st.write("")
    if section == SUPER_SECTION and is_super:
        from . import superadmin

        return superadmin.render(vault, auth["username"])
    {
        "Overview": _overview, "Settings": _settings, "Players": _players,
        "Questions": _questions, "Review answers": _review, "Results": _results,
        "Leaderboard": _leaderboard, "Memories": _memories, "Share": _share,
        "Preview & publish": _publish,
    }.get(section, _overview)(vault, quiz)


# =========================================================================
# Overview (setup wizard checklist + analytics)
# =========================================================================
def _overview(vault, quiz) -> None:
    s = quiz["settings"]
    active = [p for p in quiz["players"] if p.get("active", True)]
    with_video = sum(1 for p in active if p.get("intro_video") or p.get("low_video") or p.get("high_video"))
    steps = [
        ("Create admin", True, "Done"),
        ("Quiz information", bool(s.get("description") or s.get("intro_message")),
         "Settings → name, description, intro, rules"),
        ("Add players", bool(active), f"Players → {len(active)} added"),
        ("Add Section 1 questions", len(quiz["mcqs"]) >= config.MCQ_REQUIRED,
         f"Questions → {len(quiz['mcqs'])} MCQs (need {config.MCQ_REQUIRED}+)"),
        ("Add Section 2 questions", len(quiz["sas"]) >= config.SA_REQUIRED,
         f"Questions → {len(quiz['sas'])} situations (need {config.SA_REQUIRED}+)"),
        ("Configure videos", with_video > 0, f"Players → {with_video} of {len(active)} have videos (optional)"),
        ("Preview", bool(st.session_state.get("previewed")), "Preview & publish → play it yourself"),
        ("Publish", s["published"], "Preview & publish → go live"),
    ]
    left, right = st.columns([1, 1.25])
    with left:
        st.markdown("#### Setup")
        for n, (label, ok, detail) in enumerate(steps, start=1):
            st.markdown(f'{"✅" if ok else "⬜"} **Step {n} · {label}**  \n'
                        f'<span style="color:var(--muted);font-size:.86rem">{h(detail)}</span>',
                        unsafe_allow_html=True)
    with right:
        st.markdown("#### At a glance")
        shared.stats_row(quiz)
        st.markdown("#### System")
        st.caption(f"💾 Storage: {vault.backend.describe()} — everything is AES-256 encrypted before it is saved.")
        if "local" in vault.backend.describe():
            st.warning("Local storage is wiped when a Streamlit Cloud app restarts. Add the GitHub "
                       "secrets from the README to keep quizzes, videos and scores permanently.")
        if grading.ai_available():
            st.caption(f"🤖 AI grading: on ({config.get('GEMINI_MODEL', grading.DEFAULT_MODEL)}).")
        else:
            st.warning("AI grading is off (no GEMINI_API_KEY in secrets). Short answers will wait "
                       "for you in **Review answers**.")


# =========================================================================
# Settings
# =========================================================================
def _image_ref(vault, quiz_id: str, upload, max_px: int) -> dict | None:
    if upload is None:
        return None
    data, mime = media.process_image(upload.getvalue(), max_px)
    return service.store_upload(vault, quiz_id, data, mime, upload.name)


def _video_ref(vault, quiz_id: str, upload) -> dict:
    data = upload.getvalue()
    return service.store_upload(vault, quiz_id, data, media.check_video(data, upload.name), upload.name)


def _settings(vault, quiz) -> None:
    s, qid = quiz["settings"], quiz["id"]
    with st.form("settings"):
        st.markdown("#### Quiz settings")
        c1, c2 = st.columns([4, 1])
        name = c1.text_input("Quiz name", s["name"], max_chars=80)
        emoji = c2.text_input("Icon (emoji)", s.get("emoji", ""), max_chars=8)
        description = st.text_input("Description", s.get("description", ""), max_chars=300)
        password = st.text_input("Quiz password (players type this)", s["password"], max_chars=40)
        intro = st.text_area("Intro message (shown before the quiz)", s.get("intro_message", ""), max_chars=500)
        rules = st.text_area("Rules / instructions from you", s.get("rules", ""), max_chars=800)
        c1, c2 = st.columns(2)
        high = c1.text_input("High-score message (10–16)", s.get("high_message", ""), max_chars=200)
        low = c2.text_input("Low-score message (0–9)", s.get("low_message", ""), max_chars=200)
        wall = st.checkbox(
            "Memory wall: let players see each other's photos, stories and what they like "
            "(the 'could improve' notes always stay private to you)", s.get("memory_wall", False))
        c1, c2 = st.columns(2)
        cover = c1.file_uploader("Cover image (optional)", type=media.IMAGE_TYPES)
        rm_cover = c1.checkbox("Remove current cover", disabled=not s.get("cover"))
        logo = c2.file_uploader("Logo (optional)", type=media.IMAGE_TYPES)
        rm_logo = c2.checkbox("Remove current logo", disabled=not s.get("logo"))
        if st.form_submit_button("Save settings", type="primary"):
            def save():
                fields = {"name": name, "emoji": emoji, "description": description, "password": password,
                          "intro_message": intro, "rules": rules, "high_message": high, "low_message": low,
                          "memory_wall": wall}
                if cover is not None:
                    fields["cover"] = _image_ref(vault, qid, cover, 1400)
                elif rm_cover:
                    fields["cover"] = None
                if logo is not None:
                    fields["logo"] = _image_ref(vault, qid, logo, 400)
                elif rm_logo:
                    fields["logo"] = None
                service.update_settings(vault, qid, fields)
            _run(save, "Settings saved")
    if s.get("cover") or s.get("logo"):
        c1, c2 = st.columns(2)
        with c1:
            ui.show_image(vault, qid, s.get("cover"), width=260)
        with c2:
            ui.show_image(vault, qid, s.get("logo"), width=120)

    with st.expander("Change admin password"):
        with st.form("admin_pw", clear_on_submit=True):
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password", type="password")
            if st.form_submit_button("Change password"):
                _run(lambda: service.change_admin_password(vault, qid, current, new), "Password changed")

    if not service.is_super(vault, quiz["owner"]["username"]):
        with st.expander("Danger zone"):
            st.caption("Deletes this quiz, its players, videos, answers and your admin account. This cannot be undone.")
            confirm = st.text_input("Type DELETE to confirm", key="delete_confirm")
            if st.button("Delete my quiz forever", disabled=confirm != "DELETE"):
                def delete():
                    service.delete_quiz(vault, qid, quiz["owner"]["username"])
                try:
                    delete()
                except ERRORS as exc:
                    st.error(str(exc))
                else:
                    _logout()


# =========================================================================
# Players
# =========================================================================
def _players(vault, quiz) -> None:
    qid = quiz["id"]
    ui.heading("Players", sub="Everyone who will play. Each gets their own picture and videos.")
    with st.expander("➕ Add a player", expanded=not quiz["players"]):
        with st.form("add_player", clear_on_submit=True):
            c1, c2, c3 = st.columns([3, 3, 1])
            name = c1.text_input("Player name *", max_chars=40)
            display = c2.text_input("Display name (optional)", max_chars=40)
            emoji = c3.text_input("Emoji", max_chars=8)
            dp = st.file_uploader("Profile picture (DP)", type=media.IMAGE_TYPES)
            if st.form_submit_button("Add player", type="primary"):
                def add():
                    if not name.strip():
                        raise service.ServiceError("Player name is required.")
                    service.add_player(vault, qid, {"name": name, "display_name": display, "emoji": emoji,
                                                    "dp": _image_ref(vault, qid, dp, 400)})
                _run(add, f"Added {name.strip()}")
        st.caption("Add the intro and outro videos after creating the player.")

    if not quiz["players"]:
        return
    rows = ""
    for p in quiz["players"]:
        name = p.get("display_name") or p["name"]
        flags = " ".join(f'{icon}{"✓" if p.get(slot) else "–"}' for slot, icon in
                         (("intro_video", "🎬"), ("low_video", "😬"), ("high_video", "🏆")))
        rows += (f'<div class="ff-row {"" if p.get("active", True) else "dim"}">'
                 f'{ui.avatar_html(vault, qid, name, p.get("dp"), p.get("emoji", ""), 40)}'
                 f'<span class="nm">{h(p.get("emoji", ""))} {h(name)}'
                 f'{"" if p.get("active", True) else " · inactive"}</span>'
                 f'<span class="sc"><small>ID {h(p["id"])} · {flags}</small></span></div>')
    st.markdown(rows, unsafe_allow_html=True)
    st.caption("🎬 intro · 😬 low-score outro · 🏆 high-score outro")

    ids = [p["id"] for p in quiz["players"]]
    names = {p["id"]: p.get("display_name") or p["name"] for p in quiz["players"]}
    pid = st.selectbox("Edit player", ids, format_func=lambda i: names[i], key="edit_player")
    player = service.get_player(quiz, pid)
    if not player:
        return

    left, right = st.columns(2)
    with left:
        with st.form(f"profile_{pid}"):
            st.markdown("##### Profile")
            name = st.text_input("Player name *", player["name"], max_chars=40)
            display = st.text_input("Display name", player.get("display_name", ""), max_chars=40)
            emoji = st.text_input("Emoji", player.get("emoji", ""), max_chars=8)
            active = st.checkbox("Active (shown to players)", player.get("active", True))
            dp = st.file_uploader("Upload / change profile picture", type=media.IMAGE_TYPES)
            rm_dp = st.checkbox("Remove current picture", disabled=not player.get("dp"))
            if st.form_submit_button("Save profile", type="primary"):
                def save():
                    fields = {"name": name, "display_name": display, "emoji": emoji, "active": active}
                    if dp is not None:
                        fields["dp"] = _image_ref(vault, qid, dp, 400)
                    elif rm_dp:
                        fields["dp"] = None
                    service.update_player(vault, qid, pid, fields)
                _run(save, "Profile saved")
        with st.expander("Remove this player"):
            st.caption("Deletes the player, their videos and their attempt.")
            if st.button("Delete player", key=f"del_{pid}"):
                _run(lambda: service.delete_player(vault, qid, pid), "Player deleted")
    with right:
        st.markdown("##### Videos")
        shared.player_media(vault, quiz, player, key="own")
        limit = config.max_video_bytes() // (1024 * 1024)
        with st.form(f"videos_{pid}", clear_on_submit=True):
            st.caption(f"MP4 or WebM, up to {limit} MB each — or paste an https link "
                       "(YouTube or a direct .mp4). Leave a slot empty to keep what is there.")
            inputs = {}
            for slot, label in shared.VIDEO_SLOTS:
                st.markdown(f"**{label}**")
                upload = st.file_uploader(f"Upload {label}", type=media.VIDEO_TYPES,
                                          key=f"v_{pid}_{slot}", label_visibility="collapsed")
                url = st.text_input(f"…or link for {label}", key=f"u_{pid}_{slot}",
                                    placeholder="https://…", label_visibility="collapsed")
                remove = st.checkbox("Remove current video", key=f"r_{pid}_{slot}",
                                     disabled=not player.get(slot))
                inputs[slot] = (upload, url, remove)
            if st.form_submit_button("Save videos", type="primary"):
                def save_videos():
                    fields = {}
                    for slot, (upload, url, remove) in inputs.items():
                        if upload is not None:
                            fields[slot] = _video_ref(vault, qid, upload)
                        elif (url or "").strip():
                            fields[slot] = service.url_ref(url)
                        elif remove:
                            fields[slot] = None
                    if not fields:
                        raise service.ServiceError("Nothing to save - upload a video or paste a link.")
                    service.update_player(vault, qid, pid, fields)
                with st.spinner("Encrypting and saving…"):
                    _run(save_videos, "Videos saved")


# =========================================================================
# Questions
# =========================================================================
def _questions(vault, quiz) -> None:
    locked = service.questions_locked(quiz)
    if locked:
        st.warning("🔒 Questions are locked because players have started this quiz. To edit them, "
                   "reset all attempts in **Results** (this clears every score).")
    n_mcq, n_sa = len(quiz["mcqs"]), len(quiz["sas"])
    labels = [f"Section 1 — MCQ ({n_mcq})", f"Section 2 — Situations ({n_sa})"]
    pick = st.segmented_control("Question section", labels, default=labels[0], key="q_section",
                                label_visibility="collapsed")
    section = "sa" if pick and pick.startswith("Section 2") else "mcq"
    required = config.MCQ_REQUIRED if section == "mcq" else config.SA_REQUIRED
    pool = quiz["mcqs"] if section == "mcq" else quiz["sas"]
    points = config.MCQ_POINTS if section == "mcq" else config.SA_POINTS
    if len(pool) < required:
        st.info(f"**{len(pool)} of {required}** required questions added. Add {required - len(pool)} more to "
                f"publish — you can add extra, and players will be able to skip.")
    else:
        st.success(f"**{len(pool)} questions in the pool.** Each player answers {required} "
                   f"({points} pt each) — only the first {required} they answer count, so they get "
                   f"**{len(pool) - required} skip(s)**.")

    edit_key = f"{section}_edit"
    for i, q in enumerate(pool):
        cols = st.columns([7, 1, 1, 1, 1, 1])
        extra = ""
        if section == "mcq":
            right = next((o for o in q["options"] if o["id"] == q.get("correct_option_id")), None)
            extra = f' · {len(q["options"])} options · ✅ {right["text"] if right else "no correct answer!"}'
        cols[0].markdown(f'<b>{i + 1}.</b> {h(q.get("emoji", ""))} {h(q["text"])}<br>'
                         f'<span style="color:var(--muted);font-size:.84rem">{h(extra.strip(" ·"))}</span>',
                         unsafe_allow_html=True)
        k = f"{section}_{q['id']}"
        if cols[1].button("↑", key=f"up_{k}", disabled=locked or i == 0, help="Move up"):
            _run(lambda q=q: service.move_question(vault, quiz["id"], section, q["id"], -1))
        if cols[2].button("↓", key=f"dn_{k}", disabled=locked or i == len(pool) - 1, help="Move down"):
            _run(lambda q=q: service.move_question(vault, quiz["id"], section, q["id"], 1))
        if cols[3].button("⧉", key=f"cp_{k}", disabled=locked, help="Duplicate"):
            _run(lambda q=q: service.duplicate_question(vault, quiz["id"], section, q["id"]), "Duplicated")
        if cols[4].button("✏️", key=f"ed_{k}", disabled=locked, help="Edit"):
            st.session_state[edit_key] = q["id"]
            st.rerun()
        if cols[5].button("🗑", key=f"rm_{k}", disabled=locked, help="Delete"):
            _run(lambda q=q: service.delete_question(vault, quiz["id"], section, q["id"]), "Deleted")

    if locked:
        return
    target = st.session_state.get(edit_key)
    if target and target != "new" and not any(q["id"] == target for q in pool):
        target = st.session_state[edit_key] = None
    st.divider()
    if target is None:
        if st.button(f"➕ Add {'an MCQ' if section == 'mcq' else 'a situation'}", type="primary"):
            st.session_state[edit_key] = "new"
            st.rerun()
        return
    (_mcq_editor if section == "mcq" else _sa_editor)(vault, quiz, target)


def _editor_done(edit_key: str, target: str) -> None:
    st.session_state["q_nonce"] = st.session_state.get("q_nonce", 0) + 1
    st.session_state[edit_key] = "new" if target == "new" else None


def _mcq_editor(vault, quiz, target: str) -> None:
    qid = quiz["id"]
    existing = next((q for q in quiz["mcqs"] if q["id"] == target), None)
    nonce = f'{target}_{st.session_state.get("q_nonce", 0)}'
    position = quiz["mcqs"].index(existing) + 1 if existing else len(quiz["mcqs"]) + 1
    st.markdown(f"#### {'Edit' if existing else 'New'} MCQ · question {position}")
    count = st.slider("Number of options", config.MIN_OPTIONS, config.MAX_OPTIONS,
                      len(existing["options"]) if existing else config.DEFAULT_OPTIONS,
                      key=f"mcq_n_{nonce}")
    with st.form(f"mcq_form_{nonce}"):
        text = st.text_area("Question *", existing["text"] if existing else "", max_chars=300,
                            placeholder="What would I most likely do on a free Sunday?")
        c1, c2 = st.columns([1, 3])
        emoji = c1.text_input("Question icon (emoji)", existing.get("emoji", "") if existing else "", max_chars=8)
        image = c2.file_uploader("Question image (optional)", type=media.IMAGE_TYPES, key=f"mcq_img_{nonce}")
        rm_image = st.checkbox("Remove current question image") if existing and existing.get("image") else False
        rows = []
        for i in range(count):
            old = existing["options"][i] if existing and i < len(existing["options"]) else {}
            c1, c2, c3 = st.columns([4, 1, 3])
            o_text = c1.text_input(f"Option {LETTERS[i]} *", old.get("text", ""), max_chars=120,
                                   key=f"mcq_t_{nonce}_{i}")
            o_emoji = c2.text_input(f"Icon {LETTERS[i]}", old.get("emoji", ""), max_chars=8,
                                    key=f"mcq_e_{nonce}_{i}")
            o_image = c3.file_uploader(f"Image {LETTERS[i]} (optional)", type=media.IMAGE_TYPES,
                                       key=f"mcq_i_{nonce}_{i}")
            rows.append((old, o_text, o_emoji, o_image))
        has_images = bool(existing) and any(o.get("image") for o in existing["options"])
        clear_images = st.checkbox("Remove all option images") if has_images else False
        current = None
        if existing:
            ids = [o["id"] for o in existing["options"]]
            if existing.get("correct_option_id") in ids and ids.index(existing["correct_option_id"]) < count:
                current = ids.index(existing["correct_option_id"])
        correct = st.radio("Correct answer *", list(range(count)), index=current, horizontal=True,
                           format_func=lambda i: LETTERS[i], key=f"mcq_c_{nonce}")
        explanation = st.text_area(
            "Short explanation (shown to the player right after they answer)",
            existing.get("explanation", "") if existing else "", max_chars=400,
            placeholder="Why this is the right answer — the story behind it.")
        c1, c2 = st.columns([3, 1])
        submitted = c1.form_submit_button("Save question", type="primary", width="stretch")
        cancelled = c2.form_submit_button("Close", width="stretch")
    if cancelled:
        st.session_state["mcq_edit"] = None
        st.rerun()
    if submitted:
        def save():
            uploaded = []
            try:
                q_image = existing.get("image") if existing else None
                if image is not None:
                    q_image = _image_ref(vault, qid, image, 1000)
                    uploaded.append(q_image)
                elif rm_image:
                    q_image = None
                options = []
                for old, o_text, o_emoji, o_image in rows:
                    ref = None if clear_images else old.get("image")
                    if o_image is not None:
                        ref = _image_ref(vault, qid, o_image, 600)
                        uploaded.append(ref)
                    options.append({"id": old.get("id"), "text": o_text, "emoji": o_emoji, "image": ref})
                service.save_mcq(vault, qid, existing["id"] if existing else None, {
                    "text": text, "emoji": emoji, "image": q_image, "options": options,
                    "correct_index": correct, "explanation": explanation})
            except Exception:
                for ref in uploaded:                 # do not leave orphaned uploads behind
                    vault.delete_media(qid, ref["id"])
                raise
            _editor_done("mcq_edit", target)
        _run(save, "Question saved")


def _sa_editor(vault, quiz, target: str) -> None:
    qid = quiz["id"]
    existing = next((q for q in quiz["sas"] if q["id"] == target), None)
    nonce = f'{target}_{st.session_state.get("q_nonce", 0)}'
    position = quiz["sas"].index(existing) + 1 if existing else len(quiz["sas"]) + 1
    st.markdown(f"#### {'Edit' if existing else 'New'} situation · question {position}")
    with st.form(f"sa_form_{nonce}"):
        text = st.text_area("Situation *", existing["text"] if existing else "", max_chars=500,
                            placeholder="We planned to meet at 7 PM. It's 7:30, I'm not there and "
                                        "I'm not replying. What do you think I'm doing?")
        answer_key = st.text_area(
            "Answer key * — what you would really do/say", existing.get("answer_key", "") if existing else "",
            max_chars=600, help="The AI gives 2 points when a friend's answer means the same thing, "
                                "1 when it is partly right, 0 otherwise. Players see this key "
                                "right after they lock in their own answer.")
        guidance = st.text_area(
            "Extra grading notes for the AI (optional)", existing.get("guidance", "") if existing else "",
            max_chars=600, placeholder="2 = says I overslept. 1 = says I'm late but not why. 0 = anything else.")
        c1, c2 = st.columns([1, 3])
        emoji = c1.text_input("Icon (emoji)", existing.get("emoji", "") if existing else "", max_chars=8)
        image = c2.file_uploader("Image (optional)", type=media.IMAGE_TYPES, key=f"sa_img_{nonce}")
        rm_image = st.checkbox("Remove current image") if existing and existing.get("image") else False
        c1, c2 = st.columns([3, 1])
        submitted = c1.form_submit_button("Save situation", type="primary", width="stretch")
        cancelled = c2.form_submit_button("Close", width="stretch")
    if cancelled:
        st.session_state["sa_edit"] = None
        st.rerun()
    if submitted:
        def save():
            ref, new = (existing.get("image") if existing else None), None
            if image is not None:
                ref = new = _image_ref(vault, qid, image, 1000)
            elif rm_image:
                ref = None
            try:
                service.save_sa(vault, qid, existing["id"] if existing else None, {
                    "text": text, "emoji": emoji, "image": ref, "answer_key": answer_key,
                    "guidance": guidance})
            except Exception:
                if new:
                    vault.delete_media(qid, new["id"])
                raise
            _editor_done("sa_edit", target)
        _run(save, "Situation saved")


# =========================================================================
# Review answers (AI grades, admin can override)
# =========================================================================
def _review(vault, quiz) -> None:
    qid = quiz["id"]
    ui.heading("Review answers", sub="The AI compares each short answer with your answer key. "
                                     "You have the final word — change any score here.")
    sas = {q["id"]: q for q in quiz["sas"]}
    submitted = [(p, quiz["attempts"][p["id"]]) for p in quiz["players"]
                 if p["id"] in quiz["attempts"] and quiz["attempts"][p["id"]]["status"] != game.IN_PROGRESS]
    if not submitted:
        st.info("Nothing to review yet — answers appear here as players finish.")
        return
    submitted.sort(key=lambda item: item[1]["status"] == game.COMPLETED)   # awaiting first
    for player, attempt in submitted:
        name = player.get("display_name") or player["name"]
        s = game.score_summary(quiz, attempt)
        done = attempt["status"] == game.COMPLETED
        title = (f"✅ {name} — Final Score Available · {s['total']}/{s['max']}" if done
                 else f"⏳ {name} — Awaiting Admin Review")
        with st.expander(title, expanded=not done):
            if not done and grading.ai_available():
                if st.button("🤖 Retry AI grading", key=f"ai_{player['id']}"):
                    with st.spinner("Asking the AI judge…"):
                        _run(lambda p=player: service.grade_attempt(vault, qid, p["id"]), "Grading updated")
            with st.form(f"grade_{attempt['id']}"):
                picks = {}
                for n, sa_id in enumerate(game.counted_sa_ids(attempt), start=1):
                    q = sas.get(sa_id)
                    if not q:
                        continue
                    grade = attempt["grades"].get(sa_id)
                    st.markdown(f"**Question {n}:** {q['text']}")
                    st.markdown(f"**Answer:** {attempt['sa_answers'][sa_id]}")
                    st.caption(f"Answer key: {q.get('answer_key', '')}"
                               + (f"  ·  Notes: {q['guidance']}" if q.get("guidance") else ""))
                    if grade and grade.get("reason"):
                        st.caption(f"🤖 AI said: {grade['reason']}")
                    c1, c2 = st.columns([1, 2])
                    score = c1.radio("Score", [0, 1, 2], index=grade["score"] if grade else None,
                                     horizontal=True, key=f"s_{attempt['id']}_{sa_id}")
                    notes = c2.text_input("Admin notes", grade.get("notes", "") if grade else "",
                                          max_chars=300, key=f"n_{attempt['id']}_{sa_id}")
                    picks[sa_id] = (score, notes, grade)
                    st.divider()
                if st.form_submit_button("SAVE", type="primary"):
                    def save():
                        changed = 0
                        for sa_id, (score, notes, grade) in picks.items():
                            if score is None:
                                continue
                            if grade and grade["score"] == score and grade.get("notes", "") == notes.strip():
                                continue                     # untouched AI grades stay marked as AI
                            service.manual_grade(vault, qid, player["id"], sa_id, score, notes)
                            changed += 1
                        if not changed:
                            raise service.ServiceError("No changes to save.")
                    _run(save, "Grades saved")


# =========================================================================
# Results
# =========================================================================
def _results(vault, quiz) -> None:
    qid = quiz["id"]
    ui.heading("Player results")
    shared.stats_row(quiz)
    st.write("")
    shared.results_table(quiz)
    st.download_button("⬇ Export results (CSV)", service.results_csv(quiz),
                       file_name=f"fake-friends-{qid}-results.csv", mime="text/csv")
    if not quiz["players"]:
        return
    st.divider()
    names = {p["id"]: p.get("display_name") or p["name"] for p in quiz["players"]}
    pid = st.selectbox("Player details", list(names), format_func=lambda i: names[i], key="result_player")
    player = service.get_player(quiz, pid)
    shared.player_detail(vault, quiz, player)
    if pid in quiz["attempts"]:
        with st.expander("Reset this player's attempt"):
            st.caption("Deletes their answers and score so they can play again.")
            if st.button("Reset attempt", key=f"reset_{pid}"):
                _run(lambda: service.reset_attempt(vault, qid, pid), "Attempt reset")
    if quiz["attempts"]:
        with st.expander("Reset ALL attempts (unlocks question editing)"):
            st.caption("Deletes every player's answers and scores.")
            confirm = st.checkbox("I understand this clears the leaderboard", key="reset_all_ok")
            if st.button("Reset all attempts", disabled=not confirm):
                _run(lambda: service.reset_all_attempts(vault, qid), "All attempts reset")


def _memories(vault, quiz) -> None:
    ui.heading("Memories", kicker="Yours to keep",
               sub="The photo and notes each friend left after the quiz: the moment, what they "
                   "like about you, and what you could improve.")
    shared.memories_gallery(vault, quiz, key="own", can_delete=True)


def _leaderboard(vault, quiz) -> None:
    ui.leaderboard(vault, quiz)
    if st.button("↻ Refresh"):
        st.rerun()


# =========================================================================
# Share / publish
# =========================================================================
def _share(vault, quiz) -> None:
    link = quiz_link(quiz["id"])
    ui.heading("Share your quiz")
    if not quiz["settings"]["published"]:
        st.warning("The quiz is still a draft — players can't open the link until you publish it.")
    left, right = st.columns([1.6, 1])
    with left:
        st.caption("Quiz link")
        st.code(link, language=None)
        st.caption("Quiz password")
        st.code(quiz["settings"]["password"], language=None)
        st.caption("Admin login — username (home page → Admin login)")
        st.code(quiz["owner"]["username"], language=None)
        st.caption("Ready-to-paste invite")
        st.code(f'Think you know me? Play my Fake Friends quiz 🎭\n{link}\nPassword: '
                f'{quiz["settings"]["password"]}', language=None)
        st.caption("Hover a box and click the copy icon. Your admin password is never shown.")
    with right:
        try:
            png = qr.qr_png(link)
            st.image(png, width=240, caption="Scan to open the quiz")
            st.download_button("⬇ Download QR", png, file_name=f"fake-friends-{quiz['id']}.png",
                               mime="image/png")
        except Exception:
            st.caption("QR code unavailable.")


def _publish(vault, quiz) -> None:
    qid = quiz["id"]
    ui.heading("Preview & publish")
    checks = service.validate_quiz(quiz)
    ready = all(c["ok"] for c in checks)
    left, right = st.columns([1.3, 1])
    with left:
        st.markdown("#### Checklist")
        for c in checks:
            detail = f' — <span style="color:var(--muted)">{h(c["detail"])}</span>' if c["detail"] else ""
            st.markdown(f'{"✅" if c["ok"] else "❌"} {h(c["label"])}{detail}', unsafe_allow_html=True)
        without = [p["name"] for p in quiz["players"] if p.get("active", True) and not p.get("intro_video")]
        if without:
            st.caption("ℹ️ No intro video (it will simply be skipped): " + ", ".join(without))
    with right:
        st.markdown("#### Preview")
        st.caption("Play the whole quiz as any player. No attempt is recorded.")
        if st.button("👀 Preview as a player", width="stretch", disabled=not quiz["players"]):
            st.session_state.pop("preview_state", None)
            ui.go("preview", previewed=True)
        st.markdown("#### Status")
        if quiz["settings"]["published"]:
            st.success("Your quiz is LIVE.")
            if st.button("Unpublish (pause the quiz)", width="stretch"):
                _run(lambda: service.set_published(vault, qid, False), "Quiz paused")
        else:
            if st.button("🚀 Publish quiz", type="primary", width="stretch", disabled=not ready):
                _run(lambda: service.set_published(vault, qid, True), "Your Fake Friends quiz is live!")
            if not ready:
                st.caption("Fix the ❌ items to publish.")
