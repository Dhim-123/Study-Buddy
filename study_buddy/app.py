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
# Think of these like importing apps onto your phone.
# Each one gives us a specific superpower.

import os
# "os" lets us talk to the operating system — like reading files,
# checking environment variables (secret settings), etc.

from flask import Flask, request, jsonify, send_from_directory
# "Flask" is a tiny web server framework.
# - Flask        → creates our web server (like setting up a shop)
# - request      → lets us read what the browser sent us
# - jsonify      → converts Python dictionaries into JSON
#                   (JSON is the language browsers speak)
# - send_from_directory → sends files (like index.html) to the browser

from flask_cors import CORS
# "CORS" = Cross-Origin Resource Sharing
# Without this, browsers block requests between different websites.
# We add it just in case — it makes things work smoothly.

from dotenv import load_dotenv
# "dotenv" reads a file called ".env" and loads its contents
# as environment variables. This is where we hide our API key
# so it doesn't get accidentally shared.

import google.generativeai as genai
# This is Google's official library to talk to Gemini AI.
# It handles all the complicated stuff — we just call simple functions.


# =====================================================================
#  STEP 2: LOAD THE SECRET API KEY
# =====================================================================
# The API key is like a password that lets us use Gemini AI.
# We store it in a file called ".env" so it stays private.

load_dotenv()
# ^ This reads the .env file and loads GEMINI_API_KEY into memory.

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# ^ os.getenv("GEMINI_API_KEY", "") means:
#   "Go find a variable called GEMINI_API_KEY.
#    If you can't find it, just give me an empty string."

# If no key was found, print a helpful warning:
if not GEMINI_API_KEY:
    print("\n[WARNING] No GEMINI_API_KEY found!")
    print("   Create a file called  .env  in this folder and add:")
    print("   GEMINI_API_KEY=your-key-here\n")

# Tell the Google library to use our API key for all future requests:
genai.configure(api_key=GEMINI_API_KEY)

# Hidden system prompt stored on the server only.
# If you want to change it, edit the .env file or this Python file.
SYSTEM_PROMPT = os.getenv(
    "STUDY_BUDDY_SYSTEM_PROMPT",
    "You are a helpful study buddy. Answer clearly, gently, and in simple language for a student. Always answer questions step by step. Number each step and include a short hint for the next step when appropriate."
)


# =====================================================================
#  STEP 3: CREATE THE WEB SERVER
# =====================================================================
# This is like opening a shop. Flask creates a server that listens
# for visitors (browsers) and responds to their requests.

app = Flask(__name__, static_folder=".", static_url_path="")
# ^ Creates our Flask app.
#   static_folder="."  →  means "serve files from this same folder"
#   static_url_path="" →  means "don't add any prefix to file URLs"

CORS(app)
# ^ Enables CORS (explained above). Just a safety net.


# =====================================================================
#  STEP 4: DEFINE ROUTES — What happens when someone visits a URL
# =====================================================================

# ── ROUTE 1: The homepage ("/") ─────────────────────────────────────
# When someone opens http://localhost:5000 in their browser,
# this function runs and sends them the index.html file.

@app.route("/")
def index():
    """Serve the main chat page (index.html)."""
    return send_from_directory(".", "index.html")
    # ^ "Hey browser, here's the index.html file from this folder!"


# ── ROUTE 1b: Career Dreamer wrapper ("/career-dreamer") ───────────────
# Serves the Google Career Dreamer session wrapper app

@app.route("/career-dreamer")
def career_dreamer():
    """Serve the Career Dreamer session wrapper app."""
    return send_from_directory(".", "career-dreamer.html")


# ── ROUTE 2: The chat endpoint ("/api/chat") ────────────────────────
# This is the important one! When the user sends a message in the chat,
# the browser sends a POST request here with:
#   - messages: the full conversation so far
#   - model: which Gemini model to use
#
# The system prompt is stored only on the server and is not sent from the browser.
# We take that info, send it to Gemini AI, and return the AI's reply.

@app.route("/api/chat", methods=["POST"])
@app.route("/api/podcast", methods=["POST"])
@app.route("/api/flashcards", methods=["POST"])
@app.route("/api/quiz", methods=["POST"])
@app.route("/api/crosscheck", methods=["POST"])
def chat():
    """
    Handle chat, podcast generation, flashcard generation, quiz generation, and crosscheck generation.
    Detects the endpoint and adapts the system prompt accordingly.
    """

    # --- Read the data the browser sent us ---
    data = request.get_json(force=True)
    
    # Detect which endpoint was called
    endpoint = request.path.split('/')[-1]  # Get last part of URL
    
    system_prompt = SYSTEM_PROMPT
    messages = data.get("messages", [])
    model_name = data.get("model", "gemini-2.0-flash")
    notes = data.get("notes", "")

    # Clean and validate the incoming conversation history.
    messages = [
        msg for msg in messages
        if isinstance(msg, dict)
        and msg.get("role") in {"user", "assistant"}
        and isinstance(msg.get("content"), str)
        and msg["content"].strip()
    ]

    # Enhance system prompt based on endpoint and require full chat context.
    if endpoint == "chat":
        system_prompt = f"{system_prompt}\n\nAlways answer in numbered steps. Each step should contain only one idea and only one part. Provide only the first step in each response, never multiple steps at once. End by telling the user to say 'move to next step' to continue, or 'hint for next step', or 'explain in simpler terms'."
    elif endpoint == "podcast":
        system_prompt = f"{system_prompt}\n\nUsing the full conversation history from the entire chat session, including earlier user questions and assistant replies, create a short podcast-style answer in English. Write as if you are speaking in a friendly, human-hosted educational podcast with natural spoken phrasing. Produce only the spoken text (plain, TTS-ready) with a natural, conversational tone. Avoid using symbols like # or @ (do not say 'hashtag' or 'at'); avoid code blocks, headers, or markup. Use short clear sentences and do not emit any extra metadata or instructions. Return only the script suitable for text-to-speech in English."
    elif endpoint == "flashcards":
        system_prompt = f"{system_prompt}\n\nUsing the full conversation history from the entire chat session, including earlier questions and answers, create flashcard questions and answers in English. Return them as a list of Q&A pairs. Format: 'Q: [question]\nA: [answer]' on separate lines. Create 5-10 cards."
    elif endpoint == "quiz":
        system_prompt = f"{system_prompt}\n\nUsing the full conversation history from the entire chat session, including earlier questions and answers, create a quiz with 5 multiple choice questions in English. Format each as: 'Q[number]: [question]\nA) [option]\nB) [option]\nC) [option]\nD) [option]\nAnswer: [correct letter]' on separate lines."
    elif endpoint == "crosscheck":
        system_prompt = f"{system_prompt}\n\nUsing the full conversation history from the entire chat session, review the student's question and answer provided below in English. If the answer is wrong, explain exactly where it is incorrect, show how to fix it, and reveal the correct answer. Do not only give hints; provide a clear correction and the correct response."

    # If notes are uploaded, instruct the AI to prioritize notes but answer anyway if outside the notes
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

    # --- Safety checks ---
    if not GEMINI_API_KEY:
        return jsonify({
            "error": "Server has no API key configured. Ask the owner to add GEMINI_API_KEY to the .env file."
        }), 500

    if not messages:
        return jsonify({"error": "No messages provided."}), 400

    # --- Talk to Gemini AI ---
    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
        )

        gemini_history = []
        for msg in messages[:-1]:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_history.append({
                "role": role,
                "parts": [msg["content"]]
            })

        chat_session = model.start_chat(history=gemini_history)
        last_message = messages[-1]["content"]
        response = chat_session.send_message(last_message)

        return jsonify({"reply": response.text})

    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Gemini API: {error_msg}")
        return jsonify({"error": error_msg}), 500


# =====================================================================
#  STEP 5: START THE SERVER
# =====================================================================
# This only runs when you execute "python app.py" directly.
# It starts the Flask server on port 5000.

if __name__ == "__main__":
    print("\n[STARTING] Study Buddy is running!")

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
