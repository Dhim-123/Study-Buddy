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
import base64
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
env_path_root_txt = os.path.join(os.path.dirname(__file__), ".env.txt")
env_path_sub = os.path.join(os.path.dirname(__file__), "study_buddy", ".env")

if os.path.exists(env_path_root):
    load_dotenv(env_path_root, override=True)
elif os.path.exists(env_path_root_txt):
    load_dotenv(env_path_root_txt, override=True)
elif os.path.exists(env_path_sub):
    load_dotenv(env_path_sub, override=True)
else:
    load_dotenv(override=True)

# Store API key in module variable to avoid environment access issues during runtime
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_IMAGE_MODEL = os.getenv(
    "GEMINI_IMAGE_MODEL",
    "gemini-2.5-flash-image",
).strip() or "gemini-2.5-flash-image"
HF_TOKEN = (
    os.getenv("HF_TOKEN", "").strip()
    or os.getenv("HUGGINGFACE_API_TOKEN", "").strip()
    or os.getenv("HUGGING_FACE_HUB_TOKEN", "").strip()
)
HF_FLUX_MODEL = os.getenv(
    "HF_FLUX_MODEL",
    "black-forest-labs/FLUX.1-schnell",
).strip()

if not GROQ_API_KEY:
    print("\n[WARNING] No GROQ_API_KEY found!")
    print("   Create a file called  .env  in this folder and add:")
    print("   GROQ_API_KEY=your-key-here\n")

if not GEMINI_API_KEY:
    print("[WARNING] No GEMINI_API_KEY — diagrams need a billed Google AI Studio key.")

# Centralized Groq Client
_groq_client_instance = None

def get_groq_client():
    # Use module-level variable instead of os.getenv to avoid runtime environment access issues
    global GROQ_API_KEY
    if not GROQ_API_KEY:
        raise ValueError("Server has no GROQ API key configured. Please set GROQ_API_KEY in .env.")
    return Groq(api_key=GROQ_API_KEY)


def get_gemini_client():
    """Gemini client for Nano Banana educational image diagrams."""
    if not GEMINI_API_KEY:
        raise ValueError("Set GEMINI_API_KEY for diagram images.")
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL",
    "meta-llama/llama-4-scout-17b-16e-instruct",
).strip()
GROQ_VISION_FALLBACKS = [
    m for m in [
        GROQ_VISION_MODEL,
        "llama-3.2-11b-vision-preview",
        "llama-3.2-90b-vision-preview",
    ]
    if m
]

def resolve_groq_model(model_name: str) -> str:
    return model_name if model_name else DEFAULT_GROQ_MODEL


# =====================================================================
#  EXPRESSIVE PODCAST TTS  (Orpheus via Groq → PlayAI → edge-tts)
#  Goal: two distinct human hosts, not flat AI narration.
# =====================================================================

ORPHEUS_MODEL = "canopylabs/orpheus-v1-english"
PLAYAI_MODEL = "playai-tts"

# Host A = energetic / enthusiastic lead
# Host B = calm / thoughtful explainer
HOST_A_ORPHEUS = "austin"
HOST_B_ORPHEUS = "hannah"
HOST_A_PLAYAI = "Fritz-PlayAI"
HOST_B_PLAYAI = "Arista-PlayAI"
# Edge conversation voices (avoid News/Authority — those sound robotic)
HOST_A_EDGE = "en-US-BrianNeural"   # Approachable, Casual → energetic host
HOST_B_EDGE = "en-US-JennyNeural"   # Friendly, Considerate, Comfort → calm explainer

PODCAST_VOICE_PRESETS = [
    {
        "id": "alex_maya_us",
        "label": "Alex & Maya (US)",
        "host_a": "en-US-BrianNeural",
        "host_b": "en-US-JennyNeural",
    },
    {
        "id": "oliver_sonia_uk",
        "label": "Oliver & Sonia (UK)",
        "host_a": "en-GB-RyanNeural",
        "host_b": "en-GB-SoniaNeural",
    },
    {
        "id": "prabhat_neerja_in",
        "label": "Prabhat & Neerja (India EN)",
        "host_a": "en-IN-PrabhatNeural",
        "host_b": "en-IN-NeerjaNeural",
    },
    {
        "id": "guy_aria_us",
        "label": "Guy & Aria (US)",
        "host_a": "en-US-GuyNeural",
        "host_b": "en-US-AriaNeural",
    },
    {
        "id": "davis_emma_us",
        "label": "Davis & Emma (US)",
        "host_a": "en-US-DavisNeural",
        "host_b": "en-US-EmmaNeural",
    },
]

# Per-host baseline prosody (personality, before emotion overlays)
_HOST_PROSODY = {
    "A": {  # energetic / enthusiastic
        "orpheus_speed": 1.06,
        "edge_rate": "+10%",
        "edge_pitch": "+5Hz",
        "default_tag": "enthusiastic",
        "pause_ms": 220,
    },
    "B": {  # calm / thoughtful / explanatory
        "orpheus_speed": 0.94,
        "edge_rate": "-8%",
        "edge_pitch": "-2Hz",
        "default_tag": "thoughtful",
        "pause_ms": 380,
    },
}

_VOCAL_TAGS = {
    "cheerful", "excited", "curious", "surprised", "thoughtful",
    "encouraging", "sympathetic", "confident", "dramatic", "whisper",
    "laugh", "gasp", "serious", "calm", "enthusiastic", "gentle",
}

_TAG_ALIASES = {
    "laughter": "laugh",
    "chuckle": "laugh",
    "ha": "laugh",
    "wow": "surprised",
    "sad": "sympathetic",
    "happy": "cheerful",
}

# Emotion → prosody overlays (applied ON TOP of host personality)
_EMOTION_PROSODY = {
    "excited":      {"orpheus_delta": 0.10, "edge_rate": "+18%", "edge_pitch": "+8Hz", "pause_ms": 160},
    "enthusiastic": {"orpheus_delta": 0.08, "edge_rate": "+15%", "edge_pitch": "+7Hz", "pause_ms": 180},
    "cheerful":     {"orpheus_delta": 0.04, "edge_rate": "+10%", "edge_pitch": "+5Hz", "pause_ms": 220},
    "curious":      {"orpheus_delta": 0.05, "edge_rate": "+8%",  "edge_pitch": "+4Hz", "pause_ms": 260},
    "surprised":    {"orpheus_delta": 0.07, "edge_rate": "+12%", "edge_pitch": "+9Hz", "pause_ms": 200},
    "laugh":        {"orpheus_delta": 0.06, "edge_rate": "+14%", "edge_pitch": "+10Hz", "pause_ms": 180},
    "gasp":         {"orpheus_delta": 0.05, "edge_rate": "+10%", "edge_pitch": "+8Hz", "pause_ms": 240},
    "thoughtful":   {"orpheus_delta": -0.08, "edge_rate": "-12%", "edge_pitch": "-3Hz", "pause_ms": 420},
    "sympathetic":  {"orpheus_delta": -0.06, "edge_rate": "-10%", "edge_pitch": "-2Hz", "pause_ms": 400},
    "calm":         {"orpheus_delta": -0.07, "edge_rate": "-10%", "edge_pitch": "-3Hz", "pause_ms": 400},
    "whisper":      {"orpheus_delta": -0.12, "edge_rate": "-15%", "edge_pitch": "-4Hz", "pause_ms": 450},
    "encouraging":  {"orpheus_delta": 0.02, "edge_rate": "+4%",  "edge_pitch": "+3Hz", "pause_ms": 280},
    "confident":    {"orpheus_delta": 0.00, "edge_rate": "+2%",  "edge_pitch": "+1Hz", "pause_ms": 300},
    "dramatic":     {"orpheus_delta": -0.04, "edge_rate": "-6%",  "edge_pitch": "+2Hz", "pause_ms": 360},
    "serious":      {"orpheus_delta": -0.05, "edge_rate": "-8%",  "edge_pitch": "-2Hz", "pause_ms": 380},
    "gentle":       {"orpheus_delta": -0.06, "edge_rate": "-10%", "edge_pitch": "-1Hz", "pause_ms": 400},
}


def _parse_podcast_turns(script: str):
    """Split named-host (Alex/Maya) or Host A/B script into ordered speaker turns."""
    turns = []
    current = None
    buf = []
    # Preferred: Alex / Maya. Fallback: Host A / Host B / A / B.
    # Also accept leading vocal tags: "[cheerful] Alex: ..."
    label_re = re.compile(
        r"^(?:\[([a-zA-Z]+)\]\s*)?(Alex|Maya|Host\s*[AB]|A|B)\s*[:\-—]\s*(.*)$",
        re.IGNORECASE,
    )
    for raw in (script or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = label_re.match(line)
        if m:
            if current and buf:
                turns.append((current, " ".join(buf).strip()))
            leading_tag = m.group(1)
            label = m.group(2).upper().replace(" ", "")
            if label in ("ALEX", "A", "HOSTA"):
                current = "A"
            else:
                current = "B"
            rest = (m.group(3) or "").strip()
            # Preserve leading tag if the model put it before the name
            if leading_tag and not rest.startswith("["):
                rest = f"[{leading_tag.lower()}] {rest}"
            buf = [rest] if rest else []
        elif current:
            buf.append(line)
    if current and buf:
        turns.append((current, " ".join(buf).strip()))
    if not turns and script and script.strip():
        turns = [("A", script.strip())]
    return [(s, t) for s, t in turns if t]


def _extract_tag(text: str):
    """Return (tag, text_without_leading_tag)."""
    m = re.match(r"^\[([a-zA-Z]+)\]\s*", (text or "").strip())
    if not m:
        return None, (text or "").strip()
    tag = _TAG_ALIASES.get(m.group(1).lower(), m.group(1).lower())
    rest = (text or "").strip()[m.end():].strip()
    if tag not in _VOCAL_TAGS:
        return None, (text or "").strip()
    return tag, rest


def _infer_vocal_tag(text: str, speaker: str = "A") -> str:
    """Pick emotion from line content, falling back to host personality default."""
    tag, _ = _extract_tag(text)
    if tag:
        return tag
    low = text.lower()
    if any(w in low for w in ("ha!", "haha", "lol", "laugh", "hilarious", "heh")):
        return "laugh"
    if "?" in text and any(w in low for w in ("wait", "really", "how", "why", "what", "which")):
        return "curious"
    if any(w in low for w in ("wow", "whoa", "no way", "incredible", "amazing", "wild", "whoah")):
        return "surprised"
    if any(w in low for w in ("don't worry", "it's okay", "tough", "hard part", "confusing", "tricky")):
        return "sympathetic"
    if any(w in low for w in ("so cool", "love this", "let's go", "awesome", "let's dive")):
        return "excited"
    if any(w in low for w in ("basically", "in other words", "think of", "imagine", "the idea is", "here's why")):
        return "thoughtful"
    if text.strip().endswith("!"):
        return "enthusiastic" if speaker == "A" else "encouraging"
    return _HOST_PROSODY.get(speaker, _HOST_PROSODY["A"])["default_tag"]


def _clean_spoken_text(text: str) -> str:
    """
    Strip ALL stage/emotion tags so they never get read aloud.
    Convert laugh markers into natural spoken laughs.
    Soften robotic punctuation for neural TTS.
    """
    cleaned = text or ""
    # Remove every [tag] occurrence (leading or mid-line)
    cleaned = re.sub(r"\[([a-zA-Z]+)\]\s*", "", cleaned)
    # Written laugh → natural spoken laugh (heard as emotion, not "bracket laugh")
    cleaned = re.sub(r"\b(ha\s*){2,}\b", "ha ha! ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(haha+|hahaha)\b", "ha ha!", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\blol\b", "ha!", cleaned, flags=re.IGNORECASE)
    # Strip leftover stage directions in parentheses like (laughs)
    cleaned = re.sub(r"\((?:laughs?|chuckles?|sighs?|gasps?|pauses?)\)", "", cleaned, flags=re.IGNORECASE)
    # Prefer commas/ellipses for breath (helps Edge neural pacing)
    cleaned = cleaned.replace("—", ", ").replace("–", ", ")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;")
    if not cleaned:
        cleaned = "Yeah."
    # Ensure sentence ends with punctuation so TTS doesn't trail flat
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _prepare_orpheus_line(text: str, tag: str) -> str:
    """Orpheus: emotion as vocal-direction tag (drives speech), clean words after."""
    spoken = _clean_spoken_text(text)
    if tag == "gasp":
        tag = "surprised"
    # Orpheus consumes [tag] as prosody control — it is NOT spoken
    return f"[{tag}] {spoken}"


def _resolve_prosody(speaker: str, tag: str):
    """Combine host personality + emotion into final rate/pitch/speed/pause."""
    base = _HOST_PROSODY.get(speaker, _HOST_PROSODY["A"])
    emo = _EMOTION_PROSODY.get(tag, {})
    speed = max(0.80, min(1.20, base["orpheus_speed"] + emo.get("orpheus_delta", 0.0)))
    # Emotion rate/pitch override when present; else host baseline
    edge_rate = emo.get("edge_rate", base["edge_rate"])
    edge_pitch = emo.get("edge_pitch", base["edge_pitch"])
    # Host B explanations stay slower even when cheerful
    if speaker == "B" and tag in ("cheerful", "encouraging", "confident"):
        edge_rate = "-4%"
        edge_pitch = "+0Hz"
        speed = min(speed, 0.98)
    # Host A stays brighter even when thoughtful
    if speaker == "A" and tag in ("thoughtful", "calm", "serious"):
        edge_rate = "-2%"
        edge_pitch = "+2Hz"
        speed = max(speed, 0.96)
    pause_ms = emo.get("pause_ms", base["pause_ms"])
    return {
        "speed": speed,
        "edge_rate": edge_rate,
        "edge_pitch": edge_pitch,
        "pause_ms": pause_ms,
    }


def _emphasis_chunks(spoken: str, speaker: str, tag: str):
    """
    Split a line so important terms get slower delivery and surrounding
    clauses keep the host's emotional pace. Returns list of (text, rate, pitch).
    """
    prosody = _resolve_prosody(speaker, tag)
    # Split on clause boundaries for natural micro-pauses via separate utterances
    parts = re.split(r"(?<=[,;:])\s+|(?<=\.\.\.)\s+", spoken)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) <= 1:
        return [(spoken, prosody["edge_rate"], prosody["edge_pitch"])]

    chunks = []
    for i, part in enumerate(parts):
        rate = prosody["edge_rate"]
        pitch = prosody["edge_pitch"]
        # Slow down definitional / explanatory clauses (Host B or thoughtful)
        if speaker == "B" or tag in ("thoughtful", "sympathetic", "calm"):
            # Slightly slower on longer explanatory clauses
            if len(part) > 60:
                rate = "-14%" if speaker == "B" else "-8%"
        # Speed up short reactions
        if len(part) < 28 and tag in ("excited", "surprised", "laugh", "enthusiastic", "curious"):
            rate = "+16%" if speaker == "A" else "+6%"
            pitch = "+8Hz" if speaker == "A" else "+3Hz"
        # Emphasize ALL-CAPS or quoted key terms by slowing that clause
        if re.search(r"\b[A-Z]{2,}\b|\"[^\"]+\"|'[^']+'", part):
            if rate.startswith("+"):
                rate = "+2%"
            elif rate.startswith("-"):
                # push a bit slower
                try:
                    n = int(rate.strip("%+-") or "0")
                    rate = f"-{min(20, n + 4)}%"
                except ValueError:
                    rate = "-12%"
            else:
                rate = "-10%"
        chunks.append((part, rate, pitch))
    return chunks


def _wav_silence_like(reference_wav: bytes, duration_ms=300) -> bytes:
    """Generate silence matching the format of a reference WAV clip."""
    import io
    import wave
    with wave.open(io.BytesIO(reference_wav), "rb") as ref:
        channels = ref.getnchannels()
        sampwidth = ref.getsampwidth()
        sample_rate = ref.getframerate()
    nframes = int(sample_rate * (duration_ms / 1000.0))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00" * nframes * channels * sampwidth)
    return buf.getvalue()


def _concat_wavs(chunks: list) -> bytes:
    """Concatenate WAV byte blobs that share the same format."""
    import io
    import wave
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    params = None
    frames = []
    for data in chunks:
        with wave.open(io.BytesIO(data), "rb") as w:
            p = w.getparams()
            if params is None:
                params = p
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        for f in frames:
            w.writeframes(f)
    return out.getvalue()


# Tiny valid silent-ish MP3 frame padding for gaps between Edge (MP3) turns
_MP3_PAUSE_FRAME = (
    b"\xff\xfb\x90\x00" + b"\x00" * 100
)


def _tts_orpheus_line(client, text: str, voice: str, speed: float = 1.0) -> bytes:
    """Synthesize one line with Groq Orpheus (expressive neural TTS)."""
    kwargs = {
        "model": ORPHEUS_MODEL,
        "voice": voice,
        "input": text,
        "response_format": "wav",
    }
    try:
        response = client.audio.speech.create(**kwargs, speed=speed)
    except TypeError:
        response = client.audio.speech.create(**kwargs)
    except Exception:
        response = client.audio.speech.create(**kwargs)

    if hasattr(response, "read"):
        data = response.read()
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)
    if hasattr(response, "content"):
        data = response.content
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)
    if hasattr(response, "write_to_file"):
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"sb_tts_{os.getpid()}.wav")
        response.write_to_file(path)
        with open(path, "rb") as f:
            data = f.read()
        try:
            os.remove(path)
        except OSError:
            pass
        return data
    return bytes(response)


def _tts_playai_line(client, text: str, voice: str, speed: float = 1.0) -> bytes:
    """Groq PlayAI TTS — tags stripped; emotion carried by speed only."""
    spoken = _clean_spoken_text(text)
    kwargs = {
        "model": PLAYAI_MODEL,
        "voice": voice,
        "input": spoken,
        "response_format": "wav",
    }
    try:
        response = client.audio.speech.create(**kwargs, speed=speed)
    except TypeError:
        response = client.audio.speech.create(**kwargs)
    except Exception:
        response = client.audio.speech.create(**kwargs)

    if hasattr(response, "read"):
        data = response.read()
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)
    if hasattr(response, "content"):
        data = response.content
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)
    if hasattr(response, "write_to_file"):
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"sb_tts_{os.getpid()}.wav")
        response.write_to_file(path)
        with open(path, "rb") as f:
            data = f.read()
        try:
            os.remove(path)
        except OSError:
            pass
        return data
    return bytes(response)


def _tts_edge_utterance(spoken: str, voice: str, rate: str, pitch: str) -> bytes:
    """Single Edge neural utterance with tuned rate/pitch."""
    import asyncio
    import edge_tts

    spoken = (spoken or "").strip() or "Yeah."

    async def _gen():
        communicate = edge_tts.Communicate(spoken, voice=voice, rate=rate, pitch=pitch)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_gen())).result(timeout=45)
        return loop.run_until_complete(_gen())
    except RuntimeError:
        return asyncio.run(_gen())


def _tts_edge_line(text: str, voice: str, speaker: str, tag: str) -> bytes:
    """
    Edge neural TTS — one utterance per turn (fast path).
    Tags never spoken — they only change rate/pitch.
    """
    spoken = _clean_spoken_text(text)
    prosody = _resolve_prosody(speaker, tag)
    return _tts_edge_utterance(
        spoken, voice, prosody["edge_rate"], prosody["edge_pitch"]
    )


def synthesize_podcast_audio(script: str, host_a_voice: str = None, host_b_voice: str = None) -> dict:
    """
    Fast two-host podcast TTS via parallel edge-tts (skips slow Orpheus/PlayAI cascade).

    Returns dict: { audio_base64, audio_mime, engine, turns }
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    voice_a = (host_a_voice or HOST_A_EDGE).strip() or HOST_A_EDGE
    voice_b = (host_b_voice or HOST_B_EDGE).strip() or HOST_B_EDGE

    turns = _parse_podcast_turns(script)
    if not turns:
        raise ValueError("Could not parse Alex / Maya (or Host A / B) turns from podcast script.")

    # Cap turns for latency budget (teaching scripts ~10 turns)
    turns = turns[:10]

    def _synth_one(index_speaker_line):
        i, speaker, line = index_speaker_line
        tag = _infer_vocal_tag(line, speaker)
        voice_e = voice_a if speaker == "A" else voice_b
        audio = _tts_edge_line(line, voice_e, speaker, tag)
        pause_ms = _resolve_prosody(speaker, tag)["pause_ms"]
        # Keep pauses short for snappy playback + smaller payload
        pause_ms = min(pause_ms, 220)
        return i, audio, pause_ms

    jobs = [(i, speaker, line) for i, (speaker, line) in enumerate(turns)]
    results = {}
    workers = min(6, max(1, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_synth_one, job) for job in jobs]
        for fut in as_completed(futures):
            i, audio, pause_ms = fut.result(timeout=60)
            results[i] = (audio, pause_ms)

    wav_parts = []
    for i in range(len(turns)):
        audio, pause_ms = results[i]
        wav_parts.append(audio)
        if i < len(turns) - 1:
            if audio.startswith(b"RIFF"):
                try:
                    wav_parts.append(_wav_silence_like(audio, pause_ms))
                except Exception:
                    pass
            else:
                frame_count = max(3, pause_ms // 50)
                wav_parts.append(_MP3_PAUSE_FRAME * frame_count)

    if wav_parts and not wav_parts[0].startswith(b"RIFF"):
        combined = b"".join(p for p in wav_parts if p)
        b64 = base64.b64encode(combined).decode("ascii")
        return {
            "audio_base64": b64,
            "audio_mime": "audio/mpeg",
            "engine": "edge-tts",
            "turns": len(turns),
            "host_a_voice": voice_a,
            "host_b_voice": voice_b,
        }

    riff_parts = [p for p in wav_parts if p.startswith(b"RIFF")]
    combined = _concat_wavs(riff_parts)
    b64 = base64.b64encode(combined).decode("ascii")
    return {
        "audio_base64": b64,
        "audio_mime": "audio/wav",
        "engine": "edge-tts",
        "turns": len(turns),
        "host_a_voice": voice_a,
        "host_b_voice": voice_b,
    }


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

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=_APP_DIR, static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "study_buddy_persistent_secret_key_2025")
# Note: Using a fixed secret_key in production — set FLASK_SECRET_KEY in .env
# to keep sessions alive across restarts.

CORS(app, supports_credentials=True)


# =====================================================================
#  STEP 4: DATABASE SETUP — SQLite for persistence
# =====================================================================

# Vercel serverless filesystem is read-only except /tmp (use OS temp dir)
if os.environ.get("VERCEL"):
    import tempfile
    DB_PATH = os.path.join(tempfile.gettempdir(), "study_buddy.db")
else:
    DB_PATH = os.path.join(_APP_DIR, "study_buddy.db")


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
                firebase_uid TEXT   UNIQUE,
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
                role            TEXT    NOT NULL CHECK(role IN ('user','assistant','ai')),
                content         TEXT    NOT NULL,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_conv_user    ON conversations(user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_msg_conv     ON messages(conversation_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS living_notebook (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject     TEXT    NOT NULL DEFAULT 'General',
                category    TEXT    NOT NULL CHECK(category IN ('Key Points', 'Formulas', 'Definitions', 'Mistakes I Made', 'Things to Revise', 'My Own Notes')),
                content     TEXT    NOT NULL,
                position    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_notebook_user ON living_notebook(user_id, subject, category);

            CREATE TABLE IF NOT EXISTS learning_dna (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                 INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                total_study_minutes     INTEGER NOT NULL DEFAULT 0,
                total_quizzes           INTEGER NOT NULL DEFAULT 0,
                total_quiz_questions    INTEGER NOT NULL DEFAULT 0,
                correct_quiz_questions  INTEGER NOT NULL DEFAULT 0,
                preferred_style         TEXT    NOT NULL DEFAULT 'Step-by-Step',
                learning_pace           TEXT    NOT NULL DEFAULT 'Steady',
                updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS subject_analytics (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject           TEXT    NOT NULL,
                questions_taken   INTEGER NOT NULL DEFAULT 0,
                questions_correct INTEGER NOT NULL DEFAULT 0,
                study_minutes     INTEGER NOT NULL DEFAULT 0,
                updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, subject)
            );

            CREATE TABLE IF NOT EXISTS student_mistakes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject         TEXT    NOT NULL DEFAULT 'General',
                topic           TEXT    NOT NULL DEFAULT 'General',
                question        TEXT    NOT NULL,
                wrong_answer    TEXT    NOT NULL,
                correct_answer  TEXT    NOT NULL,
                explanation     TEXT    NOT NULL,
                mastered        INTEGER NOT NULL DEFAULT 0,
                source_type     TEXT    NOT NULL DEFAULT 'quiz', -- 'quiz', 'crosscheck', 'practice'
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                mastered_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mistakes_user ON student_mistakes(user_id, subject);
            CREATE INDEX IF NOT EXISTS idx_mistakes_mastered ON student_mistakes(user_id, mastered);
        """)
        try:
            conn.execute("ALTER TABLE living_notebook ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

        # Migration check for living_notebook table CHECK constraint to support all 6 categories without data loss
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='living_notebook'").fetchone()
        if row and row["sql"] and "Things to Revise" not in row["sql"]:
            print("[DB] Migrating living_notebook schema to support all 6 categories...")
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("""
                CREATE TABLE living_notebook_migration (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    subject     TEXT    NOT NULL DEFAULT 'General',
                    category    TEXT    NOT NULL CHECK(category IN ('Key Points', 'Formulas', 'Definitions', 'Mistakes I Made', 'Things to Revise', 'My Own Notes')),
                    content     TEXT    NOT NULL,
                    position    INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
                );
            """)
            conn.execute("""
                INSERT INTO living_notebook_migration (id, user_id, subject, category, content, position, created_at, updated_at)
                SELECT id, user_id, subject, category, content, COALESCE(position, 0), created_at, updated_at
                FROM living_notebook;
            """)
            conn.execute("DROP TABLE living_notebook")
            conn.execute("ALTER TABLE living_notebook_migration RENAME TO living_notebook")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notebook_user ON living_notebook(user_id, subject, category)")
            conn.execute("PRAGMA foreign_keys=ON")
            print("[DB] Migration completed successfully.")

        # Migration check for student_mistakes table to support enhanced Mistake Vault
        mistake_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='student_mistakes'").fetchone()
        if mistake_row and mistake_row["sql"] and "question" not in mistake_row["sql"]:
            print("[DB] Migrating student_mistakes schema for Mistake Vault...")
            conn.execute("PRAGMA foreign_keys=OFF")
            
            # Check if old table has data
            old_data = conn.execute("SELECT COUNT(*) as count FROM student_mistakes").fetchone()
            has_old_data = old_data and old_data["count"] > 0
            
            conn.execute("""
                CREATE TABLE student_mistakes_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    subject         TEXT    NOT NULL DEFAULT 'General',
                    topic           TEXT    NOT NULL DEFAULT 'General',
                    question        TEXT    NOT NULL,
                    wrong_answer    TEXT    NOT NULL,
                    correct_answer  TEXT    NOT NULL,
                    explanation     TEXT    NOT NULL,
                    mastered        INTEGER NOT NULL DEFAULT 0,
                    source_type     TEXT    NOT NULL DEFAULT 'quiz',
                    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                    mastered_at     TEXT
                );
            """)
            
            # Migrate old data if exists
            if has_old_data:
                conn.execute("""
                    INSERT INTO student_mistakes_new (user_id, subject, topic, question, wrong_answer, correct_answer, explanation, source_type, created_at)
                    SELECT user_id, subject, 'General', 
                           COALESCE(SUBSTR(mistake, 1, 200), 'Legacy mistake'), 
                           'Unknown', 'See explanation', mistake, 'legacy', created_at
                    FROM student_mistakes
                """)
            
            conn.execute("DROP TABLE student_mistakes")
            conn.execute("ALTER TABLE student_mistakes_new RENAME TO student_mistakes")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mistakes_user ON student_mistakes(user_id, subject)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mistakes_mastered ON student_mistakes(user_id, mastered)")
            conn.execute("PRAGMA foreign_keys=ON")
            print("[DB] Mistake Vault migration complete!")

        # Firebase Auth: link Google users to local SQLite rows
        try:
            conn.execute("ALTER TABLE users ADD COLUMN firebase_uid TEXT")
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_firebase_uid ON users(firebase_uid)"
            )
        except Exception:
            pass


# =====================================================================
#  STEP 5: AUTH HELPERS
# =====================================================================

_firebase_app = None


def init_firebase_admin():
    """Initialize firebase-admin once from env. Returns app or None if not configured."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        print("[Firebase] firebase-admin not installed.")
        return None

    if firebase_admin._apps:
        _firebase_app = firebase_admin.get_app()
        return _firebase_app

    sa_json = (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
    sa_path = (os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH") or "").strip()
    try:
        if sa_json:
            info = json.loads(sa_json)
            cred = credentials.Certificate(info)
        elif sa_path and os.path.exists(sa_path):
            cred = credentials.Certificate(sa_path)
        else:
            return None
        _firebase_app = firebase_admin.initialize_app(cred)
        print("[Firebase] Admin SDK initialized.")
        return _firebase_app
    except Exception as e:
        print(f"[Firebase] Failed to initialize Admin SDK: {e}")
        return None


def get_firebase_web_config():
    """Public Firebase web client config (safe to expose to the browser)."""
    raw = (os.getenv("FIREBASE_WEB_CONFIG") or "").strip()
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
        if not cfg.get("apiKey") or not cfg.get("projectId"):
            return None
        return {
            "apiKey": cfg.get("apiKey"),
            "authDomain": cfg.get("authDomain"),
            "projectId": cfg.get("projectId"),
            "appId": cfg.get("appId"),
            "messagingSenderId": cfg.get("messagingSenderId"),
            "storageBucket": cfg.get("storageBucket"),
        }
    except Exception:
        return None


def get_firestore():
    """Lazy Firestore client from Admin SDK. Returns None if Firebase is not configured."""
    if not init_firebase_admin():
        return None
    try:
        from firebase_admin import firestore
        return firestore.client()
    except Exception as e:
        print(f"[Firestore] Failed to create client: {e}")
        return None


def _fs_notebook_ref(db, user_id, entry_id):
    return (
        db.collection("users")
        .document(str(user_id))
        .collection("notebook")
        .document(str(entry_id))
    )


def _entry_to_fs_payload(user_id, entry):
    return {
        "user_id": int(user_id),
        "subject": entry.get("subject") or "General",
        "category": entry.get("category") or "Key Points",
        "content": entry.get("content") or "",
        "position": int(entry.get("position") or 0),
        "created_at": entry.get("created_at") or "",
        "updated_at": entry.get("updated_at") or "",
    }


def fs_upsert_notebook_entry(user_id, entry):
    """Mirror one notebook entry to Firestore. Soft-fails if Firestore is unavailable."""
    db = get_firestore()
    if not db or not entry:
        return
    try:
        entry_id = entry.get("id")
        if entry_id is None:
            return
        _fs_notebook_ref(db, user_id, entry_id).set(
            _entry_to_fs_payload(user_id, entry),
            merge=True,
        )
    except Exception as e:
        print(f"[Firestore] upsert notebook entry failed: {e}")


def fs_delete_notebook_entry(user_id, entry_id):
    """Delete one notebook entry from Firestore. Soft-fails if unavailable."""
    db = get_firestore()
    if not db or entry_id is None:
        return
    try:
        _fs_notebook_ref(db, user_id, entry_id).delete()
    except Exception as e:
        print(f"[Firestore] delete notebook entry failed: {e}")


def fs_pull_notebook_into_sqlite(user_id):
    """Pull remote notebook docs into local SQLite for this user. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        docs = (
            db.collection("users")
            .document(str(user_id))
            .collection("notebook")
            .stream()
        )
        with get_db() as conn:
            for doc in docs:
                data = doc.to_dict() or {}
                try:
                    entry_id = int(doc.id)
                except (TypeError, ValueError):
                    continue
                subject = (data.get("subject") or "General")[:50]
                category = data.get("category") or "Key Points"
                if category not in VALID_NOTEBOOK_CATEGORIES:
                    category = "My Own Notes"
                content = data.get("content") or ""
                if not str(content).strip():
                    continue
                position = int(data.get("position") or 0)
                created_at = data.get("created_at") or None
                updated_at = data.get("updated_at") or None

                owned = conn.execute(
                    "SELECT id FROM living_notebook WHERE id=? AND user_id=?",
                    (entry_id, user_id),
                ).fetchone()
                if owned:
                    conn.execute(
                        """
                        UPDATE living_notebook
                        SET subject=?, category=?, content=?, position=?,
                            created_at=COALESCE(?, created_at),
                            updated_at=COALESCE(?, updated_at)
                        WHERE id=? AND user_id=?
                        """,
                        (subject, category, content, position, created_at, updated_at, entry_id, user_id),
                    )
                    continue

                id_taken = conn.execute(
                    "SELECT id, user_id FROM living_notebook WHERE id=?",
                    (entry_id,),
                ).fetchone()
                if id_taken:
                    # Local id belongs to another user — insert as a new row and re-key in Firestore
                    cur = conn.execute(
                        """
                        INSERT INTO living_notebook (user_id, subject, category, content, position)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (user_id, subject, category, content, position),
                    )
                    new_id = cur.lastrowid
                    row = conn.execute(
                        "SELECT * FROM living_notebook WHERE id=?", (new_id,)
                    ).fetchone()
                    try:
                        _fs_notebook_ref(db, user_id, new_id).set(
                            _entry_to_fs_payload(user_id, dict(row)),
                            merge=True,
                        )
                        _fs_notebook_ref(db, user_id, entry_id).delete()
                    except Exception as e:
                        print(f"[Firestore] re-key notebook entry failed: {e}")
                else:
                    if created_at and updated_at:
                        conn.execute(
                            """
                            INSERT INTO living_notebook
                              (id, user_id, subject, category, content, position, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (entry_id, user_id, subject, category, content, position, created_at, updated_at),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO living_notebook
                              (id, user_id, subject, category, content, position)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (entry_id, user_id, subject, category, content, position),
                        )
    except Exception as e:
        print(f"[Firestore] pull notebook failed: {e}")


def fs_push_all_notebook_entries(user_id):
    """Push all local notebook entries for a user to Firestore. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM living_notebook WHERE user_id=?",
                (user_id,),
            ).fetchall()
        for row in rows:
            fs_upsert_notebook_entry(user_id, dict(row))
    except Exception as e:
        print(f"[Firestore] push all notebook failed: {e}")


def _fs_conversation_ref(db, user_id, conv_id):
    return (
        db.collection("users")
        .document(str(user_id))
        .collection("conversations")
        .document(str(conv_id))
    )


def _fs_message_ref(db, user_id, conv_id, msg_id):
    return _fs_conversation_ref(db, user_id, conv_id).collection("messages").document(str(msg_id))


def _conv_to_fs_payload(user_id, conv):
    return {
        "user_id": int(user_id),
        "title": (conv.get("title") or "New Chat")[:100],
        "pinned": 1 if conv.get("pinned") else 0,
        "archived": 1 if conv.get("archived") else 0,
        "created_at": conv.get("created_at") or "",
        "updated_at": conv.get("updated_at") or "",
    }


def _msg_to_fs_payload(msg):
    role = msg.get("role") or "user"
    if role == "ai":
        role = "assistant"
    return {
        "role": role,
        "content": msg.get("content") or "",
        "created_at": msg.get("created_at") or "",
        "conversation_id": int(msg.get("conversation_id") or 0),
    }


def fs_upsert_conversation(user_id, conv):
    """Mirror one conversation metadata doc to Firestore. Soft-fails."""
    db = get_firestore()
    if not db or not conv:
        return
    try:
        conv_id = conv.get("id")
        if conv_id is None:
            return
        _fs_conversation_ref(db, user_id, conv_id).set(
            _conv_to_fs_payload(user_id, conv),
            merge=True,
        )
    except Exception as e:
        print(f"[Firestore] upsert conversation failed: {e}")


def fs_upsert_message(user_id, conv_id, msg):
    """Mirror one chat message to Firestore. Soft-fails."""
    db = get_firestore()
    if not db or not msg or conv_id is None:
        return
    try:
        msg_id = msg.get("id")
        if msg_id is None:
            return
        payload = _msg_to_fs_payload(msg)
        payload["conversation_id"] = int(conv_id)
        _fs_message_ref(db, user_id, conv_id, msg_id).set(payload, merge=True)
    except Exception as e:
        print(f"[Firestore] upsert message failed: {e}")


def fs_delete_conversation(user_id, conv_id):
    """Delete conversation doc and its message subcollection. Soft-fails."""
    db = get_firestore()
    if not db or conv_id is None:
        return
    try:
        conv_ref = _fs_conversation_ref(db, user_id, conv_id)
        for msg_doc in conv_ref.collection("messages").stream():
            msg_doc.reference.delete()
        conv_ref.delete()
    except Exception as e:
        print(f"[Firestore] delete conversation failed: {e}")


def fs_pull_conversations_into_sqlite(user_id):
    """Pull remote conversations + messages into local SQLite. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        conv_docs = (
            db.collection("users")
            .document(str(user_id))
            .collection("conversations")
            .stream()
        )
        with get_db() as conn:
            for conv_doc in conv_docs:
                data = conv_doc.to_dict() or {}
                try:
                    remote_conv_id = int(conv_doc.id)
                except (TypeError, ValueError):
                    continue

                title = (data.get("title") or "New Chat")[:100]
                pinned = 1 if data.get("pinned") else 0
                archived = 1 if data.get("archived") else 0
                created_at = data.get("created_at") or None
                updated_at = data.get("updated_at") or None

                local_conv_id = None
                owned = conn.execute(
                    "SELECT id FROM conversations WHERE id=? AND user_id=?",
                    (remote_conv_id, user_id),
                ).fetchone()
                if owned:
                    local_conv_id = remote_conv_id
                    conn.execute(
                        """
                        UPDATE conversations
                        SET title=?, pinned=?, archived=?,
                            created_at=COALESCE(?, created_at),
                            updated_at=COALESCE(?, updated_at)
                        WHERE id=? AND user_id=?
                        """,
                        (title, pinned, archived, created_at, updated_at, remote_conv_id, user_id),
                    )
                else:
                    id_taken = conn.execute(
                        "SELECT id FROM conversations WHERE id=?",
                        (remote_conv_id,),
                    ).fetchone()
                    if id_taken:
                        cur = conn.execute(
                            "INSERT INTO conversations (user_id, title, pinned, archived) VALUES (?,?,?,?)",
                            (user_id, title, pinned, archived),
                        )
                        local_conv_id = cur.lastrowid
                        row = conn.execute(
                            "SELECT * FROM conversations WHERE id=?", (local_conv_id,)
                        ).fetchone()
                        try:
                            _fs_conversation_ref(db, user_id, local_conv_id).set(
                                _conv_to_fs_payload(user_id, dict(row)),
                                merge=True,
                            )
                            # Move messages under new id, then delete old remote conv
                            for msg_doc in _fs_conversation_ref(db, user_id, remote_conv_id).collection("messages").stream():
                                msg_data = msg_doc.to_dict() or {}
                                _fs_message_ref(db, user_id, local_conv_id, msg_doc.id).set(msg_data, merge=True)
                                msg_doc.reference.delete()
                            _fs_conversation_ref(db, user_id, remote_conv_id).delete()
                        except Exception as e:
                            print(f"[Firestore] re-key conversation failed: {e}")
                    else:
                        if created_at and updated_at:
                            conn.execute(
                                """
                                INSERT INTO conversations
                                  (id, user_id, title, pinned, archived, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (remote_conv_id, user_id, title, pinned, archived, created_at, updated_at),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO conversations (id, user_id, title, pinned, archived)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (remote_conv_id, user_id, title, pinned, archived),
                            )
                        local_conv_id = remote_conv_id

                if local_conv_id is None:
                    continue

                # Messages live under the local conversation id in Firestore after any re-key
                try:
                    msg_docs = list(
                        _fs_conversation_ref(db, user_id, local_conv_id)
                        .collection("messages")
                        .stream()
                    )
                except Exception:
                    msg_docs = []

                for msg_doc in msg_docs:
                    mdata = msg_doc.to_dict() or {}
                    try:
                        remote_msg_id = int(msg_doc.id)
                    except (TypeError, ValueError):
                        continue
                    role = mdata.get("role") or "user"
                    if role == "ai":
                        role = "assistant"
                    if role not in ("user", "assistant"):
                        continue
                    content = (mdata.get("content") or "").strip()
                    if not content:
                        continue
                    msg_created = mdata.get("created_at") or None

                    msg_owned = conn.execute(
                        "SELECT id FROM messages WHERE id=? AND conversation_id=?",
                        (remote_msg_id, local_conv_id),
                    ).fetchone()
                    if msg_owned:
                        conn.execute(
                            """
                            UPDATE messages
                            SET role=?, content=?, created_at=COALESCE(?, created_at)
                            WHERE id=? AND conversation_id=?
                            """,
                            (role, content, msg_created, remote_msg_id, local_conv_id),
                        )
                        continue

                    msg_id_taken = conn.execute(
                        "SELECT id FROM messages WHERE id=?",
                        (remote_msg_id,),
                    ).fetchone()
                    if msg_id_taken:
                        cur = conn.execute(
                            "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                            (local_conv_id, role, content),
                        )
                        new_msg_id = cur.lastrowid
                        row = conn.execute(
                            "SELECT * FROM messages WHERE id=?", (new_msg_id,)
                        ).fetchone()
                        try:
                            fs_upsert_message(user_id, local_conv_id, dict(row))
                            _fs_message_ref(db, user_id, local_conv_id, remote_msg_id).delete()
                        except Exception as e:
                            print(f"[Firestore] re-key message failed: {e}")
                    else:
                        if msg_created:
                            conn.execute(
                                """
                                INSERT INTO messages (id, conversation_id, role, content, created_at)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (remote_msg_id, local_conv_id, role, content, msg_created),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO messages (id, conversation_id, role, content)
                                VALUES (?, ?, ?, ?)
                                """,
                                (remote_msg_id, local_conv_id, role, content),
                            )
    except Exception as e:
        print(f"[Firestore] pull conversations failed: {e}")


def fs_push_all_conversations(user_id):
    """Push all local conversations + messages for a user to Firestore. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        with get_db() as conn:
            convs = conn.execute(
                "SELECT * FROM conversations WHERE user_id=?",
                (user_id,),
            ).fetchall()
            for conv in convs:
                c = dict(conv)
                fs_upsert_conversation(user_id, c)
                msgs = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
                    (c["id"],),
                ).fetchall()
                for msg in msgs:
                    fs_upsert_message(user_id, c["id"], dict(msg))
    except Exception as e:
        print(f"[Firestore] push all conversations failed: {e}")


def _fs_learning_dna_ref(db, user_id):
    return (
        db.collection("users")
        .document(str(user_id))
        .collection("learning_dna")
        .document("profile")
    )


def _fs_subject_analytics_ref(db, user_id, subject):
    # Firestore doc ids cannot contain /
    safe = re.sub(r"[/\\]", "_", (subject or "General").strip()[:80]) or "General"
    return (
        db.collection("users")
        .document(str(user_id))
        .collection("subject_analytics")
        .document(safe)
    )


def fs_upsert_learning_dna(user_id, profile):
    """Mirror learning_dna profile row to Firestore. Soft-fails."""
    db = get_firestore()
    if not db or not profile:
        return
    try:
        payload = {
            "user_id": int(user_id),
            "total_study_minutes": int(profile.get("total_study_minutes") or 0),
            "total_quizzes": int(profile.get("total_quizzes") or 0),
            "total_quiz_questions": int(profile.get("total_quiz_questions") or 0),
            "correct_quiz_questions": int(profile.get("correct_quiz_questions") or 0),
            "preferred_style": profile.get("preferred_style") or "Step-by-Step",
            "learning_pace": profile.get("learning_pace") or "Steady",
            "updated_at": profile.get("updated_at") or "",
        }
        _fs_learning_dna_ref(db, user_id).set(payload, merge=True)
    except Exception as e:
        print(f"[Firestore] upsert learning_dna failed: {e}")


def fs_upsert_subject_analytics(user_id, row):
    """Mirror one subject_analytics row to Firestore. Soft-fails."""
    db = get_firestore()
    if not db or not row:
        return
    try:
        subject = (row.get("subject") or "General").strip()[:50] or "General"
        payload = {
            "user_id": int(user_id),
            "subject": subject,
            "questions_taken": int(row.get("questions_taken") or 0),
            "questions_correct": int(row.get("questions_correct") or 0),
            "study_minutes": int(row.get("study_minutes") or 0),
            "updated_at": row.get("updated_at") or "",
        }
        _fs_subject_analytics_ref(db, user_id, subject).set(payload, merge=True)
    except Exception as e:
        print(f"[Firestore] upsert subject_analytics failed: {e}")


def fs_pull_learning_dna_into_sqlite(user_id):
    """Pull Learning DNA profile + subject analytics into SQLite. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        with get_db() as conn:
            # Profile
            snap = _fs_learning_dna_ref(db, user_id).get()
            if snap.exists:
                data = snap.to_dict() or {}
                get_or_create_learning_dna(conn, user_id)
                conn.execute(
                    """
                    UPDATE learning_dna SET
                      total_study_minutes=?,
                      total_quizzes=?,
                      total_quiz_questions=?,
                      correct_quiz_questions=?,
                      preferred_style=?,
                      learning_pace=?,
                      updated_at=COALESCE(?, updated_at)
                    WHERE user_id=?
                    """,
                    (
                        int(data.get("total_study_minutes") or 0),
                        int(data.get("total_quizzes") or 0),
                        int(data.get("total_quiz_questions") or 0),
                        int(data.get("correct_quiz_questions") or 0),
                        (data.get("preferred_style") or "Step-by-Step")[:50],
                        (data.get("learning_pace") or "Steady")[:50],
                        data.get("updated_at") or None,
                        user_id,
                    ),
                )

            # Subject analytics
            for doc in (
                db.collection("users")
                .document(str(user_id))
                .collection("subject_analytics")
                .stream()
            ):
                data = doc.to_dict() or {}
                subject = (data.get("subject") or doc.id or "General").strip()[:50] or "General"
                conn.execute(
                    """
                    INSERT INTO subject_analytics
                      (user_id, subject, questions_taken, questions_correct, study_minutes, updated_at)
                    VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                    ON CONFLICT(user_id, subject) DO UPDATE SET
                      questions_taken=excluded.questions_taken,
                      questions_correct=excluded.questions_correct,
                      study_minutes=excluded.study_minutes,
                      updated_at=COALESCE(excluded.updated_at, subject_analytics.updated_at)
                    """,
                    (
                        user_id,
                        subject,
                        int(data.get("questions_taken") or 0),
                        int(data.get("questions_correct") or 0),
                        int(data.get("study_minutes") or 0),
                        data.get("updated_at") or None,
                    ),
                )
    except Exception as e:
        print(f"[Firestore] pull learning_dna failed: {e}")


def fs_push_all_learning_dna(user_id):
    """Push local Learning DNA profile + all subject analytics. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        with get_db() as conn:
            profile = get_or_create_learning_dna(conn, user_id)
            rows = conn.execute(
                "SELECT * FROM subject_analytics WHERE user_id=?",
                (user_id,),
            ).fetchall()
        if profile:
            fs_upsert_learning_dna(user_id, profile)
        for row in rows:
            fs_upsert_subject_analytics(user_id, dict(row))
    except Exception as e:
        print(f"[Firestore] push learning_dna failed: {e}")


def _fs_mistake_ref(db, user_id, mistake_id):
    return (
        db.collection("users")
        .document(str(user_id))
        .collection("mistakes")
        .document(str(mistake_id))
    )


def _mistake_to_fs_payload(user_id, mistake):
    return {
        "user_id": int(user_id),
        "subject": mistake.get("subject") or "General",
        "topic": mistake.get("topic") or "General",
        "question": mistake.get("question") or "",
        "wrong_answer": mistake.get("wrong_answer") or "",
        "correct_answer": mistake.get("correct_answer") or "",
        "explanation": mistake.get("explanation") or "",
        "mastered": int(mistake.get("mastered") or 0),
        "source_type": mistake.get("source_type") or "quiz",
        "created_at": mistake.get("created_at") or "",
        "mastered_at": mistake.get("mastered_at") or "",
    }


def fs_upsert_mistake(user_id, mistake):
    """Mirror one Mistake Vault row to Firestore. Soft-fails."""
    db = get_firestore()
    if not db or not mistake:
        return
    try:
        mistake_id = mistake.get("id")
        if mistake_id is None:
            return
        _fs_mistake_ref(db, user_id, mistake_id).set(
            _mistake_to_fs_payload(user_id, mistake),
            merge=True,
        )
    except Exception as e:
        print(f"[Firestore] upsert mistake failed: {e}")


def fs_delete_mistake(user_id, mistake_id):
    """Delete one mistake from Firestore. Soft-fails."""
    db = get_firestore()
    if not db or mistake_id is None:
        return
    try:
        _fs_mistake_ref(db, user_id, mistake_id).delete()
    except Exception as e:
        print(f"[Firestore] delete mistake failed: {e}")


def fs_pull_mistakes_into_sqlite(user_id):
    """Pull remote Mistake Vault docs into local SQLite. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        docs = (
            db.collection("users")
            .document(str(user_id))
            .collection("mistakes")
            .stream()
        )
        with get_db() as conn:
            for doc in docs:
                data = doc.to_dict() or {}
                try:
                    mistake_id = int(doc.id)
                except (TypeError, ValueError):
                    continue
                question = (data.get("question") or "").strip()
                correct_answer = (data.get("correct_answer") or "").strip()
                explanation = (data.get("explanation") or "").strip()
                if not question or not correct_answer or not explanation:
                    continue
                subject = (data.get("subject") or "General")[:50]
                topic = (data.get("topic") or "General")[:100]
                wrong_answer = data.get("wrong_answer") or ""
                mastered = 1 if data.get("mastered") else 0
                source_type = (data.get("source_type") or "quiz")[:50]
                created_at = data.get("created_at") or None
                mastered_at = data.get("mastered_at") or None

                owned = conn.execute(
                    "SELECT id FROM student_mistakes WHERE id=? AND user_id=?",
                    (mistake_id, user_id),
                ).fetchone()
                if owned:
                    conn.execute(
                        """
                        UPDATE student_mistakes SET
                          subject=?, topic=?, question=?, wrong_answer=?,
                          correct_answer=?, explanation=?, mastered=?,
                          source_type=?,
                          created_at=COALESCE(?, created_at),
                          mastered_at=?
                        WHERE id=? AND user_id=?
                        """,
                        (
                            subject, topic, question, wrong_answer,
                            correct_answer, explanation, mastered,
                            source_type, created_at, mastered_at,
                            mistake_id, user_id,
                        ),
                    )
                    continue

                id_taken = conn.execute(
                    "SELECT id FROM student_mistakes WHERE id=?",
                    (mistake_id,),
                ).fetchone()
                if id_taken:
                    cur = conn.execute(
                        """
                        INSERT INTO student_mistakes
                          (user_id, subject, topic, question, wrong_answer,
                           correct_answer, explanation, mastered, source_type, mastered_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id, subject, topic, question, wrong_answer,
                            correct_answer, explanation, mastered, source_type,
                            mastered_at,
                        ),
                    )
                    new_id = cur.lastrowid
                    row = conn.execute(
                        "SELECT * FROM student_mistakes WHERE id=?", (new_id,)
                    ).fetchone()
                    try:
                        _fs_mistake_ref(db, user_id, new_id).set(
                            _mistake_to_fs_payload(user_id, dict(row)),
                            merge=True,
                        )
                        _fs_mistake_ref(db, user_id, mistake_id).delete()
                    except Exception as e:
                        print(f"[Firestore] re-key mistake failed: {e}")
                else:
                    if created_at:
                        conn.execute(
                            """
                            INSERT INTO student_mistakes
                              (id, user_id, subject, topic, question, wrong_answer,
                               correct_answer, explanation, mastered, source_type,
                               created_at, mastered_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                mistake_id, user_id, subject, topic, question,
                                wrong_answer, correct_answer, explanation,
                                mastered, source_type, created_at, mastered_at,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO student_mistakes
                              (id, user_id, subject, topic, question, wrong_answer,
                               correct_answer, explanation, mastered, source_type,
                               mastered_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                mistake_id, user_id, subject, topic, question,
                                wrong_answer, correct_answer, explanation,
                                mastered, source_type, mastered_at,
                            ),
                        )
    except Exception as e:
        print(f"[Firestore] pull mistakes failed: {e}")


def fs_push_all_mistakes(user_id):
    """Push all local Mistake Vault rows for a user to Firestore. Soft-fails."""
    db = get_firestore()
    if not db:
        return
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM student_mistakes WHERE user_id=?",
                (user_id,),
            ).fetchall()
        for row in rows:
            fs_upsert_mistake(user_id, dict(row))
    except Exception as e:
        print(f"[Firestore] push all mistakes failed: {e}")


def _fs_progress_ref(db, user_id):
    return (
        db.collection("users")
        .document(str(user_id))
        .collection("progress")
        .document("summary")
    )


def fs_upsert_progress_summary(user_id, summary):
    """Mirror durable progress snapshot to Firestore. Soft-fails."""
    db = get_firestore()
    if not db or not summary:
        return
    try:
        payload = {
            "user_id": int(user_id),
            "total_study_minutes": int(summary.get("total_study_minutes") or 0),
            "total_quizzes": int(summary.get("total_quizzes") or 0),
            "total_quiz_questions": int(summary.get("total_quiz_questions") or 0),
            "correct_quiz_questions": int(summary.get("correct_quiz_questions") or 0),
            "accuracy": float(summary.get("accuracy") or 0),
            "study_streak": int(summary.get("study_streak") or 0),
            "exam_readiness": float(summary.get("exam_readiness") or 0),
            "preferred_style": summary.get("preferred_style") or "Step-by-Step",
            "learning_pace": summary.get("learning_pace") or "Steady",
            "subject_count": int(summary.get("subject_count") or 0),
            "mistake_count": int(summary.get("mistake_count") or 0),
            "mistakes_mastered": int(summary.get("mistakes_mastered") or 0),
            "updated_at": summary.get("updated_at") or "",
        }
        _fs_progress_ref(db, user_id).set(payload, merge=True)
    except Exception as e:
        print(f"[Firestore] upsert progress failed: {e}")


def fs_push_progress_from_sqlite(user_id, extra=None):
    """Build + push progress summary from Learning DNA / mistakes. Soft-fails."""
    extra = extra or {}
    try:
        with get_db() as conn:
            profile = get_or_create_learning_dna(conn, user_id)
            subj_count = conn.execute(
                "SELECT COUNT(*) AS c FROM subject_analytics WHERE user_id=?",
                (user_id,),
            ).fetchone()["c"]
            mist = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END) AS mastered
                FROM student_mistakes WHERE user_id=?
                """,
                (user_id,),
            ).fetchone()
        tq = int(profile.get("total_quiz_questions") or 0)
        cq = int(profile.get("correct_quiz_questions") or 0)
        accuracy = round((cq / tq * 100.0), 1) if tq > 0 else float(extra.get("accuracy") or 0)
        summary = {
            "total_study_minutes": profile.get("total_study_minutes") or 0,
            "total_quizzes": profile.get("total_quizzes") or 0,
            "total_quiz_questions": tq,
            "correct_quiz_questions": cq,
            "accuracy": accuracy,
            "study_streak": int(extra.get("study_streak") or 0),
            "exam_readiness": float(extra.get("exam_readiness") or 0),
            "preferred_style": profile.get("preferred_style") or "Step-by-Step",
            "learning_pace": profile.get("learning_pace") or "Steady",
            "subject_count": int(subj_count or 0),
            "mistake_count": int(mist["total"] or 0) if mist else 0,
            "mistakes_mastered": int(mist["mastered"] or 0) if mist else 0,
            "updated_at": profile.get("updated_at") or "",
        }
        fs_upsert_progress_summary(user_id, summary)
    except Exception as e:
        print(f"[Firestore] push progress failed: {e}")


def hash_password(password: str) -> str:
    """SHA-256 hash of the password with a salt prefix."""
    salt = "studybuddy_salt_2025"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def nickname_from_email(email: str) -> str:
    """Derive a short username from an email local-part (last segment)."""
    local = (email or "").split("@", 1)[0].strip().lower()
    parts = [p for p in re.split(r"[._\-]+", local) if p]
    if parts:
        return parts[-1][:32]
    return (local or "user")[:32]


def allocate_unique_identifier(conn, base: str) -> str:
    """Return base, or base2/base3/... if taken."""
    candidate = (base or "user").strip().lower() or "user"
    candidate = re.sub(r"[^a-z0-9_]+", "", candidate)[:32] or "user"
    existing = conn.execute(
        "SELECT id FROM users WHERE identifier=?", (candidate,)
    ).fetchone()
    if not existing:
        return candidate
    for i in range(2, 1000):
        alt = f"{candidate}{i}"[:32]
        existing = conn.execute(
            "SELECT id FROM users WHERE identifier=?", (alt,)
        ).fetchone()
        if not existing:
            return alt
    return f"user{secrets.token_hex(4)}"


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


def save_mistake_to_vault(user_id: int, subject: str, topic: str, question: str, 
                         wrong_answer: str, correct_answer: str, explanation: str, 
                         source_type: str = "quiz"):
    """Helper function to automatically save mistakes to the vault."""
    try:
        with get_db() as conn:
            cur = conn.execute("""
                INSERT INTO student_mistakes (user_id, subject, topic, question, wrong_answer, correct_answer, explanation, source_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, subject, topic, question, wrong_answer, correct_answer, explanation, source_type))
            row = conn.execute(
                "SELECT * FROM student_mistakes WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        if row:
            fs_upsert_mistake(user_id, dict(row))
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save mistake to vault: {e}")
        return False


# =====================================================================
#  STEP 6: ROUTES
# =====================================================================

# ── Homepage ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main page."""
    return send_from_directory(_APP_DIR, "index.html")


@app.route("/career-dreamer")
def career_dreamer():
    """Redirect legacy Career Dreamer requests to home."""
    return redirect("/")


@app.route("/api/health", methods=["GET"])
def api_health():
    """Lightweight JSON health check for deployment verification."""
    return jsonify({"ok": True, "service": "study-buddy"})


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
    """Register a new user with username + password."""
    data = request.get_json(force=True)
    identifier = (data.get("identifier") or "").strip()
    password   = (data.get("password")   or "").strip()
    buddy_name = (data.get("buddyName")  or "Max").strip() or "Max"

    if not identifier or not password:
        return jsonify({"error": "Username and password are required."}), 400
    if "@" in identifier:
        return jsonify({"error": "Please use a username, not an email."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400

    ph = hash_password(password)
    try:
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM users WHERE identifier=?", (identifier,)).fetchone()
            if existing:
                return jsonify({"error": "Username is taken"}), 400

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
    """Log in with username + password."""
    data = request.get_json(force=True)
    identifier = (data.get("identifier") or "").strip()
    password   = (data.get("password")   or "").strip()

    if not identifier or not password:
        return jsonify({"error": "Incorrect username or password"}), 400

    ph = hash_password(password)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE identifier=? AND password_hash=?",
            (identifier, ph)
        ).fetchone()

    if not row:
        return jsonify({"error": "Incorrect username or password"}), 401

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


@app.route("/api/config/firebase", methods=["GET"])
def firebase_config():
    """Public Firebase web config for the browser SDK (no service account secrets)."""
    cfg = get_firebase_web_config()
    if not cfg:
        return jsonify({"enabled": False})
    return jsonify({"enabled": True, **cfg})


@app.route("/api/auth/firebase", methods=["POST"])
def auth_firebase():
    """Verify a Firebase ID token and create/link a local SQLite session user."""
    if not init_firebase_admin():
        return jsonify({"error": "Firebase auth is not configured on the server."}), 503

    from firebase_admin import auth as fb_auth

    data = request.get_json(force=True) or {}
    id_token = (data.get("idToken") or "").strip()
    if not id_token:
        return jsonify({"error": "idToken is required."}), 400

    try:
        decoded = fb_auth.verify_id_token(id_token)
    except Exception:
        return jsonify({"error": "Invalid or expired Firebase token."}), 401

    firebase_uid = decoded.get("uid")
    if not firebase_uid:
        return jsonify({"error": "Token missing uid."}), 401

    email = (decoded.get("email") or "").strip()
    base_nick = nickname_from_email(email) if email else f"google{firebase_uid[:6]}"

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE firebase_uid=?", (firebase_uid,)
            ).fetchone()

            if not row and email:
                existing = conn.execute(
                    "SELECT * FROM users WHERE identifier=?", (email,)
                ).fetchone()
                if existing:
                    nick = allocate_unique_identifier(conn, base_nick)
                    conn.execute(
                        "UPDATE users SET firebase_uid=?, identifier=? WHERE id=?",
                        (firebase_uid, nick, existing["id"]),
                    )
                    row = conn.execute(
                        "SELECT * FROM users WHERE id=?", (existing["id"],)
                    ).fetchone()

            if not row:
                nick = allocate_unique_identifier(conn, base_nick)
                unusable = "firebase_only:" + secrets.token_hex(32)
                conn.execute(
                    "INSERT INTO users (identifier, password_hash, buddy_name, firebase_uid) VALUES (?,?,?,?)",
                    (nick, unusable, "Max", firebase_uid),
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE firebase_uid=?", (firebase_uid,)
                ).fetchone()
            else:
                current_id = row["identifier"] or ""
                if "@" in current_id:
                    nick = allocate_unique_identifier(conn, base_nick)
                    conn.execute(
                        "UPDATE users SET identifier=? WHERE id=?",
                        (nick, row["id"]),
                    )
                    row = conn.execute(
                        "SELECT * FROM users WHERE id=?", (row["id"],)
                    ).fetchone()

        session.permanent = True
        session["user_id"] = row["id"]
        return jsonify({
            "identifier": row["identifier"],
            "buddyName": row["buddy_name"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Conversation Routes ───────────────────────────────────────────────

@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    """List all conversations for the logged-in user, newest first."""
    user, err = require_auth()
    if err:
        return err

    fs_pull_conversations_into_sqlite(user["id"])
    fs_push_all_conversations(user["id"])

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

    conv = dict(row)
    fs_upsert_conversation(user["id"], conv)
    return jsonify(conv), 201


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

    conv = dict(updated)
    fs_upsert_conversation(user["id"], conv)
    return jsonify(conv)


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

    fs_delete_conversation(user["id"], conv_id)
    return jsonify({"ok": True})


@app.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
def get_messages(conv_id):
    """Return all messages for a conversation."""
    user, err = require_auth()
    if err:
        return err

    # Ensure remote history is available before reading messages
    fs_pull_conversations_into_sqlite(user["id"])

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

    if role in ("assistant", "ai"):
        role = "assistant"

    if role not in ("user", "assistant") or not content:
        return jsonify({"error": "Invalid role or empty content."}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user["id"])
        ).fetchone()
        if not row:
            return jsonify({"error": "Conversation not found."}), 404

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
            (conv_id, role, content)
        )
        msg_id = cur.lastrowid
        conn.execute(
            "UPDATE conversations SET updated_at=datetime('now') WHERE id=?",
            (conv_id,)
        )
        msg_row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        conv_row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()

    if conv_row:
        fs_upsert_conversation(user["id"], dict(conv_row))
    if msg_row:
        fs_upsert_message(user["id"], conv_id, dict(msg_row))
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
@app.route("/api/definitions", methods=["POST"])
def chat():
    """
    Handle chat, podcast generation, flashcard generation, quiz generation, crosscheck generation, and definitions extraction.
    For /api/chat: also persists messages to SQLite (auto-creates conversation on first message).
    """
    data = request.get_json(force=True)

    endpoint   = request.path.split("/")[-1]
    messages   = data.get("messages", [])
    model_name = data.get("model", "llama-3.3-70b-versatile")
    notes      = data.get("notes", "")
    conv_id    = data.get("conversation_id")   # may be None (first message)
    system_prompt = SYSTEM_PROMPT

    # Clean messages (support both 'assistant' and 'ai' roles)
    messages = [
        msg for msg in messages
        if isinstance(msg, dict)
        and msg.get("role") in {"user", "assistant", "ai"}
        and isinstance(msg.get("content"), str)
        and msg["content"].strip()
    ]

# Feature routing is now handled in frontend JavaScript

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
            "You write a student-friendly educational podcast with TWO named hosts.\n"
            "Alex = energetic lead teacher who explains clearly.\n"
            "Maya = curious co-host who asks the questions a confused student would ask.\n\n"
            "FORMAT (strict — every line MUST start with Alex: or Maya: — never Host A/B):\n"
            "Alex: [tag] spoken line\n"
            "Maya: [tag] spoken line\n"
            "Do NOT put the tag before the name. Wrong: [cheerful] Alex: hello\n"
            "Correct: Alex: [cheerful] hello\n\n"
            "LENGTH:\n"
            "- Exactly 10 dialogue turns total.\n"
            "- About 280 words in the entire script (~2 minutes spoken).\n"
            "- Each line can be 1–2 clear sentences.\n\n"
            "TEACHING ARC (cover all of these):\n"
            "1) Quick hook + say what today's topic is.\n"
            "2) Clear definition of the core idea in plain language.\n"
            "3) Step-by-step explanation with one concrete everyday example.\n"
            "4) Common mistake / 'don't confuse this with…'.\n"
            "5) Short recap students can remember.\n"
            "Maya asks clarifying questions; Alex answers with detail and examples.\n\n"
            "VOCAL TAGS (at start of spoken text):\n"
            "Use one of: [cheerful] [excited] [curious] [surprised] [thoughtful] "
            "[encouraging] [sympathetic] [confident] [laugh]\n"
            "Vary tags. English only. No markdown, bullets, or stage directions outside [tags].\n"
            "Return ONLY the Alex / Maya script."
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
    elif endpoint == "definitions":
        system_prompt = (
            f"{system_prompt}\n\n"
            "Using the full conversation history from the entire chat session and any provided study notes, "
            "extract key terms, concepts, or vocabulary words along with their clear, concise definitions.\n"
            "Format each definition on a new line EXACTLY as:\n"
            "1. [Term]: [Definition]\n"
            "2. [Term]: [Definition]\n"
            "Extract 3 to 10 terms if available.\n"
            "If no clear key terms or definitions are discussed or found, reply ONLY with the exact string: NO_DEFINITIONS"
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
        # Podcast: fast model + enough tokens for named teaching scripts
        if endpoint == "podcast":
            target_model = "llama-3.1-8b-instant"
            completion_kwargs = {"max_tokens": 750}
        else:
            target_model = resolve_groq_model(model_name)
            completion_kwargs = {}

        groq_messages = []
        if system_prompt:
            groq_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = "assistant" if msg["role"] in ("assistant", "ai") else "user"
            groq_messages.append({
                "role": role,
                "content": msg["content"]
            })

        response = client.chat.completions.create(
            model=target_model,
            messages=groq_messages,
            **completion_kwargs,
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

                # Mirror chat persistence to Firestore
                if endpoint == "chat":
                    uid_fs = current_user_id()
                    if uid_fs and conv_id:
                        try:
                            with get_db() as conn_fs:
                                conv_full = conn_fs.execute(
                                    "SELECT * FROM conversations WHERE id=? AND user_id=?",
                                    (conv_id, uid_fs),
                                ).fetchone()
                                recent_msgs = conn_fs.execute(
                                    """
                                    SELECT * FROM messages
                                    WHERE conversation_id=?
                                    ORDER BY id DESC LIMIT 4
                                    """,
                                    (conv_id,),
                                ).fetchall()
                            if conv_full:
                                fs_upsert_conversation(uid_fs, dict(conv_full))
                            for msg in reversed(list(recent_msgs or [])):
                                fs_upsert_message(uid_fs, conv_id, dict(msg))
                        except Exception as e:
                            print(f"[Firestore] chat persist mirror failed: {e}")

        # Podcast script only — TTS is a separate /api/podcast/tts call (avoids proxy timeouts)
        return jsonify({"reply": reply, "conversation_id": conv_id})

    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Groq API: {error_msg}")
        return jsonify({"error": error_msg}), 500


@app.route("/api/podcast/tts", methods=["POST"])
def podcast_tts():
    """Synthesize Alex/Maya podcast audio from an existing script (separate from LLM)."""
    data = request.get_json(force=True) or {}
    script = (data.get("script") or data.get("reply") or "").strip()
    if not script:
        return jsonify({"error": "No podcast script provided."}), 400
    host_a = (data.get("host_a_voice") or data.get("voice_a") or "").strip() or None
    host_b = (data.get("host_b_voice") or data.get("voice_b") or "").strip() or None
    preset_id = (data.get("voice_preset") or "").strip()
    if preset_id and (not host_a or not host_b):
        for p in PODCAST_VOICE_PRESETS:
            if p["id"] == preset_id:
                host_a = host_a or p["host_a"]
                host_b = host_b or p["host_b"]
                break
    try:
        audio_payload = synthesize_podcast_audio(script, host_a, host_b)
        return jsonify({
            "audio_base64": audio_payload["audio_base64"],
            "audio_mime": audio_payload["audio_mime"],
            "tts_engine": audio_payload["engine"],
            "tts_turns": audio_payload["turns"],
            "host_a_voice": audio_payload.get("host_a_voice"),
            "host_b_voice": audio_payload.get("host_b_voice"),
        })
    except Exception as tts_err:
        print(f"[ERROR] Podcast TTS: {tts_err}")
        return jsonify({"error": str(tts_err), "tts_error": str(tts_err)}), 500


@app.route("/api/podcast/voices", methods=["GET"])
def podcast_voices():
    """List available two-host podcast voice presets."""
    return jsonify({"presets": PODCAST_VOICE_PRESETS})


def _guess_image_mime(filename: str, fallback: str = "image/jpeg") -> str:
    name = (filename or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    return fallback or "image/jpeg"


def _ocr_with_groq_vision(image_bytes: bytes, mime: str, extra_prompt: str = "") -> str:
    """Extract study text from an image using Groq vision models."""
    client = get_groq_client()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    prompt = (
        "You are an OCR engine for students. Extract ALL readable text from this study material image. "
        "Preserve structure: headings, bullet points, numbered lists, equations, and labels. "
        "Return plain text only — no commentary, no markdown fences."
    )
    if extra_prompt:
        prompt += f"\n\nExtra instruction: {extra_prompt.strip()}"

    last_err = None
    for model in GROQ_VISION_FALLBACKS:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            text = (completion.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as e:
            last_err = e
            print(f"[OCR] vision model {model} failed: {e}")
            continue
    raise RuntimeError(str(last_err) if last_err else "OCR failed with all vision models.")


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    """OCR: upload image (or base64) → extracted study text."""
    image_bytes = None
    mime = "image/jpeg"
    filename = "upload.jpg"
    extra = ""

    if request.files.get("file"):
        f = request.files["file"]
        filename = f.filename or filename
        mime = (f.mimetype or _guess_image_mime(filename)).split(";")[0].strip()
        image_bytes = f.read()
        extra = (request.form.get("prompt") or "").strip()
    else:
        data = request.get_json(silent=True) or {}
        b64 = (data.get("image_base64") or data.get("image") or "").strip()
        if "," in b64 and b64.startswith("data:"):
            header, b64 = b64.split(",", 1)
            if "image/" in header:
                mime = header.split(";")[0].replace("data:", "")
        if b64:
            try:
                image_bytes = base64.b64decode(b64)
            except Exception:
                return jsonify({"error": "Invalid base64 image data."}), 400
        mime = (data.get("mime") or mime).strip() or mime
        filename = data.get("filename") or filename
        extra = (data.get("prompt") or "").strip()

    if not image_bytes:
        return jsonify({"error": "No image provided. Upload a photo of your notes/textbook page."}), 400

    max_bytes = 8 * 1024 * 1024
    if len(image_bytes) > max_bytes:
        return jsonify({"error": "Image too large. Maximum size is 8MB."}), 400

    if not mime.startswith("image/"):
        return jsonify({"error": "Only image files are supported for OCR (jpg, png, webp)."}), 400

    try:
        text = _ocr_with_groq_vision(image_bytes, mime, extra)
        if not text:
            return jsonify({"error": "No text could be extracted from this image."}), 422
        return jsonify({
            "text": text,
            "filename": filename,
            "chars": len(text),
        })
    except Exception as e:
        print(f"[ERROR] OCR: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/stt", methods=["POST"])
def api_stt():
    """Speech-to-text via Groq Whisper (audio upload from mic)."""
    if not request.files.get("file") and not request.files.get("audio"):
        data = request.get_json(silent=True) or {}
        b64 = (data.get("audio_base64") or "").strip()
        if not b64:
            return jsonify({"error": "No audio provided."}), 400
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            audio_bytes = base64.b64decode(b64)
        except Exception:
            return jsonify({"error": "Invalid audio base64."}), 400
        mime = (data.get("mime") or "audio/webm").strip()
        ext = "webm" if "webm" in mime else "wav" if "wav" in mime else "mp3"
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"sb_stt_{os.getpid()}.{ext}")
        with open(path, "wb") as out:
            out.write(audio_bytes)
        try:
            client = get_groq_client()
            with open(path, "rb") as af:
                tr = client.audio.transcriptions.create(
                    file=(f"speech.{ext}", af.read()),
                    model="whisper-large-v3",
                    language=data.get("language") or "en",
                )
            text = (getattr(tr, "text", None) or str(tr) or "").strip()
            return jsonify({"text": text})
        except Exception as e:
            print(f"[ERROR] STT: {e}")
            return jsonify({"error": str(e)}), 500
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    f = request.files.get("file") or request.files.get("audio")
    try:
        client = get_groq_client()
        raw = f.read()
        name = f.filename or "speech.webm"
        tr = client.audio.transcriptions.create(
            file=(name, raw),
            model="whisper-large-v3",
            language=request.form.get("language") or "en",
        )
        text = (getattr(tr, "text", None) or str(tr) or "").strip()
        return jsonify({"text": text})
    except Exception as e:
        print(f"[ERROR] STT: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """Single-voice TTS for buddy voice replies (edge-tts)."""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "No text provided."}), 400
    # Keep replies short for latency
    text = text[:1200]
    voice = (data.get("voice") or "en-US-JennyNeural").strip() or "en-US-JennyNeural"
    rate = (data.get("rate") or "+0%").strip() or "+0%"
    pitch = (data.get("pitch") or "+0Hz").strip() or "+0Hz"
    try:
        audio = _tts_edge_utterance(text, voice, rate, pitch)
        mime = "audio/mpeg" if not audio.startswith(b"RIFF") else "audio/wav"
        return jsonify({
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "audio_mime": mime,
            "voice": voice,
        })
    except Exception as e:
        print(f"[ERROR] TTS: {e}")
        return jsonify({"error": str(e)}), 500


def _parse_mock_test_json(raw: str) -> dict:
    """Extract mock-test JSON object from model output."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object found in model output")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError("JSON must include a questions array")
    return data


@app.route("/api/mock-test", methods=["POST"])
def api_mock_test():
    """Generate a structured takeable mock test (JSON) via Groq."""
    data = request.get_json(force=True) or {}
    subject = (data.get("subject") or "Physics").strip()[:80]
    exam = (data.get("exam") or "Board exam").strip()[:80]
    grade = (data.get("grade") or "Class 10").strip()[:40]
    chapters = (data.get("chapters") or data.get("topics") or "").strip()[:400]
    difficulty = (data.get("difficulty") or "Medium").strip()[:20]
    size = (data.get("size") or "quick").strip().lower()
    if size not in ("quick", "standard"):
        size = "quick"

    if size == "standard":
        count_line = "Exactly 20 questions: 16 type=mcq and 4 type=short."
        duration = 45
        total_marks = 24  # 16*1 + 4*2
    else:
        count_line = "Exactly 10 questions: all type=mcq."
        duration = 20
        total_marks = 10

    chapter_line = f"Focus chapters/topics: {chapters}." if chapters else "Cover a representative syllabus mix."
    prompt = (
        f"Create a {difficulty} mock test for {grade} {subject} ({exam}). {chapter_line}\n"
        f"{count_line}\n"
        f"Suggested duration_minutes={duration}, total_marks={total_marks}.\n\n"
        "Return ONLY valid JSON (no markdown) with this shape:\n"
        "{\n"
        '  "title": string,\n'
        '  "total_marks": number,\n'
        '  "duration_minutes": number,\n'
        '  "questions": [\n'
        "    {\n"
        '      "id": "q1",\n'
        '      "type": "mcq" | "short",\n'
        '      "question": string,\n'
        '      "options": ["A","B","C","D"],   // required for mcq, omit or [] for short\n'
        '      "answer_index": 0,              // 0-3 for mcq\n'
        '      "answer": "model answer",       // required for short\n'
        '      "marks": number,\n'
        '      "explanation": string\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules: MCQ must have exactly 4 options and a valid answer_index. "
        "Short questions need a clear model answer string. "
        "Explanations must help a student learn. Age-appropriate school level."
    )

    try:
        client = get_groq_client()
        completion = client.chat.completions.create(
            model=resolve_groq_model(data.get("model")),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate school mock tests as strict JSON only. "
                        "No markdown fences, no commentary before or after the JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=4500,
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            return jsonify({"error": "Empty mock test returned."}), 500
        try:
            payload = _parse_mock_test_json(raw)
        except Exception as pe:
            print(f"[ERROR] Mock test JSON parse: {pe}\nRaw: {raw[:400]}")
            return jsonify({"error": "Could not parse mock test JSON. Please try again."}), 500

        questions = []
        for i, q in enumerate(payload.get("questions") or []):
            if not isinstance(q, dict):
                continue
            qtype = (q.get("type") or "mcq").strip().lower()
            if qtype not in ("mcq", "short"):
                qtype = "mcq"
            item = {
                "id": str(q.get("id") or f"q{i + 1}")[:40],
                "type": qtype,
                "question": str(q.get("question") or "").strip()[:800],
                "marks": int(q.get("marks") or (2 if qtype == "short" else 1)),
                "explanation": str(q.get("explanation") or "").strip()[:600],
            }
            if not item["question"]:
                continue
            if qtype == "mcq":
                opts = q.get("options") or []
                if not isinstance(opts, list):
                    opts = []
                opts = [str(o).strip()[:200] for o in opts][:4]
                while len(opts) < 4:
                    opts.append(f"Option {len(opts) + 1}")
                try:
                    ans_i = int(q.get("answer_index"))
                except Exception:
                    ans_i = 0
                if ans_i < 0 or ans_i > 3:
                    ans_i = 0
                item["options"] = opts
                item["answer_index"] = ans_i
            else:
                item["answer"] = str(q.get("answer") or q.get("model_answer") or "").strip()[:600]
                if not item["answer"]:
                    item["answer"] = item["explanation"] or "(See explanation)"
            questions.append(item)

        if size == "quick":
            questions = [q for q in questions if q["type"] == "mcq"][:10]
        else:
            mcqs = [q for q in questions if q["type"] == "mcq"][:16]
            shorts = [q for q in questions if q["type"] == "short"][:4]
            questions = mcqs + shorts

        if len(questions) < 5:
            return jsonify({"error": "Mock test too short — try generating again."}), 500

        title = str(payload.get("title") or f"{subject} Mock Test").strip()[:120]
        return jsonify({
            "title": title,
            "subject": subject,
            "exam": exam,
            "grade": grade,
            "difficulty": difficulty,
            "size": size,
            "total_marks": int(payload.get("total_marks") or sum(q["marks"] for q in questions)),
            "duration_minutes": int(payload.get("duration_minutes") or duration),
            "questions": questions,
        })
    except Exception as e:
        print(f"[ERROR] Mock test: {e}")
        return jsonify({"error": str(e)}), 500


def _normalize_diagram_topic(topic: str) -> str:
    """water-cycle / water_cycle → water cycle; fix common typos."""
    t = (topic or "").strip()
    t = re.sub(r"[-_]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Common student typos
    fixes = (
        (r"\bstucture\b", "structure"),
        (r"\batommic\b", "atomic"),
        (r"\bphotosyntesis\b", "photosynthesis"),
        (r"\bphotosythesis\b", "photosynthesis"),
    )
    for pat, repl in fixes:
        t = re.sub(pat, repl, t, flags=re.I)
    return t[:300]


_DIAGRAM_NEGATIVES = (
    "NOT abstract art, NOT blurry, NOT out of focus, NOT dreamlike, NOT crystalline flower, "
    "NOT surreal, NOT photo of planet Earth, NOT globe, NOT world map, NOT space nebula, "
    "NOT gray box flowchart, NOT childish cartoon clip-art, NOT watermark, NOT UI chrome"
)


def _diagram_topic_template(topic: str):
    """Curated prompts for common school topics (beats vague Groq rewrites)."""
    t = _normalize_diagram_topic(topic).lower()

    is_atomic = (
        t in (
            "atom",
            "atoms",
            "atomic structure",
            "bohr model",
            "structure of atom",
            "structure of an atom",
            "structure of the atom",
        )
        or ("atomic" in t and "structure" in t)
        or ("bohr" in t and "model" in t)
        or (re.search(r"\batoms?\b", t) and "structure" in t)
    )
    if is_atomic:
        return (
            "sharp clear Bohr model diagram of an atom for ICSE school science textbook, "
            "white background, black outlines, central nucleus circle labeled Nucleus with "
            "protons (+) and neutrons (n) inside, three concentric electron orbits labeled "
            "K shell, L shell, M shell, small electron dots on the orbits labeled e-, "
            "title Atomic Structure at top, readable sans-serif text labels, "
            "flat educational vector illustration style, high detail, crisp lines, "
            f"{_DIAGRAM_NEGATIVES}"
        )

    if "water cycle" in t or "hydrologic" in t:
        return (
            "sharp educational textbook illustration of the water cycle, white background, "
            "landscape with sun, ocean, land, trees, clouds and rain, curved arrows labeled "
            "Evaporation, Condensation, Precipitation, Collection, readable labels, "
            "muted academic colors, ICSE science book figure, crisp lines, "
            f"{_DIAGRAM_NEGATIVES}"
        )

    if "photosynthesis" in t:
        return (
            "sharp labeled textbook diagram of photosynthesis in a green leaf cross-section, "
            "sunlight, CO2, H2O arrows in, O2 and glucose arrows out, chloroplast labeled, "
            "white background, ICSE science figure, crisp readable labels, "
            f"{_DIAGRAM_NEGATIVES}"
        )

    if "neuron" in t or "nerve cell" in t:
        return (
            "sharp labeled textbook diagram of a neuron / nerve cell, dendrites, cell body, "
            "axon, myelin sheath, axon terminals, white background, clear labels with lines, "
            "ICSE biology figure, crisp lines, "
            f"{_DIAGRAM_NEGATIVES}"
        )

    if "electrolysis" in t:
        return (
            "sharp labeled textbook diagram of electrolysis of water, beaker, electrodes anode "
            "cathode, battery, bubbles of hydrogen and oxygen labeled, white background, "
            "ICSE chemistry figure, crisp lines, "
            f"{_DIAGRAM_NEGATIVES}"
        )

    return None


def _diagram_prompt_image(topic: str, style: str = "") -> str:
    """Strict handcrafted prompt for real AI image generators."""
    topic = _normalize_diagram_topic(topic)
    templated = _diagram_topic_template(topic)
    if templated:
        return templated

    style_bit = (style or "clean educational textbook illustration").strip()
    return (
        f"sharp clear educational science textbook diagram of {topic}, {style_bit}, "
        f"labeled schematic with readable text labels and leader lines, white background, "
        "flat vector textbook figure for ICSE students, crisp sharp lines, high detail, "
        f"{_DIAGRAM_NEGATIVES}"
    )


def _groq_rewrite_diagram_prompt(topic: str, style: str = "") -> str:
    """Build image prompt: curated template first, else Groq, else generic."""
    topic = _normalize_diagram_topic(topic)
    templated = _diagram_topic_template(topic)
    if templated:
        return templated[:700]

    if not GROQ_API_KEY:
        return _diagram_prompt_image(topic, style)
    try:
        client = get_groq_client()
        completion = client.chat.completions.create(
            model=DEFAULT_GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write ONE English text-to-image prompt for a sharp school textbook diagram. "
                        "Return only the prompt line, no quotes or markdown. "
                        "Describe concrete labeled parts (nucleus, shells, arrows, organs, etc.), "
                        "white background, crisp lines, readable labels. "
                        "Never describe abstract art, blur, flowers, crystals, or dreamlike imagery."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Topic: {topic}\nStyle: {(style or 'clean educational textbook illustration').strip()}\n"
                        "Write the prompt now."
                    ),
                },
            ],
            temperature=0.15,
            max_tokens=260,
        )
        line = (completion.choices[0].message.content or "").strip()
        line = re.sub(r'^["`\']+|["`\']+$', "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 20:
            return _diagram_prompt_image(topic, style)
        line += f", {_DIAGRAM_NEGATIVES}"
        return line[:700]
    except Exception as e:
        print(f"[Diagram] Groq prompt rewrite failed: {e}")
        return _diagram_prompt_image(topic, style)


def generate_diagram_hf_flux(topic: str, style: str = "") -> dict:
    """
    Real AI image via Hugging Face Inference (FLUX.1-schnell).
    Requires HF_TOKEN. Returns { image_base64, mime, model, engine }.
    """
    import json
    import time
    import urllib.error
    import urllib.request

    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN is not set")

    topic = _normalize_diagram_topic(topic)
    prompt = _groq_rewrite_diagram_prompt(topic, style)
    print(f"[Diagram] HF prompt ({len(prompt)} chars): {prompt[:160]}…")
    # Schnell is fast; a few more steps + stronger guidance reduces abstract mush
    payload = json.dumps({
        "inputs": prompt,
        "parameters": {
            "guidance_scale": 7.5,
            "num_inference_steps": 10,
        },
    }).encode("utf-8")
    endpoints = [
        f"https://router.huggingface.co/hf-inference/models/{HF_FLUX_MODEL}",
        f"https://api-inference.huggingface.co/models/{HF_FLUX_MODEL}",
    ]
    last_err = None
    for attempt in range(3):
        for url in endpoints:
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {HF_TOKEN}",
                        "Content-Type": "application/json",
                        "Accept": "image/png",
                        "User-Agent": "StudyBuddy/1.0 (educational diagrams)",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = resp.read()
                    ctype = (resp.headers.get("Content-Type") or "image/png").split(";")[0].strip()
                if data[:1] == b"{":
                    try:
                        err_obj = json.loads(data.decode("utf-8", errors="replace"))
                    except Exception:
                        err_obj = {}
                    msg = str(err_obj.get("error") or err_obj.get("message") or data[:200])
                    if "loading" in msg.lower() or err_obj.get("estimated_time"):
                        wait = min(float(err_obj.get("estimated_time") or 15), 40)
                        print(f"[Diagram] HF model loading, wait {wait}s…")
                        time.sleep(wait)
                        last_err = RuntimeError(msg)
                        continue
                    raise RuntimeError(msg)
                if not data or len(data) < 500:
                    raise RuntimeError("Empty or tiny image from Hugging Face")
                if not ctype.startswith("image/"):
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        ctype = "image/png"
                    elif data[:2] == b"\xff\xd8":
                        ctype = "image/jpeg"
                    else:
                        raise RuntimeError(f"Non-image response from HF ({ctype})")
                return {
                    "image_base64": base64.b64encode(data).decode("ascii"),
                    "mime": ctype,
                    "model": HF_FLUX_MODEL,
                    "engine": "hf-flux",
                }
            except urllib.error.HTTPError as e:
                body = e.read()[:300] if hasattr(e, "read") else b""
                last_err = RuntimeError(f"HF HTTP {e.code}: {body.decode('utf-8', errors='replace')}")
                print(f"[Diagram] HF failed: {last_err}")
                if e.code == 503:
                    time.sleep(12)
                    continue
            except Exception as e:
                last_err = e
                print(f"[Diagram] HF failed: {e}")
                continue
        time.sleep(2)
    raise RuntimeError(str(last_err) if last_err else "Hugging Face image generation failed")


def generate_diagram_pollinations(topic: str, style: str = "") -> dict:
    """
    Free real-image diagram via Pollinations (no API key / no billing).
    Returns { image_base64, mime, model, engine }.
    """
    import urllib.parse
    import urllib.request

    topic = _normalize_diagram_topic(topic)
    prompt = _groq_rewrite_diagram_prompt(topic, style)
    encoded = urllib.parse.quote(prompt, safe="")
    seed = abs(hash(topic)) % 999999
    urls = [
        (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1024&height=1024&nologo=true&model=flux&seed={seed}"
        ),
        (
            f"https://gen.pollinations.ai/image/{encoded}"
            f"?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
        ),
    ]
    last_err = None
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "StudyBuddy/1.0 (educational diagrams)",
                    "Accept": "image/*,*/*",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
                ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if not data or len(data) < 500:
                raise RuntimeError("Empty or tiny image response")
            if not ctype.startswith("image/"):
                if data[:20].lstrip().startswith(b"<") or data[:1] == b"{":
                    raise RuntimeError("Non-image response from Pollinations")
                ctype = "image/jpeg"
            return {
                "image_base64": base64.b64encode(data).decode("ascii"),
                "mime": ctype,
                "model": "pollinations-flux",
                "engine": "pollinations",
            }
        except Exception as e:
            last_err = e
            print(f"[Diagram] Pollinations failed: {e}")
            continue
    raise RuntimeError(str(last_err) if last_err else "Pollinations image generation failed")


def _bytes_to_b64(data) -> str:
    """Normalize Gemini image payload (bytes or base64 str) to base64 ascii."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, (bytes, bytearray)):
        return base64.b64encode(bytes(data)).decode("ascii")
    return base64.b64encode(bytes(data)).decode("ascii")


def _extract_image_from_gemini_response(response):
    """Return (mime, base64_str) from a generate_content response, or (None, None)."""
    try:
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    mime = getattr(inline, "mime_type", None) or "image/png"
                    return mime, _bytes_to_b64(inline.data)
    except Exception as e:
        print(f"[Diagram] parse generate_content failed: {e}")
    return None, None


def generate_diagram_gemini(topic: str, style: str = "") -> dict:
    """
    Textbook-quality diagram via Gemini image model (Nano Banana / flash-image).
    Requires billed GEMINI_API_KEY. Returns { image_base64, mime, model, engine }.
    """
    topic = _normalize_diagram_topic(topic)
    client = get_gemini_client()
    model = GEMINI_IMAGE_MODEL
    detail = _groq_rewrite_diagram_prompt(topic, style)
    prompt = (
        f"Create ONE textbook-quality educational diagram image for ICSE/school students.\n"
        f"Topic: {topic}\n"
        f"Visual brief: {detail}\n"
        f"Style: {(style or 'clean educational textbook illustration').strip()}.\n"
        "Hard requirements:\n"
        "- Accurate labeled scientific diagram (e.g. Bohr atomic structure with nucleus and shells)\n"
        "- White or light paper background, high contrast, sharp readable labels on every key part\n"
        "- Title at top; neat school science book figure — not abstract art, not blurry, not surreal\n"
        "- No cartoon mascots, watermarks, or UI chrome\n"
        "- Output an IMAGE only (not SVG, not code, not text description)"
    )

    # Preferred: Interactions API
    try:
        interaction = client.interactions.create(model=model, input=prompt)
        out_img = getattr(interaction, "output_image", None)
        if out_img is not None:
            data = getattr(out_img, "data", None)
            mime = getattr(out_img, "mime_type", None) or "image/png"
            if data:
                return {
                    "image_base64": _bytes_to_b64(data),
                    "mime": mime,
                    "model": model,
                    "engine": "gemini",
                }
        outputs = getattr(interaction, "outputs", None) or getattr(interaction, "output", None) or []
        if not isinstance(outputs, (list, tuple)):
            outputs = [outputs]
        for item in outputs:
            if item is None:
                continue
            data = getattr(item, "data", None)
            mime = getattr(item, "mime_type", None) or "image/png"
            typ = getattr(item, "type", None) or ""
            if data and ("image" in str(typ).lower() or str(mime).startswith("image/")):
                return {
                    "image_base64": _bytes_to_b64(data),
                    "mime": mime if str(mime).startswith("image/") else "image/png",
                    "model": model,
                    "engine": "gemini",
                }
    except Exception as e:
        print(f"[Diagram] interactions.create failed ({model}): {e}")

    # Fallback: generate_content with IMAGE modality
    try:
        from google.genai import types
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )
        mime, b64 = _extract_image_from_gemini_response(response)
        if b64:
            return {
                "image_base64": b64,
                "mime": mime or "image/png",
                "model": model,
                "engine": "gemini",
            }
    except Exception as e:
        print(f"[Diagram] generate_content failed ({model}): {e}")
        raise

    raise RuntimeError(
        "Gemini returned no image. Enable billing on Google AI Studio / Cloud for "
        f"model {model}, and confirm GEMINI_API_KEY is correct."
    )


@app.route("/api/diagram", methods=["POST"])
def api_diagram():
    """Educational diagram: Gemini Nano Banana (paid) primary — textbook images."""
    data = request.get_json(force=True) or {}
    topic = _normalize_diagram_topic(data.get("topic") or data.get("prompt") or "")
    if not topic:
        return jsonify({"error": "Provide a topic for the diagram."}), 400
    style = (data.get("style") or "clean educational textbook illustration").strip()[:80]

    if not GEMINI_API_KEY:
        return jsonify({
            "error": "Diagrams need GEMINI_API_KEY (Google AI Studio) with billing enabled.",
            "hint": (
                "1) https://aistudio.google.com/apikey — create key. "
                "2) Enable billing on the Google Cloud project linked to that key "
                "(free tier image quota is often 0). "
                "3) Set GEMINI_API_KEY on Render and redeploy."
            ),
        }), 503

    try:
        result = generate_diagram_gemini(topic, style)
        return jsonify({
            "image_base64": result["image_base64"],
            "mime": result["mime"],
            "model": result.get("model"),
            "engine": result.get("engine") or "gemini",
            "topic": topic,
        })
    except Exception as e:
        err = str(e)
        print(f"[ERROR] Diagram Gemini: {err}")
        hint = "Check GEMINI_API_KEY and that billing is enabled for image generation."
        low = err.lower()
        if "resource_exhausted" in low or "limit: 0" in low or "quota" in low:
            hint = (
                "Image quota is 0 on the free tier. Enable billing for your Google Cloud "
                "project (AI Studio → linked project → Billing), then retry."
            )
        return jsonify({
            "error": "Could not generate diagram with Gemini.",
            "detail": err[:300],
            "hint": hint,
        }), 500


@app.route("/api/formulas", methods=["POST"])
def api_formulas():
    """Generate a formula sheet for a topic/subject."""
    data = request.get_json(force=True) or {}
    topic = (data.get("topic") or data.get("subject") or "").strip()
    if not topic:
        return jsonify({"error": "Provide a topic or subject."}), 400
    topic = topic[:200]
    level = (data.get("level") or "high school").strip()[:60]

    prompt = (
        f"Create a concise formula sheet for: {topic} (level: {level}).\n"
        "Format as plain text with sections:\n"
        "- Title\n"
        "- Core formulas (one per line: Name — Formula — Variables explained briefly)\n"
        "- Useful identities / conversions\n"
        "- Common pitfalls (2-4 bullets)\n"
        "Use ASCII/Unicode math (e.g. F = ma, Δx, √, π). No markdown code fences."
    )

    try:
        client = get_groq_client()
        completion = client.chat.completions.create(
            model=resolve_groq_model(data.get("model")),
            messages=[
                {"role": "system", "content": "You write clear student formula sheets. Accurate and compact."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2500,
        )
        sheet = (completion.choices[0].message.content or "").strip()
        if not sheet:
            return jsonify({"error": "Empty formula sheet."}), 500
        return jsonify({"formulas": sheet, "topic": topic, "level": level})
    except Exception as e:
        print(f"[ERROR] Formulas: {e}")
        return jsonify({"error": str(e)}), 500


# Feature routing is now handled in frontend JavaScript


# ── Living Notebook Routes ───────────────────────────────────────────

VALID_NOTEBOOK_CATEGORIES = {'Key Points', 'Formulas', 'Definitions', 'Mistakes I Made', 'Things to Revise', 'My Own Notes'}


@app.route("/api/notebook", methods=["GET"])
def get_notebook_entries():
    """Retrieve all notebook entries for the logged-in user."""
    user, err = require_auth()
    if err:
        return err

    # Pull remote notes into SQLite, then mirror local notes up (soft-fail)
    fs_pull_notebook_into_sqlite(user["id"])
    fs_push_all_notebook_entries(user["id"])

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
        rows = conn.execute(
            "SELECT * FROM living_notebook WHERE user_id=?",
            (user["id"],),
        ).fetchall()

    for row in rows:
        fs_upsert_notebook_entry(user["id"], dict(row))

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

    entry = dict(row)
    fs_upsert_notebook_entry(user["id"], entry)
    return jsonify(entry), 201


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

    entry = dict(updated)
    fs_upsert_notebook_entry(user["id"], entry)
    return jsonify(entry)


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

    fs_delete_notebook_entry(user["id"], entry_id)
    return jsonify({"ok": True})


@app.route("/api/notebook/ai_extract", methods=["POST"])
def notebook_ai_extract():
    """Extract important points and merge with existing notes for each subject/topic."""
    import json as _json
    import re as _re

    data = request.get_json(force=True) or {}
    text_to_extract = (data.get("text") or "").strip()
    model_name = data.get("model", "llama-3.3-70b-versatile")
    default_subject = (data.get("subject") or "General").strip()

    if not text_to_extract:
        return jsonify({"error": "No content provided to extract notes from."}), 400

    system_prompt = (
        "You are an ICSE Class 9-10 study assistant. Extract concise, exam-oriented bullet points from the provided text. "
        "Focus on creating clear, memorable points suitable for 14-15 year old students. "
        "Format each point as a single bullet point starting with '•'. "
        "Keep points factual, specific, and directly useful for exams. "
        "Organize under clear topic headings when useful. "
        "Return ONLY a JSON object with 'subject' and 'points' fields. "
        "The 'points' field should be an array of bullet point strings."
    )

    user_prompt = f"""Extract important points from this ICSE Class 9-10 study content:

--- CONTENT START ---
{text_to_extract}
--- CONTENT END ---

Subject: {default_subject}

Return ONLY JSON format:
{{
  "subject": "Biology",
  "points": [
    "• Photosynthesis occurs in chloroplasts",
    "• Chlorophyll absorbs sunlight for energy conversion",
    "• Produces glucose and oxygen as end products"
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
        subject = parsed.get("subject", default_subject).strip()[:50]
        new_points = parsed.get("points", [])

        if not new_points:
            return jsonify({"error": "No important points could be extracted from the text."}), 400

        uid = current_user_id()
        if not uid:
            return jsonify({"error": "Please log in to save notes."}), 401

        with get_db() as conn:
            # Check if "Important Points" entry already exists for this subject
            existing = conn.execute("""
                SELECT id, content FROM living_notebook 
                WHERE user_id = ? AND subject = ? AND category = 'Key Points'
                ORDER BY created_at DESC LIMIT 1
            """, (uid, subject)).fetchone()

            # Prepare the points content
            if existing:
                # Parse existing points
                existing_content = existing["content"] or ""
                existing_points = []
                
                # Extract existing bullet points
                for line in existing_content.split('\n'):
                    line = line.strip()
                    if line.startswith('•') or line.startswith('-'):
                        # Normalize to bullet format
                        clean_point = line[1:].strip()
                        if clean_point:
                            existing_points.append(f"• {clean_point}")

                # Merge new points, avoiding duplicates
                all_points = existing_points[:]
                for new_point in new_points:
                    new_point = new_point.strip()
                    if not new_point.startswith('•'):
                        new_point = f"• {new_point}"
                    
                    # Check for duplicates (case-insensitive, ignore minor differences)
                    clean_new = new_point[1:].strip().lower()
                    is_duplicate = False
                    for existing_point in all_points:
                        clean_existing = existing_point[1:].strip().lower()
                        # Consider duplicate if 80% similar or contains same key terms
                        if (clean_new in clean_existing or clean_existing in clean_new or 
                            len(set(clean_new.split()) & set(clean_existing.split())) >= 3):
                            is_duplicate = True
                            break
                    
                    if not is_duplicate:
                        all_points.append(new_point)

                # Update existing entry with merged points
                merged_content = '\n'.join(all_points)
                conn.execute("""
                    UPDATE living_notebook 
                    SET content = ?, updated_at = datetime('now')
                    WHERE id = ?
                """, (merged_content, existing["id"]))
                
                entry_id = existing["id"]
                action = "merged"

            else:
                # Create new "Important Points" entry
                points_content = '\n'.join(new_points)
                cur = conn.execute("""
                    INSERT INTO living_notebook (user_id, subject, category, content)
                    VALUES (?, ?, 'Key Points', ?)
                """, (uid, subject, points_content))
                
                entry_id = cur.lastrowid
                action = "created"

            # Get the final entry to return
            final_entry = conn.execute("""
                SELECT * FROM living_notebook WHERE id = ?
            """, (entry_id,)).fetchone()

        if final_entry:
            fs_upsert_notebook_entry(uid, dict(final_entry))

        return jsonify({
            "action": action,
            "entry": dict(final_entry) if final_entry else None,
            "new_points_added": len([p for p in new_points if p.strip()]),
            "subject": subject
        })

    except Exception as e:
        print(f"[ERROR] AI Extract Important Points: {e}")
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


# ── Learning DNA Routes ───────────────────────────────────────────────

def get_or_create_learning_dna(conn, user_id: int):
    """Fetch or initialize learning_dna row for user."""
    row = conn.execute("SELECT * FROM learning_dna WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO learning_dna (user_id) VALUES (?)", (user_id,))
        row = conn.execute("SELECT * FROM learning_dna WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else {}


@app.route("/api/learning_dna", methods=["GET"])
def get_learning_dna():
    """Comprehensive AI Learning Analytics Dashboard - Retrieve full Learning DNA profile with extensive insights."""
    user, err = require_auth()
    if err:
        return err

    uid = user["id"]
    # Pull remote Learning DNA into SQLite, then mirror local up (soft-fail)
    fs_pull_learning_dna_into_sqlite(uid)
    fs_push_all_learning_dna(uid)

    with get_db() as conn:
        import json
        from datetime import datetime, timedelta
        
        profile = get_or_create_learning_dna(conn, uid)

        # Subject analytics with enhanced metrics
        subj_rows = conn.execute(
            "SELECT subject, questions_taken, questions_correct, study_minutes, updated_at FROM subject_analytics WHERE user_id=? ORDER BY study_minutes DESC, questions_taken DESC",
            (uid,)
        ).fetchall()

        subject_breakdown = []
        for r in subj_rows:
            d = dict(r)
            qt = d["questions_taken"]
            qc = d["questions_correct"]
            acc = round((qc / qt * 100.0), 1) if qt > 0 else 0.0
            d["accuracy"] = acc
            subject_breakdown.append(d)

        # Enhanced strongest/weakest analysis
        sorted_by_acc = sorted(
            [s for s in subject_breakdown if s["questions_taken"] > 0 or s["study_minutes"] > 0],
            key=lambda x: (x["accuracy"], x["study_minutes"]),
            reverse=True
        )

        if len(sorted_by_acc) == 1:
            strongest_subjects = [{"subject": sorted_by_acc[0]["subject"], "accuracy": sorted_by_acc[0]["accuracy"]}]
            weakest_subjects = []
        elif len(sorted_by_acc) > 1:
            # Show ALL strongest subjects (top 50%) and ALL weakest subjects (bottom subjects not in strongest)
            mid = max(1, len(sorted_by_acc) // 2)
            strongest_subjects = [{"subject": s["subject"], "accuracy": s["accuracy"]} for s in sorted_by_acc[:mid]]
            weakest_subjects = [{"subject": s["subject"], "accuracy": s["accuracy"]} for s in reversed(sorted_by_acc) if s["subject"] not in [st["subject"] for st in strongest_subjects]]
        else:
            strongest_subjects = [{"subject": "General", "accuracy": 0}]
            weakest_subjects = []

        # Chapter-level analysis from chat history and mistakes
        chat_messages = conn.execute("""
            SELECT m.content, m.created_at, c.title 
            FROM messages m 
            JOIN conversations c ON m.conversation_id = c.id 
            WHERE c.user_id = ? AND m.role = 'user' 
            ORDER BY m.created_at DESC LIMIT 100
        """, (uid,)).fetchall()

        # Extract topics/chapters from chat messages using basic keyword analysis
        topic_mentions = {}
        for msg in chat_messages:
            content = msg["content"].lower()
            # Simple topic extraction - look for common chapter/topic patterns
            words = content.split()
            for i, word in enumerate(words):
                if word in ['chapter', 'unit', 'lesson', 'topic'] and i + 1 < len(words):
                    topic = words[i + 1]
                    if topic not in topic_mentions:
                        topic_mentions[topic] = 0
                    topic_mentions[topic] += 1

        # Show ALL chapters mentioned, sorted by frequency
        strongest_chapters = sorted(topic_mentions.items(), key=lambda x: x[1], reverse=True)
        weakest_chapters = sorted(topic_mentions.items(), key=lambda x: x[1])

        # Quiz performance history (last 30 days)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        quiz_history = []
        
        # Get recent quiz sessions from learning_dna tracking calls
        # We'll simulate this with subject analytics updated dates
        for subj in subject_breakdown:  # ALL subjects with quiz history
            if subj["questions_taken"] > 0:
                quiz_history.append({
                    "date": subj.get("updated_at", datetime.now().strftime('%Y-%m-%d')),
                    "subject": subj["subject"],
                    "accuracy": subj["accuracy"],
                    "questions": subj["questions_taken"]
                })

        # Chat activity analysis
        chat_stats = conn.execute("""
            SELECT DATE(m.created_at) as date, COUNT(*) as messages
            FROM messages m 
            JOIN conversations c ON m.conversation_id = c.id 
            WHERE c.user_id = ? AND m.role = 'user' AND DATE(m.created_at) >= ?
            GROUP BY DATE(m.created_at) 
            ORDER BY date DESC LIMIT 60
        """, (uid, thirty_days_ago)).fetchall()

        daily_chat_activity = [{"date": row["date"], "messages": row["messages"]} for row in chat_stats]

        # Mistake Vault enhanced analysis
        mistakes_analysis = conn.execute("""
            SELECT subject, COUNT(*) as mistake_count, 
                   SUM(CASE WHEN mastered = 1 THEN 1 ELSE 0 END) as mastered_count
            FROM student_mistakes 
            WHERE user_id = ? 
            GROUP BY subject 
            ORDER BY mistake_count DESC
        """, (uid,)).fetchall()

        mistake_vault_stats = [{"subject": row["subject"], "total": row["mistake_count"], "mastered": row["mastered_count"]} for row in mistakes_analysis]

        # Study streak calculation
        study_dates = set()
        for row in chat_stats:
            study_dates.add(row["date"])
        
        # Add quiz dates
        for subj in subject_breakdown:
            if subj.get("updated_at"):
                study_dates.add(subj["updated_at"][:10])  # Extract date part

        # Calculate current streak
        today = datetime.now().date()
        streak = 0
        current_date = today
        
        while current_date.strftime('%Y-%m-%d') in study_dates:
            streak += 1
            current_date -= timedelta(days=1)

        # Learning pace analysis
        total_study_time = profile.get("total_study_minutes", 0)
        total_days = max(1, len(study_dates))
        avg_daily_time = round(total_study_time / total_days, 1) if total_days > 0 else 0

        # Determine learning pace
        if avg_daily_time >= 60:
            pace_category = "Intensive"
        elif avg_daily_time >= 30:
            pace_category = "Steady"
        elif avg_daily_time >= 15:
            pace_category = "Moderate"
        else:
            pace_category = "Light"

        # Exam readiness calculation
        tq = profile.get("total_quiz_questions", 0)
        cq = profile.get("correct_quiz_questions", 0)
        overall_accuracy = round((cq / tq * 100.0), 1) if tq > 0 else 0.0

        # Complex readiness algorithm
        accuracy_score = min(overall_accuracy, 100) * 0.4  # 40% weight
        volume_score = min(tq / 50 * 100, 100) * 0.3      # 30% weight (50 questions = 100%)
        consistency_score = min(streak / 7 * 100, 100) * 0.2  # 20% weight (7 days = 100%)
        breadth_score = min(len(subject_breakdown) / 5 * 100, 100) * 0.1  # 10% weight (5 subjects = 100%)

        exam_readiness = round(accuracy_score + volume_score + consistency_score + breadth_score, 1)

        # Topics mastered (enhanced)
        topics_mastered = [
            {
                "subject": s["subject"], 
                "accuracy": s["accuracy"], 
                "questionsTaken": s["questions_taken"],
                "studyTime": s["study_minutes"]
            }
            for s in subject_breakdown if s["questions_taken"] >= 2 and s["accuracy"] >= 75.0
        ]

        # Topics needing revision (enhanced)
        revision_rows = conn.execute("""
            SELECT id, subject, category, content, created_at FROM living_notebook
            WHERE user_id=? AND category IN ('Things to Revise', 'Mistakes I Made')
            ORDER BY created_at DESC LIMIT 15
        """, (uid,)).fetchall()
        topics_to_revise = [dict(r) for r in revision_rows]

        # Add low-accuracy subjects
        for s in subject_breakdown:
            if s["questions_taken"] >= 2 and s["accuracy"] < 60.0:
                if not any(r["subject"].lower() == s["subject"].lower() for r in topics_to_revise):
                    topics_to_revise.append({
                        "id": f"subj_{s['subject']}",
                        "subject": s["subject"],
                        "category": "Low Accuracy",
                        "content": f"Current accuracy: {s['accuracy']}% - Needs focused practice",
                        "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })

        # AI-powered personalized recommendations (Generate ALL applicable insights)
        buddy_name = user["buddy_name"] if "buddy_name" in user.keys() and user["buddy_name"] else "Max"
        
        recommendations = []
        
        # Accuracy-based recommendations
        if overall_accuracy < 70 and tq > 10:
            recommendations.append("Focus on reviewing your mistake vault - understanding errors leads to better scores!")
        elif overall_accuracy >= 85:
            recommendations.append("Outstanding accuracy! Consider tackling more challenging topics to push your limits.")
        elif overall_accuracy >= 70:
            recommendations.append("Good accuracy! Aim for 80%+ by reviewing incorrect answers more carefully.")
        
        # Study streak recommendations
        if streak == 0:
            recommendations.append("Start a study streak! Even 15 minutes daily builds momentum.")
        elif streak >= 7:
            recommendations.append(f"Amazing {streak}-day streak! Keep this consistency to maximize retention.")
        elif streak >= 3:
            recommendations.append(f"Great {streak}-day streak! Try to reach a full week of daily study.")
        
        # Subject balance recommendations
        if len(strongest_subjects) > 0 and len(weakest_subjects) > 0:
            recommendations.append(f"Balance your study: maintain strength in {strongest_subjects[0]['subject']} while improving {weakest_subjects[0]['subject']}.")
        elif len(strongest_subjects) > 2:
            recommendations.append("You're excelling in multiple subjects! Consider teaching others to reinforce your knowledge.")
        elif len(subject_breakdown) == 1:
            recommendations.append("Explore additional subjects to build a well-rounded knowledge base.")
        
        # Activity-based recommendations
        if total_study_time > 0 and tq == 0:
            recommendations.append("You're chatting well! Try taking a quiz to test your knowledge retention.")
        elif tq > 20 and total_study_time < 60:
            recommendations.append("You're quiz-active! Balance with more reading and note-taking for deeper understanding.")
        elif total_study_time > 120 and tq < 5:
            recommendations.append("Great study time! Test your knowledge with more quizzes to identify gaps.")
        
        # Content management recommendations
        if len(topics_to_revise) > 5:
            recommendations.append("Your revision list is growing. Pick 2-3 priority topics to focus on this week.")
        elif len(topics_mastered) > 3:
            recommendations.append("Excellent mastery! Review mastered topics monthly to maintain long-term retention.")
        
        # Exam readiness recommendations
        if exam_readiness < 40:
            recommendations.append("Build your exam readiness: Take more quizzes and maintain daily study habits.")
        elif exam_readiness >= 80:
            recommendations.append("You're exam-ready! Practice under timed conditions and review edge cases.")
        elif exam_readiness >= 60:
            recommendations.append("Good exam prep progress! Focus on your weaker subjects to boost overall readiness.")
        
        # Time management recommendations
        if avg_daily_time < 15:
            recommendations.append("Try studying for at least 25 minutes daily - the optimal focus duration.")
        elif avg_daily_time > 120:
            recommendations.append("Impressive study time! Make sure to take breaks every 45-60 minutes for optimal retention.")
        
        # Engagement pattern recommendations
        if len(daily_chat_activity) > 0:
            avg_daily_messages = sum(d['messages'] for d in daily_chat_activity) / len(daily_chat_activity)
            if avg_daily_messages > 20:
                recommendations.append("High engagement! Try converting some chat questions into quiz practice.")
            elif avg_daily_messages < 5:
                recommendations.append("Ask more questions! Active learning through chat boosts understanding.")

        # Always provide at least one recommendation
        if not recommendations:
            recommendations.append("Keep up the great work! You're building solid learning habits.")

        # Suggested next study session
        if len(weakest_subjects) > 0:
            next_session = f"Practice {weakest_subjects[0]['subject']} for 25 minutes, then take a short quiz"
        elif len(topics_to_revise) > 0:
            next_session = f"Review notes on {topics_to_revise[0]['subject']} and identify key concepts to practice"
        else:
            next_session = "Explore a new topic or deepen your strongest subject knowledge"

        # Weekly and monthly progress
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        weekly_messages = len([msg for msg in daily_chat_activity if msg["date"] >= week_ago])
        monthly_messages = len([msg for msg in daily_chat_activity if msg["date"] >= month_ago])

        # Enhanced buddy advice with more personality
        if tq == 0 and profile.get("total_study_minutes", 0) < 5:
            buddy_advice = f"Hi there! I'm {buddy_name}, your AI Study Buddy 🤖. I'm excited to help you learn! Start by asking questions or taking your first quiz to unlock your complete Learning DNA dashboard with detailed analytics, progress tracking, and personalized recommendations!"
        elif exam_readiness >= 80:
            buddy_advice = f"🎉 Outstanding! You're at {exam_readiness}% exam readiness with {overall_accuracy}% accuracy. You're clearly mastering the material. Keep this momentum going!"
        elif exam_readiness >= 60:
            buddy_advice = f"💪 Great progress! You're at {exam_readiness}% exam readiness. " + (recommendations[0] if recommendations else "Keep practicing consistently!")
        elif streak >= 3:
            buddy_advice = f"🔥 Love your {streak}-day study streak! Consistency is key to mastery. " + (recommendations[0] if recommendations else "You're building excellent habits!")
        else:
            buddy_advice = f"Let's boost your learning! " + (recommendations[0] if recommendations else "Take it one step at a time - you've got this!")

    resp = jsonify({
        # Core metrics (existing)
        "totalStudyMinutes": profile.get("total_study_minutes", 0),
        "totalQuizzes": profile.get("total_quizzes", 0),
        "totalQuestions": tq,
        "correctQuestions": cq,
        "accuracy": overall_accuracy,
        "preferredStyle": profile.get("preferred_style", "Step-by-Step"),
        "learningPace": profile.get("learning_pace", "Steady"),
        
        # Enhanced subject analysis
        "strongestSubjects": strongest_subjects,
        "weakestSubjects": weakest_subjects,
        "subjectBreakdown": subject_breakdown,
        
        # NEW: Chapter-level insights
        "strongestChapters": [{"chapter": ch[0], "mentions": ch[1]} for ch in strongest_chapters],
        "weakestChapters": [{"chapter": ch[0], "mentions": ch[1]} for ch in weakest_chapters],
        
        # NEW: Performance tracking
        "quizPerformanceHistory": quiz_history[-30:],  # Last 30 entries
        "dailyChatActivity": daily_chat_activity,
        
        # NEW: Mistake analysis
        "mistakeVaultAnalysis": mistake_vault_stats,
        
        # Enhanced learning metrics
        "topicsMastered": topics_mastered,
        "topicsToRevise": topics_to_revise[:15],
        
        # NEW: Advanced analytics
        "studyStreak": streak,
        "learningPaceCategory": pace_category,
        "averageDailyStudyTime": avg_daily_time,
        "examReadiness": exam_readiness,
        
        # NEW: AI recommendations
        "personalizedRecommendations": recommendations,
        "suggestedNextSession": next_session,
        
        # NEW: Progress tracking
        "weeklyProgress": {
            "chatMessages": weekly_messages,
            "studyDays": len([d for d in study_dates if d >= week_ago]),
            "avgAccuracy": overall_accuracy
        },
        "monthlyProgress": {
            "chatMessages": monthly_messages,
            "studyDays": len([d for d in study_dates if d >= month_ago]),
            "totalQuizzes": profile.get("total_quizzes", 0),
            "totalStudyTime": profile.get("total_study_minutes", 0)
        },
        
        # Enhanced buddy interaction
        "buddyAdvice": buddy_advice,
        
        # Legacy compatibility
        "commonMistakes": mistake_vault_stats,
        "recentProgress": {
            "totalSessions": profile.get("total_quizzes", 0) + (1 if profile.get("total_study_minutes", 0) > 0 else 0),
            "studyMinutes": profile.get("total_study_minutes", 0),
            "accuracy": overall_accuracy,
            "streak": streak,
            "examReadiness": exam_readiness
        }
    })

    # Mirror computed progress snapshot to Firestore (soft-fail)
    fs_push_progress_from_sqlite(uid, {
        "accuracy": overall_accuracy,
        "study_streak": streak,
        "exam_readiness": exam_readiness,
    })

    return resp


@app.route("/api/learning_dna/track", methods=["POST"])
def track_learning_dna():
    """Track study pings, quiz metrics, style preferences, and mistakes."""
    user, err = require_auth()
    if err:
        return err

    uid = user["id"]
    data = request.get_json(force=True) or {}

    study_mins = int(data.get("studyMinutes", 0))
    subject = (data.get("subject") or "General").strip()[:50]
    quiz_res = data.get("quizResult") # {"questionsTaken": 5, "questionsCorrect": 4, "subject": "Physics"}
    pref_style = data.get("preferredStyle")
    pace = data.get("learningPace")
    mistake_text = (data.get("mistake") or "").strip()

    with get_db() as conn:
        profile = get_or_create_learning_dna(conn, uid)

        fields = ["updated_at=datetime('now')"]
        vals = []

        if study_mins > 0:
            fields.append("total_study_minutes = total_study_minutes + ?")
            vals.append(study_mins)

        if isinstance(quiz_res, dict):
            qt = max(0, int(quiz_res.get("questionsTaken", 0)))
            qc = max(0, int(quiz_res.get("questionsCorrect", 0)))
            q_subj = (quiz_res.get("subject") or subject).strip()[:50]

            fields.append("total_quizzes = total_quizzes + 1")
            fields.append("total_quiz_questions = total_quiz_questions + ?")
            vals.append(qt)
            fields.append("correct_quiz_questions = correct_quiz_questions + ?")
            vals.append(qc)

            # Update subject analytics
            conn.execute("""
                INSERT INTO subject_analytics (user_id, subject, questions_taken, questions_correct, study_minutes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, subject) DO UPDATE SET
                    questions_taken = questions_taken + excluded.questions_taken,
                    questions_correct = questions_correct + excluded.questions_correct,
                    updated_at = datetime('now')
            """, (uid, q_subj, qt, qc, study_mins))

        elif study_mins > 0:
            conn.execute("""
                INSERT INTO subject_analytics (user_id, subject, questions_taken, questions_correct, study_minutes)
                VALUES (?, ?, 0, 0, ?)
                ON CONFLICT(user_id, subject) DO UPDATE SET
                    study_minutes = study_minutes + excluded.study_minutes,
                    updated_at = datetime('now')
            """, (uid, subject, study_mins))

        if pref_style:
            fields.append("preferred_style = ?")
            vals.append(str(pref_style).strip()[:50])

        if pace:
            fields.append("learning_pace = ?")
            vals.append(str(pace).strip()[:50])

        if len(fields) > 1:
            vals.append(uid)
            conn.execute(f"UPDATE learning_dna SET {', '.join(fields)} WHERE user_id=?", vals)

        if mistake_text:
            # Use the new enhanced mistakes format
            cur = conn.execute("""
                INSERT INTO student_mistakes (user_id, subject, topic, question, wrong_answer, correct_answer, explanation, source_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (uid, subject, 'General', 'Legacy mistake entry', 'Unknown', 'See explanation', mistake_text[:500], 'learning_dna'))
            mist_row = conn.execute(
                "SELECT * FROM student_mistakes WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        else:
            mist_row = None

    # Mirror updated Learning DNA + subject analytics to Firestore (soft-fail)
    fs_push_all_learning_dna(uid)
    if mist_row:
        fs_upsert_mistake(uid, dict(mist_row))
    fs_push_progress_from_sqlite(uid)

    return jsonify({"ok": True})


# ── Mistake Vault Routes ─────────────────────────────────────────────

@app.route("/api/mistakes", methods=["GET"])
def get_mistakes():
    """Get all mistakes for the logged-in user with optional filtering."""
    user, err = require_auth()
    if err:
        return err

    # Pull remote mistakes into SQLite, then mirror local up (soft-fail)
    fs_pull_mistakes_into_sqlite(user["id"])
    fs_push_all_mistakes(user["id"])

    subject = request.args.get('subject', '').strip()
    search = request.args.get('search', '').strip()
    mastered_only = request.args.get('mastered') == 'true'
    unmastered_only = request.args.get('unmastered') == 'true'

    with get_db() as conn:
        query = """
            SELECT id, subject, topic, question, wrong_answer, correct_answer, 
                   explanation, mastered, source_type, created_at, mastered_at
            FROM student_mistakes 
            WHERE user_id=?
        """
        params = [user["id"]]

        if subject:
            query += " AND subject=?"
            params.append(subject)

        if search:
            query += " AND (question LIKE ? OR explanation LIKE ? OR topic LIKE ?)"
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])

        if mastered_only:
            query += " AND mastered=1"
        elif unmastered_only:
            query += " AND mastered=0"

        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()

    return jsonify({"mistakes": [dict(r) for r in rows]})


@app.route("/api/mistakes", methods=["POST"])
def add_mistake():
    """Add a new mistake to the vault."""
    user, err = require_auth()
    if err:
        return err

    data = request.get_json(force=True) or {}
    subject = (data.get("subject") or "General").strip()
    topic = (data.get("topic") or "General").strip()
    question = (data.get("question") or "").strip()
    wrong_answer = (data.get("wrong_answer") or "").strip()
    correct_answer = (data.get("correct_answer") or "").strip()
    explanation = (data.get("explanation") or "").strip()
    source_type = (data.get("source_type") or "manual").strip()

    if not question or not correct_answer or not explanation:
        return jsonify({"error": "Question, correct answer, and explanation are required."}), 400

    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO student_mistakes (user_id, subject, topic, question, wrong_answer, correct_answer, explanation, source_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], subject, topic, question, wrong_answer, correct_answer, explanation, source_type))
        
        mistake_id = cur.lastrowid
        row = conn.execute("SELECT * FROM student_mistakes WHERE id=?", (mistake_id,)).fetchone()

    entry = dict(row)
    fs_upsert_mistake(user["id"], entry)
    fs_push_progress_from_sqlite(user["id"])
    return jsonify(entry), 201


@app.route("/api/mistakes/<int:mistake_id>", methods=["PATCH"])
def update_mistake(mistake_id):
    """Update a mistake (mainly for marking as mastered)."""
    user, err = require_auth()
    if err:
        return err

    data = request.get_json(force=True) or {}

    with get_db() as conn:
        # Verify ownership
        row = conn.execute("SELECT * FROM student_mistakes WHERE id=? AND user_id=?", (mistake_id, user["id"])).fetchone()
        if not row:
            return jsonify({"error": "Mistake not found."}), 404

        fields = []
        vals = []

        if "mastered" in data:
            mastered = 1 if data["mastered"] else 0
            fields.append("mastered=?")
            vals.append(mastered)
            
            if mastered:
                fields.append("mastered_at=datetime('now')")
            else:
                fields.append("mastered_at=NULL")

        if "subject" in data and data["subject"].strip():
            fields.append("subject=?")
            vals.append(data["subject"].strip())

        if "topic" in data and data["topic"].strip():
            fields.append("topic=?")
            vals.append(data["topic"].strip())

        if "question" in data and data["question"].strip():
            fields.append("question=?")
            vals.append(data["question"].strip())

        if "wrong_answer" in data:
            fields.append("wrong_answer=?")
            vals.append(data["wrong_answer"].strip())

        if "correct_answer" in data and data["correct_answer"].strip():
            fields.append("correct_answer=?")
            vals.append(data["correct_answer"].strip())

        if "explanation" in data and data["explanation"].strip():
            fields.append("explanation=?")
            vals.append(data["explanation"].strip())

        if fields:
            vals.append(mistake_id)
            conn.execute(f"UPDATE student_mistakes SET {', '.join(fields)} WHERE id=?", vals)

        updated = conn.execute("SELECT * FROM student_mistakes WHERE id=?", (mistake_id,)).fetchone()

    entry = dict(updated)
    fs_upsert_mistake(user["id"], entry)
    fs_push_progress_from_sqlite(user["id"])
    return jsonify(entry)


@app.route("/api/mistakes/<int:mistake_id>", methods=["DELETE"])
def delete_mistake(mistake_id):
    """Delete a mistake."""
    user, err = require_auth()
    if err:
        return err

    with get_db() as conn:
        row = conn.execute("SELECT id FROM student_mistakes WHERE id=? AND user_id=?", (mistake_id, user["id"])).fetchone()
        if not row:
            return jsonify({"error": "Mistake not found."}), 404
        
        conn.execute("DELETE FROM student_mistakes WHERE id=?", (mistake_id,))

    fs_delete_mistake(user["id"], mistake_id)
    fs_push_progress_from_sqlite(user["id"])
    return jsonify({"ok": True})


@app.route("/api/mistakes/subjects", methods=["GET"])
def get_mistake_subjects():
    """Get all subjects that have mistakes."""
    user, err = require_auth()
    if err:
        return err

    fs_pull_mistakes_into_sqlite(user["id"])

    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT subject, COUNT(*) as count
            FROM student_mistakes 
            WHERE user_id=?
            GROUP BY subject
            ORDER BY count DESC, subject ASC
        """, (user["id"],)).fetchall()

    return jsonify({"subjects": [dict(r) for r in rows]})


# =====================================================================
#  STEP 7: START THE SERVER
# =====================================================================

# Cold-start DB tables when running as a Vercel serverless function
# (local `python app.py` still initializes in __main__ below).
if os.environ.get("VERCEL"):
    try:
        init_db()
    except Exception as e:
        print(f"[WARN] init_db on Vercel cold start: {e}")


if __name__ == "__main__":
    init_db()
    print("\n[STARTING] Study Buddy is running!")
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
