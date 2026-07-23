"""
env_audit.py — Audit which .env is loaded, its modification time, and whether
Windows environment variables are shadowing the .env value.
Does NOT call the Groq API.
"""

import os
import sys
import datetime

APP_DIR = r"c:\Users\Aditya\Downloads\study_buddy\study_buddy"

# ── 1. Locate and read .env raw bytes ─────────────────────────────────────────
env_candidates = [
    os.path.join(APP_DIR, ".env"),
    os.path.join(APP_DIR, "study_buddy", ".env"),
]

print("=" * 60)
print("STEP 1 — Raw .env file contents")
print("=" * 60)
for path in env_candidates:
    exists = os.path.exists(path)
    print(f"\nPath   : {path}")
    print(f"Exists : {exists}")
    if exists:
        mtime = os.path.getmtime(path)
        print(f"Modified (UTC): {datetime.datetime.utcfromtimestamp(mtime)} UTC")
        print(f"Modified (IST): {datetime.datetime.fromtimestamp(mtime)} local")
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        print(f"Raw contents:")
        for line in raw.splitlines():
            if "=" in line:
                key_name, _, value = line.partition("=")
                key_name = key_name.strip()
                value = value.strip()
                if "KEY" in key_name.upper() or "SECRET" in key_name.upper() or "TOKEN" in key_name.upper():
                    masked = value[:8] + "****" if len(value) >= 8 else "TOO_SHORT"
                    print(f"  {key_name} = {masked}")
                else:
                    print(f"  {line}")
            else:
                print(f"  {line!r}")

# ── 2. Windows environment variable check ─────────────────────────────────────
print()
print("=" * 60)
print("STEP 2 — Windows environment variable GROQ_API_KEY")
print("=" * 60)
win_val = os.environ.get("GROQ_API_KEY", None)
if win_val is not None:
    masked_win = win_val[:8] + "****" if len(win_val) >= 8 else "TOO_SHORT"
    print(f"  os.environ['GROQ_API_KEY'] = {masked_win}")
    print("  [!] Windows env var IS SET -- it will override the .env file!")
else:
    print("  os.environ['GROQ_API_KEY'] = NOT SET (no Windows override)")

# ── 3. Load .env fresh using dotenv and compare ───────────────────────────────
print()
print("=" * 60)
print("STEP 3 — Value loaded by python-dotenv (fresh load, override=True)")
print("=" * 60)
from dotenv import load_dotenv, dotenv_values

primary_env = os.path.join(APP_DIR, ".env")

# Use dotenv_values to read WITHOUT touching os.environ — pure file read
file_values = dotenv_values(primary_env)
file_key = file_values.get("GROQ_API_KEY", "")
if file_key:
    print(f"  dotenv_values (file only): {file_key[:8]}****")
else:
    print(f"  dotenv_values (file only): NOT FOUND in .env")

# Now load with override=True and check os.environ
load_dotenv(primary_env, override=True)
env_after_load = (os.getenv("GROQ_API_KEY") or "").strip()
if env_after_load:
    print(f"  os.getenv after load_dotenv(override=True): {env_after_load[:8]}****")
else:
    print(f"  os.getenv after load_dotenv(override=True): EMPTY/NOT FOUND")

# ── 4. Confirm prefix comparison ──────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 4 — Key prefix summary")
print("=" * 60)
print(f"  File contains key starting with : {file_key[:8] if file_key else 'N/A'}")
print(f"  os.getenv reads key starting with: {env_after_load[:8] if env_after_load else 'N/A'}")
if file_key and env_after_load:
    if file_key[:8] == env_after_load[:8]:
        print("  OK  Both match -- the correct key IS being loaded.")
    else:
        print("  !! MISMATCH -- Windows env var or another source is overriding .env!")
