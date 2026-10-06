"""End-to-end tests of the real Streamlit app (headless, via AppTest)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ff import game, grading, service
from ff.runtime import get_vault

from .conftest import build_quiz, fixed_grader

def _png():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (640, 480), "#3355ff").save(buf, "PNG")
    return buf.getvalue()


PNG = _png()
APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_STORAGE", "local")
    monkeypatch.delenv("FF_MASTER_KEY", raising=False)
    st.cache_resource.clear()
    yield get_vault()
    st.cache_resource.clear()


def new_app(quiz_id=None):
    at = AppTest.from_file(APP, default_timeout=30)
    if quiz_id:
        at.query_params["quiz"] = quiz_id
    return at.run()


def click(at, label):
    button = next(b for b in at.button if label in b.label)
    return button.click().run()


def page_text(at):
    """Everything rendered on the page, minus the theme stylesheet."""
    text = " ".join(str(e.value) for e in at.markdown) + " ".join(str(e.value) for e in at.info)
    return text.split("</style>")[-1]


def enter_quiz(at, password):
    at.text_input[0].set_value(password)
    return click(at, "Enter")


def no_crash(at):
    assert not at.exception, at.exception
    assert "Something went wrong" not in " ".join(e.value for e in at.error)


def play_ui(at, quiz, mcq_correct=8, skip_mcq=0, skip_sa=0, memory=True):
    """Play from the player picker to the result screen."""
    click(at, "That's me")
    click(at, "START QUIZ")
    text = page_text(at)
    assert "All players will receive the same questions" in text and "compared" not in text
    click(at, "I'm ready")
    for _ in range(skip_mcq):
        click(at, "Skip")
    for i in range(8):
        text = page_text(at)
        assert f"Answered <b>{i}</b> / 8" in text
        assert "ff-opt" not in text and "says</b>" not in text      # nothing revealed before answering
        prefix = "right" if i < mcq_correct else "wrong-a"
        click(at, prefix)
        click(at, "Lock in answer")
        text = page_text(at)                                        # the reveal screen
        assert ("✅ Correct!" in text) == (i < mcq_correct)
        assert 'class="ff-opt ok"' in text and "why right" in text
        assert ('class="ff-opt bad"' in text) == (i >= mcq_correct)
        click(at, "→")
    assert "Things just got real." in page_text(at)
    click(at, "Bring it on")
    for _ in range(skip_sa):
        click(at, "Skip")
    for i in range(4):
        text = page_text(at)
        assert f"Answered <b>{i}</b> / 4" in text and "ff-key" not in text
        at.text_area[0].set_value(f"typed answer {i}")
        click(at, "Lock in answer")
        text = page_text(at)
        assert "real answer" in text and "ff-key" in text and f"typed answer {i}" in text
        click(at, "→")
    at.run()            # the grading screen reruns into the memory step
    no_crash(at)
    assert "One last thing before your score" in page_text(at)
    assert "ff-podium" not in page_text(at) and "QUIZ COMPLETE!" not in page_text(at)
    if memory:
        fill_memory(at)
    return at


def fill_memory(at, photo=True):
    areas = {a.label: a for a in at.text_area}
    next(a for label, a in areas.items() if "story" in label).set_value("Goa trip, sunrise on the beach")
    next(a for label, a in areas.items() if "like about" in label).set_value("Always shows up")
    next(a for label, a in areas.items() if "improve" in label).set_value("Reply to texts faster")
    if photo:
        at.file_uploader[0].upload("us two.png", PNG, "image/png")
    else:
        at.checkbox[0].check()
    click(at, "Save my memory")
    no_crash(at)
    return at


# ---------------------------------------------------------------------------
def test_landing_and_create_quiz_flow(vault):
    at = new_app()
    no_crash(at)
    assert [b.label for b in at.button] == ["🎮  PLAY A QUIZ", "✨  CREATE YOUR OWN QUIZ", "Admin login"]
    assert "encrypted" in page_text(at)
    click(at, "CREATE YOUR OWN QUIZ")
    fields = {t.label: t for t in at.text_input}
    fields["Your name"].set_value("HK")
    fields["Quiz name"].set_value("Know HK?")
    fields["Admin username"].set_value("hkadmin")
    fields["Admin password"].set_value("secret123")
    fields["Repeat password"].set_value("different")
    click(at, "Create my quiz")
    assert "don't match" in at.error[0].value
    next(t for t in at.text_input if t.label == "Repeat password").set_value("secret123")
    click(at, "Create my quiz")
    no_crash(at)
    assert at.session_state["view"] == "created"
    link, quiz_password, username = [c.value for c in at.code]
    quiz_id = link.split("quiz=")[1]
    assert username == "hkadmin" and "password" not in link
    assert "secret123" not in page_text(at)
    assert "super-admin" in at.success[0].value          # first admin on the site
    click(at, "Open my dashboard")
    no_crash(at)
    assert at.session_state["view"] == "admin"
    quiz = vault.load_quiz(quiz_id)
    assert quiz["settings"]["password"] == quiz_password and not quiz["settings"]["published"]
    # a draft quiz cannot be opened by players
    player = new_app(quiz_id)
    assert "isn't live yet" in page_text(player)


def test_every_admin_section_renders(vault):
    made = build_quiz(vault, "hkadmin", n_mcq=10, n_sa=5)
    build_quiz(vault, "second", players=("Zed",))
    at = new_app()
    click(at, "Admin login")
    at.text_input[0].set_value("hkadmin")
    at.text_input[1].set_value("wrong-password")
    click(at, "Log in")
    assert at.error[0].value == "Wrong username or password."
    at.text_input[1].set_value("secret123")
    click(at, "Log in")
    assert at.session_state["admin"]["quiz_id"] == made["quiz_id"]
    for name in ["Overview", "Settings", "Players", "Questions", "Review answers", "Results",
                 "Leaderboard", "Share", "Preview & publish", "👑 All quizzes"]:
        at.session_state["admin_section"] = name
        at.run()
        no_crash(at)
        assert not at.error, (name, [e.value for e in at.error])
    assert "Everything other admins have created" in page_text(at)
    assert made["quiz_password"] not in page_text(at)


def test_only_super_admin_sees_other_quizzes(vault):
    build_quiz(vault, "first")
    build_quiz(vault, "second", players=("Zed",))
    at = new_app()
    click(at, "Admin login")
    at.text_input[0].set_value("second")
    at.text_input[1].set_value("secret123")
    click(at, "Log in")
    assert "Admin dashboard" in page_text(at) and "Super-admin" not in page_text(at)
    # even if a normal admin forces the section, the server refuses
    at.session_state["admin_section"] = "👑 All quizzes"
    at.run()
    assert "Everything other admins have created" not in page_text(at)
    assert "Rahul" not in page_text(at)
    # a forged session pointing at someone else's quiz is thrown out
    at.session_state["admin"] = {"username": "second",
                                 "quiz_id": service.authenticate_admin(vault, "first", "secret123")}
    at.run()
    assert at.session_state["view"] == "admin_login" and "admin" not in at.session_state


def test_player_gate(vault):
    made = build_quiz(vault)
    assert "couldn't find that quiz" in new_app("NOPE1234").error[0].value
    at = new_app(made["quiz_id"])
    assert "Enter Quiz Password" in page_text(at) and "Rahul" not in page_text(at)
    enter_quiz(at, "wrong")
    assert "not the password" in at.error[0].value and "Rahul" not in page_text(at)
    enter_quiz(at, made["quiz_password"])
    assert "Choose Your Player" in page_text(at) and "Rahul" in page_text(at)
    assert "encrypted" in page_text(at)


def test_full_game_with_ai_grading_and_leaderboard(vault, monkeypatch):
    monkeypatch.setattr(grading, "grade_short_answers", fixed_grader(2))
    made = build_quiz(vault, n_mcq=11, n_sa=6)
    qid = made["quiz_id"]
    quiz = vault.load_quiz(qid)
    at = enter_quiz(new_app(qid), made["quiz_password"])
    play_ui(at, quiz, mcq_correct=5, skip_mcq=3, skip_sa=2)
    text = page_text(at)
    assert "QUIZ COMPLETE!" in text and "13<small> / 16</small>" in text and "81.25%" in text
    assert "ff-podium" in text and "1ST" in text
    assert "right 0" not in text and "wrong-a" not in text and "key 1" not in text          # no answers leak to players
    pid = quiz["players"][0]["id"]
    attempt = vault.load_quiz(qid)["attempts"][pid]
    assert attempt["status"] == game.COMPLETED
    assert len(attempt["mcq_skipped"]) == 3 and len(attempt["mcq_answers"]) == 8
    assert list(attempt["sa_answers"].values()) == [f"typed answer {i}" for i in range(4)]
    assert [quiz["sas"].index(q) for q in quiz["sas"] if q["id"] in attempt["sa_answers"]] == [2, 3, 4, 5]
    assert vault.pending_sync() == 0                            # everything reached storage

    # coming back in a new browser session: no second attempt
    again = enter_quiz(new_app(qid), made["quiz_password"])
    assert "✅ played" in page_text(again)
    click(again, "See result")
    assert "You have already completed this quiz." in page_text(again)
    assert vault.load_quiz(qid)["attempts"][pid]["id"] == attempt["id"]
    click(again, "CREATE MY OWN QUIZ")
    assert again.session_state["view"] == "create"


def test_refresh_mid_quiz_resumes(vault, monkeypatch):
    monkeypatch.setattr(grading, "grade_short_answers", fixed_grader(1))
    made = build_quiz(vault, n_mcq=9)
    qid = made["quiz_id"]
    at = enter_quiz(new_app(qid), made["quiz_password"])
    click(at, "That's me"), click(at, "START QUIZ"), click(at, "I'm ready")
    for _ in range(3):
        click(at, "right"), click(at, "Lock in answer"), click(at, "→")
    st.cache_resource.clear()                                    # the server itself restarts
    assert get_vault() is not vault
    resumed = enter_quiz(new_app(qid), made["quiz_password"])    # fresh session = page refresh
    assert "⏳ in progress" in page_text(resumed)
    click(resumed, "Continue")
    assert "Answered <b>3</b> / 8" in page_text(resumed) and "Question <b>4</b> of <b>9</b>" in page_text(resumed)


def test_without_ai_key_answers_wait_for_admin_review(vault):
    made = build_quiz(vault)
    qid = made["quiz_id"]
    quiz = vault.load_quiz(qid)
    at = enter_quiz(new_app(qid), made["quiz_password"])
    play_ui(at, quiz)
    assert "Awaiting Admin Review" in page_text(at)
    pid = quiz["players"][0]["id"]
    attempt = vault.load_quiz(qid)["attempts"][pid]
    assert attempt["status"] == game.AWAITING_REVIEW
    for sa_id in game.counted_sa_ids(attempt):
        service.manual_grade(vault, qid, pid, sa_id, 1)
    click(at, "Check again")
    assert "12<small> / 16</small>" in page_text(at) and "Awaiting Admin Review" not in page_text(at)


def test_preview_records_nothing(vault, monkeypatch):
    monkeypatch.setattr(grading, "grade_short_answers", fixed_grader(2))
    made = build_quiz(vault, publish=False)                      # preview works on drafts too
    qid = made["quiz_id"]
    at = new_app()
    at.session_state["admin"] = {"username": "hkadmin", "quiz_id": qid}
    at.session_state["view"] = "preview"
    at.run()
    assert "Preview mode" in page_text(at)
    play_ui(at, vault.load_quiz(qid), mcq_correct=8)
    assert "16<small> / 16</small>" in page_text(at)
    assert vault.load_quiz(qid)["attempts"] == {}
    assert not service.questions_locked(vault.load_quiz(qid))


def test_memory_step_gates_score_and_is_kept_for_host(vault, monkeypatch):
    monkeypatch.setattr(grading, "grade_short_answers", fixed_grader(2))
    made = build_quiz(vault, "hkadmin")
    other = build_quiz(vault, "second", players=("Zed",))
    qid = made["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    at = enter_quiz(new_app(qid), made["quiz_password"])
    play_ui(at, quiz, memory=False)

    # the form insists on a photo (or the explicit "never took one") and on all three notes
    click(at, "Save my memory")
    assert "Add a photo of you two" in at.error[0].value
    at.checkbox[0].check()
    click(at, "Save my memory")
    assert "Please fill in" in at.error[0].value and vault.load_quiz(qid)["memories"] == {}
    at.checkbox[0].uncheck()

    # a refresh at this point comes back to the memory step, not to the score
    back = enter_quiz(new_app(qid), made["quiz_password"])
    click(back, "See result")
    assert "One last thing before your score" in page_text(back) and "ff-podium" not in page_text(back)

    fill_memory(at)
    text = page_text(at)
    assert "QUIZ COMPLETE!" in text and "ff-podium" in text
    memory = vault.load_quiz(qid)["memories"][pid]
    assert (memory["player_name"], memory["like"], memory["dislike"], memory["story"]) == (
        "Rahul", "Always shows up", "Reply to texts faster", "Goa trip, sunrise on the beach")
    assert memory["photo"]["name"] == "us_two.png" and vault.get_media(qid, memory["photo"]["id"])

    # the player's Memories page
    at.session_state["result_page"] = "📸 Memories"
    at.run()
    no_crash(at)
    text = page_text(at)
    assert "Goa trip, sunrise on the beach" in text and "Reply to texts faster" in text
    assert "Only you, Owner hkadmin and the site admin can see this page." in " ".join(c.value for c in at.caption)

    # the host keeps it, even after resetting that attempt
    service.reset_attempt(vault, qid, pid)
    admin = new_app()
    admin.session_state["admin"] = {"username": "hkadmin", "quiz_id": qid}
    admin.session_state["view"] = "admin"
    admin.session_state["admin_section"] = "Memories"
    admin.run()
    no_crash(admin)
    assert "Rahul × Owner hkadmin" in page_text(admin) and "Reply to texts faster" in page_text(admin)

    # another admin cannot see it; the super-admin (first admin) can see theirs
    service.start_attempt(vault, other["quiz_id"], vault.load_quiz(other["quiz_id"])["players"][0]["id"])
    zed = vault.load_quiz(other["quiz_id"])["players"][0]["id"]
    vault.mutate_quiz(other["quiz_id"], lambda d: d["attempts"][zed].update(status=game.COMPLETED))
    service.save_memory(vault, other["quiz_id"], zed, "Zed likes", "Zed dislikes", "Zed story", None)
    second = new_app()
    second.session_state["admin"] = {"username": "second", "quiz_id": other["quiz_id"]}
    second.session_state["view"] = "admin"
    second.session_state["admin_section"] = "Memories"
    second.run()
    assert "Zed story" in page_text(second) and "Goa trip" not in page_text(second)
    admin.session_state["admin_section"] = "👑 All quizzes"
    admin.session_state["super_tab"] = "Memories"
    admin.run()
    no_crash(admin)
    assert "Zed story" in page_text(admin) and "Zed dislikes" in page_text(admin)
    assert not [b for b in admin.button if "Delete memory" in b.label]      # view only


def test_memory_wall_hides_dislikes_from_other_players(vault, monkeypatch):
    monkeypatch.setattr(grading, "grade_short_answers", fixed_grader(1))
    made = build_quiz(vault)
    qid = made["quiz_id"]
    quiz = vault.load_quiz(qid)
    arjun = quiz["players"][1]["id"]
    service.start_attempt(vault, qid, arjun)
    vault.mutate_quiz(qid, lambda d: d["attempts"][arjun].update(status=game.COMPLETED))
    service.save_memory(vault, qid, arjun, "Arjun likes", "ARJUN-PRIVATE-DISLIKE", "Arjun story", None)

    at = enter_quiz(new_app(qid), made["quiz_password"])
    play_ui(at, quiz)
    at.session_state["result_page"] = "📸 Memories"
    at.run()
    assert "Arjun story" not in page_text(at)                       # wall is off by default

    service.update_settings(vault, qid, {"memory_wall": True})
    at.run()
    text = page_text(at)
    assert "Arjun story" in text and "Arjun likes" in text
    assert "ARJUN-PRIVATE-DISLIKE" not in text and "Reply to texts faster" in text
