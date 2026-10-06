"""Reusable UI pieces. All user text is HTML-escaped before rendering."""
from __future__ import annotations

import time
from html import escape

import streamlit as st

from .. import config, game, media


def h(text) -> str:
    return escape(str(text or ""), quote=True)


def go(view: str | None, **extra) -> None:
    """Switch view and rerun."""
    st.session_state["view"] = view
    st.session_state.update(extra)
    st.rerun()


def flash(message: str, icon: str = "✅") -> None:
    st.session_state["flash"] = {"message": message, "icon": icon, "shown_at": None}


def show_flash() -> None:
    """Toast the pending message.

    A save is often followed by a second automatic rerun (cleared uploaders
    report their new state), which would wipe a toast shown only once - so the
    toast is repeated for runs that start within 1.5 s of the first showing.
    """
    item = st.session_state.get("flash")
    if not item:
        return
    now = time.time()
    if item["shown_at"] is None:
        item["shown_at"] = now
    elif now - item["shown_at"] > 5:
        st.session_state.pop("flash", None)
        return
    if now - item["shown_at"] <= 1.5:
        st.toast(item["message"], icon=item["icon"])


def flash_banner() -> None:
    """Inline confirmation for dashboards. Streamlit can swallow a toast that
    arrives while the previous one is still on screen; this line never is."""
    item = st.session_state.get("flash")
    if item and item["shown_at"] and time.time() - item["shown_at"] <= 5:
        st.markdown(f'<div class="ff-banner">{h(item["icon"])} {h(item["message"])}</div>',
                    unsafe_allow_html=True)


def hero(subtitle: str = "How well do your friends really know you?") -> None:
    st.markdown(
        '<div class="ff-hero"><span class="mask">🎭</span>'
        '<h1><span class="fake">FAKE</span><br><span class="friends">FRIENDS</span></h1>'
        f'<div class="tag">{h(subtitle)}</div></div>', unsafe_allow_html=True)


def heading(title: str, kicker: str = "", sub: str = "") -> None:
    html = ""
    if kicker:
        html += f'<div class="ff-kicker">{h(kicker)}</div>'
    html += f'<div class="ff-title">{h(title)}</div>'
    if sub:
        html += f'<div class="ff-sub">{h(sub)}</div>'
    st.markdown(html, unsafe_allow_html=True)


def chips(items: list[str], css: str = "") -> None:
    """``items`` are pre-built safe HTML snippets (use h() for user text)."""
    st.markdown('<div class="ff-meta">' + "".join(
        f'<span class="ff-chip {css}">{item}</span>' for item in items) + "</div>",
        unsafe_allow_html=True)


def note(html_body: str) -> None:
    st.markdown(f'<div class="ff-note">{html_body}</div>', unsafe_allow_html=True)


def privacy_note() -> None:
    st.markdown(f'<div class="ff-lock"><span class="i">🔒</span><span>{h(config.PRIVACY_NOTE)}</span></div>',
                unsafe_allow_html=True)


def question_card(text: str, emoji: str = "") -> None:
    icon = f'<div class="emoji">{h(emoji)}</div>' if emoji else ""
    st.markdown(f'<div class="ff-q">{icon}<div class="text">{h(text)}</div></div>',
                unsafe_allow_html=True)


# ---- media -----------------------------------------------------------------
def media_bytes(vault, quiz_id: str, ref: dict | None) -> bytes | None:
    if ref and ref.get("type") == "upload":
        return vault.get_media(quiz_id, ref["id"])
    return None


def show_image(vault, quiz_id: str, ref: dict | None, width: int | None = None) -> bool:
    if not ref:
        return False
    try:
        if ref["type"] == "url":
            st.image(ref["url"], width=width or "stretch")
            return True
        data = media_bytes(vault, quiz_id, ref)
        if data:
            st.image(data, width=width or "stretch")
            return True
    except Exception:
        pass
    return False


def show_video(vault, quiz_id: str, ref: dict | None) -> bool:
    """Play a video. Returns False (after a gentle fallback) if it cannot be shown."""
    if not ref:
        return False
    try:
        if ref["type"] == "url":
            st.video(ref["url"])
            return True
        data = media_bytes(vault, quiz_id, ref)
        if data:
            st.video(data, format=ref.get("mime", "video/mp4"))
            return True
    except Exception:
        pass
    st.caption("🎬 The video could not be loaded - carrying on without it.")
    return False


def avatar_html(vault, quiz_id: str, name: str, dp: dict | None, emoji: str = "", size: int = 72) -> str:
    style = f"width:{size}px;height:{size}px;font-size:{int(size * .42)}px;"
    src = None
    if dp:
        if dp.get("type") == "url":
            src = dp["url"]
        else:
            data = media_bytes(vault, quiz_id, dp)
            if data:
                src = media.data_uri(data, dp.get("mime", "image/jpeg"))
    if src:
        return f'<img class="ff-av" style="{style}" src="{h(src)}" alt="{h(name)}">'
    label = emoji or (name.strip()[:1].upper() if name.strip() else "?")
    return f'<span class="ff-av" style="{style}">{h(label)}</span>'


# ---- result & leaderboard --------------------------------------------------
def result_card(vault, quiz: dict, player: dict, summary: dict, pending: bool = False) -> None:
    name = player.get("display_name") or player["name"]
    avatar = avatar_html(vault, quiz["id"], name, player.get("dp"), player.get("emoji", ""), 92)
    if pending:
        score = f'{summary["mcq"]}<small> / {summary["mcq_max"]} so far</small>'
        pct = "Short answers are waiting for review"
        react = "⏳ Awaiting Admin Review"
    else:
        score = f'{summary["total"]}<small> / {summary["max"]}</small>'
        pct = f'{summary["percentage"]:g}%'
        react = h(quiz["settings"]["high_message"] if summary["outcome"] == "HIGH"
                  else quiz["settings"]["low_message"])
    sa = "pending" if pending else f'{summary["sa"]}/{summary["sa_max"]}'
    st.markdown(
        f'<div class="ff-result">{avatar}<div class="who">{h(name)}</div>'
        f'<div class="score">{score}</div><div class="pct">{pct}</div>'
        f'<div class="ff-meta" style="justify-content:center">'
        f'<span class="ff-chip">Section 1 · <b>{summary["mcq"]}/{summary["mcq_max"]}</b></span>'
        f'<span class="ff-chip">Section 2 · <b>{sa}</b></span></div>'
        f'<div class="react">{react}</div></div>', unsafe_allow_html=True)


_MEDALS = {1: ("🥇", "1ST"), 2: ("🥈", "2ND"), 3: ("🥉", "3RD")}


def leaderboard(vault, quiz: dict, me: str | None = None, title: str = "Leaderboard") -> None:
    board = game.build_leaderboard(quiz)
    ranked = board["ranked"]
    heading(title, kicker="Who knows them best?")
    if not ranked:
        st.markdown('<div class="ff-card">No final scores yet - the podium fills up as players finish.</div>',
                    unsafe_allow_html=True)
    else:
        cols = ""
        for place in (2, 1, 3):                      # classic podium layout
            medal, label = _MEDALS[place]
            if len(ranked) >= place:
                row = ranked[place - 1]
                avatar = avatar_html(vault, quiz["id"], row["name"], row["dp"], row["emoji"],
                                     84 if place == 1 else 64)
                cols += (f'<div class="col p{place}">{avatar}<div class="pname">{h(row["name"])}</div>'
                         f'<div class="pscore">{row["total"]}/{row["max"]} · {row["percentage"]:g}%</div>'
                         f'<div class="block"><span class="medal">{medal}</span>'
                         f'<span class="place">{label}</span></div></div>')
            else:
                cols += (f'<div class="col p{place} empty"><div class="pname">&nbsp;</div>'
                         f'<div class="pscore">open</div><div class="block">'
                         f'<span class="place">{label}</span></div></div>')
        st.markdown(f'<div class="ff-podium">{cols}</div>', unsafe_allow_html=True)

    rows = ""
    for row in ranked:
        avatar = avatar_html(vault, quiz["id"], row["name"], row["dp"], row["emoji"], 38)
        medal = _MEDALS.get(row["rank"], ("", ""))[0]
        rows += (f'<div class="ff-row {"me" if row["player_id"] == me else ""}">'
                 f'<span class="rk">{medal or row["rank"]}</span>{avatar}'
                 f'<span class="nm">{h(row["name"])}{" · you" if row["player_id"] == me else ""}</span>'
                 f'<span class="sc">{row["total"]}/{row["max"]} <small>· {row["percentage"]:g}%'
                 f'<span class="x"> · {game.format_duration(row["duration"])}</span></small></span></div>')
    for row in board["pending"]:
        label = "awaiting review" if row["status"] != game.IN_PROGRESS else "playing now"
        avatar = avatar_html(vault, quiz["id"], row["name"], row["dp"], row["emoji"], 38)
        rows += (f'<div class="ff-row dim {"me" if row["player_id"] == me else ""}"><span class="rk">…</span>'
                 f'{avatar}<span class="nm">{h(row["name"])}</span><span class="sc"><small>{label}</small></span></div>')
    for row in board["not_played"]:
        avatar = avatar_html(vault, quiz["id"], row["name"], row["dp"], row["emoji"], 38)
        rows += (f'<div class="ff-row dim"><span class="rk">–</span>{avatar}'
                 f'<span class="nm">{h(row["name"])}</span><span class="sc"><small>not played yet</small></span></div>')
    if rows:
        st.markdown(rows, unsafe_allow_html=True)
    if len(ranked) > 1:
        st.caption("Ties are broken by who finished faster.")
