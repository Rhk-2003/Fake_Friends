"""GitHub storage backend and Gemini grading, against in-memory fakes.

No network is used: the fakes mimic the documented behaviour of the GitHub
contents API (sha-based optimistic locking, 1 MB inline limit, 404s) and of
the Gemini generateContent response shape.
"""
import base64
import hashlib
import json
import re

import pytest

from ff import crypto, game, grading, service
from ff.storage import GitHubBackend, StorageError, Vault

from .conftest import build_quiz, play_through


class Resp:
    def __init__(self, status, body=None, content=b""):
        self.status_code, self._body, self.content = status, body, content
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class FakeGitHub:
    def __init__(self, branch_exists=False):
        self.branches = {"main"} | ({"ff-data"} if branch_exists else set())
        self.files, self.calls, self.fail_next = {}, [], []

    @staticmethod
    def _sha(data):
        return hashlib.sha1(data).hexdigest()

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        assert headers["Authorization"] == "Bearer tok"
        path = url.split("/repos/me/app", 1)[1]
        self.calls.append((method, path))
        if self.fail_next:
            return Resp(self.fail_next.pop(0), {})
        if m := re.fullmatch(r"/branches/(.+)", path):
            return Resp(200 if m.group(1) in self.branches else 404, {})
        if path == "/git/trees":
            return Resp(201, {"sha": "tree1"})
        if path == "/git/commits":
            assert json["parents"] == []            # orphan branch: no app code copied
            return Resp(201, {"sha": "commit1"})
        if path == "/git/refs":
            self.branches.add(json["ref"].split("/")[-1])
            return Resp(201, {})
        if m := re.fullmatch(r"/git/blobs/(.+)", path):
            data = next(d for d in self.files.values() if self._sha(d) == m.group(1))
            return Resp(200, content=data)
        name = path.split("/contents/", 1)[1]
        branch = (params or {}).get("ref") or (json or {}).get("branch")
        assert branch == "ff-data" and branch in self.branches
        if method == "GET":
            if name not in self.files:
                return Resp(404, {})
            data = self.files[name]
            big = len(data) > 1024 * 1024
            return Resp(200, {"sha": self._sha(data), "encoding": "none" if big else "base64",
                              "content": "" if big else base64.b64encode(data).decode()})
        if method == "PUT":
            exists = name in self.files
            if exists and json.get("sha") != self._sha(self.files[name]):
                return Resp(409, {})
            if not exists and "sha" in json:
                return Resp(422, {})
            self.files[name] = base64.b64decode(json["content"])
            return Resp(200 if exists else 201, {"content": {"sha": self._sha(self.files[name])}})
        if method == "DELETE":
            if name not in self.files or json["sha"] != self._sha(self.files[name]):
                return Resp(404, {})
            del self.files[name]
            return Resp(200, {})
        raise AssertionError(path)


def make(gh):
    return GitHubBackend("tok", "me/app", session=gh)


def test_github_creates_orphan_data_branch_once():
    gh = FakeGitHub()
    backend = make(gh)
    assert backend.read("q/a.bin") is None
    assert "ff-data" in gh.branches
    assert [c[1] for c in gh.calls[:4]] == ["/branches/ff-data", "/git/trees", "/git/commits", "/git/refs"]
    backend.write("q/a.bin", b"one")
    assert sum(1 for c in gh.calls if c[1] == "/git/refs") == 1


def test_github_roundtrip_update_delete():
    gh = FakeGitHub(branch_exists=True)
    backend = make(gh)
    backend.write("q/a.bin", b"one")
    backend.write("q/a.bin", b"two")                       # uses the cached sha
    assert gh.files["vault/q/a.bin"] == b"two"
    assert make(gh).read("q/a.bin") == b"two"
    fresh = make(gh)
    fresh.write("q/a.bin", b"three")                       # no sha known -> 422 -> refresh -> ok
    assert gh.files["vault/q/a.bin"] == b"three"
    backend.write("q/a.bin", b"four")                      # stale sha -> 409 -> refresh -> ok
    assert gh.files["vault/q/a.bin"] == b"four"
    make(gh).delete("q/a.bin")
    assert "vault/q/a.bin" not in gh.files
    make(gh).delete("q/missing.bin")                       # no error


def test_github_large_file_uses_blob_api():
    gh = FakeGitHub(branch_exists=True)
    video = bytes(range(256)) * 6000                       # 1.5 MB
    make(gh).write("m/x/v.bin", video)
    assert make(gh).read("m/x/v.bin") == video
    assert any(c[1].startswith("/git/blobs/") for c in gh.calls)


def test_github_retries_server_errors_and_reports_auth(monkeypatch):
    monkeypatch.setattr("ff.storage.time.sleep", lambda s: None)
    gh = FakeGitHub(branch_exists=True)
    backend = make(gh)
    backend.read("q/none.bin")
    gh.fail_next = [502, 503]
    backend.write("q/a.bin", b"ok")
    assert gh.files["vault/q/a.bin"] == b"ok"
    gh.fail_next = [401]
    with pytest.raises(StorageError, match="GITHUB_TOKEN"):
        backend.write("q/a.bin", b"x")
    gh.fail_next = [403]
    with pytest.raises(StorageError, match="rate limit"):
        backend.read("q/a.bin")
    with pytest.raises(StorageError):
        GitHubBackend("tok", "not-a-repo", session=gh)


def test_full_game_persists_through_github_and_restart():
    gh = FakeGitHub()
    key = crypto.normalize_master_key(crypto.generate_master_key())
    vault = Vault(make(gh), key)
    made = build_quiz(vault, n_mcq=10, n_sa=6)
    qid = made["quiz_id"]
    pid = vault.load_quiz(qid)["players"][0]["id"]
    ref = service.store_upload(vault, qid, b"\x00\x00\x00\x18ftypmp42" + b"v" * 5000, "video/mp4", "i.mp4")
    service.update_player(vault, qid, pid, {"intro_video": ref})
    play_through(vault, qid, pid, skip_mcq=2, skip_sa=1)
    service.grade_attempt(vault, qid, pid, grader=lambda items: {
        i["id"]: {"score": 2, "reason": "ok"} for i in items})
    # a brand-new process (Streamlit restart) sees everything
    reborn = Vault(make(gh), key)
    assert service.authenticate_admin(reborn, "hkadmin", "secret123") == qid
    quiz = reborn.load_quiz(qid)
    assert game.score_summary(quiz, quiz["attempts"][pid])["total"] == 16
    assert reborn.get_media(qid, ref["id"]).startswith(b"\x00\x00\x00\x18ftyp")
    # and the repository only ever saw ciphertext under opaque names
    dump = b"".join(gh.files.values()) + " ".join(gh.files).encode()
    for secret in (b"Rahul", b"Situation", b"my answer", qid.encode(), b"hkadmin", b"ftypmp42"):
        assert secret not in dump
    assert all(re.fullmatch(r"vault/(i|q|m/[0-9a-f]{20})/[0-9a-f]{40}\.bin", n) for n in gh.files)


# ---- Gemini ----------------------------------------------------------------
ITEMS = [{"id": "a", "situation": "s1", "answer_key": "k1", "grading_notes": "", "friend_answer": "f1"},
         {"id": "b", "situation": "s2", "answer_key": "k2", "grading_notes": "", "friend_answer": "f2"}]


def gemini_reply(grades):
    return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps({"grades": grades})}]}}]})


def test_gemini_request_and_parsing():
    seen = {}

    def post(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload)
        return gemini_reply([{"id": "b", "score": 7, "reason": "x" * 999},
                             {"id": "a", "score": 1, "reason": "partly"}])

    result = grading.grade_short_answers(ITEMS, api_key="KEY", post=post)
    assert seen["url"].endswith("/models/gemini-2.5-flash-lite:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "KEY" and "KEY" not in seen["url"]
    assert seen["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    sent = seen["payload"]["contents"][0]["parts"][0]["text"]
    assert '"answer_key": "k1"' in sent and '"friend_answer": "f2"' in sent
    assert list(result) == ["a", "b"]
    assert result["a"] == {"score": 1, "reason": "partly"}
    assert result["b"]["score"] == 2 and len(result["b"]["reason"]) == 300      # clamped


def test_gemini_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ENVKEY")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-next")
    seen = {}

    def post(url, headers, payload, timeout):
        seen.update(url=url, key=headers["x-goog-api-key"])
        return gemini_reply([{"id": "a", "score": 0, "reason": ""}, {"id": "b", "score": 2, "reason": ""}])

    assert grading.ai_available()
    grading.grade_short_answers(ITEMS, post=post)
    assert "gemini-next" in seen["url"] and seen["key"] == "ENVKEY"


@pytest.mark.parametrize("replies,calls", [
    ([(500, "boom"), (500, "boom")], 2),                                   # server error: one retry
    ([(403, "bad key")], 1),                                               # auth error: no retry
    ([(200, "not json"), (200, "{}")], 2),                                 # unreadable
    ([gemini_reply([{"id": "a", "score": 2, "reason": ""}])] * 2, 2),      # an answer was skipped
])
def test_gemini_failures_raise(replies, calls):
    queue = list(replies)
    count = []

    def post(*args):
        count.append(1)
        return queue.pop(0)

    with pytest.raises(grading.GradingError):
        grading.grade_short_answers(ITEMS, api_key="KEY", post=post)
    assert len(count) == calls


def test_gemini_network_error_and_missing_key():
    def post(*args):
        raise ConnectionError("no route")

    with pytest.raises(grading.GradingError):
        grading.grade_short_answers(ITEMS, api_key="KEY", post=post)
    with pytest.raises(grading.GradingError, match="not configured"):
        grading.grade_short_answers(ITEMS)
    assert not grading.ai_available()
    assert grading.grade_short_answers([], api_key="KEY") == {}
