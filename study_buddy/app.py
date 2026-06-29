"""
==========================================================
  STUDY BUDDY — Backend Server (the "brain" of the app)
==========================================================

Think of this file like the KITCHEN in a restaurant:
  - The customer (your browser) places an order (sends a message)
  - The kitchen (this server) takes that order to the chef (Gemini AI)
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
import sqlite3
import re
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai


# =====================================================================
#  STEP 2: LOAD CONFIG & SECRETS
# =====================================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    print("\n[WARNING] No GEMINI_API_KEY found!")
    print("   Create a file called  .env  in this folder and add:")
    print("   GEMINI_API_KEY=your-key-here\n")

genai.configure(api_key=GEMINI_API_KEY)

# Secret key for signing session cookies.
# On Render/production set SECRET_KEY in the environment dashboard.
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(32).hex())

# Path to the SQLite database file (lives next to app.py)
DB_PATH = os.path.join(os.path.dirname(__file__), "studybuddy.db")

# Hidden system prompt stored on the server only.
SYSTEM_PROMPT = os.getenv(
    "STUDY_BUDDY_SYSTEM_PROMPT",
    (
        "You are a helpful study buddy. Answer clearly, gently, and in simple language for a student. "
        "Always answer questions step by step. Number each step and include a short hint for the next step when appropriate.\n\n"
        "VISUAL AIDS — CHARTS:\n"
        "When your answer involves data that is easier to understand visually — such as comparisons between values, "
        "trends over time, distributions, rankings, or historical timelines — include ONE chart in your response "
        "using this exact format on its own line:\n\n"
        "[CHART]\n"
        "{\"type\":\"bar\",\"title\":\"Chart Title\",\"labels\":[\"Label1\",\"Label2\"],\"data\":[10,20]}\n"
        "[/CHART]\n\n"
        "Chart rules:\n"
        "- type must be: bar, line, or pie\n"
        "- For a simple single-series chart use: {\"type\":\"bar\",\"title\":\"...\",\"labels\":[...],\"data\":[...]}\n"
        "- For multiple series use: {\"type\":\"line\",\"title\":\"...\",\"labels\":[...],\"datasets\":[{\"label\":\"Series A\",\"data\":[...]},{\"label\":\"Series B\",\"data\":[...]}]}\n"
        "- Only output a chart when data or comparison genuinely helps the student\n"
        "- Never output a chart for a plain question, definition, or concept explanation that has no numerical data\n"
        "- Never make up numbers — only chart real, factually correct values\n"
        "- Keep labels short (1-4 words)\n"
        "- Place the [CHART]...[/CHART] block AFTER your text explanation, never before"
    )
)


# =====================================================================
#  STEP 3: DATABASE SETUP
# =====================================================================

def get_db():
    """Open a database connection (one per request)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")  # safer concurrent writes
    return conn


def init_db():
    """Create tables if they don't exist yet."""
    with get_db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    identifier   TEXT    NOT NULL UNIQUE,
                    password_hash TEXT   NOT NULL,
                    buddy_name   TEXT    NOT NULL DEFAULT 'Nova',
                    created_at   TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role       TEXT    NOT NULL CHECK(role IN ('user','assistant')),
                    content    TEXT    NOT NULL,
                    created_at TEXT    NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chat_history_user_id ON chat_history (user_id);
            """)
            print("[DB] Database ready ->", DB_PATH)


# =====================================================================
#  STEP 4: CREATE THE WEB SERVER
# =====================================================================

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

CORS(app, supports_credentials=True)


# ───────────────────────────────────────────────
#  HELPER: Validate identifier (email or mobile)
# ───────────────────────────────────────────────
EMAIL_RE    = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MOBILE_RE   = re.compile(r"^\+?[0-9]{7,15}$")

def is_valid_identifier(val: str) -> bool:
    return bool(EMAIL_RE.match(val) or MOBILE_RE.match(val))


def current_user_id():
    """Return the logged-in user's id, or None."""
    return session.get("user_id")


# =====================================================================
#  STEP 5: ROUTES
# =====================================================================

# ── Pages ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/career-dreamer")
def career_dreamer():
    return send_from_directory(".", "career-dreamer.html")


# ── AUTH: Register ──────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(force=True)
    identifier  = (data.get("identifier") or "").strip().lower()
    password    = (data.get("password")   or "").strip()
    buddy_name  = (data.get("buddyName")  or "Nova").strip()[:40]

    if not identifier or not password:
        return jsonify({"error": "Email/mobile and password are required."}), 400
    if not is_valid_identifier(identifier):
        return jsonify({"error": "Enter a valid email address or mobile number (e.g. +91XXXXXXXXXX)."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400

    pw_hash = generate_password_hash(password)
    now     = datetime.utcnow().isoformat()

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (identifier, password_hash, buddy_name, created_at) VALUES (?,?,?,?)",
                (identifier, pw_hash, buddy_name, now)
            )
            user = conn.execute(
                "SELECT id, identifier, buddy_name FROM users WHERE identifier=?", (identifier,)
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with this email/mobile already exists."}), 409

    session["user_id"]     = user["id"]
    session["identifier"]  = user["identifier"]
    session["buddy_name"]  = user["buddy_name"]
    return jsonify({"ok": True, "identifier": user["identifier"], "buddyName": user["buddy_name"]}), 201


# ── AUTH: Login ─────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data       = request.get_json(force=True)
    identifier = (data.get("identifier") or "").strip().lower()
    password   = (data.get("password")   or "").strip()

    if not identifier or not password:
        return jsonify({"error": "Email/mobile and password are required."}), 400

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, identifier, password_hash, buddy_name FROM users WHERE identifier=?",
            (identifier,)
        ).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Incorrect email/mobile or password."}), 401

    session["user_id"]    = user["id"]
    session["identifier"] = user["identifier"]
    session["buddy_name"] = user["buddy_name"]
    return jsonify({"ok": True, "identifier": user["identifier"], "buddyName": user["buddy_name"]})


# ── AUTH: Logout ────────────────────────────────────────────────────

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


# ── AUTH: Current user ──────────────────────────────────────────────

@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    uid = current_user_id()
    if not uid:
        return jsonify({"loggedIn": False}), 200
    return jsonify({
        "loggedIn":   True,
        "identifier": session.get("identifier"),
        "buddyName":  session.get("buddy_name"),
    })


# ── HISTORY: Get ────────────────────────────────────────────────────

@app.route("/api/history", methods=["GET"])
def history_get():
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "Not logged in."}), 401

    limit = min(int(request.args.get("limit", 200)), 500)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)
        ).fetchall()

    messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    return jsonify({"messages": messages})


# ── HISTORY: Save a pair (user + assistant) ─────────────────────────

@app.route("/api/history", methods=["POST"])
def history_save():
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "Not logged in."}), 401

    data = request.get_json(force=True)
    messages = data.get("messages", [])
    now  = datetime.utcnow().isoformat()

    rows = [
        (uid, m["role"], m["content"], now)
        for m in messages
        if isinstance(m, dict)
        and m.get("role") in {"user", "assistant"}
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    if not rows:
        return jsonify({"error": "No valid messages."}), 400

    with get_db() as conn:
        conn.executemany(
            "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?,?,?,?)",
            rows
        )
    return jsonify({"ok": True, "saved": len(rows)})


# ── HISTORY: Clear ──────────────────────────────────────────────────

@app.route("/api/history", methods=["DELETE"])
def history_clear():
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "Not logged in."}), 401
    with get_db() as conn:
        conn.execute("DELETE FROM chat_history WHERE user_id=?", (uid,))
    return jsonify({"ok": True})


# ── CHAT / AI endpoints ─────────────────────────────────────────────

@app.route("/api/chat",        methods=["POST"])
@app.route("/api/podcast",     methods=["POST"])
@app.route("/api/flashcards",  methods=["POST"])
@app.route("/api/quiz",        methods=["POST"])
@app.route("/api/crosscheck",  methods=["POST"])
@app.route("/api/definitions", methods=["POST"])
@app.route("/api/dictionary",  methods=["POST"])
def chat():
    data     = request.get_json(force=True)
    endpoint = request.path.split("/")[-1]

    system_prompt = SYSTEM_PROMPT
    messages   = data.get("messages", [])
    model_name = data.get("model", "gemini-2.0-flash")
    notes      = data.get("notes", "")

    messages = [
        msg for msg in messages
        if isinstance(msg, dict)
        and msg.get("role") in {"user", "assistant"}
        and isinstance(msg.get("content"), str)
        and msg["content"].strip()
    ]

    is_dictionary = (data.get("mode") == "dictionary")

    if endpoint == "chat":
        if is_dictionary:
            system_prompt = (
                "You are a friendly and helpful dictionary assistant for a student. "
                "Given a word, return a student-friendly definition, its phonetic pronunciation, "
                "its part of speech, and a simple example sentence. "
                "Format the output strictly as a JSON object with the following keys, and nothing else (no markdown wrapping, no ```json):\n"
                "{\n"
                "  \"word\": \"the word in lowercase\",\n"
                "  \"phonetic\": \"phonetic spelling in slash brackets, e.g. /ha-pee/\",\n"
                "  \"pos\": \"part of speech, e.g. adjective, noun, verb\",\n"
                "  \"definition\": \"a simple, clear, student-friendly definition of the word\",\n"
                "  \"example\": \"a simple example sentence showing how to use the word in context\"\n"
                "}"
            )
        else:
            system_prompt += "\n\nAlways answer in numbered steps. Each step should contain only one idea and only one part. Provide only the first step in each response, never multiple steps at once. End by telling the user to say 'move to next step' to continue, or 'hint for next step', or 'explain in simpler terms'."
    elif endpoint == "podcast":
        system_prompt += "\n\nUsing the full conversation history from the entire chat session, including earlier user questions and assistant replies, create a short podcast-style answer in English. Write as if you are speaking in a friendly, human-hosted educational podcast with natural spoken phrasing. Produce only the spoken text (plain, TTS-ready) with a natural, conversational tone. Avoid using symbols like # or @ (do not say 'hashtag' or 'at'); avoid code blocks, headers, or markup. Use short clear sentences and do not emit any extra metadata or instructions. Return only the script suitable for text-to-speech in English."
    elif endpoint == "flashcards":
        system_prompt += "\n\nUsing the full conversation history from the entire chat session, including earlier questions and answers, create flashcard questions and answers in English. Return them as a list of Q&A pairs. Format: 'Q: [question]\nA: [answer]' on separate lines. Create 5-10 cards."
    elif endpoint == "quiz":
        system_prompt += "\n\nUsing the full conversation history from the entire chat session, including earlier questions and answers, create a quiz with 5 multiple choice questions in English. Format each as: 'Q[number]: [question]\nA) [option]\nB) [option]\nC) [option]\nD) [option]\nAnswer: [correct letter]' on separate lines."
    elif endpoint == "crosscheck":
        system_prompt += "\n\nUsing the full conversation history from the entire chat session, review the student's question and answer provided below in English. If the answer is wrong, explain exactly where it is incorrect, show how to fix it, and reveal the correct answer. Do not only give hints; provide a clear correction and the correct response."
    elif endpoint == "definitions":
        system_prompt += "\n\nScan the full conversation history from the entire chat session and extract every key term, concept, or vocabulary word that was defined or explained. Return ONLY a numbered list in this exact format with no extra text:\n1. Term: definition\n2. Term: definition\n...\nIf no definitions were found, return exactly: NO_DEFINITIONS"
    elif endpoint == "dictionary":
        system_prompt = (
            "You are a friendly and helpful dictionary assistant for a student. "
            "Given a word, return a student-friendly definition, its phonetic pronunciation, "
            "its part of speech, and a simple example sentence. "
            "Format the output strictly as a JSON object with the following keys, and nothing else (no markdown wrapping, no ```json):\n"
            "{\n"
            "  \"word\": \"the word in lowercase\",\n"
            "  \"phonetic\": \"phonetic spelling in slash brackets, e.g. /ha-pee/\",\n"
            "  \"pos\": \"part of speech, e.g. adjective, noun, verb\",\n"
            "  \"definition\": \"a simple, clear, student-friendly definition of the word\",\n"
            "  \"example\": \"a simple example sentence showing how to use the word in context\"\n"
            "}"
        )

    if isinstance(notes, str) and notes.strip():
        notes_stripped = notes.strip()
        system_prompt = (
            f"{system_prompt}\n\n"
            f"CONTEXT: The student has uploaded the following study notes:\n"
            f"--- START OF NOTES ---\n{notes_stripped}\n--- END OF NOTES ---\n\n"
            f"IMPORTANT: You must answer mainly with respect to the provided study notes above. Prioritize "
            f"using the information in these notes to answer the user's questions and generate any content (podcasts, "
            f"quizzes, flashcards, or reviews). However, if the user asks a question or requests something that is "
            f"not covered in these notes, you MUST still answer the question and fulfill the request fully using "
            f"your general knowledge."
        )

    if not GEMINI_API_KEY:
        return jsonify({"error": "Server has no API key configured. Ask the owner to add GEMINI_API_KEY to the .env file."}), 500
    if not messages:
        return jsonify({"error": "No messages provided."}), 400

    try:
        model = genai.GenerativeModel(model_name=model_name, system_instruction=system_prompt)
        gemini_history = []
        for msg in messages[:-1]:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat_session = model.start_chat(history=gemini_history)
        response = chat_session.send_message(messages[-1]["content"])
        return jsonify({"reply": response.text})

    except Exception as e:
        print(f"[ERROR] Gemini API: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================================================
#  STEP 6: START THE SERVER
# =====================================================================

def find_free_port(default_port=5000):
    import socket
    port = default_port
    while port < default_port + 100:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            port += 1
    return default_port

def open_browser(port):
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{port}")

if __name__ == "__main__":
    from threading import Timer

    # Initialise the database before starting the server
    init_db()

    render_port = os.environ.get("PORT")
    is_cloud    = render_port is not None

    if is_cloud:
        port  = int(render_port)
        host  = "0.0.0.0"
        debug = False
    else:
        port  = find_free_port(5000)
        host  = "127.0.0.1"
        debug = True
        url   = f"http://{host}:{port}"
        print("\n[STARTING] Study Buddy is running!")
        print(f"   Open  {url}  in your browser\n")

        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            Timer(1.0, lambda: open_browser(port)).start()
        elif not os.environ.get("WERKZEUG_RUN_MAIN"):
            Timer(1.0, lambda: open_browser(port)).start()

    app.run(host=host, debug=debug, port=port)
