"""
==========================================================
  STUDY BUDDY — Backend Server (the "brain" of the app)
==========================================================

Think of this file like the KITCHEN in a restaurant:
  - The customer (your browser) places an order (sends a message)
  - The kitchen (this server) takes that order to the chef (Groq AI)
  - The chef cooks up a response
  - The kitchen sends the food (AI reply) back to the customer

To run this:
  1. Open a terminal / command prompt
  2. Type:  python app.py
  3. Open your browser to:  http://localhost:5000

That's it! 🎉
"""

# =====================================================================
#  STEP 1: IMPORTS — Loading the tools we need
# =====================================================================

import os
import re
import sqlite3
import hashlib
import secrets
import json
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from dotenv import load_dotenv

from groq import Groq


# =====================================================================
#  STEP 2: LOAD THE SECRET GROQ API KEY & INITIALIZE GROQ CLIENT
# =====================================================================

env_path_root = os.path.join(os.path.dirname(__file__), ".env")
env_path_sub = os.path.join(os.path.dirname(__file__), "study_buddy", ".env")

if os.path.exists(env_path_root):
    load_dotenv(env_path_root, override=True)
elif os.path.exists(env_path_sub):
    load_dotenv(env_path_sub, override=True)
else:
    load_dotenv(override=True)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

if not GROQ_API_KEY:
    print("\n[WARNING] No GROQ_API_KEY found!")
    print("   Create a file called  .env  in this folder and add:")
    print("   GROQ_API_KEY=your-key-here\n")

# Centralized Groq Client
_groq_client_instance = None

def get_groq_client():
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        raise ValueError("Server has no GROQ API key configured. Please set GROQ_API_KEY in .env.")
    return Groq(api_key=key)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

def resolve_groq_model(model_name: str) -> str:
    return model_name if model_name else DEFAULT_GROQ_MODEL

SYSTEM_PROMPT = os.getenv(
    "STUDY_BUDDY_SYSTEM_PROMPT",
    "You are a helpful, friendly study buddy for students. "
    "Answer clearly and in simple language. "
    "Adapt your style to the question: be conversational for casual messages, "
    "and thorough for educational topics."
)


# =====================================================================
#  STEP 3: CREATE THE WEB SERVER
# =====================================================================

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "study_buddy_persistent_secret_key_2025")
# Note: Using a fixed secret_key in production — set FLASK_SECRET_KEY in .env
# to keep sessions alive across restarts.

CORS(app, supports_credentials=True)


# =====================================================================
#  STEP 4: DATABASE SETUP — SQLite for persistence
# =====================================================================

DB_PATH = os.path.join(os.path.dirname(__file__), "study_buddy.db")


def get_db():
    """Get a database connection with row_factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # Better concurrency
    conn.execute("PRAGMA foreign_keys=ON")    # Enforce FK constraints
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier  TEXT    NOT NULL UNIQUE,
                password_hash TEXT  NOT NULL,
                buddy_name  TEXT    NOT NULL DEFAULT 'Max',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title       TEXT    NOT NULL DEFAULT 'New Chat',
                pinned      INTEGER NOT NULL DEFAULT 0,
                archived    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT    NOT NULL CHECK(role IN ('user','assistant')),
                content         TEXT    NOT NULL,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_conv_user    ON conversations(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_msg_conv     ON messages(conversation_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS living_notebook (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject     TEXT    NOT NULL DEFAULT 'General',
                category    TEXT    NOT NULL CHECK(category IN ('Key Points', 'Formulas', 'Definitions', 'Mistakes I Made')),
                content     TEXT    NOT NULL,
                position    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_notebook_user ON living_notebook(user_id, subject, category);
        """)
        try:
            conn.execute("ALTER TABLE living_notebook ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass


# =====================================================================
#  STEP 5: AUTH HELPERS
# =====================================================================

def hash_password(password: str) -> str:
    """SHA-256 hash of the password with a salt prefix."""
    salt = "studybuddy_salt_2025"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def current_user_id():
    """Return the logged-in user's DB id, or None."""
    return session.get("user_id")


def require_auth():
    """Return (user_row, None) or (None, error_response)."""
    uid = current_user_id()
    if not uid:
        return None, (jsonify({"error": "Not logged in."}), 401)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        session.clear()
        return None, (jsonify({"error": "User not found."}), 401)
    return row, None


# =====================================================================
#  STEP 6: ROUTES
# =====================================================================

# ── Homepage ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main page."""
    return send_from_directory(".", "index.html")


@app.route("/career-dreamer")
def career_dreamer():
    """Redirect legacy Career Dreamer requests to home."""
    return redirect("/")


# ── Auth Routes ──────────────────────────────────────────────────────

@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """Check if a user is logged in."""
    uid = current_user_id()
    if not uid:
        return jsonify({"loggedIn": False})
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        session.clear()
        return jsonify({"loggedIn": False})
    return jsonify({
        "loggedIn": True,
        "identifier": row["identifier"],
        "buddyName": row["buddy_name"],
    })


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    """Register a new user."""
    data = request.get_json(force=True)
    identifier = (data.get("identifier") or "").strip()
    password   = (data.get("password")   or "").strip()
    buddy_name = (data.get("buddyName")  or "Max").strip()

    if not identifier or not password:
        return jsonify({"error": "Identifier and password are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400

    ph = hash_password(password)
    try:
        with get_db() as conn:
            # Check if exists
            existing = conn.execute("SELECT id FROM users WHERE identifier=?", (identifier,)).fetchone()
            if existing:
                return jsonify({"error": "Identifier already registered."}), 400

            conn.execute(
                "INSERT INTO users (identifier, password_hash, buddy_name) VALUES (?,?,?)",
                (identifier, ph, buddy_name)
            )
            row = conn.execute("SELECT * FROM users WHERE identifier=?", (identifier,)).fetchone()
        session.permanent = True
        session["user_id"] = row["id"]
        return jsonify({"identifier": row["identifier"], "buddyName": row["buddy_name"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """Log in with identifier + password."""
    data = request.get_json(force=True)
    identifier = (data.get("identifier") or "").strip()
    password   = (data.get("password")   or "").strip()

    if not identifier or not password:
        return jsonify({"error": "Identifier and password are required."}), 400

    ph = hash_password(password)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE identifier=? AND password_hash=?",
            (identifier, ph)
        ).fetchone()

    if not row:
        return jsonify({"error": "Invalid identifier or password."}), 401

    session.permanent = True
    session["user_id"] = row["id"]
    return jsonify({"identifier": row["identifier"], "buddyName": row["buddy_name"]})

@app.route("/api/auth/update_buddy", methods=["POST"])
def auth_update_buddy():
    """Update buddy name for logged in user."""
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "Not logged in."}), 401
    
    data = request.get_json(force=True)
    buddy_name = (data.get("buddyName") or "Max").strip()
    
    with get_db() as conn:
        conn.execute("UPDATE users SET buddy_name=? WHERE id=?", (buddy_name, uid))
        
    return jsonify({"ok": True, "buddyName": buddy_name})


@app.route("/api/auth/update_username", methods=["POST"])
def auth_update_username():
    """Update username (identifier) for logged in user."""
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "Not logged in."}), 401
    
    data = request.get_json(force=True)
    new_identifier = (data.get("identifier") or "").strip()
    
    if not new_identifier:
        return jsonify({"error": "Username cannot be empty."}), 400
        
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE identifier=?", (new_identifier,)).fetchone()
        if existing and existing["id"] != uid:
            return jsonify({"error": "Username already taken."}), 400
            
        conn.execute("UPDATE users SET identifier=? WHERE id=?", (new_identifier, uid))
        
    return jsonify({"ok": True, "identifier": new_identifier})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """Clear the session."""
    session.clear()
    return jsonify({"ok": True})


# ── Conversation Routes ───────────────────────────────────────────────

@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    """List all conversations for the logged-in user, newest first."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.title, c.pinned, c.archived, c.created_at, c.updated_at,
                   (SELECT content FROM messages WHERE conversation_id=c.id ORDER BY created_at ASC LIMIT 1) AS first_msg
            FROM conversations c
            WHERE c.user_id=?
            ORDER BY c.pinned DESC, c.updated_at DESC
        """, (user["id"],)).fetchall()

    return jsonify({"conversations": [dict(r) for r in rows]})


@app.route("/api/conversations", methods=["POST"])
def create_conversation():
    """Create a new conversation."""
    user, err = require_auth()
    if err:
        return err

    data  = request.get_json(force=True) or {}
    title = (data.get("title") or "New Chat").strip()[:100]

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, title) VALUES (?,?)",
            (user["id"], title)
        )
        conv_id = cur.lastrowid
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()

    return jsonify(dict(row)), 201


@app.route("/api/conversations/<int:conv_id>", methods=["PATCH"])
def update_conversation(conv_id):
    """Rename, pin/unpin, or archive/unarchive a conversation."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user["id"])
        ).fetchone()
        if not row:
            return jsonify({"error": "Conversation not found."}), 404

        data = request.get_json(force=True) or {}
        fields, vals = [], []

        if "title" in data:
            fields.append("title=?")
            vals.append(data["title"].strip()[:100] or "New Chat")
        if "pinned" in data:
            fields.append("pinned=?")
            vals.append(1 if data["pinned"] else 0)
        if "archived" in data:
            fields.append("archived=?")
            vals.append(1 if data["archived"] else 0)

        if fields:
            fields.append("updated_at=datetime('now')")
            vals.append(conv_id)
            conn.execute(
                f"UPDATE conversations SET {', '.join(fields)} WHERE id=?",
                vals
            )
        updated = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()

    return jsonify(dict(updated))


@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    """Delete a conversation and all its messages."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user["id"])
        ).fetchone()
        if not row:
            return jsonify({"error": "Conversation not found."}), 404
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    return jsonify({"ok": True})


@app.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
def get_messages(conv_id):
    """Return all messages for a conversation."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user["id"])
        ).fetchone()
        if not row:
            return jsonify({"error": "Conversation not found."}), 404

        msgs = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
            (conv_id,)
        ).fetchall()

    return jsonify({"messages": [dict(m) for m in msgs]})


@app.route("/api/conversations/<int:conv_id>/messages", methods=["POST"])
def post_message(conv_id):
    """Append a single message to a conversation (used internally)."""
    user, err = require_auth()
    if err:
        return err

    data    = request.get_json(force=True) or {}
    role    = data.get("role", "user")
    content = (data.get("content") or "").strip()

    if role not in ("user", "assistant") or not content:
        return jsonify({"error": "Invalid role or empty content."}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user["id"])
        ).fetchone()
        if not row:
            return jsonify({"error": "Conversation not found."}), 404

        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
            (conv_id, role, content)
        )
        conn.execute(
            "UPDATE conversations SET updated_at=datetime('now') WHERE id=?",
            (conv_id,)
        )

    return jsonify({"ok": True}), 201


# ── Legacy history routes (kept for backward compat) ─────────────────

@app.route("/api/history", methods=["GET"])
def get_history():
    """Legacy: return messages from the most recent conversation."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE user_id=? AND archived=0 ORDER BY updated_at DESC LIMIT 1",
            (user["id"],)
        ).fetchone()
        if not conv:
            return jsonify({"messages": []})
        msgs = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
            (conv["id"],)
        ).fetchall()

    return jsonify({"messages": [dict(m) for m in msgs]})


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    """Legacy: delete all conversations for the current user."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        conn.execute("DELETE FROM conversations WHERE user_id=?", (user["id"],))

    return jsonify({"ok": True})


GREETING_TITLE_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|howdy|good\s*(morning|afternoon|evening|night)|greetings|what'?s\s*up|sup|yo|hiya|thanks?|thank\s*you|ty|thx|cheers|ok(ay)?|great|awesome|cool|nice|bye+|goodbye|see\s*ya|later|cya|got\s*it|sure|no\s*problem|np|alright|ok+)\s*[!?.]*\s*$",
    re.IGNORECASE
)

def generate_smart_title(client, user_msg: str, target_model: str) -> str:
    """Generate a short natural title (3-5 words) using Groq AI from conversation prompt.
    Returns empty string if user_msg is a pure greeting/smalltalk.
    """
    cleaned = (user_msg or "").strip()
    if not cleaned or GREETING_TITLE_RE.match(cleaned):
        return ""

    try:
        title_prompt = (
            "Generate a short, concise, natural title (3 to 5 words maximum) for a study chat conversation "
            "based on the following student query. "
            "Return ONLY the title text as plain text. Do not use quotes, punctuation, markdown, or prefix words.\n\n"
            f"Student Query: {cleaned}"
        )
        res = client.chat.completions.create(
            model=target_model,
            messages=[{"role": "user", "content": title_prompt}],
            max_tokens=15,
            temperature=0.3
        )
        raw_title = (res.choices[0].message.content or "").strip()
        raw_title = re.sub(r'^[#*"`\'\s]+|[#*"`\'\s\.]+$', '', raw_title)
        if raw_title and len(raw_title) <= 50:
            return raw_title
    except Exception as e:
        print(f"[WARNING] Failed to generate smart AI title: {e}")

    return cleaned[:40].strip()


# ── Chat (main AI endpoint) ───────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
@app.route("/api/podcast", methods=["POST"])
@app.route("/api/flashcards", methods=["POST"])
@app.route("/api/quiz", methods=["POST"])
@app.route("/api/crosscheck", methods=["POST"])
def chat():
    """
    Handle chat, podcast generation, flashcard generation, quiz generation, and crosscheck generation.
    For /api/chat: also persists messages to SQLite (auto-creates conversation on first message).
    """
    data = request.get_json(force=True)

    endpoint   = request.path.split("/")[-1]
    messages   = data.get("messages", [])
    model_name = data.get("model", "llama-3.3-70b-versatile")
    notes      = data.get("notes", "")
    conv_id    = data.get("conversation_id")   # may be None (first message)
    system_prompt = SYSTEM_PROMPT

    # Clean messages
    messages = [
        msg for msg in messages
        if isinstance(msg, dict)
        and msg.get("role") in {"user", "assistant"}
        and isinstance(msg.get("content"), str)
        and msg["content"].strip()
    ]

    # Endpoint-specific system prompt enhancement
    if endpoint == "chat":
        system_prompt = (
            f"{system_prompt}\n\n"
            "RESPONSE STYLE RULES — follow these precisely:\n\n"
            "1. GREETINGS & SMALL TALK (e.g. 'Hello', 'Hi', 'Thanks', 'Good morning', 'Bye'):\n"
            "   → Reply warmly in ONE or TWO natural sentences. Stop there.\n"
            "   → Do NOT add steps, numbered lists, or any instruction like 'say move to next step'.\n\n"
            "2. EDUCATIONAL QUESTIONS (explanations, definitions, history, biology, literature, geography):\n"
            "   → Explain clearly and naturally. Use prose or bullet points as appropriate.\n"
            "   → Do NOT add 'move to next step', 'hint for next step', or any similar prompt.\n\n"
            "3. MATHS / PHYSICS / CHEMISTRY PROBLEM-SOLVING (equations, calculations, derivations, proofs):\n"
            "   → Work through the solution in numbered steps labelled exactly: 'Step 1:', 'Step 2:', etc.\n"
            "   → Show full working. One idea per step.\n"
            "   → Do NOT instruct the user to type anything — the UI handles progression.\n\n"
            "IMPORTANT: Never end any response with 'say move to next step', "
            "'type move to next step', 'hint for next step', or 'explain in simpler terms' "
            "as a prompt for the user. The interface provides those buttons automatically."
        )
    elif endpoint == "podcast":
        system_prompt = (
            f"{system_prompt}\n\n"
            "You are a scriptwriter for an engaging, educational podcast featuring two hosts: Host A and Host B. "
            "Based on the provided context, generate a natural, engaging, and human-like conversational podcast script. "
            "The podcast should feature a dynamic back-and-forth between the two hosts. "
            "Host A can lead the discussion while Host B asks insightful questions and provides reactions. "
            "If there is a lot of content, make the podcast long and detailed (spanning 5-10 minutes of spoken audio). "
            "Produce ONLY the spoken text (plain, TTS-ready) with a natural, conversational tone. "
            "Do not use symbols like # or @; avoid code blocks, headers, or markup. "
            "Format the script simply as 'Host A: [text]' and 'Host B: [text]' on separate lines. "
            "Return only the script suitable for text-to-speech in English."
        )
    elif endpoint == "flashcards":
        system_prompt = (
            f"{system_prompt}\n\n"
            "Using the full conversation history from the entire chat session, including earlier "
            "questions and answers, create flashcard questions and answers in English. Return them "
            "as a list of Q&A pairs. Format: 'Q: [question]\nA: [answer]' on separate lines. "
            "Create 5-10 cards."
        )
    elif endpoint == "quiz":
        system_prompt = (
            f"{system_prompt}\n\n"
            "Using the full conversation history from the entire chat session, including earlier "
            "questions and answers, create a quiz with 5 multiple choice questions in English. "
            "Format each as: 'Q[number]: [question]\nA) [option]\nB) [option]\nC) [option]\n"
            "D) [option]\nAnswer: [correct letter]' on separate lines."
        )
    elif endpoint == "crosscheck":
        system_prompt = (
            f"{system_prompt}\n\n"
            "Using the full conversation history from the entire chat session, review the student's "
            "question and answer provided below in English. If the answer is wrong, explain exactly "
            "where it is incorrect, show how to fix it, and reveal the correct answer. Do not only "
            "give hints; provide a clear correction and the correct response."
        )

    if isinstance(notes, str) and notes.strip():
        notes_stripped = notes.strip()
        system_prompt = (
            f"{system_prompt}\n\n"
            f"CONTEXT: The student has uploaded the following study notes:\n"
            f"--- START OF NOTES ---\n{notes_stripped}\n--- END OF NOTES ---\n\n"
            f"IMPORTANT: You must answer mainly with respect to the provided study notes above. "
            f"Prioritize using the information in these notes to answer the user's questions and "
            f"generate any content (podcasts, quizzes, flashcards, or reviews). However, if the user "
            f"asks a question or requests something that is not covered in these notes, you MUST still "
            f"answer the question and fulfill the request fully using your general knowledge."
        )


    if not messages:
        return jsonify({"error": "No messages provided."}), 400

    # --- Talk to Groq AI ---
    try:
        client = get_groq_client()
        target_model = resolve_groq_model(model_name)

        groq_messages = []
        if system_prompt:
            groq_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = "assistant" if msg["role"] == "assistant" else "user"
            groq_messages.append({
                "role": role,
                "content": msg["content"]
            })

        response = client.chat.completions.create(
            model=target_model,
            messages=groq_messages,
        )
        reply = response.choices[0].message.content
        last_message = messages[-1]["content"] if messages else ""

        # --- Persist to DB (only for /api/chat when user is logged in) ---
        if endpoint == "chat":
            uid = current_user_id()
            if uid:
                with get_db() as conn:
                    # Auto-create conversation if no conv_id given
                    if not conv_id:
                        smart_title = generate_smart_title(client, last_message, target_model)
                        title = smart_title if smart_title else "New Chat"
                        cur = conn.execute(
                            "INSERT INTO conversations (user_id, title) VALUES (?,?)",
                            (uid, title)
                        )
                        conv_id = cur.lastrowid
                    else:
                        # If conversation exists and title is still default 'New Chat', attempt smart title generation on first meaningful question
                        conv_row = conn.execute("SELECT id, title FROM conversations WHERE id=? AND user_id=?", (conv_id, uid)).fetchone()
                        if conv_row and conv_row["title"] == "New Chat":
                            smart_title = generate_smart_title(client, last_message, target_model)
                            if smart_title:
                                conn.execute("UPDATE conversations SET title=?, updated_at=datetime('now') WHERE id=?", (smart_title, conv_id))

                    # Always verify conv belongs to user before writing
                    conv_row = conn.execute(
                        "SELECT id FROM conversations WHERE id=? AND user_id=?",
                        (conv_id, uid)
                    ).fetchone()

                    if conv_row:
                        # Save the last user message
                        user_msg = messages[-1]["content"]
                        # Check if already saved (idempotency: only insert if not already the last msg)
                        last_db = conn.execute(
                            "SELECT content, role FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1",
                            (conv_id,)
                        ).fetchone()
                        if not last_db or last_db["role"] != "user" or last_db["content"] != user_msg:
                            conn.execute(
                                "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                                (conv_id, "user", user_msg)
                            )

                        # Save AI reply
                        conn.execute(
                            "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                            (conv_id, "assistant", reply)
                        )

                        conn.execute(
                            "UPDATE conversations SET updated_at=datetime('now') WHERE id=?",
                            (conv_id,)
                        )

        return jsonify({"reply": reply, "conversation_id": conv_id})

    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Groq API: {error_msg}")
        return jsonify({"error": error_msg}), 500


# ── Living Notebook Routes ───────────────────────────────────────────

VALID_NOTEBOOK_CATEGORIES = {'Key Points', 'Formulas', 'Definitions', 'Mistakes I Made', 'Things to Revise', 'My Own Notes'}


@app.route("/api/notebook", methods=["GET"])
def get_notebook_entries():
    """Retrieve all notebook entries for the logged-in user."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, user_id, subject, category, content, position, created_at, updated_at
            FROM living_notebook
            WHERE user_id=?
            ORDER BY subject ASC, category ASC, position ASC, updated_at DESC
        """, (user["id"],)).fetchall()

    return jsonify({"entries": [dict(r) for r in rows]})


@app.route("/api/notebook/reorder", methods=["POST"])
def reorder_notebook_entries():
    """Reorder notebook entries."""
    user, err = require_auth()
    if err:
        return err

    data = request.get_json(force=True) or {}
    orders = data.get("orders", [])

    if not isinstance(orders, list):
        return jsonify({"error": "Invalid order format."}), 400

    with get_db() as conn:
        for item in orders:
            entry_id = item.get("id")
            pos = item.get("position", 0)
            if entry_id:
                conn.execute(
                    "UPDATE living_notebook SET position=? WHERE id=? AND user_id=?",
                    (pos, entry_id, user["id"])
                )

    return jsonify({"ok": True})


@app.route("/api/notebook/entry", methods=["POST"])
def add_notebook_entry():
    """Add a new notebook entry."""
    user, err = require_auth()
    if err:
        return err

    data = request.get_json(force=True) or {}
    subject = (data.get("subject") or "General").strip()[:50]
    category = (data.get("category") or "Key Points").strip()
    content = (data.get("content") or "").strip()

    if category not in VALID_NOTEBOOK_CATEGORIES:
        return jsonify({"error": f"Invalid category. Must be one of {list(VALID_NOTEBOOK_CATEGORIES)}"}), 400

    if not content:
        return jsonify({"error": "Content cannot be empty."}), 400

    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO living_notebook (user_id, subject, category, content)
            VALUES (?, ?, ?, ?)
        """, (user["id"], subject, category, content))
        entry_id = cur.lastrowid
        row = conn.execute("SELECT * FROM living_notebook WHERE id=?", (entry_id,)).fetchone()

    return jsonify(dict(row)), 201


@app.route("/api/notebook/entry/<int:entry_id>", methods=["PATCH"])
def update_notebook_entry(entry_id):
    """Update an existing notebook entry."""
    user, err = require_auth()
    if err:
        return err

    data = request.get_json(force=True) or {}
    content = data.get("content")
    subject = data.get("subject")
    category = data.get("category")

    with get_db() as conn:
        row = conn.execute("SELECT * FROM living_notebook WHERE id=? AND user_id=?", (entry_id, user["id"])).fetchone()
        if not row:
            return jsonify({"error": "Entry not found."}), 404

        fields = []
        vals = []

        if content is not None:
            cleaned_content = str(content).strip()
            if not cleaned_content:
                return jsonify({"error": "Content cannot be empty."}), 400
            fields.append("content=?")
            vals.append(cleaned_content)

        if subject is not None:
            fields.append("subject=?")
            vals.append(str(subject).strip()[:50] or "General")

        if category is not None:
            cleaned_cat = str(category).strip()
            if cleaned_cat not in VALID_NOTEBOOK_CATEGORIES:
                return jsonify({"error": "Invalid category."}), 400
            fields.append("category=?")
            vals.append(cleaned_cat)

        if fields:
            fields.append("updated_at=datetime('now')")
            vals.append(entry_id)
            conn.execute(f"UPDATE living_notebook SET {', '.join(fields)} WHERE id=?", vals)

        updated = conn.execute("SELECT * FROM living_notebook WHERE id=?", (entry_id,)).fetchone()

    return jsonify(dict(updated))


@app.route("/api/notebook/entry/<int:entry_id>", methods=["DELETE"])
def delete_notebook_entry(entry_id):
    """Delete a notebook entry."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        row = conn.execute("SELECT id FROM living_notebook WHERE id=? AND user_id=?", (entry_id, user["id"])).fetchone()
        if not row:
            return jsonify({"error": "Entry not found."}), 404
        conn.execute("DELETE FROM living_notebook WHERE id=?", (entry_id,))

    return jsonify({"ok": True})


@app.route("/api/notebook/ai_extract", methods=["POST"])
def notebook_ai_extract():
    """Extract key points, formulas, definitions, and mistakes from text or chat history and save to notebook."""
    import json as _json
    import re as _re

    data = request.get_json(force=True) or {}
    text_to_extract = (data.get("text") or "").strip()
    model_name = data.get("model", "llama-3.3-70b-versatile")
    default_subject = (data.get("subject") or "General").strip()

    if not text_to_extract:
        return jsonify({"error": "No content provided to extract notes from."}), 400

    system_prompt = (
        "You are an AI study assistant. Read the provided text or study notes carefully and extract important study information. "
        "Categorize each extracted note into ONE of six categories: 'Key Points', 'Formulas', 'Definitions', 'Mistakes I Made', 'Things to Revise', or 'My Own Notes'. "
        "Assign a suitable subject (e.g. Physics, Mathematics, Chemistry, Biology, History, English, General) for each note. "
        "Keep each note content clear, concise, and educational. "
        "Return ONLY a valid JSON object with the key 'items', where 'items' is an array of objects. "
        "Each object must have 'subject', 'category', and 'content' fields. No markdown fences or extra text."
    )

    user_prompt = f"""Extract all key study notes from the following text:

--- CONTENT START ---
{text_to_extract}
--- CONTENT END ---

If default subject '{default_subject}' is appropriate, use it unless the content clearly specifies another subject (e.g., Physics, Math, Chemistry, Biology).
Return ONLY JSON format:
{{
  "items": [
    {{
      "subject": "Physics",
      "category": "Formulas",
      "content": "F = m * a (Force = mass * acceleration)"
    }}
  ]
}}
"""

    try:
        client = get_groq_client()
        target_model = resolve_groq_model(model_name)

        response = client.chat.completions.create(
            model=target_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )

        reply_text = response.choices[0].message.content.strip()
        if "```" in reply_text:
            reply_text = _re.sub(r"```(?:json)?\s*", "", reply_text)
            reply_text = _re.sub(r"```\s*$", "", reply_text).strip()

        parsed = _json.loads(reply_text)
        items = parsed.get("items", [])

        valid_items = []
        uid = current_user_id()

        with get_db() as conn:
            for item in items:
                subj = (item.get("subject") or default_subject or "General").strip()[:50]
                cat = (item.get("category") or "Key Points").strip()
                cnt = (item.get("content") or "").strip()

                if cat not in VALID_NOTEBOOK_CATEGORIES:
                    cat = "Key Points"

                if cnt:
                    inserted_id = None
                    if uid:
                        cur = conn.execute("""
                            INSERT INTO living_notebook (user_id, subject, category, content)
                            VALUES (?, ?, ?, ?)
                        """, (uid, subj, cat, cnt))
                        inserted_id = cur.lastrowid

                    valid_items.append({
                        "id": inserted_id,
                        "subject": subj,
                        "category": cat,
                        "content": cnt
                    })

        return jsonify({"extracted": valid_items})

    except Exception as e:
        print(f"[ERROR] AI Extract: {e}")
        return jsonify({"error": str(e)}), 500


# ── Career Analyzer ───────────────────────────────────────────────────

@app.route("/api/career-analyze", methods=["POST"])
def career_analyze():
    """Generate AI career analysis report based on student assessment answers."""
    import json as _json
    import re as _re


    data = request.get_json(force=True)
    answers    = data.get("answers", {})
    model_name = data.get("model", "llama-3.3-70b-versatile")

    if not answers:
        return jsonify({"error": "No assessment answers provided."}), 200

    career_system_prompt = (
        "You are an expert career counselor and education advisor specialising in "
        "helping Grade 9\u201310 ICSE students in India discover their ideal career paths. "
        "You analyse student profiles holistically, considering interests, skills, "
        "personality, and the Indian education system (ICSE, streams after Grade 10, "
        "entrance exams like JEE, NEET, CLAT, NID, NLU, CA CPT, etc.). "
        "Always return ONLY raw valid JSON \u2014 no markdown code fences, no extra text."
    )

    answers_text = "\n".join(
        [f"- {k}: {v}" for k, v in answers.items() if v and str(v).strip()]
    )

    prompt = f"""Analyse this Grade 9\u201310 ICSE student profile and generate a comprehensive career guidance report.

STUDENT PROFILE:
{answers_text}

Return ONLY a valid JSON object with this exact structure (no markdown, no code fences, no extra text):

{{
  "careers": [
    {{
      "rank": 1,
      "name": "Career Name",
      "icon": "single emoji",
      "compatibility": 92,
      "why_matches": "Detailed 2\u20133 sentence explanation personalised to this student's profile...",
      "required_strengths": ["Strength 1", "Strength 2", "Strength 3"],
      "weaknesses_to_improve": ["Area 1", "Area 2"],
      "grade_9_10_subjects": ["Subject 1", "Subject 2", "Subject 3"],
      "recommended_streams": ["Science (PCM)", "Commerce"],
      "entrance_exams": ["Exam 1", "Exam 2"],
      "college_degree": "Degree name and duration",
      "avg_salary_india": "\u20b9X\u2013Y LPA (entry to senior level)",
      "future_demand": "Short description of demand trend and growth rate",
      "difficulty_level": "High / Medium / Low",
      "daily_work_life": "2\u20133 sentences describing a typical workday...",
      "pros": ["Pro 1", "Pro 2", "Pro 3"],
      "cons": ["Con 1", "Con 2"],
      "ai_future_impact": "2 sentences on how AI will affect this career in 10\u201315 years...",
      "alternative_careers": ["Alternative 1", "Alternative 2", "Alternative 3"]
    }}
  ],
  "summary": "2\u20133 sentence personalised summary of the student's overall profile and career direction."
}}

Provide exactly 5 careers ranked by compatibility (highest first, range 60\u201398). Be specific, realistic, and use the Indian education context."""

    try:
        client = get_groq_client()
        target_model = resolve_groq_model(model_name)

        groq_messages = [
            {"role": "system", "content": career_system_prompt},
            {"role": "user", "content": prompt}
        ]

        response = client.chat.completions.create(
            model=target_model,
            messages=groq_messages,
            response_format={"type": "json_object"}
        )

        reply_text = response.choices[0].message.content.strip()

        if "```" in reply_text:
            reply_text = _re.sub(r"```(?:json)?\s*", "", reply_text)
            reply_text = _re.sub(r"```\s*$", "", reply_text).strip()

        parsed = _json.loads(reply_text)

        if "careers" not in parsed or not isinstance(parsed["careers"], list):
            return jsonify({"error": "AI returned an invalid report structure. Please try again."}), 200

        return jsonify({"report": parsed})

    except _json.JSONDecodeError as e:
        return jsonify({"error": f"AI returned invalid JSON. Please retry. ({str(e)})"}), 200
    except Exception as e:
        error_msg = str(e)
        return jsonify({"error": error_msg}), 200


# =====================================================================
#  STEP 7: START THE SERVER
# =====================================================================

if __name__ == "__main__":
    init_db()
    print("\n[STARTING] Study Buddy is running!")
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
