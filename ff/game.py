"""Pure game rules: question pools, skips, scoring, leaderboard.

Everything here works on plain dicts (the quiz document and an attempt) and
has no I/O, so the same rules run for real attempts, admin previews and tests.

Pool rule
---------
The admin may add more questions than are scored. A player walks the pool in
order and may answer or skip each one. Only the first ``required`` answered
questions count, so the number of skips available is ``pool - required``.
A skipped question never comes back.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import (HIGH_SCORE_MIN, MAX_ANSWER_CHARS, MAX_SCORE, MCQ_POINTS,
                     MCQ_REQUIRED, SA_POINTS, SA_REQUIRED)

NOT_STARTED = "NOT_STARTED"
IN_PROGRESS = "IN_PROGRESS"
SUBMITTED = "SUBMITTED"
AWAITING_REVIEW = "AWAITING_REVIEW"
COMPLETED = "COMPLETED"

STATUS_LABELS = {
    NOT_STARTED: "Not started",
    IN_PROGRESS: "In progress",
    SUBMITTED: "Submitted",
    AWAITING_REVIEW: "Awaiting review",
    COMPLETED: "Completed",
}


class GameError(ValueError):
    """A rule was violated (user-facing message)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_attempt(attempt_id: str, player_id: str) -> dict:
    return {
        "id": attempt_id, "player_id": player_id, "status": IN_PROGRESS,
        "started_at": now_iso(), "completed_at": None,
        "mcq_answers": {}, "mcq_skipped": [],
        "sa_answers": {}, "sa_skipped": [],
        "grades": {}, "ai_tries": 0,
    }


# ---- pool walking ----------------------------------------------------------
_SECTIONS = {
    "mcq": ("mcqs", "mcq_answers", "mcq_skipped", MCQ_REQUIRED),
    "sa": ("sas", "sa_answers", "sa_skipped", SA_REQUIRED),
}


def section_state(quiz: dict, attempt: dict, section: str) -> dict:
    """Where the player is in a section ('mcq' or 'sa')."""
    pool_key, ans_key, skip_key, required = _SECTIONS[section]
    pool = quiz[pool_key]
    answered, skipped = attempt[ans_key], attempt[skip_key]
    done = len(answered) >= required
    current, position = None, None
    if not done:
        for i, question in enumerate(pool):
            if question["id"] not in answered and question["id"] not in skipped:
                current, position = question, i + 1
                break
    skips_total = max(0, len(pool) - required)
    return {
        "pool_size": len(pool), "required": required,
        "answered": len(answered), "skips_used": len(skipped),
        "skips_left": max(0, skips_total - len(skipped)), "skips_total": skips_total,
        "done": done, "current": current, "position": position,
    }


def _require_current(quiz: dict, attempt: dict, section: str, question_id: str) -> dict:
    if attempt["status"] != IN_PROGRESS:
        raise GameError("This attempt has already been submitted.")
    if section == "sa" and not section_state(quiz, attempt, "mcq")["done"]:
        raise GameError("Finish Section 1 first.")
    state = section_state(quiz, attempt, section)
    if state["done"] or state["current"] is None:
        raise GameError("This section is already complete.")
    if state["current"]["id"] != question_id:
        raise GameError("That question is no longer active.")
    return state


def answer_mcq(quiz: dict, attempt: dict, question_id: str, option_id: str) -> None:
    state = _require_current(quiz, attempt, "mcq", question_id)
    if option_id not in {o["id"] for o in state["current"]["options"]}:
        raise GameError("Pick one of the options.")
    attempt["mcq_answers"][question_id] = option_id


def answer_sa(quiz: dict, attempt: dict, question_id: str, text: str) -> None:
    _require_current(quiz, attempt, "sa", question_id)
    text = (text or "").strip()
    if not text:
        raise GameError("Type an answer, or skip this one.")
    attempt["sa_answers"][question_id] = text[:MAX_ANSWER_CHARS]


def skip_question(quiz: dict, attempt: dict, section: str, question_id: str) -> None:
    state = _require_current(quiz, attempt, section, question_id)
    if state["skips_left"] <= 0:
        raise GameError("No skips left - you have to answer this one.")
    attempt[_SECTIONS[section][2]].append(question_id)


def ready_to_submit(quiz: dict, attempt: dict) -> bool:
    return (section_state(quiz, attempt, "mcq")["done"]
            and section_state(quiz, attempt, "sa")["done"])


def submit(quiz: dict, attempt: dict) -> None:
    if attempt["status"] != IN_PROGRESS:
        raise GameError("This quiz has already been submitted.")
    if not ready_to_submit(quiz, attempt):
        raise GameError("Answer all required questions first.")
    attempt["status"] = SUBMITTED
    attempt["completed_at"] = now_iso()


# ---- scoring (always recomputed from the stored raw answers) ---------------
def counted_mcq_ids(attempt: dict) -> list[str]:
    return list(attempt["mcq_answers"])[:MCQ_REQUIRED]


def counted_sa_ids(attempt: dict) -> list[str]:
    return list(attempt["sa_answers"])[:SA_REQUIRED]


def mcq_score(quiz: dict, attempt: dict) -> int:
    correct = {q["id"]: q.get("correct_option_id") for q in quiz["mcqs"]}
    return MCQ_POINTS * sum(
        1 for qid in counted_mcq_ids(attempt)
        if correct.get(qid) is not None and attempt["mcq_answers"][qid] == correct[qid]
    )


def clamp_grade(score) -> int:
    try:
        return max(0, min(SA_POINTS, int(score)))
    except (TypeError, ValueError):
        return 0


def sa_score(attempt: dict) -> int:
    grades = attempt.get("grades", {})
    return sum(clamp_grade(grades[qid]["score"]) for qid in counted_sa_ids(attempt) if qid in grades)


def fully_graded(attempt: dict) -> bool:
    ids = counted_sa_ids(attempt)
    return len(ids) >= SA_REQUIRED and all(qid in attempt.get("grades", {}) for qid in ids)


def outcome_for(total: int) -> str:
    """HIGH needs >= 60% of 16 = 9.6, i.e. an integer total of 10 or more."""
    return "HIGH" if total >= HIGH_SCORE_MIN else "LOW"


def score_summary(quiz: dict, attempt: dict) -> dict:
    mcq, sa = mcq_score(quiz, attempt), sa_score(attempt)
    total = mcq + sa
    return {
        "mcq": mcq, "mcq_max": MCQ_REQUIRED * MCQ_POINTS,
        "sa": sa, "sa_max": SA_REQUIRED * SA_POINTS,
        "total": total, "max": MAX_SCORE,
        "percentage": round(total / MAX_SCORE * 100, 2),
        "outcome": outcome_for(total),
        "final": attempt["status"] == COMPLETED,
    }


def set_grade(attempt: dict, question_id: str, score, by: str, reason: str = "", notes: str = "") -> None:
    if question_id not in counted_sa_ids(attempt):
        raise GameError("That answer is not part of this attempt.")
    previous = attempt["grades"].get(question_id, {})
    attempt["grades"][question_id] = {
        "score": clamp_grade(score), "by": by,
        "reason": reason if by == "ai" else previous.get("reason", ""),
        "notes": notes if by == "admin" else previous.get("notes", ""),
    }


def finalize_if_graded(attempt: dict) -> None:
    if attempt["status"] in (SUBMITTED, AWAITING_REVIEW, COMPLETED):
        attempt["status"] = COMPLETED if fully_graded(attempt) else AWAITING_REVIEW


def duration_seconds(attempt: dict) -> int | None:
    try:
        start = datetime.fromisoformat(attempt["started_at"])
        end = datetime.fromisoformat(attempt["completed_at"])
        return max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError, KeyError):
        return None


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


# ---- leaderboard -----------------------------------------------------------
def build_leaderboard(quiz: dict) -> dict:
    """Ranked list of finished players.

    Order: higher score first; ties broken by faster completion time, then by
    who finished earlier. Ranks are 1, 2, 3... with no gaps.
    """
    ranked, pending, not_played = [], [], []
    for player in quiz["players"]:
        if not player.get("active", True):
            continue
        attempt = quiz["attempts"].get(player["id"])
        base = {"player_id": player["id"], "name": player.get("display_name") or player["name"],
                "emoji": player.get("emoji") or "", "dp": player.get("dp")}
        if not attempt:
            not_played.append({**base, "status": NOT_STARTED})
        elif attempt["status"] == COMPLETED:
            summary = score_summary(quiz, attempt)
            ranked.append({**base, **summary, "status": COMPLETED,
                           "duration": duration_seconds(attempt),
                           "completed_at": attempt.get("completed_at") or ""})
        else:
            pending.append({**base, "status": attempt["status"]})
    ranked.sort(key=lambda r: (-r["total"], r["duration"] if r["duration"] is not None else 10**9,
                               r["completed_at"]))
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return {"ranked": ranked, "pending": pending, "not_played": not_played}


def analytics(quiz: dict) -> dict:
    players = [p for p in quiz["players"] if p.get("active", True)]
    attempts = [quiz["attempts"][p["id"]] for p in players if p["id"] in quiz["attempts"]]
    done = [a for a in attempts if a["status"] == COMPLETED]
    totals = [score_summary(quiz, a)["total"] for a in done]
    durations = [d for d in (duration_seconds(a) for a in attempts if a.get("completed_at")) if d is not None]
    return {
        "players": len(players), "started": len(attempts), "completed": len(done),
        "awaiting": sum(1 for a in attempts if a["status"] in (SUBMITTED, AWAITING_REVIEW)),
        "average": round(sum(totals) / len(totals), 2) if totals else None,
        "highest": max(totals) if totals else None,
        "lowest": min(totals) if totals else None,
        "completion_rate": round(len(done) / len(players) * 100, 1) if players else 0.0,
        "avg_duration": int(sum(durations) / len(durations)) if durations else None,
    }
