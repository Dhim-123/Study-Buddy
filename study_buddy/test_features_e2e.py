#!/usr/bin/env python3
"""
Full feature reliability suite for Study Buddy.

- Auth, notebook, mistakes, DNA, conversations (always)
- Chat x N (default 50) + quiz/flashcards/podcast/crosscheck/definitions/formulas/mock-test
  use live Groq when GROQ_API_KEY is set; otherwise a content-rich mock so the
  request path, persistence, and quality gates still run.

Usage:
  cd study_buddy
  python test_features_e2e.py

Env:
  SB_CHAT_TURNS=50
  SB_SKIP_LIVE=1          # skip generative tests entirely
  SB_LIVE=1               # use real Groq (default is content-rich mock to save TPD)
  SB_FORCE_MOCK=1         # force mock even with SB_LIVE
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
print("e2e: starting…", flush=True)

# Load .env before importing app
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, ".env")
if os.path.isfile(_ENV):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV, override=True)
    except Exception:
        pass

print("e2e: importing app…", flush=True)
# Isolate from the developer's real SQLite file
os.environ.setdefault(
    "SB_E2E_DB",
    os.path.join(_HERE, f"_e2e_{os.getpid()}.db"),
)
import app as server  # noqa: E402

# Point the app at an ephemeral DB before any connections
_e2e_db = os.environ.get("SB_E2E_DB")
if _e2e_db:
    server.DB_PATH = _e2e_db
print("e2e: app imported", flush=True)
print(f"e2e: DB_PATH={server.DB_PATH}", flush=True)


CHAT_TURNS = max(1, int(os.environ.get("SB_CHAT_TURNS", "50") or "50"))
SKIP_LIVE = os.environ.get("SB_SKIP_LIVE", "").strip() in ("1", "true", "yes", "on")
FORCE_MOCK = os.environ.get("SB_FORCE_MOCK", "").strip() in ("1", "true", "yes", "on")
HAS_GROQ = bool((os.environ.get("GROQ_API_KEY") or "").strip())
# Prefer mock for high-volume runs unless SB_LIVE=1 (avoids burning daily Groq TPD)
FORCE_LIVE = os.environ.get("SB_LIVE", "").strip() in ("1", "true", "yes", "on")
FORCE_MOCK = os.environ.get("SB_FORCE_MOCK", "").strip() in ("1", "true", "yes", "on")
USE_MOCK = FORCE_MOCK or (not FORCE_LIVE) or (not HAS_GROQ)


# ── Results ────────────────────────────────────────────────────────────

@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


RESULTS: List[Result] = []


def record(name: str, ok: bool, detail: str = "", ms: float = 0.0) -> None:
    RESULTS.append(Result(name=name, ok=ok, detail=detail, ms=ms))
    mark = "PASS" if ok else "FAIL"
    extra = f" — {detail}" if detail else ""
    timing = f" ({ms:.0f}ms)" if ms else ""
    print(f"  [{mark}] {name}{timing}{extra}")


def run_check(name: str, fn: Callable[[], str]) -> None:
    t0 = time.time()
    try:
        detail = fn() or ""
        record(name, True, detail, (time.time() - t0) * 1000)
    except Exception as e:
        record(name, False, f"{type(e).__name__}: {e}", (time.time() - t0) * 1000)
        if os.environ.get("SB_E2E_VERBOSE"):
            traceback.print_exc()


# ── Quality helpers ────────────────────────────────────────────────────

_MONOLOGUE_RE = re.compile(
    r"(?:\bno\.{2,}|\bi got it\b|\bover[\s-]?thinking\b|\bisn'?t correct\b|"
    r"\bi think i have it\b|\bwait[, ]+(?:no|actually)\b)",
    re.I,
)


def assert_chat_reply(reply: str, *, greeting: bool = False) -> None:
    text = (reply or "").strip()
    if not text:
        raise AssertionError("empty reply")
    if "❌ **Error**" in text or text.startswith("❌"):
        raise AssertionError(f"error-looking reply: {text[:120]}")
    min_len = 8 if greeting else 20
    if len(text) < min_len:
        raise AssertionError(f"reply too short ({len(text)} < {min_len}): {text!r}")
    if len(_MONOLOGUE_RE.findall(text)) >= 2:
        raise AssertionError("monologue/thinking-aloud patterns in reply")


def clear_rate_buckets() -> None:
    try:
        with server._RATE_LOCK:
            server._RATE_BUCKETS.clear()
    except Exception:
        pass


# ── Fake Groq (content-rich) ───────────────────────────────────────────

class _Msg:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


def _sys_blob(messages: list) -> str:
    return " ".join(
        (m.get("content") or "") for m in (messages or []) if m.get("role") == "system"
    ).lower()


def _last_user(messages: list) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _mock_completion_content(messages: list) -> str:
    sys_t = _sys_blob(messages)
    user = _last_user(messages)

    if "flashcard" in sys_t:
        cards = []
        for i in range(1, 7):
            cards.append(f"Q: Sample question {i} about the topic?\nA: Clear answer {i} with a key fact.")
        return "\n\n".join(cards)

    if "multiple choice quiz" in sys_t or "5-question" in sys_t or ("quiz" in sys_t and "flashcard" not in sys_t):
        parts = []
        for i in range(1, 6):
            parts.append(
                f"Q{i}. What is concept {i}?\n"
                f"A) Option A\nB) Option B\nC) Option C\nD) Option D\n"
                f"Answer: A\nExplanation: Because concept {i} is defined that way."
            )
        return "\n\n".join(parts)

    if "podcast" in sys_t or "host" in sys_t and ("alex" in sys_t or "maya" in sys_t or "two named"):
        return (
            "Alex: [cheerful] Welcome! Today we explore the topic clearly.\n"
            "Maya: [curious] What should students remember first?\n"
            "Alex: [confident] Start with the definition, then one example.\n"
            "Maya: [thoughtful] And a common mistake?\n"
            "Alex: [encouraging] Don't mix related terms — keep them separate.\n"
            "Maya: [cheerful] Got it — thanks!"
        )

    if "crosscheck" in sys_t or "review" in sys_t and "answer" in sys_t:
        return (
            "Verdict: Mostly correct.\n"
            "Strengths: Clear structure and right final idea.\n"
            "Gaps: Add one formula or unit check.\n"
            "Suggested fix: State the definition, then show one worked example."
        )

    if "definition" in sys_t:
        return (
            "Force — A push or pull on an object.\n"
            "Inertia — Tendency to resist change in motion.\n"
            "Acceleration — Rate of change of velocity."
        )

    if "formula sheet" in sys_t or "formulas" in sys_t:
        return (
            "Newton's Laws — Formula Sheet\n"
            "Core formulas:\n"
            "F = ma — Force equals mass times acceleration\n"
            "p = mv — Momentum\n"
            "Useful identities: weight W = mg\n"
            "Common pitfalls: mixing mass and weight; forgetting units."
        )

    if "pre-board" in sys_t or ("json" in sys_t and "exam paper" in sys_t) or "mock test" in (user + sys_t).lower():
        # quick 20-mark paper shape expected by api_mock_test
        paper = {
            "title": "Mock Test — Physics",
            "total_marks": 20,
            "duration_minutes": 40,
            "instructions": ["Read all questions.", "Write neatly.", "Show working.", "Manage time.", "Check answers.", "Attempt all."],
            "sections": [
                {
                    "id": "A",
                    "title": "Section A — Multiple Choice Questions",
                    "questions": [
                        {
                            "id": f"a{i}",
                            "number": i,
                            "type": "mcq",
                            "question": f"MCQ {i}: Which statement about Newton's laws is correct?",
                            "options": ["Inertia resists change", "Force is m/a", "Action has no reaction", "Mass equals weight"],
                            "answer_index": 0,
                            "marks": 1,
                            "explanation": "First law / inertia.",
                        }
                        for i in range(1, 11)
                    ],
                },
                {
                    "id": "B",
                    "title": "Section B — Assertion & Reason",
                    "questions": [
                        {
                            "id": f"b{i}",
                            "number": i,
                            "type": "assertion_reason",
                            "assertion": f"Assertion {i}: Every action has an equal and opposite reaction.",
                            "reason": f"Reason {i}: Forces always occur in pairs.",
                            "options": [
                                "Both A and R true and R explains A",
                                "Both A and R true but R does not explain A",
                                "A true R false",
                                "A false R true",
                            ],
                            "answer_index": 0,
                            "marks": 1,
                            "explanation": "Third law.",
                        }
                        for i in range(1, 6)
                    ],
                },
                {
                    "id": "D",
                    "title": "Section D — Case Study",
                    "questions": [
                        {
                            "id": "d1",
                            "number": 1,
                            "type": "case_study",
                            "passage": (
                                "A student pulls a trolley of mass 5 kg with a force of 10 N on a smooth floor. "
                                "Friction is negligible. Use Newton's laws to answer the subquestions."
                            ),
                            "marks": 5,
                            "subquestions": [
                                {
                                    "id": f"d1_{i}",
                                    "number": str(i),
                                    "type": "mcq",
                                    "question": f"Sub-question {i}: What is the acceleration?",
                                    "options": ["2 m/s^2", "0.5 m/s^2", "10 m/s^2", "5 m/s^2"],
                                    "answer_index": 0,
                                    "marks": 1,
                                    "explanation": "a = F/m = 2.",
                                }
                                for i in range(1, 6)
                            ],
                        }
                    ],
                },
            ],
        }
        return json.dumps(paper)

    if "title" in sys_t and len(user) < 200 and "short" in sys_t:
        return "Newton's Laws Chat"

    # Default chat-style reply
    if re.match(r"^\s*(hi+|hello+|hey+|thanks?|bye+)\s*[!?.]*\s*$", user, re.I):
        return "Hi! I'm Max — ready when you are."
    topic = user[:120] if user else "your question"
    return (
        f"Here's a clear answer about: {topic}\n\n"
        "1) Core idea: state the definition in plain language.\n"
        "2) Example: apply it to a simple everyday case.\n"
        "3) Check: ask yourself one quick follow-up to confirm you understood.\n"
        "Keep units consistent and don't skip the final statement of the answer."
    )


class FakeCompletions:
    def create(self, **kwargs):
        messages = kwargs.get("messages") or []
        # mock-test sometimes asks for response_format json
        content = _mock_completion_content(messages)
        return _Resp(content)


class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()


class FakeGroqClient:
    def __init__(self):
        self.chat = FakeChat()


_ORIG_GET_GROQ = None


def install_groq_mock() -> None:
    global _ORIG_GET_GROQ
    _ORIG_GET_GROQ = server.get_groq_client

    def _fake():
        return FakeGroqClient()

    server.get_groq_client = _fake  # type: ignore


def restore_groq() -> None:
    if _ORIG_GET_GROQ is not None:
        server.get_groq_client = _ORIG_GET_GROQ  # type: ignore


# ── HTTP helpers ───────────────────────────────────────────────────────

class Api:
    def __init__(self):
        server.app.config["TESTING"] = True
        server.app.config["SECRET_KEY"] = "e2e-test-secret"
        try:
            server.init_db()
        except Exception:
            pass
        self.client = server.app.test_client()

    def post(self, path: str, payload: dict | None = None, expect: int | tuple = 200):
        clear_rate_buckets()
        res = self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
        )
        allowed = expect if isinstance(expect, tuple) else (expect,)
        if res.status_code not in allowed:
            body = res.get_data(as_text=True)[:400]
            raise AssertionError(f"POST {path} -> {res.status_code}, expected {allowed}: {body}")
        try:
            return res.get_json(force=True) or {}
        except Exception:
            return {"_raw": res.get_data(as_text=True)}

    def get(self, path: str, expect: int = 200):
        clear_rate_buckets()
        res = self.client.get(path)
        if res.status_code != expect:
            body = res.get_data(as_text=True)[:400]
            raise AssertionError(f"GET {path} -> {res.status_code}: {body}")
        try:
            return res.get_json(force=True) or {}
        except Exception:
            return {"_raw": res.get_data(as_text=True)}

    def patch(self, path: str, payload: dict, expect: int | tuple = 200):
        clear_rate_buckets()
        res = self.client.patch(
            path,
            data=json.dumps(payload),
            content_type="application/json",
        )
        allowed = expect if isinstance(expect, tuple) else (expect,)
        if res.status_code not in allowed:
            raise AssertionError(f"PATCH {path} -> {res.status_code}: {res.get_data(as_text=True)[:300]}")
        return res.get_json(force=True) or {}

    def delete(self, path: str, expect: int | tuple = 200):
        clear_rate_buckets()
        res = self.client.delete(path)
        allowed = expect if isinstance(expect, tuple) else (expect,)
        if res.status_code not in allowed:
            raise AssertionError(f"DELETE {path} -> {res.status_code}: {res.get_data(as_text=True)[:300]}")
        try:
            return res.get_json(force=True) or {}
        except Exception:
            return {}


# ── Prompt bank for 50 chats ───────────────────────────────────────────

def build_chat_prompts(n: int) -> list[tuple[str, bool]]:
    """Return list of (prompt, is_greeting)."""
    bank = [
        ("hello", True),
        ("What is Newton's first law?", False),
        ("Explain inertia with one example.", False),
        ("What is F = ma?", False),
        ("Define acceleration.", False),
        ("thanks", True),
        ("Solve: a car of mass 1000 kg accelerates at 2 m/s^2. Find force.", False),
        ("What is momentum?", False),
        ("Difference between mass and weight?", False),
        ("Riddle: what gets wetter as it dries?", False),
        ("Back to physics — what is action-reaction?", False),
        ("State Newton's third law.", False),
        ("Give a classroom example of third law.", False),
        ("hi", True),
        ("What is photosynthesis in one sentence?", False),
        ("Why are leaves green?", False),
        ("Define velocity vs speed.", False),
        ("What is a scalar quantity?", False),
        ("What is a vector quantity?", False),
        ("Explain displacement.", False),
        ("bye", True),
        ("What is Ohm's law?", False),
        ("V = IR — what does each letter mean?", False),
        ("What is current?", False),
        ("What is resistance?", False),
        ("Quick: unit of force?", False),
        ("Unit of energy?", False),
        ("What is kinetic energy formula?", False),
        ("What is potential energy formula?", False),
        ("ok", True),
        ("Explain friction simply.", False),
        ("Types of friction?", False),
        ("What is gravity?", False),
        ("What is g on Earth approximately?", False),
        ("Define work in physics.", False),
        ("What is power?", False),
        ("Convert 5 km to metres.", False),
        ("What is density?", False),
        ("hello again", True),
        ("Summarize Newton's three laws briefly.", False),
        ("What is a balanced force?", False),
        ("What is an unbalanced force?", False),
        ("Explain free fall.", False),
        ("What is terminal velocity (simple)?", False),
        ("Define pressure.", False),
        ("What is Pascal's principle in one line?", False),
        ("thanks Max", True),
        ("What is a molecule?", False),
        ("What is an atom?", False),
        ("Give one study tip for remembering formulas.", False),
    ]
    out = []
    for i in range(n):
        out.append(bank[i % len(bank)])
    return out


# ── Suite ──────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 64, flush=True)
    print("Study Buddy — full feature reliability suite", flush=True)
    print("=" * 64, flush=True)
    print(f"Chat turns: {CHAT_TURNS}", flush=True)
    print(f"Groq key: {'yes' if HAS_GROQ else 'no'}", flush=True)
    print(
        f"Mode: {'MOCK Groq' if (USE_MOCK and not SKIP_LIVE) else ('LIVE Groq' if not SKIP_LIVE else 'SKIP live AI')}",
        flush=True,
    )
    print(flush=True)

    if USE_MOCK and not SKIP_LIVE:
        install_groq_mock()
        print("[INFO] Installed content-rich Groq mock (default; set SB_LIVE=1 for real Groq).", flush=True)
    print(flush=True)

    print("e2e: creating Api client / init_db…", flush=True)
    api = Api()
    print("e2e: Api ready", flush=True)

    # Health
    def t_health():
        data = api.get("/api/health")
        if not data.get("ok"):
            raise AssertionError(data)
        return "ok"

    run_check("health", t_health)

    # Auth — unique user each run
    uname = f"e2e_{int(time.time()) % 10_000_000}"
    password = "testpass123"

    def t_register():
        data = api.post(
            "/api/auth/register",
            {
                "identifier": uname,
                "password": password,
                "confirmPassword": password,
                "buddyName": "Max",
                "section": "Whiz 1",
            },
            expect=200,
        )
        if data.get("identifier") != uname:
            raise AssertionError(data)
        me = api.get("/api/auth/me")
        if not me.get("loggedIn"):
            raise AssertionError(me)
        return f"user={uname}"

    run_check("auth/register+me", t_register)

    # Conversations CRUD
    conv_id_box: dict[str, Any] = {}

    def t_conversations():
        created = api.post("/api/conversations", {"title": "E2E Chat"}, expect=(200, 201))
        cid = created.get("id") or created.get("conversation_id")
        if not cid:
            # some APIs return the row differently
            listing = api.get("/api/conversations")
            convs = listing.get("conversations") or listing.get("items") or []
            if not convs:
                raise AssertionError(f"no conversation id: {created} / {listing}")
            cid = convs[0].get("id")
        conv_id_box["id"] = cid
        msgs = api.get(f"/api/conversations/{cid}/messages")
        if "messages" not in msgs and not isinstance(msgs, list):
            # accept either shape
            if not isinstance(msgs, dict):
                raise AssertionError(msgs)
        listing = api.get("/api/conversations")
        return f"conv_id={cid}, listed={bool(listing)}"

    run_check("conversations/create+list", t_conversations)

    # Living Notebook CRUD
    nb_id_box: dict[str, Any] = {}

    def t_notebook():
        entry = api.post(
            "/api/notebook/entry",
            {
                "subject": "Physics",
                "category": "Key Points",
                "content": "E2E: F = ma is Newton's second law.",
            },
            expect=201,
        )
        eid = entry.get("id")
        if not eid:
            raise AssertionError(entry)
        nb_id_box["id"] = eid
        listing = api.get("/api/notebook")
        entries = listing.get("entries") or listing.get("notebook") or []
        if isinstance(listing, dict) and not entries:
            # some responses nest differently
            for v in listing.values():
                if isinstance(v, list):
                    entries = v
                    break
        api.patch(f"/api/notebook/entry/{eid}", {"content": "E2E updated: F = ma."})
        api.delete(f"/api/notebook/entry/{eid}", expect=(200, 204))
        return f"entry_id={eid}"

    run_check("notebook/crud", t_notebook)

    # Mistake vault
    def t_mistakes():
        row = api.post(
            "/api/mistakes",
            {
                "subject": "Physics",
                "topic": "Forces",
                "question": "What is F=ma?",
                "wrong_answer": "F=m/a",
                "correct_answer": "F=ma",
                "explanation": "Force equals mass times acceleration.",
                "source_type": "e2e",
            },
            expect=201,
        )
        mid = row.get("id")
        if not mid:
            raise AssertionError(row)
        listing = api.get("/api/mistakes")
        mistakes = listing.get("mistakes") or []
        if not any(m.get("id") == mid for m in mistakes):
            raise AssertionError("created mistake not listed")
        api.delete(f"/api/mistakes/{mid}", expect=(200, 204))
        return f"mistake_id={mid}"

    run_check("mistakes/crud", t_mistakes)

    # Learning DNA
    def t_dna():
        api.post(
            "/api/learning_dna/track",
            {
                "studyMinutes": 5,
                "subject": "Physics",
                "quizResult": {"questionsTaken": 5, "questionsCorrect": 4, "subject": "Physics"},
                "preferredStyle": "Step-by-Step",
                "learningPace": "Steady",
            },
        )
        dash = api.get("/api/learning_dna")
        if not isinstance(dash, dict) or not dash:
            raise AssertionError(dash)
        return "tracked+dashboard"

    run_check("learning_dna/track+get", t_dna)

    # Planner / exams
    def t_exams():
        data = api.get("/api/exams/upcoming")
        if "exams" not in data and "error" in data:
            raise AssertionError(data)
        return f"exams={len(data.get('exams') or [])}"

    run_check("exams/upcoming", t_exams)

    # ── Generative features ────────────────────────────────────────────
    if SKIP_LIVE:
        record("chat/xN", False, "skipped (SB_SKIP_LIVE=1)")
        record("flashcards", False, "skipped (SB_SKIP_LIVE=1)")
        record("quiz", False, "skipped (SB_SKIP_LIVE=1)")
        record("podcast", False, "skipped (SB_SKIP_LIVE=1)")
        record("crosscheck", False, "skipped (SB_SKIP_LIVE=1)")
        record("definitions", False, "skipped (SB_SKIP_LIVE=1)")
        record("formulas", False, "skipped (SB_SKIP_LIVE=1)")
        record("mock-test", False, "skipped (SB_SKIP_LIVE=1)")
    else:
        history: list[dict] = []
        conv_id = None
        prompts = build_chat_prompts(CHAT_TURNS)
        fails = 0
        t0 = time.time()
        print(f"\n--- Chat x{CHAT_TURNS} ---")
        for i, (prompt, is_greeting) in enumerate(prompts, 1):
            clear_rate_buckets()
            history.append({"role": "user", "content": prompt})
            # Keep payload bounded
            payload_msgs = history[-24:]
            try:
                data = api.post(
                    "/api/chat",
                    {
                        "messages": payload_msgs,
                        "conversation_id": conv_id,
                        "grade": 9,
                        "language": "en",
                    },
                )
                if data.get("error") and not data.get("reply"):
                    raise AssertionError(data.get("error"))
                reply = data.get("reply") or ""
                assert_chat_reply(reply, greeting=is_greeting)
                history.append({"role": "assistant", "content": reply})
                if data.get("conversation_id"):
                    conv_id = data["conversation_id"]
                if i % 10 == 0 or i == CHAT_TURNS:
                    print(f"    … {i}/{CHAT_TURNS} ok (last {len(reply)} chars)")
            except Exception as e:
                fails += 1
                print(f"    … {i}/{CHAT_TURNS} FAIL: {e}")
                # still keep a stub so conversation continues
                history.append({"role": "assistant", "content": "(failed turn)"})
            # gentle pacing for live Groq TPM
            if not USE_MOCK and i % 20 == 0:
                time.sleep(2.0)

        elapsed = (time.time() - t0) * 1000
        ok = fails == 0
        record(
            f"chat/x{CHAT_TURNS}",
            ok,
            f"fails={fails}/{CHAT_TURNS}, conv_id={conv_id}",
            elapsed,
        )

        # Seed topic for generative features
        seed = [
            {"role": "user", "content": "Teach me Newton's laws of motion briefly."},
            {
                "role": "assistant",
                "content": (
                    "Newton's first law is inertia. Second law is F=ma. "
                    "Third law is action-reaction. Example: rocket propulsion."
                ),
            },
        ]

        def t_flashcards():
            data = api.post("/api/flashcards", {"messages": seed, "grade": 9, "language": "en"})
            reply = data.get("reply") or ""
            if data.get("error") and not reply:
                raise AssertionError(data.get("error"))
            q_count = len(re.findall(r"(?m)^Q\s*[:：]", reply)) + len(re.findall(r"(?i)Q\d*[:.)]", reply))
            if q_count < 3 and reply.lower().count("q:") < 3:
                # also accept "Question" style
                if reply.lower().count("question") < 3:
                    raise AssertionError(f"expected multiple Q/A cards, got: {reply[:240]}")
            return f"chars={len(reply)}"

        run_check("flashcards", t_flashcards)

        def t_quiz():
            data = api.post("/api/quiz", {"messages": seed, "grade": 9, "language": "en"})
            reply = data.get("reply") or ""
            if data.get("error") and not reply:
                raise AssertionError(data.get("error"))
            if len(reply) < 80:
                raise AssertionError(f"quiz too short: {reply[:120]}")
            return f"chars={len(reply)}"

        run_check("quiz", t_quiz)

        def t_podcast():
            data = api.post("/api/podcast", {"messages": seed, "grade": 9, "language": "en"})
            reply = data.get("reply") or ""
            if data.get("error") and not reply:
                raise AssertionError(data.get("error"))
            if len(reply) < 40:
                raise AssertionError(reply[:120])
            return f"chars={len(reply)}"

        run_check("podcast", t_podcast)

        def t_crosscheck():
            data = api.post(
                "/api/crosscheck",
                {
                    "messages": [
                        {"role": "user", "content": "Question: What is F=ma?\nMy answer: Force equals mass times acceleration."},
                    ],
                    "grade": 9,
                    "language": "en",
                },
            )
            reply = data.get("reply") or ""
            if data.get("error") and not reply:
                raise AssertionError(data.get("error"))
            if len(reply) < 20:
                raise AssertionError(reply[:120])
            return f"chars={len(reply)}"

        run_check("crosscheck", t_crosscheck)

        def t_definitions():
            data = api.post("/api/definitions", {"messages": seed, "grade": 9, "language": "en"})
            reply = data.get("reply") or ""
            if data.get("error") and not reply:
                raise AssertionError(data.get("error"))
            if len(reply) < 20:
                raise AssertionError(reply[:120])
            return f"chars={len(reply)}"

        run_check("definitions", t_definitions)

        def t_formulas():
            data = api.post("/api/formulas", {"topic": "Newton's laws", "grade": 9})
            sheet = data.get("formulas") or data.get("reply") or ""
            if data.get("error") and not sheet:
                raise AssertionError(data.get("error"))
            if len(sheet) < 40:
                raise AssertionError(sheet[:120])
            return f"chars={len(sheet)}"

        run_check("formulas", t_formulas)

        def t_mock():
            data = api.post(
                "/api/mock-test",
                {
                    "subject": "Physics",
                    "exam": "CBSE",
                    "grade": "Class 9",
                    "chapters": "Laws of Motion",
                    "size": "quick",
                },
            )
            if data.get("error") and not data.get("sections") and not data.get("paper"):
                raise AssertionError(data.get("error"))
            sections = data.get("sections") or (data.get("paper") or {}).get("sections") or []
            if not sections and not data.get("title"):
                # some versions return raw under different keys
                if "total_marks" not in data and "reply" not in data:
                    raise AssertionError(f"unexpected mock payload keys: {list(data.keys())}")
            return f"sections={len(sections) if sections else 'n/a'}"

        run_check("mock-test", t_mock)

    restore_groq()

    # Cleanup ephemeral DB
    try:
        if _e2e_db and os.path.isfile(_e2e_db):
            os.remove(_e2e_db)
    except Exception:
        pass

    # Scoreboard
    print("\n" + "=" * 64, flush=True)
    print("SCOREBOARD", flush=True)
    print("=" * 64, flush=True)
    passed = sum(1 for r in RESULTS if r.ok)
    failed = sum(1 for r in RESULTS if not r.ok)
    for r in RESULTS:
        print(
            f"  {'OK' if r.ok else 'FAIL'} {r.name}"
            + (f" — {r.detail}" if r.detail and not r.ok else ""),
            flush=True,
        )
    print("-" * 64, flush=True)
    print(f"TOTAL: {passed} passed, {failed} failed, {len(RESULTS)} checks", flush=True)
    if USE_MOCK and not SKIP_LIVE:
        print("NOTE: Generative checks used MOCK Groq (re-run with SB_LIVE=1 when quota allows).", flush=True)
    print("=" * 64, flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
