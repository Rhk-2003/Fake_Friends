"""Read-only views used by both the admin dashboard and the super-admin."""
from __future__ import annotations

import streamlit as st

from .. import config, game
from . import components as ui

h = ui.h
LETTERS = "ABCDEF"


def stats_row(quiz: dict) -> None:
    a = game.analytics(quiz)
    cols = st.columns(4)
    cols[0].metric("Players", a["players"])
    cols[1].metric("Started", a["started"])
    cols[2].metric("Completed", a["completed"])
    cols[3].metric("Completion rate", f'{a["completion_rate"]:g}%')
    cols = st.columns(4)
    fmt = lambda v: "—" if v is None else f"{v:g} / {config.MAX_SCORE}"  # noqa: E731
    cols[0].metric("Average score", fmt(a["average"]))
    cols[1].metric("Highest score", fmt(a["highest"]))
    cols[2].metric("Lowest score", fmt(a["lowest"]))
    cols[3].metric("Avg. completion time", game.format_duration(a["avg_duration"]))


def results_table(quiz: dict) -> None:
    rows = []
    for player in quiz["players"]:
        attempt = quiz["attempts"].get(player["id"])
        row = {"Player": player.get("display_name") or player["name"], "MCQ": "—",
               "Short Answer": "—", "Total": "—", "%": "—",
               "Status": game.STATUS_LABELS[game.NOT_STARTED], "Time": "—"}
        if attempt:
            s = game.score_summary(quiz, attempt)
            row["Status"] = game.STATUS_LABELS[attempt["status"]]
            if attempt["status"] != game.IN_PROGRESS:
                row["MCQ"] = f'{s["mcq"]}/{s["mcq_max"]}'
                row["Time"] = game.format_duration(game.duration_seconds(attempt))
            if attempt["status"] == game.COMPLETED:
                row.update({"Short Answer": f'{s["sa"]}/{s["sa_max"]}',
                            "Total": f'{s["total"]}/{s["max"]}', "%": f'{s["percentage"]:g}%'})
        rows.append(row)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("No players yet.")


def player_detail(vault, quiz: dict, player: dict) -> None:
    """One player's full answer sheet (admin-only information)."""
    name = player.get("display_name") or player["name"]
    attempt = quiz["attempts"].get(player["id"])
    status = game.STATUS_LABELS[attempt["status"] if attempt else game.NOT_STARTED]
    avatar = ui.avatar_html(vault, quiz["id"], name, player.get("dp"), player.get("emoji", ""), 64)
    st.markdown(f'<div class="ff-row">{avatar}<span class="nm">{h(name)}</span>'
                f'<span class="sc"><small>{h(status)}</small></span></div>', unsafe_allow_html=True)
    if not attempt:
        st.caption("This player has not started.")
        return
    s = game.score_summary(quiz, attempt)
    chips = [f'MCQ <b>{s["mcq"]}/{s["mcq_max"]}</b>']
    if attempt["status"] == game.COMPLETED:
        chips += [f'Short answer <b>{s["sa"]}/{s["sa_max"]}</b>', f'Total <b>{s["total"]}/{s["max"]}</b>',
                  f'<b>{s["percentage"]:g}%</b>', f'Outcome <b>{s["outcome"]}</b>']
    chips.append(f'Time <b>{game.format_duration(game.duration_seconds(attempt))}</b>')
    ui.chips(chips)

    mcqs = {q["id"]: q for q in quiz["mcqs"]}
    st.markdown("**Section 1 — MCQ answers** (first 8 answered count)")
    for n, qid in enumerate(game.counted_mcq_ids(attempt), start=1):
        q = mcqs.get(qid)
        if not q:
            continue
        options = {o["id"]: o for o in q["options"]}
        chosen = options.get(attempt["mcq_answers"][qid], {})
        right = options.get(q.get("correct_option_id"), {})
        ok = chosen.get("id") == right.get("id")
        line = f'{"✅" if ok else "❌"} **{n}. {q["text"]}** — {chosen.get("emoji", "")} {chosen.get("text", "?")}'
        if not ok:
            line += f' _(correct: {right.get("emoji", "")} {right.get("text", "?")})_'
        st.markdown(line)
    if attempt["mcq_skipped"]:
        st.caption("Skipped: " + "; ".join(mcqs[q]["text"] for q in attempt["mcq_skipped"] if q in mcqs))

    sas = {q["id"]: q for q in quiz["sas"]}
    if attempt["sa_answers"]:
        st.markdown("**Section 2 — Situations** (first 4 answered count)")
    for n, qid in enumerate(game.counted_sa_ids(attempt), start=1):
        q = sas.get(qid)
        if not q:
            continue
        grade = attempt["grades"].get(qid)
        with st.container(border=True):
            st.markdown(f"**{n}. {q['text']}**")
            st.markdown(f"Answer: {attempt['sa_answers'][qid]}")
            st.caption(f"Answer key: {q.get('answer_key', '')}")
            if grade:
                by = "AI" if grade["by"] == "ai" else "Admin"
                extra = f' — {grade["reason"]}' if grade.get("reason") else ""
                st.markdown(f'Score: **{grade["score"]}/{config.SA_POINTS}** ({by}){extra}')
                if grade.get("notes"):
                    st.caption(f'Admin notes: {grade["notes"]}')
            else:
                st.markdown("Score: _awaiting review_")
    if attempt["sa_skipped"]:
        st.caption("Skipped: " + "; ".join(sas[q]["text"] for q in attempt["sa_skipped"] if q in sas))


def questions_readonly(vault, quiz: dict) -> None:
    st.markdown(f"**Section 1 — MCQ** · {len(quiz['mcqs'])} in pool, first {config.MCQ_REQUIRED} answered count")
    for n, q in enumerate(quiz["mcqs"], start=1):
        with st.container(border=True):
            st.markdown(f'**{n}. {q.get("emoji", "")} {q["text"]}**')
            ui.show_image(vault, quiz["id"], q.get("image"), width=220)
            for i, o in enumerate(q["options"]):
                mark = " ✅" if o["id"] == q.get("correct_option_id") else ""
                st.markdown(f'{LETTERS[i]}. {o.get("emoji", "")} {o["text"]}{mark}')
                ui.show_image(vault, quiz["id"], o.get("image"), width=120)
            if q.get("explanation"):
                st.caption(f'Explanation: {q["explanation"]}')
    st.markdown(f"**Section 2 — Situations** · {len(quiz['sas'])} in pool, first {config.SA_REQUIRED} answered count")
    for n, q in enumerate(quiz["sas"], start=1):
        with st.container(border=True):
            st.markdown(f'**{n}. {q.get("emoji", "")} {q["text"]}**')
            ui.show_image(vault, quiz["id"], q.get("image"), width=220)
            st.caption(f'Answer key: {q.get("answer_key", "")}')
            if q.get("guidance"):
                st.caption(f'Grading notes: {q["guidance"]}')


VIDEO_SLOTS = [("intro_video", "🎬 Intro video"),
               ("low_video", "😬 Low-score outro (0–9)"),
               ("high_video", "🏆 High-score outro (10–16)")]


def player_media(vault, quiz: dict, player: dict, key: str) -> None:
    """Show one player's DP and videos. Videos load only when toggled on."""
    name = player.get("display_name") or player["name"]
    avatar = ui.avatar_html(vault, quiz["id"], name, player.get("dp"), player.get("emoji", ""), 96)
    st.markdown(f'<div class="ff-row">{avatar}<span class="nm">{h(name)}'
                f'{"" if player.get("active", True) else " · inactive"}</span></div>',
                unsafe_allow_html=True)
    for slot, label in VIDEO_SLOTS:
        ref = player.get(slot)
        if not ref:
            st.caption(f"{label}: not set")
            continue
        if ref["type"] == "url":
            info = ref["url"]
        else:
            size = ref["size"]
            info = f'{ref["name"]} · ' + (f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, size // 1024)} KB")
        if st.toggle(f"{label} — {info}", key=f"{key}_{player['id']}_{slot}"):
            ui.show_video(vault, quiz["id"], ref)


def memory_card(vault, quiz: dict, memory: dict, show_dislike: bool = True) -> None:
    """One memory: the photo of host + player, and the player's notes."""
    host = quiz["owner"]["name"]
    left, right = st.columns([1, 1.2])
    with left:
        if not ui.show_image(vault, quiz["id"], memory.get("photo")):
            st.markdown('<div class="ff-mem" style="text-align:center;color:var(--muted)">📷<br>'
                        "No photo together yet</div>", unsafe_allow_html=True)
    with right:
        html = (f'<div class="ff-mem"><div class="who">{h(memory.get("player_name", "?"))} × {h(host)}</div>'
                f'<div class="when">{h(memory.get("created_at", "")[:10])}</div>'
                f'<div class="lbl">The moment</div><div class="txt">{h(memory.get("story", ""))}</div>'
                f'<div class="lbl">What they like about {h(host)}</div>'
                f'<div class="txt">{h(memory.get("like", ""))}</div>')
        if show_dislike:
            html += (f'<div class="lbl">What {h(host)} could improve</div>'
                     f'<div class="txt">{h(memory.get("dislike", ""))}</div>')
        st.markdown(html + "</div>", unsafe_allow_html=True)


def memories_gallery(vault, quiz: dict, key: str, can_delete: bool = False) -> None:
    """Host / super-admin view of every memory in a quiz."""
    from .. import service

    memories = service.list_memories(quiz)
    if not memories:
        st.info("No memories yet. Each player adds a photo and a few notes right after finishing the quiz.")
        return
    st.caption(f"{len(memories)} memor{'y' if len(memories) == 1 else 'ies'} · kept even if a "
               "player's attempt is reset.")
    for memory in memories:
        memory_card(vault, quiz, memory, show_dislike=True)
        if can_delete:
            with st.expander(f"Remove {memory.get('player_name', 'this')}'s memory"):
                st.caption("Deletes the photo and notes permanently.")
                if st.button("Delete memory", key=f"{key}_del_{memory['player_id']}"):
                    service.delete_memory(vault, quiz["id"], memory["player_id"])
                    ui.flash("Memory deleted")
                    st.rerun()
        st.write("")
