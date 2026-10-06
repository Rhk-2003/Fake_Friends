"""Quiz management: accounts, players, questions, publishing, attempts.

Every function takes the vault and a ``quiz_id``; a quiz document is the
isolation boundary - nothing in one quiz can reference another.
"""
from __future__ import annotations

import copy
import re
import secrets
import uuid

from . import config, crypto, game, grading, media
from .storage import NotFound, StorageError, Vault

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,24}$")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_DUMMY_HASH = crypto.hash_password("not-a-real-password")


class ServiceError(ValueError):
    """User-facing validation / permission error."""


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _code(n: int) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


def _clean(text, limit: int) -> str:
    return (text or "").strip()[:limit]


# =========================================================================
# Accounts
# =========================================================================
def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def super_username(vault: Vault) -> str | None:
    configured = config.get("SUPER_ADMIN_USERNAME")
    if configured:
        return normalize_username(configured)
    return vault.load_index().get("super")


def is_super(vault: Vault, username: str) -> bool:
    return bool(username) and super_username(vault) == normalize_username(username)


def create_quiz(vault: Vault, owner_name: str, username: str, password: str, quiz_name: str) -> dict:
    owner_name, quiz_name = _clean(owner_name, 60), _clean(quiz_name, 80)
    username = normalize_username(username)
    if not owner_name:
        raise ServiceError("Tell us your name.")
    if not USERNAME_RE.match(username):
        raise ServiceError("Username: 3-24 characters, letters, numbers, dot, dash or underscore.")
    if len(password or "") < 6:
        raise ServiceError("Password must be at least 6 characters.")
    if not quiz_name:
        raise ServiceError("Give your quiz a name.")

    quiz_id = _code(8)
    while vault.load_quiz(quiz_id) is not None:
        quiz_id = _code(8)

    def reserve(index: dict) -> bool:
        if username in index["admins"]:
            raise ServiceError("That username is taken - pick another.")
        index["admins"][username] = {"quiz_id": quiz_id, "name": owner_name,
                                     "created_at": game.now_iso()}
        if not index.get("super"):
            index["super"] = username   # the very first admin is the super-admin
        return index["super"] == username

    vault.mutate_index(reserve)
    quiz_password = _code(6)
    doc = {
        "id": quiz_id, "version": 1, "created_at": game.now_iso(),
        "owner": {"name": owner_name, "username": username,
                  "password_hash": crypto.hash_password(password)},
        "settings": {
            "name": quiz_name, "description": "", "password": quiz_password,
            "intro_message": "", "rules": "", "emoji": "🎭", "cover": None, "logo": None,
            "published": False, "memory_wall": False,
            "high_message": "Okay, they actually know you. 👀",
            "low_message": "Uh-oh. Maybe you need to have a conversation. 👀",
        },
        "players": [], "mcqs": [], "sas": [], "attempts": {}, "memories": {},
    }
    try:
        vault.create_quiz(doc)
    except Exception:
        vault.mutate_index(lambda index: index["admins"].pop(username, None))
        raise
    return {"quiz_id": quiz_id, "quiz_password": quiz_password, "username": username,
            "is_super": is_super(vault, username)}


def authenticate_admin(vault: Vault, username: str, password: str) -> str | None:
    """Return the admin's quiz id, or None. Constant-ish time for unknown users."""
    username = normalize_username(username)
    entry = vault.load_index()["admins"].get(username)
    quiz = vault.load_quiz(entry["quiz_id"]) if entry else None
    stored = quiz["owner"]["password_hash"] if quiz else _DUMMY_HASH
    ok = crypto.verify_password(password or "", stored)
    return quiz["id"] if (quiz and ok and quiz["owner"]["username"] == username) else None


def owns_quiz(vault: Vault, username: str, quiz_id: str) -> bool:
    entry = vault.load_index()["admins"].get(normalize_username(username))
    return bool(entry) and entry["quiz_id"] == quiz_id


def change_admin_password(vault: Vault, quiz_id: str, current: str, new: str) -> None:
    if len(new or "") < 6:
        raise ServiceError("Password must be at least 6 characters.")

    def fn(doc):
        if not crypto.verify_password(current or "", doc["owner"]["password_hash"]):
            raise ServiceError("Current password is wrong.")
        doc["owner"]["password_hash"] = crypto.hash_password(new)

    vault.mutate_quiz(quiz_id, fn)


def check_quiz_password(quiz: dict, password: str) -> bool:
    return crypto.constant_time_equal((password or "").strip().upper(),
                                      quiz["settings"]["password"].upper())


def list_all_quizzes(vault: Vault) -> list[dict]:
    """Super-admin overview of every quiz on this deployment."""
    rows = []
    for username, entry in vault.load_index()["admins"].items():
        quiz = vault.load_quiz(entry["quiz_id"])
        if not quiz:
            continue
        stats = game.analytics(quiz)
        rows.append({"quiz_id": quiz["id"], "name": quiz["settings"]["name"],
                     "owner": quiz["owner"]["name"], "username": username,
                     "created_at": quiz["created_at"], "published": quiz["settings"]["published"],
                     "players": stats["players"], "started": stats["started"],
                     "completed": stats["completed"], "mcqs": len(quiz["mcqs"]),
                     "sas": len(quiz["sas"])})
    rows.sort(key=lambda r: r["created_at"])
    return rows


def delete_quiz(vault: Vault, quiz_id: str, username: str) -> None:
    if is_super(vault, username):
        raise ServiceError("The super-admin account cannot be deleted from the app.")
    quiz = vault.load_quiz(quiz_id)
    if not quiz or not owns_quiz(vault, username, quiz_id):
        raise ServiceError("Not allowed.")
    for ref in iter_media(quiz):
        vault.delete_media(quiz_id, ref["id"])
    vault.delete_quiz(quiz_id)
    vault.mutate_index(lambda index: index["admins"].pop(normalize_username(username), None))


# =========================================================================
# Media references
# =========================================================================
def store_upload(vault: Vault, quiz_id: str, data: bytes, mime: str, name: str) -> dict:
    media_id = media.new_media_id()
    vault.put_media(quiz_id, media_id, data)
    return {"type": "upload", "id": media_id, "mime": mime,
            "name": media.safe_name(name), "size": len(data)}


def url_ref(url: str) -> dict:
    return {"type": "url", "url": media.check_url(url)}


def iter_media(quiz: dict):
    """Every uploaded media reference in a quiz document."""
    def uploads(*refs):
        for ref in refs:
            if ref and ref.get("type") == "upload":
                yield ref

    yield from uploads(quiz["settings"].get("cover"), quiz["settings"].get("logo"))
    for p in quiz["players"]:
        yield from uploads(p.get("dp"), p.get("intro_video"), p.get("low_video"), p.get("high_video"))
    for q in quiz["mcqs"]:
        yield from uploads(q.get("image"), *[o.get("image") for o in q["options"]])
    for q in quiz["sas"]:
        yield from uploads(q.get("image"))
    for memory in quiz.get("memories", {}).values():
        yield from uploads(memory.get("photo"))


def _gc_media(vault: Vault, quiz_id: str, before: dict, after: dict) -> None:
    """Delete uploads that are no longer referenced anywhere."""
    still = {ref["id"] for ref in iter_media(after)}
    for ref in iter_media(before):
        if ref["id"] not in still:
            vault.delete_media(quiz_id, ref["id"])


def _edit(vault: Vault, quiz_id: str, fn):
    """Mutate a quiz, then garbage-collect media the change orphaned."""
    before = vault.load_quiz(quiz_id)
    if before is None:
        raise NotFound("Quiz not found.")
    result = vault.mutate_quiz(quiz_id, fn)
    _gc_media(vault, quiz_id, before, vault.load_quiz(quiz_id))
    return result


# =========================================================================
# Settings
# =========================================================================
SETTING_LIMITS = {"name": 80, "description": 300, "intro_message": 500, "rules": 800,
                  "emoji": 8, "high_message": 200, "low_message": 200}


def update_settings(vault: Vault, quiz_id: str, fields: dict) -> None:
    def fn(doc):
        s = doc["settings"]
        for key, limit in SETTING_LIMITS.items():
            if key in fields:
                s[key] = _clean(fields[key], limit)
        if not s["name"]:
            raise ServiceError("The quiz needs a name.")
        if "password" in fields:
            pw = _clean(fields["password"], 40)
            if len(pw) < 4:
                raise ServiceError("Quiz password must be at least 4 characters.")
            s["password"] = pw
        for key in ("cover", "logo"):
            if key in fields:
                s[key] = fields[key]
        if "memory_wall" in fields:
            s["memory_wall"] = bool(fields["memory_wall"])

    _edit(vault, quiz_id, fn)


# =========================================================================
# Players
# =========================================================================
PLAYER_MEDIA = ("dp", "intro_video", "low_video", "high_video")


def _apply_player(player: dict, fields: dict) -> None:
    if "name" in fields:
        player["name"] = _clean(fields["name"], 40)
        if not player["name"]:
            raise ServiceError("Player name is required.")
    if "display_name" in fields:
        player["display_name"] = _clean(fields["display_name"], 40)
    if "emoji" in fields:
        player["emoji"] = _clean(fields["emoji"], 8)
    if "active" in fields:
        player["active"] = bool(fields["active"])
    for key in PLAYER_MEDIA:
        if key in fields:
            player[key] = fields[key]


def add_player(vault: Vault, quiz_id: str, fields: dict) -> str:
    def fn(doc):
        if len(doc["players"]) >= config.MAX_PLAYERS:
            raise ServiceError(f"A quiz can have at most {config.MAX_PLAYERS} players.")
        player = {"id": _id(), "name": "", "display_name": "", "emoji": "", "active": True,
                  **{k: None for k in PLAYER_MEDIA}}
        _apply_player(player, {"name": "", **fields})
        if any(p["name"].lower() == player["name"].lower() for p in doc["players"]):
            raise ServiceError("There is already a player with that name.")
        doc["players"].append(player)
        return player["id"]

    return _edit(vault, quiz_id, fn)


def update_player(vault: Vault, quiz_id: str, player_id: str, fields: dict) -> None:
    def fn(doc):
        _apply_player(_find(doc["players"], player_id, "Player"), fields)

    _edit(vault, quiz_id, fn)


def delete_player(vault: Vault, quiz_id: str, player_id: str) -> None:
    def fn(doc):
        _find(doc["players"], player_id, "Player")
        doc["players"] = [p for p in doc["players"] if p["id"] != player_id]
        doc["attempts"].pop(player_id, None)

    _edit(vault, quiz_id, fn)


def _find(items: list, item_id: str, what: str) -> dict:
    for item in items:
        if item["id"] == item_id:
            return item
    raise ServiceError(f"{what} not found.")


def get_player(quiz: dict, player_id: str) -> dict | None:
    return next((p for p in quiz["players"] if p["id"] == player_id), None)


# =========================================================================
# Questions
# =========================================================================
def questions_locked(quiz: dict) -> bool:
    """Questions freeze once any player has started, to protect results."""
    return bool(quiz["attempts"])


def _require_unlocked(doc: dict) -> None:
    if questions_locked(doc):
        raise ServiceError("Questions are locked because players have started this quiz.")


def save_mcq(vault: Vault, quiz_id: str, question_id: str | None, data: dict) -> str:
    """Create (question_id=None) or update an MCQ.

    ``data``: text, emoji, image, options=[{id?, text, emoji, image}], correct_index,
    explanation (shown to the player after they answer).
    """
    text = _clean(data.get("text"), 300)
    options = [{"id": o.get("id") or _id(), "text": _clean(o.get("text"), 120),
                "emoji": _clean(o.get("emoji"), 8), "image": o.get("image")}
               for o in data.get("options", [])]
    if not text:
        raise ServiceError("Write the question.")
    if not config.MIN_OPTIONS <= len(options) <= config.MAX_OPTIONS:
        raise ServiceError(f"An MCQ needs {config.MIN_OPTIONS}-{config.MAX_OPTIONS} options.")
    if any(not o["text"] for o in options):
        raise ServiceError("Every option needs text.")
    if len({o["text"].lower() for o in options}) != len(options):
        raise ServiceError("Two options have the same text.")
    correct = data.get("correct_index")
    if correct is None or not 0 <= int(correct) < len(options):
        raise ServiceError("Choose the correct answer.")

    def fn(doc):
        _require_unlocked(doc)
        record = {"text": text, "emoji": _clean(data.get("emoji"), 8), "image": data.get("image"),
                  "options": options, "correct_option_id": options[int(correct)]["id"],
                  "explanation": _clean(data.get("explanation"), 400)}
        if question_id:
            _find(doc["mcqs"], question_id, "Question").update(record)
            return question_id
        if len(doc["mcqs"]) >= config.MAX_MCQ_POOL:
            raise ServiceError(f"A quiz can have at most {config.MAX_MCQ_POOL} MCQs.")
        doc["mcqs"].append({"id": _id(), **record})
        return doc["mcqs"][-1]["id"]

    return _edit(vault, quiz_id, fn)


def save_sa(vault: Vault, quiz_id: str, question_id: str | None, data: dict) -> str:
    text, key = _clean(data.get("text"), 500), _clean(data.get("answer_key"), 600)
    if not text:
        raise ServiceError("Write the situation.")
    if not key:
        raise ServiceError("Write the answer key - the AI grades against it.")

    def fn(doc):
        _require_unlocked(doc)
        record = {"text": text, "emoji": _clean(data.get("emoji"), 8), "image": data.get("image"),
                  "answer_key": key, "guidance": _clean(data.get("guidance"), 600)}
        if question_id:
            _find(doc["sas"], question_id, "Question").update(record)
            return question_id
        if len(doc["sas"]) >= config.MAX_SA_POOL:
            raise ServiceError(f"A quiz can have at most {config.MAX_SA_POOL} situations.")
        doc["sas"].append({"id": _id(), **record})
        return doc["sas"][-1]["id"]

    return _edit(vault, quiz_id, fn)


def _pool(section: str) -> str:
    if section not in ("mcq", "sa"):
        raise ServiceError("Unknown section.")
    return "mcqs" if section == "mcq" else "sas"


def delete_question(vault: Vault, quiz_id: str, section: str, question_id: str) -> None:
    def fn(doc):
        _require_unlocked(doc)
        _find(doc[_pool(section)], question_id, "Question")
        doc[_pool(section)] = [q for q in doc[_pool(section)] if q["id"] != question_id]

    _edit(vault, quiz_id, fn)


def move_question(vault: Vault, quiz_id: str, section: str, question_id: str, delta: int) -> None:
    def fn(doc):
        _require_unlocked(doc)
        pool = doc[_pool(section)]
        i = pool.index(_find(pool, question_id, "Question"))
        j = i + delta
        if 0 <= j < len(pool):
            pool[i], pool[j] = pool[j], pool[i]

    vault.mutate_quiz(quiz_id, fn)


def duplicate_question(vault: Vault, quiz_id: str, section: str, question_id: str) -> None:
    def fn(doc):
        _require_unlocked(doc)
        pool = doc[_pool(section)]
        limit = config.MAX_MCQ_POOL if section == "mcq" else config.MAX_SA_POOL
        if len(pool) >= limit:
            raise ServiceError("The question pool is full.")
        source = _find(pool, question_id, "Question")
        clone = copy.deepcopy(source)
        clone["id"] = _id()
        clone["text"] = (clone["text"] + " (copy)")[:300]
        clone["image"] = None                      # uploads are not shared between questions
        if section == "mcq":
            mapping = {}
            for option in clone["options"]:
                mapping[option["id"]] = option["id"] = _id()
                option["image"] = None
            clone["correct_option_id"] = mapping.get(source["correct_option_id"])
        pool.insert(pool.index(source) + 1, clone)

    vault.mutate_quiz(quiz_id, fn)


# =========================================================================
# Validation & publishing
# =========================================================================
def validate_quiz(quiz: dict) -> list[dict]:
    s = quiz["settings"]
    active = [p for p in quiz["players"] if p.get("active", True)]
    mcqs, sas = quiz["mcqs"], quiz["sas"]
    bad_mcq = [i + 1 for i, q in enumerate(mcqs)
               if len(q["options"]) < config.MIN_OPTIONS
               or q.get("correct_option_id") not in {o["id"] for o in q["options"]}]
    no_key = [i + 1 for i, q in enumerate(sas) if not q.get("answer_key")]

    def item(label, ok, detail=""):
        return {"label": label, "ok": bool(ok), "detail": detail}

    return [
        item("Admin account exists", quiz["owner"].get("password_hash")),
        item("Quiz has a name", s.get("name")),
        item("Quiz password is set", len(s.get("password", "")) >= 4),
        item("At least one active player", active, f"{len(active)} active player(s)"),
        item(f"At least {config.MCQ_REQUIRED} MCQs", len(mcqs) >= config.MCQ_REQUIRED,
             f"{len(mcqs)} in the pool - the first {config.MCQ_REQUIRED} a player answers count"),
        item("Every MCQ has 2+ options and a correct answer", not bad_mcq,
             f"Fix question(s): {', '.join(map(str, bad_mcq))}" if bad_mcq else ""),
        item(f"At least {config.SA_REQUIRED} situations", len(sas) >= config.SA_REQUIRED,
             f"{len(sas)} in the pool - the first {config.SA_REQUIRED} a player answers count"),
        item("Every situation has an answer key", not no_key,
             f"Add a key to situation(s): {', '.join(map(str, no_key))}" if no_key else ""),
    ]


def set_published(vault: Vault, quiz_id: str, published: bool) -> None:
    def fn(doc):
        if published:
            failed = [c["label"] for c in validate_quiz(doc) if not c["ok"]]
            if failed:
                raise ServiceError("Not ready to publish: " + "; ".join(failed))
        doc["settings"]["published"] = bool(published)

    vault.mutate_quiz(quiz_id, fn)


# =========================================================================
# Attempts
# =========================================================================
def start_attempt(vault: Vault, quiz_id: str, player_id: str) -> dict:
    """Create the attempt, or return the existing one (never a duplicate)."""
    def fn(doc):
        if not doc["settings"]["published"]:
            raise ServiceError("This quiz is not live.")
        player = _find(doc["players"], player_id, "Player")
        if not player.get("active", True):
            raise ServiceError("This player is not active.")
        if player_id not in doc["attempts"]:
            doc["attempts"][player_id] = game.new_attempt(_id(), player_id)
        return copy.deepcopy(doc["attempts"][player_id])

    return vault.mutate_quiz(quiz_id, fn)


def _attempt(doc: dict, player_id: str) -> dict:
    attempt = doc["attempts"].get(player_id)
    if attempt is None:
        raise ServiceError("No attempt found for this player.")
    return attempt


def play(vault: Vault, quiz_id: str, player_id: str, action: str, question_id: str,
         value: str = "") -> dict:
    """Record one answer/skip and save it to permanent storage straight away.

    Every answer is a saved draft: after a refresh, a dropped connection or a
    server restart the player continues from the next unanswered question. If
    the storage write fails, the answer stays in memory and is retried.
    """
    def fn(doc):
        attempt = _attempt(doc, player_id)
        if action == "answer_mcq":
            game.answer_mcq(doc, attempt, question_id, value)
        elif action == "answer_sa":
            game.answer_sa(doc, attempt, question_id, value)
        elif action in ("skip_mcq", "skip_sa"):
            game.skip_question(doc, attempt, action.split("_")[1], question_id)
        else:
            raise ServiceError("Unknown action.")
        return copy.deepcopy(attempt)

    return vault.mutate_quiz(quiz_id, fn, durable=True)


def checkpoint(vault: Vault) -> None:
    vault.flush_all()


def submit_attempt(vault: Vault, quiz_id: str, player_id: str) -> dict:
    def fn(doc):
        attempt = _attempt(doc, player_id)
        if attempt["status"] == game.IN_PROGRESS:   # idempotent: no duplicate submissions
            game.submit(doc, attempt)
        return copy.deepcopy(attempt)

    return vault.mutate_quiz(quiz_id, fn)


def grade_into(quiz: dict, attempt: dict, grader=None) -> bool:
    """Grade an attempt dict in place with AI. Returns True when it is final."""
    grader = grader or grading.grade_short_answers
    attempt["ai_tries"] = attempt.get("ai_tries", 0) + 1
    try:
        results = grader(grading.build_items(quiz, attempt))
    except grading.GradingError:
        game.finalize_if_graded(attempt)
        return False
    for qid, grade in results.items():
        if attempt["grades"].get(qid, {}).get("by") == "admin":
            continue                                   # never overwrite a human grade
        game.set_grade(attempt, qid, grade["score"], "ai", reason=grade.get("reason", ""))
    game.finalize_if_graded(attempt)
    return attempt["status"] == game.COMPLETED


def grade_attempt(vault: Vault, quiz_id: str, player_id: str, grader=None) -> dict:
    """AI-grade a submitted attempt. The network call happens outside any lock."""
    quiz = vault.load_quiz(quiz_id)
    if quiz is None:
        raise NotFound("Quiz not found.")
    attempt = _attempt(quiz, player_id)
    if attempt["status"] not in (game.SUBMITTED, game.AWAITING_REVIEW):
        return attempt
    graded = copy.deepcopy(attempt)
    grade_into(quiz, graded, grader)

    def fn(doc):
        live = _attempt(doc, player_id)
        if live["id"] != graded["id"] or live["status"] == game.COMPLETED:
            return copy.deepcopy(live)                 # reset or finished meanwhile
        live["ai_tries"] = graded["ai_tries"]
        for qid, grade in graded["grades"].items():
            if live["grades"].get(qid, {}).get("by") != "admin":
                live["grades"][qid] = grade
        game.finalize_if_graded(live)
        return copy.deepcopy(live)

    return vault.mutate_quiz(quiz_id, fn)


def manual_grade(vault: Vault, quiz_id: str, player_id: str, question_id: str,
                 score: int, notes: str = "") -> None:
    def fn(doc):
        attempt = _attempt(doc, player_id)
        if attempt["status"] == game.IN_PROGRESS:
            raise ServiceError("This player has not submitted yet.")
        game.set_grade(attempt, question_id, score, "admin", notes=_clean(notes, 300))
        game.finalize_if_graded(attempt)

    vault.mutate_quiz(quiz_id, fn)


def reset_attempt(vault: Vault, quiz_id: str, player_id: str) -> None:
    vault.mutate_quiz(quiz_id, lambda doc: doc["attempts"].pop(player_id, None))


def reset_all_attempts(vault: Vault, quiz_id: str) -> None:
    vault.mutate_quiz(quiz_id, lambda doc: doc["attempts"].clear())


# =========================================================================
# Memories (photo + notes each player leaves for the host after the quiz)
# =========================================================================
def get_memory(quiz: dict, player_id: str) -> dict | None:
    return quiz.get("memories", {}).get(player_id)


def clean_memory_text(like: str, dislike: str, story: str) -> dict:
    fields = {"like": _clean(like, config.MAX_MEMORY_CHARS),
              "dislike": _clean(dislike, config.MAX_MEMORY_CHARS),
              "story": _clean(story, config.MAX_MEMORY_CHARS)}
    labels = {"like": "what you like about them", "dislike": "what they could improve",
              "story": "the moment and its story"}
    missing = [labels[k] for k, v in fields.items() if len(v) < 3]
    if missing:
        raise ServiceError("Please fill in: " + ", ".join(missing) + ".")
    return fields


def save_memory(vault: Vault, quiz_id: str, player_id: str, like: str, dislike: str,
                story: str, photo: dict | None) -> None:
    """Store a player's memory. Kept even if their attempt is reset later."""
    fields = clean_memory_text(like, dislike, story)

    def fn(doc):
        player = _find(doc["players"], player_id, "Player")
        attempt = _attempt(doc, player_id)
        if attempt["status"] == game.IN_PROGRESS and not game.ready_to_submit(doc, attempt):
            raise ServiceError("Finish the quiz first.")
        memories = doc.setdefault("memories", {})
        if player_id in memories:
            raise ServiceError("You have already shared your memory.")
        memories[player_id] = {"player_id": player_id,
                               "player_name": player.get("display_name") or player["name"],
                               "photo": photo, "created_at": game.now_iso(), **fields}

    vault.mutate_quiz(quiz_id, fn)


def delete_memory(vault: Vault, quiz_id: str, player_id: str) -> None:
    def fn(doc):
        if doc.setdefault("memories", {}).pop(player_id, None) is None:
            raise ServiceError("Memory not found.")

    _edit(vault, quiz_id, fn)


def list_memories(quiz: dict) -> list[dict]:
    return sorted(quiz.get("memories", {}).values(), key=lambda m: m.get("created_at", ""))


# =========================================================================
# Export
# =========================================================================
def results_rows(quiz: dict) -> list[dict]:
    rows = []
    for player in quiz["players"]:
        attempt = quiz["attempts"].get(player["id"])
        row = {"Player": player.get("display_name") or player["name"], "MCQ Score": "",
               "Short Answer Score": "", "Total Score": "", "Percentage": "",
               "Status": game.STATUS_LABELS[game.NOT_STARTED], "Started At": "", "Completed At": ""}
        if attempt:
            s = game.score_summary(quiz, attempt)
            row.update({"Status": game.STATUS_LABELS[attempt["status"]],
                        "Started At": attempt.get("started_at") or "",
                        "Completed At": attempt.get("completed_at") or ""})
            if attempt["status"] != game.IN_PROGRESS:
                row["MCQ Score"] = s["mcq"]
            if attempt["status"] == game.COMPLETED:
                row.update({"Short Answer Score": s["sa"], "Total Score": s["total"],
                            "Percentage": s["percentage"]})
        rows.append(row)
    return rows


def results_csv(quiz: dict) -> str:
    import csv
    import io

    rows = results_rows(quiz)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else
                            ["Player", "MCQ Score", "Short Answer Score", "Total Score",
                             "Percentage", "Status", "Started At", "Completed At"])
    writer.writeheader()
    for row in rows:
        # neutralise spreadsheet formula injection in player names
        if str(row["Player"])[:1] in ("=", "+", "-", "@"):
            row = {**row, "Player": "'" + str(row["Player"])}
        writer.writerow(row)
    return buf.getvalue()

