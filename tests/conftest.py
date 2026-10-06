import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ff import crypto, service  # noqa: E402
from ff.storage import MemoryBackend, Vault  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("SUPER_ADMIN_USERNAME", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture
def vault(backend):
    return Vault(backend, crypto.normalize_master_key(crypto.generate_master_key()))


def build_quiz(vault, username="hkadmin", n_mcq=8, n_sa=4, players=("Rahul", "Arjun", "Meera"),
               publish=True):
    """Create a ready-to-play quiz. Option index 0 is always correct."""
    made = service.create_quiz(vault, "Owner " + username, username, "secret123", f"{username}'s quiz")
    qid = made["quiz_id"]
    for name in players:
        service.add_player(vault, qid, {"name": name})
    for i in range(n_mcq):
        service.save_mcq(vault, qid, None, {
            "text": f"MCQ {i + 1}", "correct_index": 0, "explanation": f"why right {i + 1}",
            "options": [{"text": f"right {i}"}, {"text": f"wrong-a {i}"},
                        {"text": f"wrong-b {i}"}, {"text": f"wrong-c {i}"}]})
    for i in range(n_sa):
        service.save_sa(vault, qid, None, {"text": f"Situation {i + 1}", "answer_key": f"key {i + 1}"})
    if publish:
        service.set_published(vault, qid, True)
    return made


def play_through(vault, quiz_id, player_id, mcq_correct=8, skip_mcq=0, skip_sa=0, submit=True):
    """Answer the quiz: skip the first N of each section, then answer."""
    service.start_attempt(vault, quiz_id, player_id)
    quiz = vault.load_quiz(quiz_id)
    answered = 0
    for i, q in enumerate(quiz["mcqs"]):
        if i < skip_mcq:
            service.play(vault, quiz_id, player_id, "skip_mcq", q["id"])
            continue
        if answered >= 8:
            break
        option = q["options"][0 if answered < mcq_correct else 1]["id"]
        service.play(vault, quiz_id, player_id, "answer_mcq", q["id"], option)
        answered += 1
    answered = 0
    for i, q in enumerate(quiz["sas"]):
        if i < skip_sa:
            service.play(vault, quiz_id, player_id, "skip_sa", q["id"])
            continue
        if answered >= 4:
            break
        service.play(vault, quiz_id, player_id, "answer_sa", q["id"], f"my answer {i}")
        answered += 1
    if submit:
        return service.submit_attempt(vault, quiz_id, player_id)


def fixed_grader(score):
    def grader(items):
        return {item["id"]: {"score": score, "reason": "test"} for item in items}
    return grader
