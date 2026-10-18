# Voice Desktop Assistant

A desktop voice assistant for Windows built with Python. Talk to it or type to it. It takes notes, sets reminders and timers, opens apps, checks the weather, answers questions with AI, and more. It comes with a dark GUI with live sound waves and a chat made of speech bubbles.

## Features

| Area | Examples |
|------|----------|
| **Notes** | "take a note buy milk", "read my notes", "what did I note about milk", "delete note number 2" |
| **Reminders** | "remind me in 5 minutes to drink water", "change reminder number 1 to 6 pm", "delete reminder number 1" |
| **Timers** | "set a timer for 5 minutes", "how much time is left", "start / stop / reset stopwatch" |
| **Dictation** | "start dictation" ... "stop dictation", "read my last dictation", "delete that" |
| **Typing mode** | "type hello world", "start typing mode" ... "stop typing" |
| **Apps** | "open notepad / calculator / chrome", "close notepad", "what can you open" |
| **PC control** | screenshot, volume up/down/mute, show desktop, wifi on/off, brightness, lock the computer (asks to confirm) |
| **Info** | time, date, battery status, CPU and RAM usage |
| **Weather** | "what's the weather", "will it rain today", "weather in Delhi" |
| **Web** | "search google for ...", "play ... on youtube", "tell me about ..." (Wikipedia) |
| **Music** | play, pause, next song, previous song |
| **Briefing** | "good morning" gives the time, weather and today's reminders |
| **Ask AI** | ask any question; "new chat" resets the conversation |
| **Fun** | "tell me a joke" |

## The window

- Live sound wave for your voice and another for the assistant
- Chat shown as speech bubbles
- Side panels for Notes, Reminders, Dictations, Timers and a Commands list
- Quick buttons for Time, Joke, Screenshot, Battery, Weather and Help
- Mic button for continuous listening, **One command** for a single command, and **Wake word** mode ("Hey Assistant")
- You can always type a command instead of speaking

## Requirements

- Windows 10 or 11 (some commands, such as wifi, brightness, lock and closing apps, are Windows-only)
- Python 3.11
- A microphone
- Internet connection (speech recognition, weather and AI use online services)

## Installation

```bash
git clone <your-repo-url>
cd voice_assistant
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 1. Wake word model (optional but recommended)

The wake word works offline with the Vosk model, which is not included in this repo.

1. Download `vosk-model-small-en-us-0.15` from https://alphacephei.com/vosk/models
2. Unzip it and put the folder next to `assistant.py`

Without it, the wake word falls back to Google recognition and needs internet.

### 2. AI answers (optional)

The "ask anything" feature needs an AI provider. Pick one in `config.py`:

- **Anthropic:** put your key in a file named `api_key.txt` next to `assistant.py` (one line), or set the `ANTHROPIC_API_KEY` environment variable.
- **Ollama (free, runs locally):** install Ollama, then set `AI_PROVIDER = "ollama"` in `config.py`.

Everything else works without AI.

## Run

```bash
python gui.py
```

The first run creates `assistant.db` and the `dictations/` and `screenshots/` folders automatically.

## Settings

| File | What to change |
|------|----------------|
| `config.py` | default city, temperature unit, wake word, AI provider and model |
| `assistant.py` | `LANGUAGE` (speech recognition language, default `en-IN`; try `en-US` if recognition is poor) |

## Troubleshooting

- **Wrong or no microphone:** run `python tools/list_mics.py`, `python tools/find_mic.py` and `python tools/compare_mics.py`.
- **Check the install:** run `python tools/check_install.py`.
- **`pyaudio` will not install:** run `pip install pipwin` and then `pipwin install pyaudio`.
- **Recognition mishears you:** try `LANGUAGE = "en-US"`, move closer to the mic, or reduce background noise.
- **"I cannot reach the internet":** speech recognition needs a connection.

## Project structure

```
voice_assistant/
├── gui.py            # window (run this)
├── assistant.py      # main brain: listening, speaking, commands
├── skills.py         # weather, timers, notes, music, wifi, brightness, etc.
├── nlu.py            # understands natural sentences
├── ai_chat.py        # AI question answering
├── config.py         # settings
├── requirements.txt
└── tools/            # microphone test and setup helpers
```

## Built with

Python, CustomTkinter, SpeechRecognition, pyttsx3, Vosk, SQLite, Open-Meteo (weather), Wikipedia, and Anthropic / Ollama for AI answers.

## Author

Tanish juneja
