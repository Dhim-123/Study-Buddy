"""
Gamification layer: XP, streaks, freezes, shop, daily puzzle, planner, prefs.
Additive only — registered onto the Flask app from app.py.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from flask import jsonify, request

# ── Constants ─────────────────────────────────────────────────────────

XP_AWARDS = {
    "chat": 10,
    "quiz": 30,
    "flashcards": 20,
    "mock": 50,
    "daily_puzzle": 25,
    "focus_10m": 40,
    "notes_upload": 20,
    "podcast": 20,
    "notes_read": 10,
    "planner": 10,
}

CHAT_XP_DAILY_CAP = 20  # max chat XP awards per local day (streak still once)

PUZZLE_SUBJECTS = [
    "Math", "Physics", "Chemistry", "Biology", "English",
    "History", "Geography", "Computer", "General Knowledge",
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
        "cost": 500,
        "category": "utility",
        "max_owned": 3,
        "description": "Protects your streak if you miss one day.",
    },
    {
        "id": "theme_aurora",
        "name": "Aurora Theme",
        "icon": "🎨",
        "cost": 800,
        "category": "themes",
        "max_owned": 1,
        "description": "Unlock the Aurora accent theme.",
    },
    {
        "id": "theme_forest",
        "name": "Forest Theme",
        "icon": "🌲",
        "cost": 800,
        "category": "themes",
        "max_owned": 1,
        "description": "Calm green study theme.",
    },
    {
        "id": "avatar_pack_1",
        "name": "Avatar Pack",
        "icon": "👤",
        "cost": 400,
        "category": "avatars",
        "max_owned": 1,
        "description": "Extra profile avatar frames.",
    },
    {
        "id": "badge_scholar",
        "name": "Scholar Badge",
        "icon": "🎖️",
        "cost": 600,
        "category": "badges",
        "max_owned": 1,
        "description": "Show off your scholar status.",
    },
    {
        "id": "voice_premium",
        "name": "Premium Voices",
        "icon": "🎵",
        "cost": 1000,
        "category": "voices",
        "max_owned": 1,
        "description": "Unlock extra podcast voice presets.",
    },
    {
        "id": "chat_sparkle",
        "name": "Chat Sparkles",
        "icon": "✨",
        "cost": 700,
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
            language TEXT NOT NULL DEFAULT 'en',
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
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_planner_user ON study_planner_tasks(user_id, due_date);
    """)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    except Exception:
        pass


def _parse_local_date(data) -> str:
    d = (data.get("localDate") or data.get("local_date") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d
    return datetime.utcnow().strftime("%Y-%m-%d")


def _ensure_xp_streak(conn, uid: int):
    conn.execute(
        "INSERT OR IGNORE INTO user_xp (user_id, balance, lifetime) VALUES (?,0,0)",
        (uid,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO user_streaks (user_id, current_streak, best_streak, freezes_owned) VALUES (?,0,0,0)",
        (uid,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO user_prefs (user_id) VALUES (?)",
        (uid,),
    )


def _get_prefs(conn, uid: int) -> dict:
    _ensure_xp_streak(conn, uid)
    row = conn.execute("SELECT * FROM user_prefs WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return {"grade": 10, "language": "en", "font_scale": 1.0}
    d = dict(row)
    try:
        d["preferred_subjects"] = json.loads(d.get("preferred_subjects") or "[]")
    except Exception:
        d["preferred_subjects"] = []
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
    s = re.sub(r"[^\w\s./+-]", "", s)
    return s


def register_gamification_routes(app, get_db, require_auth, get_groq_client, resolve_groq_model):
    """Attach all gamification routes to the Flask app."""

    @app.route("/api/gamification/summary", methods=["GET"])
    def gamification_summary():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        local_date = _parse_local_date(request.args)
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

        return jsonify({
            "xp": int(xp["balance"] or 0) if xp else 0,
            "lifetimeXp": int(xp["lifetime"] or 0) if xp else 0,
            "currentStreak": int(st["current_streak"] or 0) if st else 0,
            "bestStreak": int(st["best_streak"] or 0) if st else 0,
            "lastStudyDate": st["last_study_date"] if st else None,
            "freezesOwned": freezes,
            "studiedToday": studied_today,
            "prefs": {
                "grade": int(prefs.get("grade") or 10),
                "language": prefs.get("language") or "en",
                "notifyStreak": bool(prefs.get("notify_streak", 1)),
                "notifyPuzzle": bool(prefs.get("notify_puzzle", 1)),
                "highContrast": bool(prefs.get("high_contrast", 0)),
                "fontScale": float(prefs.get("font_scale") or 1.0),
                "reducedMotion": bool(prefs.get("reduced_motion", 0)),
                "preferredSubjects": prefs.get("preferred_subjects") or [],
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
        return jsonify({"ok": True, "xp": int(new_bal), "itemId": item_id, "owned": owned + 1})

    @app.route("/api/prefs", methods=["GET", "POST"])
    def user_prefs_route():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        if request.method == "GET":
            with get_db() as conn:
                prefs = _get_prefs(conn, uid)
            return jsonify({"prefs": prefs})

        data = request.get_json(force=True) or {}
        with get_db() as conn:
            _ensure_xp_streak(conn, uid)
            grade = data.get("grade", 10)
            try:
                grade = max(1, min(12, int(grade)))
            except Exception:
                grade = 10
            language = (data.get("language") or "en").strip()[:10] or "en"
            subjects = data.get("preferredSubjects") or data.get("preferred_subjects") or []
            if not isinstance(subjects, list):
                subjects = []
            subjects_json = json.dumps([str(s)[:40] for s in subjects[:12]])
            conn.execute(
                """
                UPDATE user_prefs SET
                    grade=?,
                    language=?,
                    notify_streak=?,
                    notify_puzzle=?,
                    high_contrast=?,
                    font_scale=?,
                    reduced_motion=?,
                    preferred_subjects=?,
                    updated_at=datetime('now')
                WHERE user_id=?
                """,
                (
                    grade,
                    language,
                    1 if data.get("notifyStreak", data.get("notify_streak", True)) else 0,
                    1 if data.get("notifyPuzzle", data.get("notify_puzzle", True)) else 0,
                    1 if data.get("highContrast", data.get("high_contrast", False)) else 0,
                    float(data.get("fontScale", data.get("font_scale", 1.0)) or 1.0),
                    1 if data.get("reducedMotion", data.get("reduced_motion", False)) else 0,
                    subjects_json,
                    uid,
                ),
            )
            prefs = _get_prefs(conn, uid)
        return jsonify({"ok": True, "prefs": prefs})

    @app.route("/api/planner", methods=["GET", "POST"])
    def planner_list_create():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        if request.method == "GET":
            with get_db() as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, due_date, done, created_at
                    FROM study_planner_tasks WHERE user_id=?
                    ORDER BY done ASC, due_date IS NULL, due_date ASC, id DESC
                    LIMIT 100
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
            cur = conn.execute(
                "INSERT INTO study_planner_tasks (user_id, title, due_date) VALUES (?,?,?)",
                (uid, title, due),
            )
            row = conn.execute(
                "SELECT id, title, due_date, done, created_at FROM study_planner_tasks WHERE id=?",
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
                "SELECT id, title, due_date, done, created_at FROM study_planner_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        return jsonify({"ok": True, "task": dict(updated)})

    def _generate_puzzle(grade: int, subject: str, local_date: str) -> dict:
        client = get_groq_client()
        prompt = (
            f"Create ONE short school puzzle for Grade {grade} students in {subject}. "
            f"Date seed: {local_date}. "
            "Return STRICT JSON only with keys: "
            "difficulty (Easy|Medium|Hard), prompt, hint, answer, solution. "
            "Answer must be a short string (number or few words). "
            "No markdown fences."
        )
        completion = client.chat.completions.create(
            model=resolve_groq_model(None),
            messages=[
                {"role": "system", "content": "You write educational daily puzzles. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=500,
        )
        raw = (completion.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
        except Exception:
            # Fallback static puzzle
            data = {
                "difficulty": "Medium",
                "prompt": f"Grade {grade} {subject}: What is 12 × 8?",
                "hint": "Think 10×8 then 2×8.",
                "answer": "96",
                "solution": "12 × 8 = 96.",
            }
        return {
            "difficulty": str(data.get("difficulty") or "Medium")[:20],
            "prompt": str(data.get("prompt") or "Solve today's puzzle.")[:2000],
            "hint": str(data.get("hint") or "Break it into smaller steps.")[:1000],
            "answer": str(data.get("answer") or "").strip()[:200],
            "solution": str(data.get("solution") or "")[:2000],
            "xp_reward": 25,
        }

    @app.route("/api/daily_puzzle", methods=["GET"])
    def daily_puzzle_get():
        user, err = require_auth()
        if err:
            return err
        uid = user["id"]
        local_date = _parse_local_date(request.args)
        with get_db() as conn:
            prefs = _get_prefs(conn, uid)
            grade = int(request.args.get("grade") or prefs.get("grade") or 10)
            grade = max(1, min(12, grade))
            subject = (request.args.get("subject") or "").strip() or _subject_for_date(local_date)
            if subject not in PUZZLE_SUBJECTS:
                subject = _subject_for_date(local_date)

            row = conn.execute(
                """
                SELECT * FROM daily_puzzles
                WHERE puzzle_date=? AND grade=? AND subject=?
                """,
                (local_date, grade, subject),
            ).fetchone()
            if not row:
                generated = _generate_puzzle(grade, subject, local_date)
                conn.execute(
                    """
                    INSERT INTO daily_puzzles
                    (puzzle_date, grade, subject, difficulty, prompt, hint, answer, xp_reward, solution)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        local_date, grade, subject,
                        generated["difficulty"], generated["prompt"], generated["hint"],
                        generated["answer"], generated["xp_reward"], generated["solution"],
                    ),
                )
                row = conn.execute(
                    """
                    SELECT * FROM daily_puzzles
                    WHERE puzzle_date=? AND grade=? AND subject=?
                    """,
                    (local_date, grade, subject),
                ).fetchone()

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
        with get_db() as conn:
            prefs = _get_prefs(conn, uid)
            grade = int(data.get("grade") or prefs.get("grade") or 10)
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

            correct = _normalize_answer(user_answer) == _normalize_answer(row["answer"])
            # Also accept if answer contained in response
            if not correct and _normalize_answer(row["answer"]) in _normalize_answer(user_answer):
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
        with get_db() as conn:
            prefs = _get_prefs(conn, uid)
            grade = int(data.get("grade") or prefs.get("grade") or 10)
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
