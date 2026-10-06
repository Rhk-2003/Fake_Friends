"""Player experience - a small state machine driven by the stored attempt.

    PASSWORD -> PLAYER_SELECTION -> INTRO_VIDEO -> INSTRUCTIONS
      -> SECTION_1 (answer -> reveal + explanation -> next)
      -> SECTION_2 (answer -> answer key -> next)
      -> SUBMITTED -> (AI grading | AWAITING_REVIEW) -> MEMORY (photo + notes)
      -> RESULT -> OUTRO_VIDEO -> LEADERBOARD / MEMORIES -> CREATE_QUIZ_PROMPT

Every answer and skip is written to permanent storage the moment it is locked
in, and the current screen is derived from that saved attempt - so a refresh,
a dropped connection or a server restart resumes at the next unanswered
question instead of starting over.
In preview mode the attempt lives only in the admin's session.
"""
from __future__ import annotations

import streamlit as st

from .. import config, game, grading, media, service, throttle
from ..storage import StorageError
from . import components as ui
from . import shared

h = ui.h


def _session(quiz_id: str, preview: bool) -> dict:
    key = "preview_state" if preview else "player_state"
    ss = st.session_state.setdefault(key, {})
    if ss.get("quiz_id") != quiz_id:
        ss.clear()
        ss["quiz_id"] = quiz_id
    return ss


def render(vault, quiz_id: str, preview: bool = False) -> None:
    quiz = vault.load_quiz((quiz_id or "").upper())
    if quiz is None:
        ui.hero()
        st.error("We couldn't find that quiz. Check the link and try again.")
        if st.button("Back to home", width="stretch"):
            st.query_params.clear()
            ui.go("landing")
        return
    ss = _session(quiz["id"], preview)
    ctx = {"vault": vault, "quiz": quiz, "ss": ss, "preview": preview}

    if preview:
        st.markdown('<div class="ff-banner">👀 Preview mode - this is what players see. '
                    "Nothing here is saved.</div>", unsafe_allow_html=True)
        if st.button("← Exit preview", key="exit_preview"):
            st.session_state.pop("preview_state", None)
            ui.go("admin")
    elif not quiz["settings"]["published"]:
        ui.hero()
        st.info("This quiz isn't live yet. Ask the person who shared it to publish it.")
        return

    if not preview and not ss.get("authed"):
        return _password(ctx)

    player = service.get_player(quiz, ss.get("player_id", ""))
    if player is None or not player.get("active", True):
        return _select(ctx)
    ctx["player"] = player
    attempt = ss.get("attempt") if preview else quiz["attempts"].get(player["id"])

    try:
        if attempt is None:
            return _instructions(ctx) if ss.get("stage") == "instructions" else _intro(ctx)
        if ss.get("feedback") and _feedback(ctx, attempt, ss["feedback"]):
            return                       # answer reveal for the question just locked in
        if attempt["status"] == game.IN_PROGRESS:
            mcq = game.section_state(quiz, attempt, "mcq")
            if not mcq["done"]:
                return _section1(ctx, mcq)
            sa = game.section_state(quiz, attempt, "sa")
            if sa["done"]:
                return _finish(ctx)
            if not ss.get("s2_seen") and sa["answered"] == 0 and sa["skips_used"] == 0:
                return _section2_intro(ctx, sa)
            return _section2(ctx, sa)
        if attempt["status"] == game.SUBMITTED:
            return _finish(ctx)
        if _own_memory(ctx) is None:
            return _memory_form(ctx)     # one last step before score + leaderboard
        return _result(ctx, attempt)
    except (game.GameError, service.ServiceError, media.MediaError, StorageError) as exc:
        st.error(str(exc))
        if st.button("Continue"):
            st.rerun()


# ---- actions (real attempt in the vault, or preview attempt in session) ----
def _start(ctx) -> None:
    if ctx["preview"]:
        ctx["ss"]["attempt"] = game.new_attempt("preview", ctx["player"]["id"])
    else:
        service.start_attempt(ctx["vault"], ctx["quiz"]["id"], ctx["player"]["id"])


def _act(ctx, action: str, question_id: str, value: str = "") -> None:
    quiz, ss = ctx["quiz"], ctx["ss"]
    if ctx["preview"]:
        attempt = ss["attempt"]
        if action == "answer_mcq":
            game.answer_mcq(quiz, attempt, question_id, value)
        elif action == "answer_sa":
            game.answer_sa(quiz, attempt, question_id, value)
        else:
            game.skip_question(quiz, attempt, action.split("_")[1], question_id)
    else:
        # saved to permanent storage immediately - every answer is a draft that survives
        # refreshes and restarts
        service.play(ctx["vault"], quiz["id"], ctx["player"]["id"], action, question_id, value)
    if action.startswith("answer"):             # next screen: reveal the answer
        ss["feedback"] = {"section": action.split("_")[1], "qid": question_id}


def _own_memory(ctx) -> dict | None:
    if ctx["preview"]:
        return ctx["ss"].get("memory")
    return service.get_memory(ctx["quiz"], ctx["player"]["id"])


# ---- screens ---------------------------------------------------------------
def _quiz_header(quiz: dict, vault) -> None:
    s = quiz["settings"]
    ui.show_image(vault, quiz["id"], s.get("cover"))
    ui.heading(f'{s.get("emoji") or "🎭"} {s["name"]}', kicker="Fake Friends", sub=s.get("description", ""))


def _password(ctx) -> None:
    quiz, ss = ctx["quiz"], ctx["ss"]
    _quiz_header(quiz, ctx["vault"])
    key = "quiz:" + quiz["id"]
    with st.form("quiz_password"):
        st.markdown("#### Enter Quiz Password")
        password = st.text_input("Quiz password", type="password", label_visibility="collapsed",
                                 placeholder="Password from your friend")
        if st.form_submit_button("Enter", type="primary", width="stretch"):
            wait = throttle.blocked_for(key)
            if wait:
                st.error(f"Too many wrong tries. Try again in {wait} seconds.")
            elif service.check_quiz_password(quiz, password):
                throttle.reset(key)
                ss["authed"] = True
                st.rerun()
            else:
                throttle.record_failure(key)
                st.error("That's not the password. Ask your friend for the right one.")
    ui.privacy_note()


def _select(ctx) -> None:
    quiz, ss, vault = ctx["quiz"], ctx["ss"], ctx["vault"]
    _quiz_header(quiz, vault)
    st.markdown("#### Choose Your Player")
    players = [p for p in quiz["players"] if p.get("active", True)]
    if not players:
        st.info("No players have been added to this quiz yet.")
        return
    grid = st.container(key="player_grid")
    for i, p in enumerate(players):
        if i % 2 == 0:
            columns = grid.columns(2)
        attempt = None if ctx["preview"] else quiz["attempts"].get(p["id"])
        name = p.get("display_name") or p["name"]
        if attempt is None:
            tag, label = "", "That's me"
        elif attempt["status"] == game.IN_PROGRESS:
            tag, label = "⏳ in progress", "Continue"
        else:
            tag, label = "✅ played", "See result"
        with columns[i % 2]:
            st.markdown(
                f'<div class="ff-player">{ui.avatar_html(vault, quiz["id"], name, p.get("dp"), p.get("emoji", ""), 80)}'
                f'<div class="name">{h(p.get("emoji", "") if p.get("dp") else "")} {h(name)}</div>'
                f'<div class="tag">{tag}</div></div>',
                unsafe_allow_html=True)
            if st.button(label, key=f"pick_{p['id']}", width="stretch",
                         type="primary" if attempt is None else "secondary"):
                ss.update(player_id=p["id"], stage="intro", returning=attempt is not None
                          and attempt["status"] != game.IN_PROGRESS)
                st.rerun()
    ui.privacy_note()


def _switch_player(ctx) -> None:
    if st.button("Not you? Switch player", key="switch_player"):
        for k in ("player_id", "stage", "attempt", "s2_seen", "returning", "just_finished",
                  "feedback", "memory"):
            ctx["ss"].pop(k, None)
        st.rerun()


def _intro(ctx) -> None:
    quiz, player, vault = ctx["quiz"], ctx["player"], ctx["vault"]
    name = player.get("display_name") or player["name"]
    st.markdown(f'<div style="text-align:center">'
                f'{ui.avatar_html(vault, quiz["id"], name, player.get("dp"), player.get("emoji", ""), 96)}</div>',
                unsafe_allow_html=True)
    if player.get("intro_video"):
        ui.heading(f"A message for {name}", kicker="Before we start")
        ui.show_video(vault, quiz["id"], player["intro_video"])
    if quiz["settings"].get("intro_message"):
        st.markdown(f'<div class="ff-card">{h(quiz["settings"]["intro_message"])}</div>',
                    unsafe_allow_html=True)
    ui.heading(f"Ready, {name}?")
    if st.button("START QUIZ", type="primary", width="stretch"):
        ctx["ss"]["stage"] = "instructions"
        st.rerun()
    _switch_player(ctx)


def _instructions(ctx) -> None:
    quiz = ctx["quiz"]
    n_mcq, n_sa = len(quiz["mcqs"]), len(quiz["sas"])
    ui.heading("How it works", kicker="One rule")
    ui.note("<b>Important:</b> All players will receive the same questions. Answer honestly — "
            "no cheating, and no changing answers after you lock them in.")
    skip1, skip2 = n_mcq - config.MCQ_REQUIRED, n_sa - config.SA_REQUIRED
    st.markdown(
        '<div class="ff-card">'
        f"<b>Section 1 · Multiple choice</b><br>{n_mcq} questions in total. Answer "
        f"<b>{config.MCQ_REQUIRED}</b> of them — {config.MCQ_POINTS} point each. "
        + (f"You can skip up to {skip1}; only the first {config.MCQ_REQUIRED} you answer count."
           if skip1 > 0 else "No skips in this section.")
        + f"<br><br><b>Section 2 · Situations</b><br>{n_sa} situations in total. Answer "
        f"<b>{config.SA_REQUIRED}</b> in your own words — up to {config.SA_POINTS} points each. "
        + (f"You can skip up to {skip2}; only the first {config.SA_REQUIRED} you answer count."
           if skip2 > 0 else "No skips in this section.")
        + f"<br><br><b>Maximum score: {config.MAX_SCORE}.</b> A skipped question does not come back. "
        "After each answer you'll see the real answer before moving on."
        "</div>", unsafe_allow_html=True)
    if quiz["settings"].get("rules"):
        st.markdown(f'<div class="ff-card"><b>From the host</b><br>{h(quiz["settings"]["rules"])}</div>',
                    unsafe_allow_html=True)
    if st.button("I'm ready — begin", type="primary", width="stretch"):
        _start(ctx)
        st.rerun()
    ui.privacy_note()


def _progress(kicker: str, state: dict) -> None:
    st.markdown(f'<div class="ff-kicker">{kicker}</div>', unsafe_allow_html=True)
    st.progress(state["answered"] / state["required"])
    ui.chips([f'Question <b>{state["position"]}</b> of <b>{state["pool_size"]}</b>',
              f'Answered <b>{state["answered"]}</b> / {state["required"]}',
              f'Skips left <b>{state["skips_left"]}</b>'])


def _skip_button(ctx, state: dict, section: str) -> None:
    if state["skips_left"] > 0:
        if st.button(f"Skip ⏭  ({state['skips_left']} left)", key=f"skip_{state['current']['id']}",
                     width="stretch"):
            _act(ctx, f"skip_{section}", state["current"]["id"])
            st.rerun()
    else:
        st.button("No skips left", key=f"skip_{state['current']['id']}", disabled=True,
                  width="stretch")


def _section1(ctx, state: dict) -> None:
    quiz, ss, vault = ctx["quiz"], ctx["ss"], ctx["vault"]
    q = state["current"]
    _progress("Section 1 · Multiple choice", state)
    ui.question_card(q["text"], q.get("emoji", ""))
    ui.show_image(vault, quiz["id"], q.get("image"))
    selected = ss.get("selected") if ss.get("selected_q") == q["id"] else None
    with_images = any(o.get("image") for o in q["options"])
    columns = st.columns(2) if with_images else None
    for i, option in enumerate(q["options"]):
        label = f'{option.get("emoji", "")}  {option["text"]}'.strip()
        box = columns[i % 2] if columns else st.container()
        with box:
            if with_images:
                ui.show_image(vault, quiz["id"], option.get("image"))
            if st.button(label, key=f"opt_{q['id']}_{option['id']}", width="stretch",
                         type="primary" if selected == option["id"] else "secondary"):
                ss.update(selected=option["id"], selected_q=q["id"])
                st.rerun()
    st.write("")
    left, right = st.columns([2, 1])
    with left:
        if st.button("Lock in answer ▶", key=f"lock_{q['id']}", disabled=selected is None,
                     width="stretch", type="primary" if selected else "secondary"):
            _act(ctx, "answer_mcq", q["id"], selected)
            ss.pop("selected", None)
            st.rerun()
    with right:
        _skip_button(ctx, state, "mcq")
    if selected is None:
        st.caption("Tap an option, then lock it in. Locked answers can't be changed.")


def _section2_intro(ctx, state: dict) -> None:
    ui.heading("Things just got real.", kicker="Section 2 · Situations",
               sub="There are no obvious right answers now.")
    st.markdown(
        f'<div class="ff-card">{state["pool_size"]} situations. Answer <b>{state["required"]}</b> in your '
        f"own words — say what you think your friend would <i>really</i> do. Each is worth up to "
        f"{config.SA_POINTS} points"
        + (f", and you can skip up to {state['skips_total']}." if state["skips_total"] else ".")
        + "</div>", unsafe_allow_html=True)
    if st.button("Bring it on", type="primary", width="stretch"):
        ctx["ss"]["s2_seen"] = True
        st.rerun()


def _section2(ctx, state: dict) -> None:
    quiz, vault = ctx["quiz"], ctx["vault"]
    q = state["current"]
    _progress("Section 2 · Situations", state)
    ui.question_card(q["text"], q.get("emoji", ""))
    ui.show_image(vault, quiz["id"], q.get("image"))
    answer = st.text_area("Your answer", key=f"sa_{q['id']}", max_chars=config.MAX_ANSWER_CHARS,
                          height=130, placeholder="What would they do? Type it the way you'd say it.")
    left, right = st.columns([2, 1])
    with left:
        if st.button("Lock in answer ▶", key=f"lock_{q['id']}", type="primary", width="stretch"):
            if not (answer or "").strip():
                st.warning("Type an answer first — or skip this one.")
            else:
                _act(ctx, "answer_sa", q["id"], answer)
                st.rerun()
    with right:
        _skip_button(ctx, state, "sa")


def _feedback(ctx, attempt: dict, feedback: dict) -> bool:
    """After an answer is locked: show the correct answer, then a button to move on.

    MCQ: the right option turns green (a wrong pick turns red) with the host's
    explanation underneath. Situation: the host's answer key is shown.
    """
    quiz, ss, vault = ctx["quiz"], ctx["ss"], ctx["vault"]
    section, qid = feedback.get("section"), feedback.get("qid")
    pool = quiz["mcqs"] if section == "mcq" else quiz["sas"]
    answers = attempt["mcq_answers"] if section == "mcq" else attempt["sa_answers"]
    q = next((x for x in pool if x["id"] == qid), None)
    if q is None or qid not in answers:
        ss.pop("feedback", None)
        return False
    host = quiz["owner"]["name"]
    state = game.section_state(quiz, attempt, section)
    st.markdown('<div class="ff-kicker">' + ("Section 1 · Multiple choice" if section == "mcq"
                                             else "Section 2 · Situations") + "</div>",
                unsafe_allow_html=True)
    st.progress(min(1.0, state["answered"] / state["required"]))
    ui.chips([f'Answered <b>{state["answered"]}</b> / {state["required"]}',
              f'Skips left <b>{state["skips_left"]}</b>'])
    ui.question_card(q["text"], q.get("emoji", ""))

    if section == "mcq":
        chosen, correct = answers[qid], q.get("correct_option_id")
        right = chosen == correct
        st.markdown('<div class="ff-verdict ok">✅ Correct!</div>' if right else
                    '<div class="ff-verdict bad">❌ Not quite — the right answer is in green.</div>',
                    unsafe_allow_html=True)
        rows = ""
        for option in q["options"]:
            css, tag, icon = "", "", option.get("emoji", "")
            if option["id"] == correct:
                css, tag = "ok", "Correct answer" + (" · your pick" if right else "")
            elif option["id"] == chosen:
                css, tag = "bad", "Your pick"
            rows += (f'<div class="ff-opt {css}"><span>{h(icon)}</span><span class="t">{h(option["text"])}</span>'
                     f'<span class="tagx">{tag}</span></div>')
        st.markdown(rows, unsafe_allow_html=True)
        if q.get("explanation"):
            st.markdown(f'<div class="ff-why"><b>💡 {h(host)} says</b><br>{h(q["explanation"])}</div>',
                        unsafe_allow_html=True)
        label = "Continue to Section 2 →" if state["done"] else "Next question →"
    else:
        st.markdown(f'<div class="ff-why"><b>Your answer</b><br>{h(answers[qid])}</div>'
                    f'<div class="ff-key"><b>🔑 {h(host)}\'s real answer</b><br>{h(q.get("answer_key", ""))}</div>',
                    unsafe_allow_html=True)
        st.caption("Your points for this one are decided at the end.")
        label = "Finish quiz →" if state["done"] else "Next question →"

    if st.button(label, key=f"next_{qid}", type="primary", width="stretch"):
        ss.pop("feedback", None)
        st.rerun()
    return True


def _memory_form(ctx) -> None:
    """One common question for every player, asked before the score and leaderboard."""
    quiz, ss, vault, player = ctx["quiz"], ctx["ss"], ctx["vault"], ctx["player"]
    host = quiz["owner"]["name"]
    limit = config.MAX_MEMORY_CHARS
    ui.heading("One last thing before your score", kicker=f"A memory for {host}")
    st.markdown(
        f'<div class="ff-card">📸 Upload a picture of <b>you and {h(host)}</b> — just the two of you. '
        "Please don't crop yourselves out of a group photo, unless you really don't have one "
        "of only you two.</div>", unsafe_allow_html=True)
    with st.form("memory_form"):
        photo = st.file_uploader(f"A photo of you and {host}", type=media.IMAGE_TYPES)
        no_photo = st.checkbox("We have never taken a photo together")
        story = st.text_area("In the picture — what was the moment, and what's the story?",
                             max_chars=limit, height=110,
                             placeholder="Where were you, what was happening… (no photo? tell your "
                                         "favourite moment with them)")
        like = st.text_area(f"What do you like about {host}?", max_chars=limit, height=100)
        dislike = st.text_area(f"What don't you like — something {host} could improve?",
                               max_chars=limit, height=100, placeholder="Be honest and kind.")
        submitted = st.form_submit_button("Save my memory & show my score", type="primary",
                                          width="stretch")
    if quiz["settings"].get("memory_wall"):
        st.caption(f"👀 Your photo, the story and what you like go on this quiz's memory wall for the "
                   f"other players. Your 'could improve' note is only shown to {host}.")
    else:
        st.caption(f"👀 Only {host} sees this (and the site admin). It stays on their Memories page.")
    if ctx["preview"] and st.button("Skip this step (preview only)"):
        ss["memory"] = {"skipped": True}
        st.rerun()
    if not submitted:
        return
    if photo is None and not no_photo:
        st.error("Add a photo of you two — or tick the box if you have never taken one together.")
        return
    try:
        fields = service.clean_memory_text(like, dislike, story)    # validate before uploading
        if ctx["preview"]:
            ss["memory"] = {"player_id": player["id"], "photo": None, "created_at": game.now_iso(),
                            "player_name": player.get("display_name") or player["name"], **fields}
        else:
            ref = None
            with st.spinner("Saving your memory…"):
                if photo is not None:
                    data, mime = media.process_image(photo.getvalue(), 1400)
                    ref = service.store_upload(vault, quiz["id"], data, mime, photo.name)
                try:
                    service.save_memory(vault, quiz["id"], player["id"], photo=ref, **fields)
                except Exception:
                    if ref:
                        vault.delete_media(quiz["id"], ref["id"])
                    raise
    except (service.ServiceError, media.MediaError, StorageError) as exc:
        st.error(str(exc))
        return
    ss["just_finished"] = True
    st.rerun()


def _memories_page(ctx) -> None:
    quiz, vault, player = ctx["quiz"], ctx["vault"], ctx["player"]
    host = quiz["owner"]["name"]
    own = _own_memory(ctx)
    ui.heading("Memories", kicker=f"You × {host}")
    if not own or own.get("skipped"):
        st.info("No memory saved in this preview.")
    else:
        shared.memory_card(vault, quiz, own, show_dislike=True)
    if quiz["settings"].get("memory_wall") and not ctx["preview"]:
        others = [m for m in service.list_memories(quiz) if m["player_id"] != player["id"]]
        if others:
            st.markdown(f"#### Everyone's memories with {host}")
            for memory in others:
                shared.memory_card(vault, quiz, memory, show_dislike=False)
                st.write("")
    else:
        st.caption(f"Only you, {host} and the site admin can see this page.")


def _finish(ctx) -> None:
    """Submit (once) and let the AI judge compare answers with the answer key."""
    quiz, ss, vault = ctx["quiz"], ctx["ss"], ctx["vault"]
    ui.heading("Quiz complete.", kicker="Hang tight",
               sub="Saving your answers…")
    with st.spinner("The judge is reading your answers…"):
        if ctx["preview"]:
            attempt = ss["attempt"]
            if attempt["status"] == game.IN_PROGRESS:
                game.submit(quiz, attempt)
            service.grade_into(quiz, attempt)
        else:
            service.submit_attempt(vault, quiz["id"], ctx["player"]["id"])
            service.grade_attempt(vault, quiz["id"], ctx["player"]["id"])
    ss["just_finished"] = True
    st.rerun()


def _result(ctx, attempt: dict) -> None:
    quiz, ss, vault, player = ctx["quiz"], ctx["ss"], ctx["vault"], ctx["player"]
    summary = game.score_summary(quiz, attempt)
    pending = attempt["status"] != game.COMPLETED

    if ss.get("returning") and not ss.get("just_finished"):
        st.info("You have already completed this quiz.")
    ui.heading("QUIZ COMPLETE!", kicker="Fake Friends")
    ui.result_card(vault, quiz, player, summary, pending=pending)

    if pending:
        st.markdown('<div class="ff-card"><b>Awaiting Admin Review</b><br>Your short answers are being '
                    "checked. Your final score and place on the leaderboard will appear here.</div>",
                    unsafe_allow_html=True)
        if st.button("🔄 Check again", width="stretch"):
            if (not ctx["preview"] and grading.ai_available() and attempt.get("ai_tries", 0) < 3):
                with st.spinner("Checking…"):
                    service.grade_attempt(vault, quiz["id"], player["id"])
            st.rerun()
    else:
        video = player.get("high_video") if summary["outcome"] == "HIGH" else player.get("low_video")
        if video:
            ui.heading("A message for you", kicker="One more thing")
            ui.show_video(vault, quiz["id"], video)
        if summary["outcome"] == "HIGH" and ss.get("just_finished") and not ss.get("celebrated"):
            ss["celebrated"] = True
            st.balloons()

    st.divider()
    pages = ["🏆 Leaderboard", "📸 Memories"]
    page = st.segmented_control("After the quiz", pages, default=pages[0], key="result_page",
                                label_visibility="collapsed") or pages[0]
    if page == pages[1]:
        _memories_page(ctx)
    else:
        ui.leaderboard(vault, quiz, me=player["id"])
        if not ctx["preview"] and st.button("↻ Refresh leaderboard", width="stretch"):
            st.rerun()
    if not ctx["preview"]:
        st.divider()
        _create_prompt(ss)
        ui.privacy_note()


def _create_prompt(ss: dict) -> None:
    if ss.get("create_dismissed"):
        st.caption("No worries — you can create your own quiz any time from the home page.")
        return
    ui.heading("Want to create a Fake Friends quiz of your own?",
               sub="Write the questions about yourself and find out who really knows you.")
    left, right = st.columns(2)
    with left:
        if st.button("CREATE MY OWN QUIZ", type="primary", width="stretch"):
            st.query_params.clear()
            ui.go("create")
    with right:
        if st.button("MAYBE LATER", width="stretch"):
            ss["create_dismissed"] = True
            st.rerun()
