"""Persistent, encrypted storage.

Layers
------
Backend  : dumb byte store (``read`` / ``write`` / ``delete`` by file name).
           * ``LocalBackend``  - files on disk (local development).
           * ``GitHubBackend`` - commits to a branch of a GitHub repo, so data
             survives Streamlit Community Cloud restarts.
           * ``MemoryBackend`` - tests.
Vault    : encrypts everything before it reaches a backend, keeps an
           in-process cache, serialises writers with per-quiz locks and
           batches low-priority writes ("checkpoints") to limit commits.

Only ciphertext ever reaches the backend, and file names are HMACs - so the
data branch can live in a public repository.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import threading
import time
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from . import crypto

log = logging.getLogger("fakefriends.storage")


class StorageError(RuntimeError):
    pass


class NotFound(StorageError):
    pass


# =========================================================================
# Backends
# =========================================================================
class MemoryBackend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.writes = 0

    def read(self, name: str) -> bytes | None:
        return self.files.get(name)

    def write(self, name: str, data: bytes) -> None:
        self.files[name] = data
        self.writes += 1

    def delete(self, name: str) -> None:
        self.files.pop(name, None)

    def describe(self) -> str:
        return "memory (not persistent)"


class LocalBackend:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        path = (self.root / name).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError("Invalid storage path.")
        return path

    def read(self, name: str) -> bytes | None:
        path = self._path(name)
        return path.read_bytes() if path.exists() else None

    def write(self, name: str, data: bytes) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def describe(self) -> str:
        return f"local folder ({self.root})"


class GitHubBackend:
    """Stores files on a dedicated branch through the GitHub REST API.

    The data branch must NOT be the branch Streamlit deploys from, otherwise
    every save would trigger a redeploy. By default it is ``ff-data`` and is
    created automatically (as an orphan branch) on first use.
    """

    API = "https://api.github.com"

    def __init__(self, token: str, repo: str, branch: str = "ff-data",
                 prefix: str = "vault", session: Any = None, timeout: int = 60) -> None:
        if "/" not in repo:
            raise StorageError("GITHUB_REPO must look like 'owner/repository'.")
        self.repo, self.branch, self.prefix = repo.strip(), branch.strip(), prefix.strip("/")
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self.http = session
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fake-friends-app",
        }
        self._sha: dict[str, str] = {}
        self._branch_ready = False
        self._lock = threading.RLock()

    def describe(self) -> str:
        return f"GitHub ({self.repo} @ {self.branch}/{self.prefix})"

    # ---- low level ----------------------------------------------------
    def _request(self, method: str, url: str, *, ok: tuple[int, ...], headers: dict | None = None, **kw):
        last: Exception | None = None
        for attempt in range(4):
            try:
                resp = self.http.request(method, url, headers={**self.headers, **(headers or {})},
                                         timeout=self.timeout, **kw)
            except Exception as exc:  # network error
                last = exc
                time.sleep(0.6 * (attempt + 1))
                continue
            if resp.status_code in ok:
                return resp
            if resp.status_code in (500, 502, 503, 504):
                last = StorageError(f"GitHub returned {resp.status_code}")
                time.sleep(0.6 * (attempt + 1))
                continue
            if resp.status_code == 401:
                raise StorageError("GitHub rejected GITHUB_TOKEN (401). Check the token in secrets.")
            if resp.status_code in (403, 429):
                raise StorageError("GitHub refused the request (rate limit or missing "
                                   "'Contents: read and write' permission on the token).")
            return resp  # caller decides (404 / 409 / 422)
        raise StorageError(f"Could not reach GitHub: {last}")

    def _url(self, name: str) -> str:
        return f"{self.API}/repos/{self.repo}/contents/{self.prefix}/{name}"

    def _ensure_branch(self) -> None:
        if self._branch_ready:
            return
        with self._lock:
            if self._branch_ready:
                return
            base = f"{self.API}/repos/{self.repo}"
            resp = self._request("GET", f"{base}/branches/{self.branch}", ok=(200,))
            if resp.status_code == 404:
                self._create_orphan_branch(base)
            elif resp.status_code != 200:
                raise StorageError(f"Could not check data branch ({resp.status_code}).")
            self._branch_ready = True

    def _create_orphan_branch(self, base: str) -> None:
        readme = ("# Fake Friends data\n\nEncrypted game data. Every file here is AES-256-GCM "
                  "ciphertext; it is unreadable without the app's master key.\n")
        tree = self._request("POST", f"{base}/git/trees", ok=(201,),
                             json={"tree": [{"path": "README.md", "mode": "100644",
                                             "type": "blob", "content": readme}]})
        if tree.status_code != 201:
            raise StorageError("Could not create the data branch. Does GITHUB_REPO exist and "
                               "does the token have 'Contents: read and write' access?")
        commit = self._request("POST", f"{base}/git/commits", ok=(201,),
                               json={"message": "ff: init data branch", "tree": tree.json()["sha"],
                                     "parents": []})
        if commit.status_code != 201:
            raise StorageError("Could not create the data branch (commit failed).")
        ref = self._request("POST", f"{base}/git/refs", ok=(201,),
                            json={"ref": f"refs/heads/{self.branch}", "sha": commit.json()["sha"]})
        if ref.status_code not in (201, 422):  # 422 = created concurrently
            raise StorageError("Could not create the data branch (ref failed).")

    def _fetch_meta(self, name: str):
        return self._request("GET", self._url(name), ok=(200,), params={"ref": self.branch})

    # ---- API ----------------------------------------------------------
    def read(self, name: str) -> bytes | None:
        self._ensure_branch()
        resp = self._fetch_meta(name)
        if resp.status_code == 404:
            self._sha.pop(name, None)
            return None
        if resp.status_code != 200:
            raise StorageError(f"GitHub read failed ({resp.status_code}).")
        meta = resp.json()
        self._sha[name] = meta["sha"]
        if meta.get("content") and meta.get("encoding") == "base64":
            return base64.b64decode(meta["content"])
        # Files over 1 MB: the contents API omits the body, fetch the raw blob.
        blob = self._request("GET", f"{self.API}/repos/{self.repo}/git/blobs/{meta['sha']}",
                             ok=(200,), headers={"Accept": "application/vnd.github.raw+json"})
        if blob.status_code != 200:
            raise StorageError(f"GitHub blob read failed ({blob.status_code}).")
        return blob.content

    def write(self, name: str, data: bytes) -> None:
        self._ensure_branch()
        body = {"message": "ff: update", "branch": self.branch,
                "content": base64.b64encode(data).decode()}
        for _ in range(4):
            if name in self._sha:
                body["sha"] = self._sha[name]
            else:
                body.pop("sha", None)
            resp = self._request("PUT", self._url(name), ok=(200, 201), json=body)
            if resp.status_code in (200, 201):
                self._sha[name] = resp.json()["content"]["sha"]
                return
            if resp.status_code in (409, 422):  # stale / missing sha -> refresh and retry
                meta = self._fetch_meta(name)
                if meta.status_code == 200:
                    self._sha[name] = meta.json()["sha"]
                else:
                    self._sha.pop(name, None)
                time.sleep(0.3)
                continue
            raise StorageError(f"GitHub write failed ({resp.status_code}).")
        raise StorageError("GitHub write kept conflicting; please try again.")

    def delete(self, name: str) -> None:
        self._ensure_branch()
        if name not in self._sha:
            meta = self._fetch_meta(name)
            if meta.status_code != 200:
                return
            self._sha[name] = meta.json()["sha"]
        self._request("DELETE", self._url(name), ok=(200, 404),
                      json={"message": "ff: remove", "branch": self.branch, "sha": self._sha[name]})
        self._sha.pop(name, None)


# =========================================================================
# Vault
# =========================================================================
INDEX_PATH = "index"


def _quiz_path(quiz_id: str) -> str:
    return f"quiz/{quiz_id}"


def _media_path(quiz_id: str, media_id: str) -> str:
    return f"media/{quiz_id}/{media_id}"


class Vault:
    """Encrypted document store: one index + one document per quiz + media."""

    MEDIA_CACHE_BYTES = 160 * 1024 * 1024
    CHECKPOINT_SECONDS = 20  # non-durable writes are flushed after this long

    def __init__(self, backend, master_key: bytes) -> None:
        self.backend = backend
        self._key = master_key
        self._docs: dict[str, dict | None] = {}
        self._dirty: dict[str, float] = {}       # path -> time it became dirty
        self._retry_at: dict[str, float] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._media: OrderedDict[str, bytes] = OrderedDict()
        self._media_bytes = 0
        self.last_error: str | None = None

    # ---- helpers ------------------------------------------------------
    def _lock(self, path: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(path, threading.RLock())

    def _name(self, path: str) -> str:
        folder = path.split("/")[0]
        if folder == "media":
            quiz_id = path.split("/")[1]
            return f"m/{crypto.opaque_name(self._key, 'quiz/' + quiz_id)[:20]}/{crypto.opaque_name(self._key, path)}.bin"
        return f"{folder[0]}/{crypto.opaque_name(self._key, path)}.bin"

    def _read_doc(self, path: str) -> dict | None:
        if path in self._docs:
            return self._docs[path]
        blob = self.backend.read(self._name(path))
        doc = None
        if blob is not None:
            doc = json.loads(zlib.decompress(crypto.decrypt(self._key, path, blob)))
            self._docs[path] = doc   # misses are not cached (unbounded ids)
        return doc

    def _persist(self, path: str) -> bool:
        doc = self._docs.get(path)
        if doc is None:
            return True
        try:
            raw = zlib.compress(json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode())
            self.backend.write(self._name(path), crypto.encrypt(self._key, path, raw))
        except Exception as exc:  # keep the change in memory, retry later
            log.exception("persist failed for %s", path.split("/")[0])
            self.last_error = str(exc)
            self._dirty.setdefault(path, time.time())
            self._retry_at[path] = time.time() + 10
            return False
        self._dirty.pop(path, None)
        self._retry_at.pop(path, None)
        self.last_error = None
        return True

    def _mutate(self, path: str, fn: Callable[[dict], Any], durable: bool, create: dict | None = None):
        with self._lock(path):
            current = self._read_doc(path)
            if current is None:
                if create is None:
                    raise NotFound("Not found.")
                current = create
            work = copy.deepcopy(current)
            result = fn(work)           # may raise -> nothing is changed
            self._docs[path] = work
            if durable:
                self._persist(path)
            else:
                self._dirty.setdefault(path, time.time())
            return result

    # ---- index --------------------------------------------------------
    def load_index(self) -> dict:
        with self._lock(INDEX_PATH):
            doc = self._read_doc(INDEX_PATH)
            return copy.deepcopy(doc) if doc else {"version": 1, "admins": {}, "super": None}

    def mutate_index(self, fn: Callable[[dict], Any]):
        return self._mutate(INDEX_PATH, fn, True, create={"version": 1, "admins": {}, "super": None})

    # ---- quizzes ------------------------------------------------------
    def load_quiz(self, quiz_id: str) -> dict | None:
        if not quiz_id or not quiz_id.isalnum() or len(quiz_id) > 16:
            return None
        path = _quiz_path(quiz_id)
        with self._lock(path):
            doc = self._read_doc(path)
            return copy.deepcopy(doc) if doc else None

    def create_quiz(self, doc: dict) -> None:
        path = _quiz_path(doc["id"])
        with self._lock(path):
            if self._read_doc(path) is not None:
                raise StorageError("Quiz id collision.")
            self._docs[path] = copy.deepcopy(doc)
            if not self._persist(path):
                raise StorageError("Could not save the new quiz. Please try again.")

    def mutate_quiz(self, quiz_id: str, fn: Callable[[dict], Any], durable: bool = True):
        """Apply ``fn(doc)`` atomically. ``durable=False`` = checkpoint later."""
        return self._mutate(_quiz_path(quiz_id), fn, durable)

    def delete_quiz(self, quiz_id: str) -> None:
        path = _quiz_path(quiz_id)
        with self._lock(path):
            self._docs.pop(path, None)
            self._dirty.pop(path, None)
            self.backend.delete(self._name(path))

    # ---- flushing -----------------------------------------------------
    def flush_stale(self, max_age: float | None = None) -> None:
        """Persist checkpointed documents. Called at the start of every run."""
        max_age = self.CHECKPOINT_SECONDS if max_age is None else max_age
        now = time.time()
        for path, since in list(self._dirty.items()):
            if now - since < max_age or now < self._retry_at.get(path, 0):
                continue
            with self._lock(path):
                if path in self._dirty:
                    self._persist(path)

    def flush_all(self) -> None:
        self._retry_at.clear()
        self.flush_stale(max_age=0)

    def pending_sync(self) -> int:
        return len(self._dirty)

    # ---- media --------------------------------------------------------
    def _cache_media(self, path: str, data: bytes) -> None:
        if len(data) > self.MEDIA_CACHE_BYTES // 2:
            return
        with self._guard:
            if path in self._media:
                self._media_bytes -= len(self._media.pop(path))
            self._media[path] = data
            self._media_bytes += len(data)
            while self._media_bytes > self.MEDIA_CACHE_BYTES and self._media:
                _, old = self._media.popitem(last=False)
                self._media_bytes -= len(old)

    def put_media(self, quiz_id: str, media_id: str, data: bytes) -> None:
        path = _media_path(quiz_id, media_id)
        try:
            self.backend.write(self._name(path), crypto.encrypt(self._key, path, data))
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Could not save the file: {exc}") from exc
        self._cache_media(path, data)

    def get_media(self, quiz_id: str, media_id: str) -> bytes | None:
        path = _media_path(quiz_id, media_id)
        with self._guard:
            if path in self._media:
                self._media.move_to_end(path)
                return self._media[path]
        try:
            blob = self.backend.read(self._name(path))
        except Exception:
            log.exception("media read failed")
            return None
        if blob is None:
            return None
        data = crypto.decrypt(self._key, path, blob)
        self._cache_media(path, data)
        return data

    def delete_media(self, quiz_id: str, media_id: str) -> None:
        path = _media_path(quiz_id, media_id)
        with self._guard:
            if path in self._media:
                self._media_bytes -= len(self._media.pop(path))
        try:
            self.backend.delete(self._name(path))
        except Exception:  # best effort - orphaned ciphertext is harmless
            log.exception("media delete failed")
