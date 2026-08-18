"""
Gamification layer: XP, streaks, freezes, shop, daily puzzle, planner, prefs.
Additive only — registered onto the Flask app from app.py.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta

from flask import jsonify, request

# Internal DB key only — grades are removed from the student UI
GRADE_KEY = 10
_puzzle_gen_locks = {}
_puzzle_gen_locks_mu = threading.Lock()

# ── Constants ─────────────────────────────────────────────────────────

XP_AWARDS = {
    "chat": 5,  # educational chat questions only (frontend filters greetings)
    "quiz": 30,
    "flashcards": 20,
    "mock": 50,
    "daily_puzzle": 25,
    "daily_fact": 5,
    "focus_10m": 40,
    "notes_upload": 20,
    "podcast": 20,
    "notes_read": 10,
    "planner": 10,  # XP when completing a planner task
}

CHAT_XP_DAILY_CAP = 40  # max chat XP awards per local day (streak still once)

ELECTIVE_BASE = (
    "Physical Education",
    "Commercial Applications",
    "Economics Application",
    "Art",
)
ELECTIVE_ECONOMICS = "Economics"
ELECTIVE_LAW = "Law"
ALL_ELECTIVES = ELECTIVE_BASE
LEGACY_SUBJECT_ELECTIVES = (ELECTIVE_ECONOMICS, ELECTIVE_LAW)

FACT_FALLBACKS = [
    {
        "title": "Everest is still growing",
        "body": "Did you know Mount Everest is still rising? Scientists estimate that in roughly 31 years its official height could settle near about 8,849 m as plates keep colliding and surveys get sharper.",
        "category": "Geography",
    },
    {
        "title": "Your brain uses sugar",
        "body": "Did you know your brain uses about 20% of your body's energy even though it is only about 2% of your body weight? Glucose is its favorite fuel.",
        "category": "Biology",
    },
    {
        "title": "Octopuses have three hearts",
        "body": "Did you know an octopus has three hearts? Two pump blood to the gills and one pumps it to the rest of the body.",
        "category": "Biology",
    },
    {
        "title": "Lightning is hotter than the Sun",
        "body": "Did you know a lightning bolt can heat the air around it to about 30,000°C — roughly five times hotter than the surface of the Sun?",
        "category": "Physics",
    },
    {
        "title": "Bananas are berries",
        "body": "Did you know botanically a banana is a berry, but a strawberry is not? Berry classifications surprise most people.",
        "category": "Science",
    },
    {
        "title": "Honey never spoils",
        "body": "Did you know archaeologists have found pots of honey thousands of years old that were still edible? Low water and natural acidity keep it stable.",
        "category": "Food science",
    },
    {
        "title": "Sharks are older than trees",
        "body": "Did you know sharks have been around for over 400 million years — long before trees appeared on Earth?",
        "category": "History of life",
    },
    {
        "title": "Water can boil and freeze",
        "body": "Did you know at the triple point of water, liquid, ice, and vapor can exist together? It is a precise temperature and pressure used in science labs.",
        "category": "Chemistry",
    },
    {
        "title": "Your bones are alive",
        "body": "Did you know bones are living tissue that constantly remodel? Adults replace most of their skeleton roughly every 10 years.",
        "category": "Biology",
    },
    {
        "title": "There are more stars than grains",
        "body": "Did you know astronomers estimate there are more stars in the observable universe than grains of sand on all Earth's beaches?",
        "category": "Astronomy",
    },
    {
        "title": "Cleopatra and the pyramids",
        "body": "Did you know Cleopatra lived closer in time to the Moon landing than to the building of the Great Pyramid of Giza?",
        "category": "History",
    },
    {
        "title": "Wombats make cube poop",
        "body": "Did you know wombats produce cube-shaped droppings? Their intestines help stack and mark territory without rolling away.",
        "category": "Animals",
    },
]

PUZZLE_SUBJECTS = [
    "Math", "Physics", "Chemistry", "Biology", "English",
    "History", "Geography", "Computer", "Sports", "General Knowledge",
]

MILESTONES = [
    {"id": "streak_3", "at": 3, "xp": 100, "reward": "xp", "label": "3-Day Streak"},
    {"id": "streak_7", "at": 7, "xp": 0, "reward": "badge_week", "label": "7-Day Badge"},
    {"id": "streak_14", "at": 14, "xp": 0, "reward": "theme_aurora", "label": "14-Day Theme"},
    {"id": "streak_30", "at": 30, "xp": 0, "reward": "badge_legend", "label": "Legend Badge"},
    {"id": "streak_100", "at": 100, "xp": 0, "reward": "badge_hof", "label": "Hall of Fame"},
]

SHOP_CATALOG = [
    {
        "id": "streak_freeze",
        "name": "Streak Freeze",
        "icon": "🧊",
        "cost": 350,
        "category": "utility",
        "max_owned": 3,
        "description": "Protects your streak if you miss one day.",
    },
    {
        "id": "theme_aurora",
        "name": "Aurora Theme",
        "icon": "🎨",
        "cost": 550,
        "category": "themes",
        "max_owned": 1,
        "description": "Unlock the Aurora accent theme.",
    },
    {
        "id": "theme_forest",
        "name": "Forest Theme",
        "icon": "🌲",
        "cost": 550,
        "category": "themes",
        "max_owned": 1,
        "description": "Calm green study theme.",
    },
    {
        "id": "avatar_pack_1",
        "name": "Avatar Pack",
        "icon": "👤",
        "cost": 280,
        "category": "avatars",
        "max_owned": 1,
        "description": "Extra profile avatar frames.",
    },
    {
        "id": "badge_scholar",
        "name": "Scholar Badge",
        "icon": "🎖️",
        "cost": 400,
        "category": "badges",
        "max_owned": 1,
        "description": "Show off your scholar status.",
    },
    {
        "id": "voice_premium",
        "name": "Premium Voices",
        "icon": "🎵",
        "cost": 700,
        "category": "voices",
        "max_owned": 1,
        "description": "Unlock extra podcast voice presets.",
    },
    {
        "id": "chat_sparkle",
        "name": "Chat Sparkles",
        "icon": "✨",
        "cost": 480,
        "category": "effects",
        "max_owned": 1,
        "description": "Subtle sparkle effects on AI replies.",
    },
]


def migrate_gamification_tables(conn):
    """Create gamification tables (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_xp (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            balance INTEGER NOT NULL DEFAULT 0,
            lifetime INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_streaks (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            current_streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            last_study_date TEXT,
            freezes_owned INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS xp_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL,
            meta TEXT,
            local_date TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_xp_ledger_user ON xp_ledger(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_xp_ledger_day ON xp_ledger(user_id, local_date, action);

        CREATE TABLE IF NOT EXISTS user_inventory (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL,
            qty INTEGER NOT NULL DEFAULT 1,
            unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, item_id)
        );

        CREATE TABLE IF NOT EXISTS user_milestones (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            milestone_id TEXT NOT NULL,
            unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, milestone_id)
        );

        CREATE TABLE IF NOT EXISTS daily_puzzles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            puzzle_date TEXT NOT NULL,
            grade INTEGER NOT NULL,
            subject TEXT NOT NULL,
            difficulty TEXT NOT NULL DEFAULT 'Medium',
            prompt TEXT NOT NULL,
            hint TEXT NOT NULL DEFAULT '',
            answer TEXT NOT NULL,
            xp_reward INTEGER NOT NULL DEFAULT 25,
            solution TEXT NOT NULL DEFAULT '',
            UNIQUE(puzzle_date, grade, subject)
        );

        CREATE TABLE IF NOT EXISTS daily_puzzle_attempts (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            puzzle_date TEXT NOT NULL,
            grade INTEGER NOT NULL,
            subject TEXT NOT NULL,
            attempted INTEGER NOT NULL DEFAULT 0,
            correct INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            xp_awarded INTEGER NOT NULL DEFAULT 0,
            user_answer TEXT,
            PRIMARY KEY (user_id, puzzle_date, grade, subject)
        );

        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            grade INTEGER NOT NULL DEFAULT 10,
            language TEXT NOT NULL DEFAULT 'multi',
            notify_streak INTEGER NOT NULL DEFAULT 1,
            notify_puzzle INTEGER NOT NULL DEFAULT 1,
            high_contrast INTEGER NOT NULL DEFAULT 0,
            font_scale REAL NOT NULL DEFAULT 1.0,
            reduced_motion INTEGER NOT NULL DEFAULT 0,
            preferred_subjects TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS study_planner_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            due_date TEXT,
            done INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual',
            exam_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_planner_user ON study_planner_tasks(user_id, due_date);

        CREATE TABLE IF NOT EXISTS daily_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_date TEXT NOT NULL,
            grade INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Fun',
            xp_reward INTEGER NOT NULL DEFAULT 5,
            UNIQUE(fact_date, grade)
        );

        CREATE TABLE IF NOT EXISTS daily_fact_views (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fact_date TEXT NOT NULL,
            grade INTEGER NOT NULL,
            viewed INTEGER NOT NULL DEFAULT 0,
            xp_awarded INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, fact_date, grade)
        );
    """)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    except Exception:
        pass
    try:
        conn.execute(
            "ALTER TABLE study_planner_tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE study_planner_tasks ADD COLUMN exam_id INTEGER")
    except Exception:
        pass
    for ddl in (
        "ALTER TABLE user_prefs ADD COLUMN section TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE user_prefs ADD COLUMN drop_math INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE user_prefs ADD COLUMN drop_science INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE user_prefs ADD COLUMN elective TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE user_prefs ADD COLUMN notify_fact INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE user_prefs ADD COLUMN age_band TEXT NOT NULL DEFAULT '14-16'",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass


def _normalize_elective_value(raw) -> str:
    s = (raw or "").strip()
    for v in ALL_ELECTIVES:
        if s.lower() == v.lower():
            return v
    return ""


def _derived_subjects(drop_math=False, drop_science=False):
    out = []
    if drop_science:
        out.append(ELECTIVE_ECONOMICS)
    if drop_math and drop_science:
        out.append(ELECTIVE_LAW)
    return out


def _allowed_electives(drop_math=False, drop_science=False):
    return list(ELECTIVE_BASE)


def _validate_elective(elective_raw, drop_math=False, drop_science=False):
    allowed = _allowed_electives(drop_math, drop_science)
    elective = _normalize_elective_value(elective_raw)
    if not elective:
        return False, "Please select an elective."
    if elective not in allowed:
        return False, f"That elective is not available. Choose one of: {', '.join(allowed)}."
    return True, elective


def _parse_local_date(data) -> str:
    d = (data.get("localDate") or data.get("local_date") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d
    return datetime.utcnow().strftime("%Y-%m-%d")


def _ensure_xp_streak(conn, uid: int):
    # Guard against stale session ids after DB wipe (FK → users.id)
    if not conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
        return
    conn.execute(
        "INSERT OR IGNORE INTO user_xp (user_id, balance, lifetime) VALUES (?,0,0)",
        (uid,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO user_streaks (user_id, current_streak, best_streak, freezes_owned) VALUES (?,0,0,0)",
        (uid,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO user_prefs (user_id, language) VALUES (?, 'multi')",
        (uid,),
    )


def _normalize_section_value(raw) -> str:
    valid = (
        "Whiz 1", "Whiz 2", "Whiz 3",
        "Super 1", "Super 2", "Super 3",
    )
    s = (raw or "").strip()
    for v in valid:
        if s.lower() == v.lower():
            return v
    return ""


def _get_prefs(conn, uid: int) -> dict:
    _ensure_xp_streak(conn, uid)
    for ddl in (
        "ALTER TABLE user_prefs ADD COLUMN section TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE user_prefs ADD COLUMN drop_math INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE user_prefs ADD COLUMN drop_science INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE user_prefs ADD COLUMN elective TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE user_prefs ADD COLUMN notify_fact INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE user_prefs ADD COLUMN age_band TEXT NOT NULL DEFAULT '14-16'",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    row = conn.execute("SELECT * FROM user_prefs WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return {
            "grade": 9,
            "language": "multi",
            "font_scale": 1.0,
            "section": "",
            "drop_math": 0,
            "drop_science": 0,
            "elective": "",
            "notify_fact": 1,
            "notify_puzzle": 1,
            "notify_streak": 1,
            "age_band": "14-16",
            "derived_subjects": [],
        }
    d = dict(row)
    try:
        d["preferred_subjects"] = json.loads(d.get("preferred_subjects") or "[]")
    except Exception:
        d["preferred_subjects"] = []
    d["section"] = _normalize_section_value(d.get("section") or "")
    if d["section"] != "Super 3":
        d["drop_math"] = 0
        d["drop_science"] = 0
    else:
        d["drop_math"] = 1 if int(d.get("drop_math") or 0) else 0
        d["drop_science"] = 1 if int(d.get("drop_science") or 0) else 0
    raw_el = (d.get("elective") or "").strip()
    if raw_el in LEGACY_SUBJECT_ELECTIVES:
        conn.execute(
            "UPDATE user_prefs SET elective='', updated_at=datetime('now') WHERE user_id=?",
            (uid,),
        )
        d["elective"] = ""
    else:
        d["elective"] = _normalize_elective_value(raw_el)
    d["notify_fact"] = 1 if int(d.get("notify_fact") if d.get("notify_fact") is not None else 1) else 0
    band = (d.get("age_band") or "14-16").strip()
    if band not in ("11-13", "14-16", "17-18"):
        band = "14-16"
    d["age_band"] = band
    d["derived_subjects"] = _derived_subjects(bool(d["drop_math"]), bool(d["drop_science"]))
    return d


def _award_xp(conn, uid: int, action: str, amount: int, local_date: str, meta: str = None):
    if amount <= 0:
        return 0
    conn.execute(
        "UPDATE user_xp SET balance=balance+?, lifetime=lifetime+?, updated_at=datetime('now') WHERE user_id=?",
        (amount, amount, uid),
    )
    conn.execute(
        "INSERT INTO xp_ledger (user_id, action, amount, meta, local_date) VALUES (?,?,?,?,?)",
        (uid, action, amount, meta, local_date),
    )
    return amount


def _inventory_qty(conn, uid: int, item_id: str) -> int:
    row = conn.execute(
        "SELECT qty FROM user_inventory WHERE user_id=? AND item_id=?",
        (uid, item_id),
    ).fetchone()
    return int(row["qty"]) if row else 0


def _add_inventory(conn, uid: int, item_id: str, qty: int = 1):
    existing = _inventory_qty(conn, uid, item_id)
    if existing:
        conn.execute(
            "UPDATE user_inventory SET qty=qty+? WHERE user_id=? AND item_id=?",
            (qty, uid, item_id),
        )
    else:
        conn.execute(
            "INSERT INTO user_inventory (user_id, item_id, qty) VALUES (?,?,?)",
            (uid, item_id, qty),
        )


def _apply_milestones(conn, uid: int, current_streak: int, local_date: str) -> list:
    unlocked = []
    for m in MILESTONES:
        if current_streak < m["at"]:
            continue
        exists = conn.execute(
            "SELECT 1 FROM user_milestones WHERE user_id=? AND milestone_id=?",
            (uid, m["id"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO user_milestones (user_id, milestone_id) VALUES (?,?)",
            (uid, m["id"]),
        )
        if m["xp"]:
            _award_xp(conn, uid, f"milestone_{m['id']}", m["xp"], local_date, m["label"])
        if m["reward"] and m["reward"] != "xp":
            _add_inventory(conn, uid, m["reward"], 1)
        unlocked.append(m)
    return unlocked


def _update_streak_for_study(conn, uid: int, local_date: str) -> dict:
    """Mark today as studied. Returns streak info + freeze/milestone events."""
    _ensure_xp_streak(conn, uid)
    row = conn.execute("SELECT * FROM user_streaks WHERE user_id=?", (uid,)).fetchone()
    current = int(row["current_streak"] or 0)
    best = int(row["best_streak"] or 0)
    last = row["last_study_date"]
    freezes = int(row["freezes_owned"] or 0)
    freeze_used = False
    protected = False

    today = datetime.strptime(local_date, "%Y-%m-%d").date()
    events = []

    if last == local_date:
        # Already studied today — streak unchanged
        return {
            "current_streak": current,
            "best_streak": best,
            "last_study_date": last,
            "freezes_owned": freezes,
            "freeze_used": False,
            "protected": False,
            "milestones": [],
            "already_today": True,
        }

    yesterday = (today - timedelta(days=1)).isoformat()

    if not last:
        current = 1
    elif last == yesterday:
        current = current + 1
    else:
        # Gap — try one freeze if missed exactly one day... actually gap could be >1
        try:
            last_d = datetime.strptime(last, "%Y-%m-%d").date()
            gap = (today - last_d).days
        except Exception:
            gap = 999
        if gap == 2 and freezes > 0:
            # Missed exactly one day (yesterday) — auto freeze
            freezes -= 1
            # Also decrement inventory streak_freeze if present
            inv = _inventory_qty(conn, uid, "streak_freeze")
            if inv > 0:
                conn.execute(
                    "UPDATE user_inventory SET qty=qty-1 WHERE user_id=? AND item_id=? AND qty>0",
                    (uid, "streak_freeze"),
                )
            freeze_used = True
            protected = True
            current = current + 1
            events.append({"type": "freeze", "message": "Streak Protected"})
        elif gap <= 1:
            current = current + 1
        else:
            current = 1

    best = max(best, current)
    conn.execute(
        """
        UPDATE user_streaks
        SET current_streak=?, best_streak=?, last_study_date=?, freezes_owned=?, updated_at=datetime('now')
        WHERE user_id=?
        """,
        (current, best, local_date, freezes, uid),
    )
    milestones = _apply_milestones(conn, uid, current, local_date)
    return {
        "current_streak": current,
        "best_streak": best,
        "last_study_date": local_date,
        "freezes_owned": freezes,
        "freeze_used": freeze_used,
        "protected": protected,
        "milestones": milestones,
        "already_today": False,
        "events": events,
    }


def _reconcile_missed_day(conn, uid: int, local_date: str) -> dict:
    """If user opens app after missing days without studying, apply freeze or reset."""
    _ensure_xp_streak(conn, uid)
    row = conn.execute("SELECT * FROM user_streaks WHERE user_id=?", (uid,)).fetchone()
    if not row or not row["last_study_date"]:
        return {"reset": False, "protected": False}
    last = row["last_study_date"]
    if last >= local_date:
        return {"reset": False, "protected": False}
    today = datetime.strptime(local_date, "%Y-%m-%d").date()
    last_d = datetime.strptime(last, "%Y-%m-%d").date()
    gap = (today - last_d).days
    if gap <= 1:
        return {"reset": False, "protected": False}
    freezes = int(row["freezes_owned"] or 0)
    current = int(row["current_streak"] or 0)
    best = int(row["best_streak"] or 0)
    if gap == 2 and freezes > 0:
        freezes -= 1
        inv = _inventory_qty(conn, uid, "streak_freeze")
        if inv > 0:
            conn.execute(
                "UPDATE user_inventory SET qty=qty-1 WHERE user_id=? AND item_id=? AND qty>0",
                (uid, "streak_freeze"),
            )
        # Treat freeze as covering yesterday; last_study_date stays until they study
        # Set last_study_date to yesterday so streak continues when they study today
        yesterday = (today - timedelta(days=1)).isoformat()
        conn.execute(
            """
            UPDATE user_streaks SET freezes_owned=?, last_study_date=?, updated_at=datetime('now')
            WHERE user_id=?
            """,
            (freezes, yesterday, uid),
        )
        return {"reset": False, "protected": True, "freezes_owned": freezes}
    # Reset streak
    conn.execute(
        """
        UPDATE user_streaks SET current_streak=0, freezes_owned=?, updated_at=datetime('now')
        WHERE user_id=?
        """,
        (freezes, uid),
    )
    return {"reset": True, "protected": False, "current_streak": 0}


def _subject_for_date(local_date: str) -> str:
    try:
        day = datetime.strptime(local_date, "%Y-%m-%d").toordinal()
    except Exception:
        day = 0
    return PUZZLE_SUBJECTS[day % len(PUZZLE_SUBJECTS)]


def _normalize_answer(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s/+-]", "", s)
    return s


def register_gamification_routes(
    app,
    get_db,
    require_auth,
    get_groq_client,
    resolve_groq_model,
    fs_pull_gamification=None,
    fs_push_gamification=None,
    llm_chat_completion=None,
):
    """Attach all gamification routes to the Flask app."""

    def _llm_json_text(messages, *, max_tokens=500, temperature=0.7, light=False):
        if llm_chat_completion:
            text, _meta = llm_chat_completion(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                light=light,
            )
            return text
        client = get_groq_client()
        completion = client.chat.completions.create(
            model=resolve_groq_model(None),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (completion.choices[0].message.content or "").strip()

    def _pull_game(uid):
        if fs_pull_gamification:
            try:
                fs_pull_gamification(uid)
            except Exception as e:
                print(f"[Gamification] pull failed: {e}")

    def _push_game(uid):
        if fs_push_gamification:
            try:
                fs_push_gamification(uid)
            except Exception as e:
                print(f"[Gamification] push failed: {e}")

    @app.route("/api/gamification/summary", methods=["GET"])
    def gamification_summary():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        local_date = _parse_local_date(request.args)
        # Restore cloud XP/streak/puzzle before reading (survives logout + Render wipes)
        _pull_game(uid)
        with get_db() as conn:
            _ensure_xp_streak(conn, uid)
            recon = _reconcile_missed_day(conn, uid, local_date)
            xp = conn.execute("SELECT * FROM user_xp WHERE user_id=?", (uid,)).fetchone()
            st = conn.execute("SELECT * FROM user_streaks WHERE user_id=?", (uid,)).fetchone()
            prefs = _get_prefs(conn, uid)
            inv = conn.execute(
                "SELECT item_id, qty FROM user_inventory WHERE user_id=?", (uid,)
            ).fetchall()
            milestones = conn.execute(
                "SELECT milestone_id, unlocked_at FROM user_milestones WHERE user_id=?",
                (uid,),
            ).fetchall()
            studied_today = bool(
                st and st["last_study_date"] == local_date
            )
            # Sync freezes_owned with inventory qty for streak_freeze
            freeze_inv = _inventory_qty(conn, uid, "streak_freeze")
            freezes = max(int(st["freezes_owned"] or 0), freeze_inv) if st else freeze_inv
            if st and freezes != int(st["freezes_owned"] or 0):
                conn.execute(
                    "UPDATE user_streaks SET freezes_owned=? WHERE user_id=?",
                    (min(3, freezes), uid),
                )
                freezes = min(3, freezes)

            next_ms = None
            cur = int(st["current_streak"] or 0) if st else 0
            unlocked_ids = {m["milestone_id"] for m in milestones}
            for m in MILESTONES:
                if m["id"] not in unlocked_ids:
                    next_ms = {
                        "id": m["id"],
                        "at": m["at"],
                        "label": m["label"],
                        "xp_needed": max(0, m["at"] - cur),
                    }
                    break

        _push_game(uid)
        return jsonify({
            "xp": int(xp["balance"] or 0) if xp else 0,
            "lifetimeXp": int(xp["lifetime"] or 0) if xp else 0,
            "currentStreak": int(st["current_streak"] or 0) if st else 0,
            "bestStreak": int(st["best_streak"] or 0) if st else 0,
            "lastStudyDate": st["last_study_date"] if st else None,
            "freezesOwned": freezes,
            "studiedToday": studied_today,
            "prefs": {
                "grade": int(prefs.get("grade") or 9),
                "language": prefs.get("language") or "multi",
                "notifyStreak": bool(prefs.get("notify_streak", 1)),
                "notifyPuzzle": bool(prefs.get("notify_puzzle", 1)),
                "notifyFact": bool(prefs.get("notify_fact", 1)),
                "highContrast": bool(prefs.get("high_contrast", 0)),
                "fontScale": float(prefs.get("font_scale") or 1.0),
                "reducedMotion": bool(prefs.get("reduced_motion", 0)),
                "preferredSubjects": prefs.get("preferred_subjects") or [],
                "section": prefs.get("section") or "",
                "dropMath": bool(prefs.get("drop_math", 0)),
                "dropScience": bool(prefs.get("drop_science", 0)),
                "elective": prefs.get("elective") or "",
                "electiveLocked": bool(prefs.get("elective")),
                "allowedElectives": _allowed_electives(
                    bool(prefs.get("drop_math", 0)),
                    bool(prefs.get("drop_science", 0)),
                ),
                "derivedSubjects": prefs.get("derived_subjects") or _derived_subjects(
                    bool(prefs.get("drop_math", 0)),
                    bool(prefs.get("drop_science", 0)),
                ),
                "ageBand": prefs.get("age_band") or "14-16",
            },
            "inventory": {r["item_id"]: r["qty"] for r in inv},
            "milestones": [dict(m) for m in milestones],
            "nextMilestone": next_ms,
            "reconcile": recon,
            "localDate": local_date,
            "email": user["email"] if "email" in user.keys() else None,
        })

    @app.route("/api/gamification/action", methods=["POST"])
    def gamification_action():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        data = request.get_json(force=True) or {}
        action = (data.get("action") or "").strip().lower()
        if action not in XP_AWARDS:
            return jsonify({"error": f"Unknown action: {action}"}), 400
        local_date = _parse_local_date(data)
        meta = data.get("meta")
        if meta is not None and not isinstance(meta, str):
            meta = json.dumps(meta)[:500]

        with get_db() as conn:
            _ensure_xp_streak(conn, uid)
            _reconcile_missed_day(conn, uid, local_date)

            # Chat spam cap
            xp_amount = XP_AWARDS[action]
            if action == "chat":
                count = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM xp_ledger
                    WHERE user_id=? AND action='chat' AND local_date=?
                    """,
                    (uid, local_date),
                ).fetchone()["c"]
                if count >= CHAT_XP_DAILY_CAP:
                    xp_amount = 0

            streak_info = _update_streak_for_study(conn, uid, local_date)
            awarded = _award_xp(conn, uid, action, xp_amount, local_date, meta) if xp_amount else 0

            xp = conn.execute("SELECT balance, lifetime FROM user_xp WHERE user_id=?", (uid,)).fetchone()

        _push_game(uid)
        return jsonify({
            "ok": True,
            "action": action,
            "xpAwarded": awarded,
            "xp": int(xp["balance"] or 0),
            "lifetimeXp": int(xp["lifetime"] or 0),
            "streak": {
                "current": streak_info["current_streak"],
                "best": streak_info["best_streak"],
                "studiedToday": True,
                "protected": streak_info.get("protected"),
                "freezeUsed": streak_info.get("freeze_used"),
                "alreadyToday": streak_info.get("already_today"),
            },
            "milestonesUnlocked": streak_info.get("milestones") or [],
            "freezesOwned": streak_info.get("freezes_owned"),
        })

    @app.route("/api/shop", methods=["GET"])
    def shop_list():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        with get_db() as conn:
            _ensure_xp_streak(conn, uid)
            xp = conn.execute("SELECT balance FROM user_xp WHERE user_id=?", (uid,)).fetchone()
            inv = {
                r["item_id"]: r["qty"]
                for r in conn.execute(
                    "SELECT item_id, qty FROM user_inventory WHERE user_id=?", (uid,)
                ).fetchall()
            }
        items = []
        for it in SHOP_CATALOG:
            owned = int(inv.get(it["id"], 0))
            items.append({**it, "owned": owned, "canBuy": owned < it["max_owned"]})
        return jsonify({
            "xp": int(xp["balance"] or 0) if xp else 0,
            "items": items,
        })

    @app.route("/api/shop/buy", methods=["POST"])
    def shop_buy():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        data = request.get_json(force=True) or {}
        item_id = (data.get("itemId") or data.get("item_id") or "").strip()
        item = next((x for x in SHOP_CATALOG if x["id"] == item_id), None)
        if not item:
            return jsonify({"error": "Item not found."}), 404
        with get_db() as conn:
            _ensure_xp_streak(conn, uid)
            xp = conn.execute("SELECT balance FROM user_xp WHERE user_id=?", (uid,)).fetchone()
            bal = int(xp["balance"] or 0) if xp else 0
            owned = _inventory_qty(conn, uid, item_id)
            if owned >= item["max_owned"]:
                return jsonify({"error": "Already own the maximum."}), 400
            if bal < item["cost"]:
                return jsonify({"error": "Not enough XP."}), 400
            conn.execute(
                "UPDATE user_xp SET balance=balance-?, updated_at=datetime('now') WHERE user_id=?",
                (item["cost"], uid),
            )
            _add_inventory(conn, uid, item_id, 1)
            if item_id == "streak_freeze":
                st = conn.execute(
                    "SELECT freezes_owned FROM user_streaks WHERE user_id=?", (uid,)
                ).fetchone()
                freezes = min(3, int(st["freezes_owned"] or 0) + 1)
                conn.execute(
                    "UPDATE user_streaks SET freezes_owned=? WHERE user_id=?",
                    (freezes, uid),
                )
            new_bal = conn.execute(
                "SELECT balance FROM user_xp WHERE user_id=?", (uid,)
            ).fetchone()["balance"]
        _push_game(uid)
        return jsonify({"ok": True, "xp": int(new_bal), "itemId": item_id, "owned": owned + 1})

    @app.route("/api/prefs", methods=["GET", "POST"])
    def user_prefs_route():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        if request.method == "GET":
            _pull_game(uid)
            with get_db() as conn:
                prefs = _get_prefs(conn, uid)
            return jsonify({"prefs": prefs})

        data = request.get_json(force=True) or {}
        with get_db() as conn:
            _ensure_xp_streak(conn, uid)
            current = _get_prefs(conn, uid)
            grade = GRADE_KEY
            language = (data.get("language") or current.get("language") or "multi").strip().lower()[:20] or "multi"
            if language not in ("en", "hi", "te", "es", "fr", "multi", "auto", "multilingual"):
                language = "multi"
            if language in ("auto", "multilingual"):
                language = "multi"
            subjects = data.get("preferredSubjects") or data.get("preferred_subjects")
            if subjects is None:
                subjects = current.get("preferred_subjects") or []
            if not isinstance(subjects, list):
                subjects = []
            subjects_json = json.dumps([str(s)[:40] for s in subjects[:12]])
            if "section" in data:
                section = _normalize_section_value(data.get("section"))
            else:
                section = _normalize_section_value(current.get("section") or "")
            if section == "Super 3":
                if "dropMath" in data or "drop_math" in data:
                    drop_math = 1 if data.get("dropMath", data.get("drop_math", False)) else 0
                else:
                    drop_math = 1 if current.get("drop_math") else 0
                if "dropScience" in data or "drop_science" in data:
                    drop_science = 1 if data.get("dropScience", data.get("drop_science", False)) else 0
                else:
                    drop_science = 1 if current.get("drop_science") else 0
            else:
                drop_math = 0
                drop_science = 0

            current_elective = _normalize_elective_value(current.get("elective") or "")
            elective = current_elective
            elective_error = None
            if "elective" in data or "Elective" in data:
                requested = data.get("elective", data.get("Elective"))
                if current_elective:
                    if _normalize_elective_value(requested) and _normalize_elective_value(requested) != current_elective:
                        elective_error = "Elective cannot be changed once chosen."
                    elective = current_elective
                else:
                    ok_e, val = _validate_elective(requested, bool(drop_math), bool(drop_science))
                    if not ok_e:
                        elective_error = val
                    else:
                        elective = val

            if elective_error:
                return jsonify({"error": elective_error}), 400

            age_band = (data.get("ageBand") or data.get("age_band") or current.get("age_band") or "14-16").strip()
            if age_band not in ("11-13", "14-16", "17-18"):
                age_band = "14-16"

            conn.execute(
                """
                UPDATE user_prefs SET
                    grade=?,
                    language=?,
                    notify_streak=?,
                    notify_puzzle=?,
                    notify_fact=?,
                    high_contrast=?,
                    font_scale=?,
                    reduced_motion=?,
                    preferred_subjects=?,
                    section=?,
                    drop_math=?,
                    drop_science=?,
                    elective=?,
                    age_band=?,
                    updated_at=datetime('now')
                WHERE user_id=?
                """,
                (
                    grade,
                    language,
                    1 if data.get("notifyStreak", data.get("notify_streak", True)) else 0,
                    1 if data.get("notifyPuzzle", data.get("notify_puzzle", True)) else 0,
                    1 if data.get("notifyFact", data.get("notify_fact", current.get("notify_fact", True))) else 0,
                    1 if data.get("highContrast", data.get("high_contrast", False)) else 0,
                    float(data.get("fontScale", data.get("font_scale", 1.0)) or 1.0),
                    1 if data.get("reducedMotion", data.get("reduced_motion", False)) else 0,
                    subjects_json,
                    section,
                    drop_math,
                    drop_science,
                    elective or "",
                    age_band,
                    uid,
                ),
            )
            prefs = _get_prefs(conn, uid)
        _push_game(uid)
        return jsonify({
            "ok": True,
            "prefs": {
                **prefs,
                "electiveLocked": bool(prefs.get("elective")),
                "allowedElectives": _allowed_electives(
                    bool(prefs.get("drop_math", 0)),
                    bool(prefs.get("drop_science", 0)),
                ),
                "derivedSubjects": prefs.get("derived_subjects") or [],
                "ageBand": prefs.get("age_band") or "14-16",
            },
        })

    def _ensure_planner_columns(conn):
        try:
            conn.execute(
                "ALTER TABLE study_planner_tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
            )
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE study_planner_tasks ADD COLUMN exam_id INTEGER")
        except Exception:
            pass

    @app.route("/api/planner", methods=["GET", "POST"])
    def planner_list_create():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        if request.method == "GET":
            with get_db() as conn:
                _ensure_planner_columns(conn)
                rows = conn.execute(
                    """
                    SELECT id, title, due_date, done, source, exam_id, created_at
                    FROM study_planner_tasks WHERE user_id=?
                    ORDER BY done ASC, due_date IS NULL, due_date ASC, id ASC
                    LIMIT 200
                    """,
                    (uid,),
                ).fetchall()
            return jsonify({"tasks": [dict(r) for r in rows]})

        data = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()[:200]
        if not title:
            return jsonify({"error": "Title required."}), 400
        due = (data.get("dueDate") or data.get("due_date") or "").strip() or None
        with get_db() as conn:
            _ensure_planner_columns(conn)
            cur = conn.execute(
                """
                INSERT INTO study_planner_tasks (user_id, title, due_date, source)
                VALUES (?,?,?,'manual')
                """,
                (uid, title, due),
            )
            row = conn.execute(
                """
                SELECT id, title, due_date, done, source, exam_id, created_at
                FROM study_planner_tasks WHERE id=?
                """,
                (cur.lastrowid,),
            ).fetchone()
        return jsonify({"ok": True, "task": dict(row)})

    @app.route("/api/planner/<int:task_id>", methods=["PATCH", "DELETE"])
    def planner_update(task_id: int):
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        if request.method == "DELETE":
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM study_planner_tasks WHERE id=? AND user_id=?",
                    (task_id, uid),
                )
            return jsonify({"ok": True})

        data = request.get_json(force=True) or {}
        with get_db() as conn:
            _ensure_planner_columns(conn)
            row = conn.execute(
                "SELECT * FROM study_planner_tasks WHERE id=? AND user_id=?",
                (task_id, uid),
            ).fetchone()
            if not row:
                return jsonify({"error": "Not found."}), 404
            title = data.get("title", row["title"])
            done = data.get("done", row["done"])
            due = data.get("dueDate", data.get("due_date", row["due_date"]))
            conn.execute(
                "UPDATE study_planner_tasks SET title=?, done=?, due_date=? WHERE id=? AND user_id=?",
                (str(title)[:200], 1 if done else 0, due, task_id, uid),
            )
            updated = conn.execute(
                """
                SELECT id, title, due_date, done, source, exam_id, created_at
                FROM study_planner_tasks WHERE id=?
                """,
                (task_id,),
            ).fetchone()
        return jsonify({"ok": True, "task": dict(updated)})

    def _map_to_puzzle_subject(raw: str) -> str:
        t = (raw or "").strip().lower()
        if not t:
            return ""
        mapping = [
            ("physics", "Physics"),
            ("chemistry", "Chemistry"),
            ("biology", "Biology"),
            ("math", "Math"),
            ("algebra", "Math"),
            ("geometry", "Math"),
            ("english", "English"),
            ("history", "History"),
            ("geography", "Geography"),
            ("geo", "Geography"),
            ("civics", "Civics"),
            ("science", "Science"),
            ("computer", "Computer"),
            ("sport", "Sports"),
            ("cricket", "Sports"),
            ("football", "Sports"),
            ("olympics", "Sports"),
            ("hindi", "Hindi"),
            ("general knowledge", "General Knowledge"),
            ("gk", "General Knowledge"),
        ]
        for key, label in mapping:
            if key in t and label in PUZZLE_SUBJECTS:
                return label
        for s in PUZZLE_SUBJECTS:
            if s.lower() in t or t in s.lower():
                return s
        return ""

    def _revision_seed_for_user(uid: int):
        """Pick puzzle subject/topic from weak subjects or Things to Revise."""
        try:
            with get_db() as conn:
                # Prefer subjects with open mistakes
                mistake_rows = conn.execute(
                    """
                    SELECT subject, COUNT(*) AS c
                    FROM student_mistakes
                    WHERE user_id=? AND COALESCE(mastered, 0)=0
                    GROUP BY subject
                    ORDER BY c DESC
                    LIMIT 5
                    """,
                    (uid,),
                ).fetchall()
                for mr in mistake_rows or []:
                    mapped = _map_to_puzzle_subject(mr["subject"] or "")
                    if mapped:
                        return mapped, (mr["subject"] or mapped)
                # Low accuracy analytics
                analytics = conn.execute(
                    """
                    SELECT subject, questions_taken, questions_correct
                    FROM subject_analytics
                    WHERE user_id=? AND questions_taken > 0
                    """,
                    (uid,),
                ).fetchall()
                ranked = []
                for r in analytics or []:
                    qt = int(r["questions_taken"] or 0)
                    qc = int(r["questions_correct"] or 0)
                    acc = (qc / qt * 100.0) if qt > 0 else 100.0
                    ranked.append((acc, r["subject"] or ""))
                ranked.sort(key=lambda x: x[0])
                for acc, subj in ranked[:3]:
                    mapped = _map_to_puzzle_subject(subj)
                    if mapped:
                        return mapped, subj or mapped
                row = conn.execute(
                    """
                    SELECT subject, content FROM living_notebook
                    WHERE user_id=? AND category IN ('Things to Revise', 'Mistakes I Made')
                      AND TRIM(COALESCE(content,'')) != ''
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (uid,),
                ).fetchone()
            if row:
                mapped = _map_to_puzzle_subject(row["subject"] or "")
                topic_hint = ""
                for line in str(row["content"] or "").split("\n"):
                    clean = line.strip().lstrip("•-* ").strip()
                    if clean:
                        topic_hint = clean[:120]
                        break
                if mapped:
                    return mapped, topic_hint or (row["subject"] or mapped)
        except Exception as e:
            print(f"[puzzle] revision seed soft-failed: {e}")
        return "", ""

    def _generate_puzzle(grade: int, subject: str, local_date: str, topic_hint: str = "") -> dict:
        hint_bit = f" Topic: {topic_hint[:80]}." if (topic_hint or "").strip() else ""
        prompt = (
            f"ONE short {subject} school puzzle.{hint_bit} Seed:{local_date}. "
            "STRICT JSON keys only: difficulty (Easy|Medium|Hard), prompt, hint, answer, solution. "
            "prompt under 40 words; answer a number or few words. No markdown."
        )
        try:
            raw = _llm_json_text(
                [
                    {"role": "system", "content": "Educational daily puzzles. JSON only. Be brief."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=200,
                temperature=0.55,
                light=True,
            )
        except Exception as e:
            print(f"[puzzle] LLM failed: {e}")
            raw = ""
        raw = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip(), flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("puzzle JSON was not an object")
        except Exception:
            data = {
                "difficulty": "Medium",
                "prompt": f"{subject}: What is 12 × 8?",
                "hint": "Think 10×8 then 2×8.",
                "answer": "96",
                "solution": "12 × 8 = 96.",
            }
        answer = str(data.get("answer") or "").strip()[:200]
        if not answer:
            data = {
                "difficulty": "Medium",
                "prompt": f"{subject}: What is 12 × 8?",
                "hint": "Think 10×8 then 2×8.",
                "answer": "96",
                "solution": "12 × 8 = 96.",
            }
            answer = "96"
        return {
            "difficulty": str(data.get("difficulty") or "Medium")[:20],
            "prompt": str(data.get("prompt") or "Solve today's puzzle.")[:2000],
            "hint": str(data.get("hint") or "Break it into smaller steps.")[:1000],
            "answer": answer,
            "solution": str(data.get("solution") or "")[:2000],
            "xp_reward": 25,
        }

    def _puzzle_lock_for(key: str) -> threading.Lock:
        with _puzzle_gen_locks_mu:
            lock = _puzzle_gen_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                _puzzle_gen_locks[key] = lock
            return lock

    @app.route("/api/daily_puzzle", methods=["GET"])
    def daily_puzzle_get():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        local_date = _parse_local_date(request.args)
        generated = None
        with get_db() as conn:
            grade = GRADE_KEY
            requested = (request.args.get("subject") or "").strip()
            topic_hint = ""
            if requested and requested in PUZZLE_SUBJECTS:
                subject = requested
            else:
                seeded, topic_hint = _revision_seed_for_user(uid)
                subject = seeded or _subject_for_date(local_date)
            if subject not in PUZZLE_SUBJECTS:
                subject = _subject_for_date(local_date)

            row = conn.execute(
                """
                SELECT * FROM daily_puzzles
                WHERE puzzle_date=? AND grade=? AND subject=?
                """,
                (local_date, grade, subject),
            ).fetchone()
            need_generate = row is None

        if need_generate:
            lock_key = f"{local_date}|{grade}|{subject}"
            lock = _puzzle_lock_for(lock_key)
            with lock:
                # Re-check after waiting — another request may have inserted
                with get_db() as conn:
                    row = conn.execute(
                        """
                        SELECT * FROM daily_puzzles
                        WHERE puzzle_date=? AND grade=? AND subject=?
                        """,
                        (local_date, grade, subject),
                    ).fetchone()
                if row is None:
                    generated = _generate_puzzle(grade, subject, local_date, topic_hint=topic_hint)

        with get_db() as conn:
            if generated:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO daily_puzzles
                        (puzzle_date, grade, subject, difficulty, prompt, hint, answer, xp_reward, solution)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            local_date, grade, subject,
                            generated["difficulty"], generated["prompt"], generated["hint"],
                            generated["answer"], generated["xp_reward"], generated["solution"],
                        ),
                    )
                except Exception as e:
                    print(f"[puzzle] insert failed: {e}")
            row = conn.execute(
                """
                SELECT * FROM daily_puzzles
                WHERE puzzle_date=? AND grade=? AND subject=?
                """,
                (local_date, grade, subject),
            ).fetchone()
            if not row:
                return jsonify({"error": "Could not load today's puzzle. Try again."}), 500

            attempt = conn.execute(
                """
                SELECT * FROM daily_puzzle_attempts
                WHERE user_id=? AND puzzle_date=? AND grade=? AND subject=?
                """,
                (uid, local_date, grade, subject),
            ).fetchone()

        payload = {
            "date": local_date,
            "grade": grade,
            "subject": subject,
            "difficulty": row["difficulty"],
            "prompt": row["prompt"],
            "hint": row["hint"],
            "xpReward": row["xp_reward"],
            "attempted": bool(attempt and attempt["attempted"]),
            "correct": bool(attempt and attempt["correct"]),
            "skipped": bool(attempt and attempt["skipped"]),
            "subjects": PUZZLE_SUBJECTS,
        }
        if attempt and (attempt["attempted"] or attempt["skipped"]):
            payload["solution"] = row["solution"]
            payload["answer"] = row["answer"]
        return jsonify(payload)

    @app.route("/api/daily_puzzle/submit", methods=["POST"])
    def daily_puzzle_submit():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        data = request.get_json(force=True) or {}
        local_date = _parse_local_date(data)
        user_answer = (data.get("answer") or "").strip()
        _pull_game(uid)
        with get_db() as conn:
            prefs = _get_prefs(conn, uid)
            grade = GRADE_KEY
            subject = (data.get("subject") or _subject_for_date(local_date)).strip()
            row = conn.execute(
                "SELECT * FROM daily_puzzles WHERE puzzle_date=? AND grade=? AND subject=?",
                (local_date, grade, subject),
            ).fetchone()
            if not row:
                return jsonify({"error": "Puzzle not found. Open today's puzzle first."}), 404

            attempt = conn.execute(
                """
                SELECT * FROM daily_puzzle_attempts
                WHERE user_id=? AND puzzle_date=? AND grade=? AND subject=?
                """,
                (uid, local_date, grade, subject),
            ).fetchone()
            if attempt and (attempt["attempted"] or attempt["skipped"]):
                return jsonify({
                    "ok": True,
                    "already": True,
                    "correct": bool(attempt["correct"]),
                    "xpAwarded": int(attempt["xp_awarded"] or 0),
                    "solution": row["solution"],
                    "answer": row["answer"],
                })

            expected = _normalize_answer(row["answer"])
            given = _normalize_answer(user_answer)
            correct = bool(expected) and given == expected
            if not correct and expected and len(expected) >= 1 and expected in given:
                correct = True

            xp_awarded = 0
            streak_info = _update_streak_for_study(conn, uid, local_date)
            if correct:
                xp_awarded = _award_xp(
                    conn, uid, "daily_puzzle", int(row["xp_reward"] or 25), local_date, subject
                )

            conn.execute(
                """
                INSERT INTO daily_puzzle_attempts
                (user_id, puzzle_date, grade, subject, attempted, correct, skipped, xp_awarded, user_answer)
                VALUES (?,?,?,?,1,?,?,?,?)
                ON CONFLICT(user_id, puzzle_date, grade, subject) DO UPDATE SET
                    attempted=1, correct=excluded.correct, xp_awarded=excluded.xp_awarded,
                    user_answer=excluded.user_answer
                """,
                (uid, local_date, grade, subject, 1 if correct else 0, 0, xp_awarded, user_answer[:500]),
            )
            xp = conn.execute("SELECT balance FROM user_xp WHERE user_id=?", (uid,)).fetchone()

        _push_game(uid)
        return jsonify({
            "ok": True,
            "correct": correct,
            "xpAwarded": xp_awarded,
            "xp": int(xp["balance"] or 0) if xp else 0,
            "solution": row["solution"],
            "answer": row["answer"],
            "streak": {
                "current": streak_info["current_streak"],
                "best": streak_info["best_streak"],
                "protected": streak_info.get("protected"),
            },
            "milestonesUnlocked": streak_info.get("milestones") or [],
        })

    @app.route("/api/daily_puzzle/skip", methods=["POST"])
    def daily_puzzle_skip():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        data = request.get_json(force=True) or {}
        local_date = _parse_local_date(data)
        _pull_game(uid)
        with get_db() as conn:
            prefs = _get_prefs(conn, uid)
            grade = GRADE_KEY
            subject = (data.get("subject") or _subject_for_date(local_date)).strip()
            row = conn.execute(
                "SELECT * FROM daily_puzzles WHERE puzzle_date=? AND grade=? AND subject=?",
                (local_date, grade, subject),
            ).fetchone()
            if not row:
                return jsonify({"error": "Puzzle not found."}), 404
            # Skipping still counts as study engagement for streak
            streak_info = _update_streak_for_study(conn, uid, local_date)
            conn.execute(
                """
                INSERT INTO daily_puzzle_attempts
                (user_id, puzzle_date, grade, subject, attempted, correct, skipped, xp_awarded)
                VALUES (?,?,?,?,0,0,1,0)
                ON CONFLICT(user_id, puzzle_date, grade, subject) DO UPDATE SET skipped=1
                """,
                (uid, local_date, grade, subject),
            )
        _push_game(uid)
        return jsonify({
            "ok": True,
            "skipped": True,
            "solution": row["solution"],
            "answer": row["answer"],
            "streak": {
                "current": streak_info["current_streak"],
                "best": streak_info["best_streak"],
            },
        })

    def _fallback_fact(local_date: str, grade: int) -> dict:
        try:
            day = datetime.strptime(local_date, "%Y-%m-%d").toordinal()
        except Exception:
            day = grade
        item = FACT_FALLBACKS[day % len(FACT_FALLBACKS)]
        return {
            "title": item["title"],
            "body": item["body"],
            "category": item["category"],
            "xp_reward": 5,
        }

    def _generate_fact(grade: int, local_date: str) -> dict:
        try:
            prompt = (
                f"ONE short school-safe fun fact. Seed:{local_date}. "
                "Body starts with 'Did you know'. Include a surprising number. "
                "STRICT JSON: title, body (2 sentences), category. No markdown."
            )
            raw = _llm_json_text(
                [
                    {
                        "role": "system",
                        "content": "Delightful educational daily facts. JSON only. Be brief.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=180,
                temperature=0.75,
                light=True,
            )
            raw = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip(), flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            title = str(data.get("title") or "Daily Fact").strip()[:120]
            body = str(data.get("body") or "").strip()[:1200]
            category = str(data.get("category") or "Fun").strip()[:60]
            if not body:
                raise ValueError("empty body")
            if not body.lower().startswith("did you know"):
                body = "Did you know " + body[0].lower() + body[1:] if body else body
            return {
                "title": title or "Daily Fact",
                "body": body,
                "category": category or "Fun",
                "xp_reward": 5,
            }
        except Exception as e:
            print(f"[daily_fact] generate failed, using fallback: {e}")
            return _fallback_fact(local_date, grade)

    @app.route("/api/daily_fact", methods=["GET"])
    def daily_fact_get():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        local_date = _parse_local_date(request.args)
        with get_db() as conn:
            migrate_gamification_tables(conn)
            grade = GRADE_KEY
            row = conn.execute(
                "SELECT * FROM daily_facts WHERE fact_date=? AND grade=?",
                (local_date, grade),
            ).fetchone()
            if not row:
                generated = _generate_fact(grade, local_date)
                conn.execute(
                    """
                    INSERT INTO daily_facts
                    (fact_date, grade, title, body, category, xp_reward)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        local_date,
                        grade,
                        generated["title"],
                        generated["body"],
                        generated["category"],
                        generated["xp_reward"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM daily_facts WHERE fact_date=? AND grade=?",
                    (local_date, grade),
                ).fetchone()
            view = conn.execute(
                """
                SELECT * FROM daily_fact_views
                WHERE user_id=? AND fact_date=? AND grade=?
                """,
                (uid, local_date, grade),
            ).fetchone()
        return jsonify({
            "date": local_date,
            "grade": grade,
            "title": row["title"],
            "body": row["body"],
            "category": row["category"],
            "xpReward": int(row["xp_reward"] or 5),
            "viewed": bool(view and view["viewed"]),
            "xpAwarded": int(view["xp_awarded"] or 0) if view else 0,
        })

    @app.route("/api/daily_fact/ack", methods=["POST"])
    def daily_fact_ack():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        data = request.get_json(force=True) or {}
        local_date = _parse_local_date(data)
        with get_db() as conn:
            migrate_gamification_tables(conn)
            grade = GRADE_KEY
            row = conn.execute(
                "SELECT * FROM daily_facts WHERE fact_date=? AND grade=?",
                (local_date, grade),
            ).fetchone()
            if not row:
                return jsonify({"error": "Fact not found. Open today's fact first."}), 404
            view = conn.execute(
                """
                SELECT * FROM daily_fact_views
                WHERE user_id=? AND fact_date=? AND grade=?
                """,
                (uid, local_date, grade),
            ).fetchone()
            awarded = 0
            if view and view["viewed"]:
                streak_info = _update_streak_for_study(conn, uid, local_date)
            else:
                reward = int(row["xp_reward"] or XP_AWARDS["daily_fact"])
                awarded = _award_xp(conn, uid, "daily_fact", reward, local_date, row["title"])
                streak_info = _update_streak_for_study(conn, uid, local_date)
                conn.execute(
                    """
                    INSERT INTO daily_fact_views
                    (user_id, fact_date, grade, viewed, xp_awarded)
                    VALUES (?,?,?,1,?)
                    ON CONFLICT(user_id, fact_date, grade) DO UPDATE SET
                      viewed=1, xp_awarded=excluded.xp_awarded
                    """,
                    (uid, local_date, grade, awarded),
                )
            xp = conn.execute(
                "SELECT balance FROM user_xp WHERE user_id=?", (uid,)
            ).fetchone()
        _push_game(uid)
        return jsonify({
            "ok": True,
            "viewed": True,
            "xpAwarded": awarded,
            "xp": int(xp["balance"] or 0) if xp else 0,
            "streak": {
                "current": streak_info["current_streak"],
                "best": streak_info["best_streak"],
            },
        })

    @app.route("/api/electives/options", methods=["GET"])
    def elective_options():
        """Allowed electives for given drop flags (UI helper)."""
        drop_math = str(request.args.get("dropMath") or request.args.get("drop_math") or "0") in (
            "1", "true", "True", "yes",
        )
        drop_science = str(request.args.get("dropScience") or request.args.get("drop_science") or "0") in (
            "1", "true", "True", "yes",
        )
        return jsonify({
            "electives": _allowed_electives(drop_math, drop_science),
            "all": list(ALL_ELECTIVES),
        })
