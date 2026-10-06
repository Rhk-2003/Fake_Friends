# 🎭 Fake Friends

> How well do your friends really know you?

A Streamlit game. One person (the **admin**) writes questions about themselves, adds their
friends as **players** with a photo and personal videos, and shares a link. Friends pick their
name, answer multiple-choice questions and real-life situations, and land on a **podium**.

- **Section 1 – MCQ**: the admin can add any number (8 or more). Each player answers **8**; 1 point each.
- **Section 2 – Situations**: any number (4 or more). Each player answers **4** in their own words; up to 2 points each, graded by AI against the admin's answer key.
- **Skips**: only the *first 8 / first 4 answered* count, so a pool of 16 MCQs gives 8 skips. A skipped question does not come back.
- **Maximum 16.** 10–16 = high score (≥ 60 %), 0–9 = low score. Each outcome plays that player's own outro video.
- **Answer reveal**: after each locked answer the player sees the right one – green for correct, red for a wrong pick – with the admin's short explanation (MCQ) or the answer key (situations), then taps *Next question*.
- **Every answer is saved the moment it is locked in.** A refresh, a dropped connection or a server restart resumes at the next unanswered question.
- **Memory step**: before the score and leaderboard, every player uploads a photo of themselves with the host and writes the story of the moment, what they like, and what the host could improve.
- **Leaderboard** with 1st / 2nd / 3rd podium (with each player's DP), plus a **Memories** page.
- **Everything saved is encrypted**, so the data can sit in a public GitHub repository.

---

## 1. Run it locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

With no secrets set, the app stores encrypted files in `./data/` and creates a development
key in `data/.dev_master_key` (git-ignored). AI grading is off until you add a Gemini key –
short answers then wait in **Review answers** for manual grading.

To try AI grading locally, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
and fill in `GEMINI_API_KEY`.

## 2. Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository (public is fine – see [Security](#5-security-model)).
2. On GitHub: **Settings → Developer settings → Fine-grained tokens → Generate new token.**
   Repository access: *only this repository*. Permission: **Contents → Read and write**. Nothing else.
3. Generate the encryption key once and keep a private copy:
   ```bash
   python -c "from ff.crypto import generate_master_key as g; print(g())"
   ```
4. On [share.streamlit.io](https://share.streamlit.io): **New app** → your repo → branch `main` → `app.py`.
5. **App → Settings → Secrets**, paste (see `.streamlit/secrets.toml.example`):
   ```toml
   FF_MASTER_KEY = "the key from step 3"
   GITHUB_TOKEN = "github_pat_…"
   GITHUB_REPO = "your-username/fake-friends"
   GEMINI_API_KEY = "your Gemini key"
   APP_BASE_URL = "https://your-app.streamlit.app"
   ```

### Why data survives restarts

Streamlit Cloud wipes its disk whenever an app sleeps or redeploys. So the app commits its data
to a separate branch, **`ff-data`**, of your repository through the GitHub API. That branch is
created automatically, contains only ciphertext, and is *not* the branch Streamlit deploys from –
so saving never restarts the app. On startup the app reads everything back.

| Setting | Purpose | Default |
|---|---|---|
| `FF_MASTER_KEY` | Encrypts everything. **Lose it = data is gone. Change it = old data unreadable.** | required with GitHub storage |
| `GITHUB_TOKEN`, `GITHUB_REPO` | Permanent storage | local folder if unset |
| `GITHUB_DATA_BRANCH`, `GITHUB_DATA_PATH` | Where data is committed | `ff-data`, `vault` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | AI grading | off, `gemini-2.5-flash-lite` |
| `APP_BASE_URL` | Share links / QR codes | detected from the browser |
| `FF_MAX_VIDEO_MB` | Per-video upload limit | 20 |

## 3. Using it

**Create a quiz** – Home → *Create your own quiz* → name, username, password. You get the quiz
link, a quiz password and your admin username. The dashboard's **Overview** lists the 8 setup steps.

| Section | What you do there |
|---|---|
| Settings | Name, description, icon, quiz password, intro, rules, score messages, memory wall on/off, cover, logo, admin password |
| Players | Add / edit / deactivate / delete; DP; intro video, low-score outro, high-score outro (upload or https link) |
| Questions | Build, edit, reorder, duplicate, delete. MCQ: 2–6 options, each with emoji and/or image, plus a **short explanation** shown after answering. Situation: question + **answer key** (graded against, and shown after answering) + optional grading notes |
| Review answers | See every short answer, the AI's score and reason; override 0 / 1 / 2 and add notes |
| Results | Analytics, results table, per-player answer sheet, CSV export, reset attempts |
| Leaderboard | The same podium players see |
| Memories | Every friend's photo with you and their notes – kept permanently, even if you reset their attempt. You can delete one if a friend asks |
| Share | Link, password, ready-to-paste invite, QR code |
| Preview & publish | Validation checklist, play-through preview (records nothing), publish / pause |
| 👑 All quizzes | 

**Play** – open the link → quiz password → pick your name → intro video → rules → Section 1 →
Section 2 (each answer is followed by the reveal) → memory photo + notes → score, outro video,
leaderboard / memories → "create your own quiz?".

### Rules worth knowing

- **Questions lock** once the first player starts, so scores stay comparable. To edit, use
  *Results → Reset ALL attempts*.
- **One attempt per player, saved answer by answer.** After a refresh the player re-enters the quiz
  password, taps **Continue** on their name and lands on the next unanswered question. A finished
  player only sees their result. The admin can reset a single attempt.
- **Answers are revealed as you go**, so a friend who finishes first can tell the others. If that
  matters, ask everyone to play at the same time.
- **Memories**: the photo is expected to be of just the two of them; "We have never taken a photo
  together" lets a player continue with notes only. By default a memory is seen by that player, the
  host. With **Memory wall** switched on in Settings, the other players also see
  the photo, the story and the "like" note – never the "could improve" note.
- **Ties** on the leaderboard go to whoever finished faster.
- **AI grading**: all four answers go to Gemini in one request with your answer key. 2 = same idea,
  1 = partly, 0 = different. If the key is missing or the call fails, the attempt shows *Awaiting
  Admin Review* and you grade it by hand – the game never blocks. A score you set by hand is never
  overwritten by the AI.

## 4. Project layout

```
app.py                  entry point + router
ff/
  config.py             game constants, secrets/env lookup
  crypto.py             AES-256-GCM, key derivation, scrypt password hashing
  storage.py            Local / GitHub / memory backends + the encrypted Vault
  game.py               pure rules: pools, skips, scoring, threshold, leaderboard
  grading.py            Gemini short-answer grading
  service.py            accounts, players, questions, publishing, attempts, CSV
  media.py              upload validation, image re-encoding
  throttle.py           password brute-force limiter
  qr.py, runtime.py     QR code, app singletons
  ui/                   theme, components, landing, player, admin, shared
tests/                  62 tests (rules, security, storage, grading, full app flows)
.streamlit/config.toml  theme + upload limit
```

## 5. Security model

- **At rest**: every file is AES-256-GCM ciphertext. Each file has its own key (HKDF from the
  master key) and is bound to its path, so files cannot be swapped or tampered with undetected.
  File names are HMACs, so quiz codes cannot be listed from the repository. Commit messages are generic.
- **Passwords**: admin passwords are scrypt hashes. The quiz password is a shared secret the admin
  must be able to copy, so it is stored inside the encrypted quiz document rather than hashed.
- **Isolation**: one encrypted document per quiz. An admin session is re-checked against the
  account index on every run; ids from one quiz mean nothing in another.
- **Scores** are always recomputed on the server from stored raw answers.
- **Uploads**: type checked by content, size-limited, stored under random ids; images are re-encoded
  (strips EXIF/GPS). All user text is HTML-escaped.
- **Who can read what** (this is what the in-app privacy note tells players):
  people with the quiz password see names, photos and the leaderboard (and the memory wall, if the
  host switched it on); the quiz creator sees everything in their quiz, including every memory;
  `FF_MASTER_KEY` can decrypt the stored files. Nobody else can.
- What a public repo still reveals: number and size of files and when they changed.

## 6. Limits and honest caveats

- **Verified here against simulators, not the live services**: the GitHub storage backend and the
  Gemini call were tested with fakes that follow the documented APIs. Do one real round after
  deploying (create a quiz, restart the app from the Streamlit menu, confirm it is still there;
  submit one attempt and check the AI score in *Review answers*).
- **Run one app instance per data branch.** The app caches data in memory and is the only writer.
- **One commit per answer.** Each locked answer is one small commit to the data branch (about 20 per
  player per quiz). If GitHub is briefly unreachable or rate-limits the app, the answer is kept in
  memory and retried automatically; it would only be lost if the server also restarted in that window.
- Text typed into a situation box but not yet locked in is not saved.
- **Videos** are stored in the repo (encrypted). Keep them short; GitHub recommends repos stay under
  ~1 GB. For long videos paste a YouTube / direct `.mp4` link instead. iPhone HEVC `.mov` files may
  not play in Chrome – export as MP4 (H.264).
- **Player identity** is by honour: anyone with the quiz password can pick any name.
- AI grading is a judgement call; the admin can always override.

## 7. Decisions that differ from the original brief

| Brief | Built | Why |
|---|---|---|
| SQLAlchemy + PostgreSQL | Encrypted document store (one doc per quiz) on GitHub or disk | Requirement that data live encrypted in a public GitHub repo and survive Streamlit restarts |
| Exactly 8 MCQ / 4 situations | Pools of ≥ 8 / ≥ 4 with skips | Later requirement |
| Manual grading only, no AI | Gemini grading with manual review as fallback / override | Later requirement |
| Players never see others' scores | Leaderboard shows names, DPs and scores (never answers) | Later requirement |
| Never expose correct answers | Right answer + explanation / answer key shown after each locked answer | Later requirement |
| "Answers may be compared" notice | Removed | Later requirement |
| "New version" to edit after start | Lock + *Reset all attempts* | Simplest reliable option |
| 8-step wizard | Account form + Setup checklist on Overview | Same steps, any order |
| `pages/` folder | `ff/ui/` + router | Avoids Streamlit's automatic multipage sidebar |

## 8. Tests

```bash
pip install -r requirements-dev.txt
pytest
```
