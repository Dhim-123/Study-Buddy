# 📚 Study Buddy

> Your AI-powered personal study assistant — built to help students learn smarter, not harder.

![Python](https://img.shields.io/badge/Python-3.8+-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-2.x-black?style=flat-square&logo=flask)
![Gemini AI](https://img.shields.io/badge/Gemini-AI-orange?style=flat-square&logo=google)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

---

## ✨ What is Study Buddy?

**Study Buddy** is an AI-powered web application that acts as your personal tutor. Upload your study notes and chat with an intelligent assistant that:

- 🧠 Answers questions based on **your uploaded notes**
- 🎙️ Generates **podcast-style** audio summaries
- 🃏 Creates **flashcards** from your study material
- 📝 Builds **quizzes** to test your knowledge
- ✅ **Cross-checks** your answers and corrects mistakes

---

## 🚀 Features

| Feature | Description |
|---|---|
| 💬 **Smart Chat** | Step-by-step AI explanations tailored to your notes |
| 📄 **Upload Notes** | Upload `.txt` files and the AI prioritizes your content |
| 🎙️ **Podcast Mode** | Converts your study session into a spoken podcast script |
| 🃏 **Flashcards** | Auto-generates Q&A flashcard pairs from your material |
| 📝 **Quiz Generator** | Creates 5 multiple-choice questions based on your notes |
| ✅ **Cross-Check** | Reviews and corrects your answers with explanations |
| 🚨 **Mistake Vault** | Automatically saves wrong answers from quizzes for review |
| 🌙 **Dark Mode UI** | Clean, modern dark-themed interface |

---

## 🛠️ Tech Stack

- **Backend**: Python, Flask, Flask-CORS
- **AI**: Google Gemini API (`google-generativeai`)
- **Frontend**: Vanilla HTML, CSS, JavaScript
- **Config**: `python-dotenv` for secure API key management

---

## ⚙️ Setup & Installation

### 1. Clone the repository

```bash
git clone https://github.com/Dhim-123/study-buddy.git
cd study-buddy
```

### 2. Install dependencies

```bash
cd study_buddy
pip install -r requirements.txt
```

### 3. Set up your API key

Create a `.env` file inside the `study_buddy/` folder:

```env
GROQ_API_KEY=your-groq-api-key-here
OPENAI_API_KEY=your-openai-api-key-here
FLASK_SECRET_KEY=a-long-random-secret
ADMIN_USERNAME=your-private-admin-username
ADMIN_PASSWORD=your-private-admin-password
```

> Chat uses Groq. Diagram photos use OpenAI (`OPENAI_API_KEY`); without it, diagrams fall back to Groq SVG.
>
> `ADMIN_USERNAME` / `ADMIN_PASSWORD` unlock the private Admin panel (Settings → Admin) to set exam dates and portions for all students. If unset, admin login returns “Admin not configured.”

### 4. Run the app

**Option A — Double-click:**
```
run.bat
```

**Option B — Terminal:**
```bash
python app.py
```

### 5. Open in browser

```
http://localhost:5000
```

---

## 📁 Project Structure

```
study-buddy/
├── study_buddy/
│   ├── app.py              # Flask backend & Gemini AI integration
│   ├── index.html          # Main frontend (chat, quiz, flashcards)
│   ├── career-dreamer.html # Career Dreamer feature
│   ├── requirements.txt    # Python dependencies
│   ├── run.bat             # One-click Windows launcher
│   ├── .env                # API key (NOT committed to git)
│   └── .gitignore          # Git ignore rules
├── .gitignore
└── README.md
```

---

## 🔒 Security

- The `.env` file containing your API key is **excluded from git** via `.gitignore`
- The system prompt runs **server-side only** — never exposed to the browser
- All user inputs are validated and sanitized before being sent to the AI

---

## 💡 How It Works

1. **Upload your notes** (`.txt` file, max 2MB)
2. The notes are stored in the browser and sent with every request
3. The Flask backend injects your notes into the AI's system prompt
4. The AI **prioritizes your notes** but can also answer general knowledge questions
5. Switch between Chat, Podcast, Flashcards, Quiz, and Cross-Check modes anytime

---

## 📦 Requirements

```
flask
flask-cors
python-dotenv
google-generativeai
```

Install with:
```bash
pip install -r requirements.txt
```

---

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you'd like to change.

---

## 📄 License

This project is licensed under the MIT License.

---

<div align="center">
  Made with ❤️ by <a href="https://github.com/Dhim-123">Dhim-123</a>
</div>
