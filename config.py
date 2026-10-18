"""
config.py - all the settings you may want to change, in one place.
"""
import os

# ---------------- WEATHER (Open-Meteo, no key needed) ----------------
DEFAULT_CITY = "Patiala"          # used when you do not say a city
TEMP_UNIT = "celsius"             # "celsius" or "fahrenheit"

# ---------------- WAKE WORD ----------------
# You say "hey assistant" / "ok assistant" / "hi assistant".
# Change WAKE_WORD to e.g. "jarvis" to say "hey jarvis".
WAKE_WORD = "assistant"
# Optional OFFLINE wake word (recommended, nothing is sent to Google while
# waiting). Download "vosk-model-small-en-us-0.15" from
# https://alphacephei.com/vosk/models , unzip it and put the folder next to
# assistant.py, then set the folder name here:
VOSK_MODEL_PATH = "vosk-model-small-en-us-0.15"

# ---------------- ASK ANYTHING (AI) ----------------
AI_ENABLED = False                # True = answer any question with AI
AI_PROVIDER = "ollama"         # "anthropic" (needs a key) or "ollama" (free, local)
ANTHROPIC_MODEL = "claude-haiku-5-5"
OLLAMA_MODEL = "llama3.2"
OLLAMA_URL = "http://localhost:11434/api/chat"
AI_MAX_WORDS = 60                 # keeps spoken answers short


def get_api_key():
    """Key comes from the ANTHROPIC_API_KEY environment variable, or from a
    one-line text file called api_key.txt next to assistant.py."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        with open("api_key.txt", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""