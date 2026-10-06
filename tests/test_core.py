"""Auth, isolation, pools/skips, scoring, threshold, leaderboard, export."""
import copy

import pytest

from ff import crypto, game, grading, service
from ff.storage import Vault

from .conftest import build_quiz, fixed_grader, play_through


# ---- authentication --------------------------------------------------------
def test_admin_login_and_invalid_password(vault):
    made = build_quiz(vault)
    assert service.authenticate_admin(vault, "hkadmin", "secret123") == made["quiz_id"]
    assert service.authenticate_admin(vault, "HKadmin ", "secret123") == made["quiz_id"]
    assert service.authenticate_admin(vault, "hkadmin", "wrong") is None
    assert service.authenticate_admin(vault, "nobody", "secret123") is None


def test_passwords_are_hashed_not_stored(vault):
    made = build_quiz(vault)
    quiz = vault.load_quiz(made["quiz_id"])
    assert "secret123" not in str(quiz["owner"])
    assert quiz["owner"]["password_hash"].startswith("scrypt$")


def test_username_unique_and_validated(vault):
    build_quiz(vault)
    with pytest.raises(service.ServiceError):
        service.create_quiz(vault, "X", "hkadmin", "secret123", "dup")
    with pytest.raises(service.ServiceError):
        service.create_quiz(vault, "X", "a b", "secret123", "bad username")
    with pytest.raises(service.ServiceError):
        service.create_quiz(vault, "X", "okname", "123", "short password")


def test_quiz_password(vault):
    made = build_quiz(vault)
    quiz = vault.load_quiz(made["quiz_id"])
    assert service.check_quiz_password(quiz, made["quiz_password"])
    assert service.check_quiz_password(quiz, " " + made["quiz_password"].lower() + " ")
    assert not service.check_quiz_password(quiz, "nope")


def test_change_admin_password(vault):
    made = build_quiz(vault)
    with pytest.raises(service.ServiceError):
        service.change_admin_password(vault, made["quiz_id"], "wrong", "newpass1")
    service.change_admin_password(vault, made["quiz_id"], "secret123", "newpass1")
    assert service.authenticate_admin(vault, "hkadmin", "secret123") is None
    assert service.authenticate_admin(vault, "hkadmin", "newpass1") == made["quiz_id"]


# ---- isolation & super-admin ----------------------------------------------
def test_quiz_isolation(vault):
    a, b = build_quiz(vault, "alice"), build_quiz(vault, "bob", players=("Zed",))
    qa, qb = vault.load_quiz(a["quiz_id"]), vault.load_quiz(b["quiz_id"])
    assert {p["name"] for p in qa["players"]} == {"Rahul", "Arjun", "Meera"}
    assert {p["name"] for p in qb["players"]} == {"Zed"}
    # an admin only ever resolves to their own quiz
    assert service.authenticate_admin(vault, "alice", "secret123") == a["quiz_id"]
    assert service.owns_quiz(vault, "alice", a["quiz_id"])
    assert not service.owns_quiz(vault, "alice", b["quiz_id"])
    # ids from quiz A are meaningless in quiz B
    with pytest.raises(service.ServiceError):
        service.start_attempt(vault, b["quiz_id"], qa["players"][0]["id"])
    with pytest.raises(service.ServiceError):
        service.update_player(vault, b["quiz_id"], qa["players"][0]["id"], {"name": "hack"})
    with pytest.raises(service.ServiceError):
        service.delete_question(vault, b["quiz_id"], "mcq", qa["mcqs"][0]["id"])
    assert vault.load_quiz(a["quiz_id"]) == qa


def test_first_admin_is_super_admin(vault):
    first, second = build_quiz(vault, "first"), build_quiz(vault, "second")
    assert first["is_super"] and not second["is_super"]
    assert service.is_super(vault, "first") and not service.is_super(vault, "second")
    assert not service.is_super(vault, "") and not service.is_super(vault, "ghost")
    assert [r["username"] for r in service.list_all_quizzes(vault)] == ["first", "second"]


def test_super_admin_from_secret(vault, monkeypatch):
    build_quiz(vault, "first"), build_quiz(vault, "second")
    monkeypatch.setenv("SUPER_ADMIN_USERNAME", "Second")
    assert service.is_super(vault, "second") and not service.is_super(vault, "first")


def test_delete_quiz(vault, backend):
    build_quiz(vault, "first")
    made = build_quiz(vault, "second")
    with pytest.raises(service.ServiceError):
        service.delete_quiz(vault, made["quiz_id"], "first")      # not the owner / is super
    service.delete_quiz(vault, made["quiz_id"], "second")
    assert vault.load_quiz(made["quiz_id"]) is None
    assert service.authenticate_admin(vault, "second", "secret123") is None


# ---- encryption at rest ----------------------------------------------------
def test_everything_on_disk_is_encrypted(vault, backend):
    made = build_quiz(vault)
    qid = made["quiz_id"]
    ref = service.store_upload(vault, qid, b"SECRET-VIDEO-BYTES" * 50, "video/mp4", "rahul intro.mp4")
    pid = vault.load_quiz(qid)["players"][0]["id"]
    service.update_player(vault, qid, pid, {"intro_video": ref})
    play_through(vault, qid, pid)
    vault.flush_all()
    blob = b"".join(backend.files.values()) + " ".join(backend.files).encode()
    for secret in (b"Rahul", b"MCQ 1", b"my answer", b"SECRET-VIDEO", qid.encode(), b"hkadmin",
                   made["quiz_password"].encode(), b"key 1", b"rahul"):
        assert secret not in blob
    assert all(v.startswith(b"FF1") for v in backend.files.values())
    assert vault.get_media(qid, ref["id"]) == b"SECRET-VIDEO-BYTES" * 50
    assert ref["name"] == "rahul_intro.mp4"


def test_wrong_master_key_cannot_read(vault, backend):
    made = build_quiz(vault)
    other = Vault(backend, crypto.normalize_master_key(crypto.generate_master_key()))
    assert other.load_quiz(made["quiz_id"]) is None          # different key -> different file names
    same_names = Vault(backend, vault._key)
    assert same_names.load_quiz(made["quiz_id"])["id"] == made["quiz_id"]   # survives a "restart"
    name = same_names._name("quiz/" + made["quiz_id"])
    backend.files[name] = backend.files[name][:-1] + bytes([backend.files[name][-1] ^ 1])
    with pytest.raises(crypto.CryptoError):
        Vault(backend, vault._key).load_quiz(made["quiz_id"])  # tampering is detected


def test_blob_cannot_be_swapped_between_paths():
    key = crypto.normalize_master_key(crypto.generate_master_key())
    blob = crypto.encrypt(key, "quiz/AAA", b"data")
    assert crypto.decrypt(key, "quiz/AAA", blob) == b"data"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(key, "quiz/BBB", blob)


def test_every_answer_is_saved_as_a_draft(vault, backend):
    """A server restart at any point resumes from the next unanswered question."""
    qid = build_quiz(vault, n_mcq=10, n_sa=5)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    service.start_attempt(vault, qid, pid)
    service.play(vault, qid, pid, "skip_mcq", quiz["mcqs"][0]["id"])
    for n, q in enumerate(quiz["mcqs"][1:9], start=1):
        before = backend.writes
        service.play(vault, qid, pid, "answer_mcq", q["id"], q["options"][0]["id"])
        assert backend.writes == before + 1 and vault.pending_sync() == 0
        restarted = Vault(backend, vault._key).load_quiz(qid)          # brand-new process
        state = game.section_state(restarted, restarted["attempts"][pid], "mcq")
        assert state["answered"] == n and state["skips_used"] == 1
        assert state["done"] or state["current"]["id"] == quiz["mcqs"][n + 1]["id"]
    service.play(vault, qid, pid, "answer_sa", quiz["sas"][0]["id"], "first situation")
    restarted = Vault(backend, vault._key).load_quiz(qid)
    assert restarted["attempts"][pid]["sa_answers"] == {quiz["sas"][0]["id"]: "first situation"}
    assert game.section_state(restarted, restarted["attempts"][pid], "sa")["current"]["id"] == quiz["sas"][1]["id"]


def test_vault_checkpoints_batch_low_priority_writes(vault, backend):
    qid = build_quiz(vault)["quiz_id"]
    before = backend.writes
    for i in range(5):
        vault.mutate_quiz(qid, lambda d, i=i: d["settings"].update(description=f"v{i}"), durable=False)
    assert backend.writes == before and vault.pending_sync() == 1   # nothing committed yet
    vault.flush_stale()                                             # too fresh to flush
    assert backend.writes == before
    vault.flush_all()
    assert backend.writes == before + 1 and vault.pending_sync() == 0
    assert Vault(backend, vault._key).load_quiz(qid)["settings"]["description"] == "v4"


def test_failed_write_is_kept_and_retried(vault, backend):
    made = build_quiz(vault)
    qid = made["quiz_id"]
    real = backend.write
    backend.write = lambda *a: (_ for _ in ()).throw(RuntimeError("github down"))
    service.add_player(vault, qid, {"name": "Late"})
    assert vault.pending_sync() == 1 and vault.last_error
    assert "Late" in [p["name"] for p in vault.load_quiz(qid)["players"]]   # not lost
    backend.write = real
    vault.flush_all()
    assert vault.pending_sync() == 0
    assert "Late" in [p["name"] for p in Vault(backend, vault._key).load_quiz(qid)["players"]]


# ---- players ---------------------------------------------------------------
def test_player_crud_and_media_gc(vault, backend):
    made = build_quiz(vault, publish=False)
    qid = made["quiz_id"]
    ref = service.store_upload(vault, qid, b"x" * 10, "image/png", "a.png")
    pid = service.add_player(vault, qid, {"name": "Neha", "dp": ref})
    with pytest.raises(service.ServiceError):
        service.add_player(vault, qid, {"name": "neha"})
    assert vault.get_media(qid, ref["id"]) == b"x" * 10
    ref2 = service.store_upload(vault, qid, b"y" * 10, "image/png", "b.png")
    service.update_player(vault, qid, pid, {"dp": ref2, "active": False})
    assert Vault(backend, vault._key).get_media(qid, ref["id"]) is None   # replaced -> deleted
    service.delete_player(vault, qid, pid)
    assert Vault(backend, vault._key).get_media(qid, ref2["id"]) is None


# ---- validation / publishing ----------------------------------------------
def test_publish_requires_complete_quiz(vault):
    made = build_quiz(vault, n_mcq=7, n_sa=3, publish=False)
    qid = made["quiz_id"]
    with pytest.raises(service.ServiceError):
        service.set_published(vault, qid, True)
    failed = [c["label"] for c in service.validate_quiz(vault.load_quiz(qid)) if not c["ok"]]
    assert failed == ["At least 8 MCQs", "At least 4 situations"]
    service.save_mcq(vault, qid, None, {"text": "one more", "correct_index": 1,
                                        "options": [{"text": "a"}, {"text": "b"}]})
    service.save_sa(vault, qid, None, {"text": "one more", "answer_key": "k"})
    service.set_published(vault, qid, True)
    assert vault.load_quiz(qid)["settings"]["published"]


def test_mcq_validation(vault):
    qid = build_quiz(vault, publish=False)["quiz_id"]
    bad = [
        {"text": "", "correct_index": 0, "options": [{"text": "a"}, {"text": "b"}]},
        {"text": "q", "correct_index": 0, "options": [{"text": "a"}]},
        {"text": "q", "correct_index": 0, "options": [{"text": str(i)} for i in range(7)]},
        {"text": "q", "correct_index": None, "options": [{"text": "a"}, {"text": "b"}]},
        {"text": "q", "correct_index": 5, "options": [{"text": "a"}, {"text": "b"}]},
        {"text": "q", "correct_index": 0, "options": [{"text": "a"}, {"text": ""}]},
        {"text": "q", "correct_index": 0, "options": [{"text": "a"}, {"text": "A"}]},
    ]
    for data in bad:
        with pytest.raises(service.ServiceError):
            service.save_mcq(vault, qid, None, data)
    with pytest.raises(service.ServiceError):
        service.save_sa(vault, qid, None, {"text": "situation", "answer_key": ""})
    no_players = service.create_quiz(vault, "N", "noplayers", "secret123", "empty")["quiz_id"]
    assert "At least one active player" in [
        c["label"] for c in service.validate_quiz(vault.load_quiz(no_players)) if not c["ok"]]


def test_questions_lock_after_first_start(vault):
    qid = build_quiz(vault, n_mcq=10)["quiz_id"]
    quiz = vault.load_quiz(qid)
    service.move_question(vault, qid, "mcq", quiz["mcqs"][0]["id"], 1)
    service.duplicate_question(vault, qid, "mcq", quiz["mcqs"][0]["id"])
    assert len(vault.load_quiz(qid)["mcqs"]) == 11
    service.start_attempt(vault, qid, quiz["players"][0]["id"])
    for call in (lambda: service.delete_question(vault, qid, "mcq", quiz["mcqs"][0]["id"]),
                 lambda: service.move_question(vault, qid, "mcq", quiz["mcqs"][0]["id"], 1),
                 lambda: service.save_sa(vault, qid, None, {"text": "x", "answer_key": "y"})):
        with pytest.raises(service.ServiceError, match="locked"):
            call()
    service.reset_all_attempts(vault, qid)
    service.delete_question(vault, qid, "mcq", quiz["mcqs"][0]["id"])


# ---- pools, skips, attempts ------------------------------------------------
def test_unpublished_quiz_cannot_be_started(vault):
    qid = build_quiz(vault, publish=False)["quiz_id"]
    with pytest.raises(service.ServiceError):
        service.start_attempt(vault, qid, vault.load_quiz(qid)["players"][0]["id"])


def test_pool_skip_budget_and_first_n_count(vault):
    qid = build_quiz(vault, n_mcq=12, n_sa=6)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    service.start_attempt(vault, qid, pid)
    state = game.section_state(quiz, vault.load_quiz(qid)["attempts"][pid], "mcq")
    assert (state["pool_size"], state["required"], state["skips_left"], state["position"]) == (12, 8, 4, 1)
    mcqs = quiz["mcqs"]
    with pytest.raises(game.GameError):                       # must act on the current question
        service.play(vault, qid, pid, "answer_mcq", mcqs[3]["id"], mcqs[3]["options"][0]["id"])
    with pytest.raises(game.GameError):                       # option from another question
        service.play(vault, qid, pid, "answer_mcq", mcqs[0]["id"], mcqs[1]["options"][0]["id"])
    for q in mcqs[:4]:
        service.play(vault, qid, pid, "skip_mcq", q["id"])
    with pytest.raises(game.GameError, match="No skips left"):
        service.play(vault, qid, pid, "skip_mcq", mcqs[4]["id"])
    with pytest.raises(game.GameError):                       # section 2 is gated on section 1
        service.play(vault, qid, pid, "answer_sa", quiz["sas"][0]["id"], "early")
    for q in mcqs[4:]:
        service.play(vault, qid, pid, "answer_mcq", q["id"], q["options"][0]["id"])
    attempt = vault.load_quiz(qid)["attempts"][pid]
    assert game.section_state(quiz, attempt, "mcq")["done"]
    assert game.counted_mcq_ids(attempt) == [q["id"] for q in mcqs[4:]]
    with pytest.raises(game.GameError):                       # no 9th answer
        service.play(vault, qid, pid, "answer_mcq", mcqs[0]["id"], mcqs[0]["options"][0]["id"])
    with pytest.raises(game.GameError):                       # cannot submit early
        service.submit_attempt(vault, qid, pid)
    sas = quiz["sas"]
    with pytest.raises(game.GameError):
        service.play(vault, qid, pid, "answer_sa", sas[0]["id"], "   ")
    service.play(vault, qid, pid, "skip_sa", sas[0]["id"])
    service.play(vault, qid, pid, "answer_sa", sas[1]["id"], "a")
    service.play(vault, qid, pid, "skip_sa", sas[2]["id"])
    with pytest.raises(game.GameError, match="No skips left"):
        service.play(vault, qid, pid, "skip_sa", sas[3]["id"])
    for q in sas[3:]:
        service.play(vault, qid, pid, "answer_sa", q["id"], "a")
    assert service.submit_attempt(vault, qid, pid)["status"] == game.SUBMITTED


def test_exact_pool_has_no_skips(vault):
    qid = build_quiz(vault)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    service.start_attempt(vault, qid, pid)
    with pytest.raises(game.GameError, match="No skips left"):
        service.play(vault, qid, pid, "skip_mcq", quiz["mcqs"][0]["id"])


def test_duplicate_submission_prevented(vault):
    qid = build_quiz(vault)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    first = play_through(vault, qid, pid)
    again = service.start_attempt(vault, qid, pid)             # "start" returns the same attempt
    assert again["id"] == first["id"] and again["status"] == game.SUBMITTED
    assert service.submit_attempt(vault, qid, pid)["completed_at"] == first["completed_at"]
    with pytest.raises(game.GameError):
        service.play(vault, qid, pid, "answer_mcq", quiz["mcqs"][0]["id"],
                     quiz["mcqs"][0]["options"][0]["id"])
    service.reset_attempt(vault, qid, pid)                     # admin reset allows a fresh attempt
    assert service.start_attempt(vault, qid, pid)["id"] != first["id"]


# ---- scoring ---------------------------------------------------------------
@pytest.mark.parametrize("total,expected", [(0, "LOW"), (9, "LOW"), (10, "HIGH"), (16, "HIGH")])
def test_threshold_is_integer_ten(total, expected):
    assert game.outcome_for(total) == expected


@pytest.mark.parametrize("mcq_correct,grade,mcq,sa,total,pct,outcome", [
    (8, 2, 8, 8, 16, 100.0, "HIGH"),
    (7, 1, 7, 4, 11, 68.75, "HIGH"),
    (5, 1, 5, 4, 9, 56.25, "LOW"),      # 56.25% -> LOW
    (2, 2, 2, 8, 10, 62.5, "HIGH"),     # exactly 10 -> HIGH
    (0, 0, 0, 0, 0, 0.0, "LOW"),
])
def test_scoring(vault, mcq_correct, grade, mcq, sa, total, pct, outcome):
    qid = build_quiz(vault)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    play_through(vault, qid, pid, mcq_correct=mcq_correct)
    attempt = service.grade_attempt(vault, qid, pid, grader=fixed_grader(grade))
    assert attempt["status"] == game.COMPLETED
    s = game.score_summary(vault.load_quiz(qid), attempt)
    assert (s["mcq"], s["sa"], s["total"], s["percentage"], s["outcome"]) == (mcq, sa, total, pct, outcome)
    assert s["max"] == 16


def test_score_is_recomputed_from_raw_answers(vault):
    qid = build_quiz(vault)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    play_through(vault, qid, pid, mcq_correct=3)
    attempt = vault.load_quiz(qid)["attempts"][pid]
    attempt["score"] = attempt["total"] = 16                   # a forged score field is ignored
    assert game.score_summary(quiz, attempt)["mcq"] == 3
    attempt["grades"] = {game.counted_sa_ids(attempt)[0]: {"score": 99}, "bogus": {"score": 2}}
    assert game.sa_score(attempt) == 2                         # clamped, unknown ids ignored


# ---- grading ---------------------------------------------------------------
def test_ai_failure_falls_back_to_manual_review(vault):
    qid = build_quiz(vault)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    play_through(vault, qid, pid)

    def broken(items):
        raise grading.GradingError("down")

    attempt = service.grade_attempt(vault, qid, pid, grader=broken)
    assert attempt["status"] == game.AWAITING_REVIEW and attempt["ai_tries"] == 1
    assert not game.build_leaderboard(vault.load_quiz(qid))["ranked"]
    ids = game.counted_sa_ids(attempt)
    for sa_id in ids[:3]:
        service.manual_grade(vault, qid, pid, sa_id, 2, "good")
    assert vault.load_quiz(qid)["attempts"][pid]["status"] == game.AWAITING_REVIEW
    service.manual_grade(vault, qid, pid, ids[3], 1)
    final = vault.load_quiz(qid)["attempts"][pid]
    assert final["status"] == game.COMPLETED and game.sa_score(final) == 7
    with pytest.raises(game.GameError):
        service.manual_grade(vault, qid, pid, "not-a-question", 2)


def test_admin_override_survives_ai_regrade(vault):
    qid = build_quiz(vault)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    play_through(vault, qid, pid)
    attempt = service.grade_attempt(vault, qid, pid, grader=fixed_grader(0))
    first = game.counted_sa_ids(attempt)[0]
    service.manual_grade(vault, qid, pid, first, 2, "I know what they meant")
    grade = vault.load_quiz(qid)["attempts"][pid]["grades"][first]
    assert (grade["score"], grade["by"], grade["reason"], grade["notes"]) == (
        2, "admin", "test", "I know what they meant")
    graded = copy.deepcopy(vault.load_quiz(qid)["attempts"][pid])
    graded["status"] = game.AWAITING_REVIEW
    service.grade_into(quiz, graded, fixed_grader(0))
    assert graded["grades"][first]["score"] == 2


def test_only_counted_answers_are_sent_to_ai(vault):
    qid = build_quiz(vault, n_sa=7)["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    play_through(vault, qid, pid, skip_sa=2)
    items = grading.build_items(quiz, vault.load_quiz(qid)["attempts"][pid])
    assert [i["situation"] for i in items] == ["Situation 3", "Situation 4", "Situation 5", "Situation 6"]
    assert items[0]["answer_key"] == "key 3" and items[0]["friend_answer"] == "my answer 2"


# ---- leaderboard, analytics, export ---------------------------------------
def test_leaderboard_podium_order_and_tiebreak(vault):
    qid = build_quiz(vault, players=("A", "B", "C", "D", "E", "Off"))["quiz_id"]
    quiz = vault.load_quiz(qid)
    ids = {p["name"]: p["id"] for p in quiz["players"]}
    service.update_player(vault, qid, ids["Off"], {"active": False})
    for name, correct in (("A", 4), ("B", 8), ("C", 4), ("D", 6)):
        play_through(vault, qid, ids[name], mcq_correct=correct)
        service.grade_attempt(vault, qid, ids[name], grader=fixed_grader(1))

    def set_time(doc):
        for name, seconds in (("A", 90), ("B", 300), ("C", 45), ("D", 60)):
            a = doc["attempts"][ids[name]]
            a["started_at"] = "2026-10-06T10:00:00+00:00"
            a["completed_at"] = f"2026-10-06T10:{seconds // 60:02d}:{seconds % 60:02d}+00:00"

    vault.mutate_quiz(qid, set_time)
    board = game.build_leaderboard(vault.load_quiz(qid))
    assert [(r["rank"], r["name"], r["total"]) for r in board["ranked"]] == [
        (1, "B", 12), (2, "D", 10), (3, "C", 8), (4, "A", 8)]       # C beats A on time
    assert [r["name"] for r in board["not_played"]] == ["E"]         # inactive player hidden
    stats = game.analytics(vault.load_quiz(qid))
    assert (stats["players"], stats["started"], stats["completed"]) == (5, 4, 4)
    assert (stats["highest"], stats["lowest"], stats["average"]) == (12, 8, 9.5)
    assert stats["completion_rate"] == 80.0 and stats["avg_duration"] == 123
    assert game.format_duration(123) == "2m 03s"


def test_csv_export_has_no_secrets(vault):
    made = build_quiz(vault)
    qid = made["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid = quiz["players"][0]["id"]
    service.update_player(vault, qid, quiz["players"][1]["id"], {"name": "=HYPERLINK(1)"})
    play_through(vault, qid, pid, mcq_correct=7)
    service.grade_attempt(vault, qid, pid, grader=fixed_grader(2))
    csv_text = service.results_csv(vault.load_quiz(qid))
    lines = csv_text.strip().splitlines()
    assert lines[0] == ("Player,MCQ Score,Short Answer Score,Total Score,Percentage,Status,"
                        "Started At,Completed At")
    assert lines[1].startswith("Rahul,7,8,15,93.75,Completed,")
    assert lines[2].startswith("'=HYPERLINK(1),,,,,Not started")
    assert "secret123" not in csv_text and made["quiz_password"] not in csv_text
    assert "scrypt" not in csv_text


# ---- memories & explanations ----------------------------------------------
def test_memory_rules(vault, backend):
    made = build_quiz(vault)
    qid = made["quiz_id"]
    quiz = vault.load_quiz(qid)
    pid, other = quiz["players"][0]["id"], quiz["players"][1]["id"]
    photo = service.store_upload(vault, qid, b"PHOTO-OF-US" * 40, "image/jpeg", "us.jpg")
    with pytest.raises(service.ServiceError):                       # has not played
        service.save_memory(vault, qid, pid, "like", "dislike", "story", photo)
    play_through(vault, qid, pid, submit=False)
    service.start_attempt(vault, qid, other)
    with pytest.raises(service.ServiceError, match="Finish the quiz"):
        service.save_memory(vault, qid, other, "like", "dislike", "story", None)
    with pytest.raises(service.ServiceError, match="Please fill in"):
        service.save_memory(vault, qid, pid, "like", "  ", "story", photo)
    service.save_memory(vault, qid, pid, "Loyal", "Always late", "Trek to Kudremukh", photo)
    with pytest.raises(service.ServiceError, match="already shared"):
        service.save_memory(vault, qid, pid, "again", "again", "again", None)
    memory = service.get_memory(vault.load_quiz(qid), pid)
    assert (memory["player_name"], memory["like"], memory["dislike"]) == ("Rahul", "Loyal", "Always late")

    # encrypted at rest, survives attempt reset, player deletion and a restart
    vault.flush_all()
    dump = b"".join(backend.files.values())
    assert b"Always late" not in dump and b"PHOTO-OF-US" not in dump and b"Kudremukh" not in dump
    service.reset_attempt(vault, qid, pid)
    service.delete_player(vault, qid, pid)
    reborn = Vault(backend, vault._key)
    assert [m["story"] for m in service.list_memories(reborn.load_quiz(qid))] == ["Trek to Kudremukh"]
    assert reborn.get_media(qid, photo["id"]) == b"PHOTO-OF-US" * 40

    # only an explicit delete removes it (and its photo)
    service.delete_memory(vault, qid, pid)
    assert service.list_memories(vault.load_quiz(qid)) == []
    assert Vault(backend, vault._key).get_media(qid, photo["id"]) is None
    with pytest.raises(service.ServiceError):
        service.delete_memory(vault, qid, pid)


def test_mcq_explanation_is_saved_and_duplicated(vault):
    qid = build_quiz(vault, publish=False)["quiz_id"]
    quiz = vault.load_quiz(qid)
    assert quiz["mcqs"][0]["explanation"] == "why right 1"
    service.duplicate_question(vault, qid, "mcq", quiz["mcqs"][0]["id"])
    assert vault.load_quiz(qid)["mcqs"][1]["explanation"] == "why right 1"
    service.save_mcq(vault, qid, quiz["mcqs"][0]["id"], {
        "text": "changed", "correct_index": 1, "explanation": "x" * 900,
        "options": [{"text": "a"}, {"text": "b"}]})
    assert len(vault.load_quiz(qid)["mcqs"][0]["explanation"]) == 400
