"""
test_groq.py — Standalone Groq API diagnostic script.
- Reads GROQ_API_KEY from the .env file in this folder.
- Sends one minimal request directly via urllib (no app.py involved).
- Prints exact HTTP status code and raw response body.
"""

import os
import json
import urllib.request
import urllib.error

# ── 1. Read .env manually (no dotenv needed) ──────────────────────────────────
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
print(f"[INFO] Loading .env from: {env_path}")
print(f"[INFO] .env exists: {os.path.exists(env_path)}")

api_key = ""
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("GROQ_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
                break

if not api_key:
    print("[ERROR] GROQ_API_KEY not found in .env")
    exit(1)

print(f"[INFO] GROQ_API_KEY loaded: {api_key[:8]}****")
print()

# ── 2. Build raw HTTP request to Groq API ─────────────────────────────────────
url = "https://api.groq.com/openai/v1/chat/completions"
payload = json.dumps({
    "model": "llama-3.3-70b-versatile",
    "messages": [{"role": "user", "content": "Say hello."}],
    "max_tokens": 10
}).encode("utf-8")

req = urllib.request.Request(
    url,
    data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    },
    method="POST"
)

# ── 3. Fire request and print results ────────────────────────────────────────
print(f"[INFO] Sending POST to: {url}")
try:
    with urllib.request.urlopen(req) as response:
        status = response.status
        body = response.read().decode("utf-8")
        print(f"\nHTTP Status: {status}")
        print(f"Raw Response Body:\n{body}")

except urllib.error.HTTPError as e:
    status = e.code
    body = e.read().decode("utf-8")
    print(f"\nHTTP Status: {status}")
    print(f"Raw Response Body:\n{body}")

except Exception as e:
    print(f"\n[EXCEPTION] {type(e).__name__}: {e}")
