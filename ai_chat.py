"""
ai_chat.py - sends any other question to an AI model and returns a short
answer that is nice to hear. Uses only the standard library (no extra install).

Providers (set in config.py):
  anthropic - needs a key (env var ANTHROPIC_API_KEY or file api_key.txt)
  ollama    - free, runs on your own PC (https://ollama.com)
"""
import json
import urllib.error
import urllib.request
from datetime import datetime

import config

_history = []          # last few turns, so follow-up questions work


class AIError(Exception):
    pass

def is_configured():
    if not getattr(config, "AI_ENABLED", True):
        return False
    if config.AI_PROVIDER == "ollama":
        return True
    return bool(config.get_api_key())


def reset():
    _history.clear()


def _system_prompt():
    return (
        "You are a friendly voice assistant on a desktop PC. Your answer will "
        f"be read aloud, so reply in plain spoken English, at most "
        f"{config.AI_MAX_WORDS} words, with no markdown, lists, emojis or "
        "code blocks. If the question needs a long answer, give the short "
        "version and offer to say more. "
        f"Right now it is {datetime.now().strftime('%A %d %B %Y, %I:%M %p')}."
    )


def _post(url, payload, headers, timeout=30):
    req = urllib.request.Request(url, json.dumps(payload).encode("utf-8"),
                                 headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:
            pass
        raise AIError(f"HTTP {e.code} {detail}".strip())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError(f"connection problem: {e}")


def _anthropic(messages):
    data = _post(
        "https://api.anthropic.com/v1/messages",
        {"model": config.ANTHROPIC_MODEL, "max_tokens": 300,
         "system": _system_prompt(), "messages": messages},
        {"content-type": "application/json",
         "x-api-key": config.get_api_key(),
         "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text").strip()


def _ollama(messages):
    data = _post(
        config.OLLAMA_URL,
        {"model": config.OLLAMA_MODEL, "stream": False,
         "messages": [{"role": "system", "content": _system_prompt()}]
                     + messages},
        {"content-type": "application/json"}, timeout=90)
    return data.get("message", {}).get("content", "").strip()


def ask(question):
    """Returns the answer text. Raises AIError when something is wrong."""
    messages = _history[-8:] + [{"role": "user", "content": question}]
    answer = _ollama(messages) if config.AI_PROVIDER == "ollama" \
        else _anthropic(messages)
    if not answer:
        raise AIError("empty answer")
    _history.append({"role": "user", "content": question})
    _history.append({"role": "assistant", "content": answer})
    del _history[:-8]
    return answer