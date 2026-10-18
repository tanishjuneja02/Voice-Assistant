"""
nlu.py - lets the assistant understand NORMAL sentences.

The old assistant only worked when you said the exact words it expected
("open notepad", "tell me a joke"). This file sits in front of it and turns
everyday speech into one of those exact commands:

    "hey, could you please open up notepad for me"  ->  "open notepad"
    "jot down that I need to buy milk"              ->  "take a note I need to buy milk"
    "ping me in 10 minutes to stretch"              ->  "remind me in 10 minutes to stretch"
    "i'm so bored"                                  ->  "tell me a joke"
    "how much juice is left on my laptop"           ->  "battery status"

It works in 4 layers (cheap and reliable first, smart and slow last):

  1. clean()     - removes polite filler ("can you please", "for me" ...)
  2. interpret() - rewrites common patterns (notes, reminders, open app)
  3. guess()     - only used when the assistant would say "I did not
                   understand": questions, then fuzzy matching against example
                   phrases for each command (needs `pip install rapidfuzz`)
  4. ask_llm()   - OPTIONAL: asks an AI model what the sentence means.
                   Off by default. See USE_LLM below.

Test it without a microphone:   python nlu.py
"""
import os
import re

try:
    from rapidfuzz import fuzz, process
except ImportError:                      # fuzzy layer is skipped without it
    fuzz = process = None

# ---------------------------------------------------------------- settings
USE_LLM = False                          # set True to enable layer 4
LLM_MODEL = "claude-haiku-5-5"           # any Claude model name works here
FUZZY_MIN_SCORE = 80                     # 0-100. Higher = stricter matching

# ------------------------------------------------------------ 1. cleaning
# Removed from the START of a sentence (modal/polite phrases).
LEADING_POLITE = [
    "can you please", "could you please", "would you please", "will you please",
    "can you", "could you", "would you", "will you", "please", "kindly",
    "i want you to", "i would like you to", "i'd like you to",
    "i need you to", "i want to", "i would like to", "i'd like to",
    "i need to", "i wanna", "i just want to", "let's", "let us",
    "go ahead and", "do me a favour and", "do me a favor and",
    "i was wondering if you could", "is it possible to", "can i", "may i",
    "could i", "i'd like", "i would like", "i want", "i need",
]
# Removed only if at least 2 words remain (so "hello" / "hi" still greet).
LEADING_FILLER = [
    "hey assistant", "hi assistant", "hello assistant", "okay assistant",
    "ok assistant", "assistant", "jarvis", "hey", "hi", "hello", "okay", "ok",
    "so", "um", "uh", "well", "yo", "hmm", "now", "then", "just",
]
TRAILING = [
    "please", "for me", "right now", "now", "if you can", "if possible",
    "will you", "quickly", "real quick", "okay", "ok", "thanks", "thank you",
]


GREETING_ONLY = re.compile(
    r"^(?:hello|hi|hey|namaste|good (?:morning|afternoon|evening))"
    r"(?: there)?(?: (?:my )?(?:friend|buddy|mate|dude|bro|everyone|assistant|jarvis))?$")


def _strip_leading(text, phrases, min_words):
    for phrase in sorted(phrases, key=len, reverse=True):
        if text == phrase or text.startswith(phrase + " "):
            rest = text[len(phrase):].strip()
            if len(rest.split()) >= min_words:
                return rest
    return text


def _strip_trailing(text):
    for phrase in sorted(TRAILING, key=len, reverse=True):
        if text.endswith(" " + phrase):
            rest = text[: -len(phrase)].strip()
            need = 1 if phrase == "please" else 2
            if len(rest.split()) >= need:
                return rest
    return text


def clean(text):
    """Lowercase, drop punctuation and polite filler words."""
    text = re.sub(r"[?!,;]", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    if GREETING_ONLY.match(text):        # "hello there my friend" stays a greeting
        return text
    for _ in range(4):                   # a sentence can start with several
        before = text
        text = _strip_leading(text, LEADING_POLITE, 1)
        text = _strip_leading(text, LEADING_FILLER, 2)
        if text == before:
            break
    for _ in range(2):
        text = _strip_trailing(text)
    return text


# ------------------------------------------------------ 2. safe rewriting
NUM = (r"(?:\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|"
       r"twenty|thirty|forty|fifty|sixty|half an?)")
UNIT = r"(?:second|sec|minute|min|hour|hr|day)s?"
TIME_LOOK = (r"(?:in\s+" + NUM + r"\s*" + UNIT + r"|after\s+" + NUM + r"\s*" +
             UNIT + r"|at\s+\d|tomorrow|today)")


def _tidy_time(text):
    """'after 10 minutes' -> 'in 10 minutes', 'half an hour' -> '30 minutes'."""
    text = re.sub(r"\bafter (" + NUM + r"\s*" + UNIT + r")", r"in \1", text)
    text = re.sub(r"\bhalf an? hour\b", "30 minutes", text)
    text = re.sub(r"\ban? hour\b", "1 hour", text)
    return text


def _match_known(target, known_names):
    """Return the app/site name that `target` means, or None."""
    target = re.sub(r"\b(?:the|my|app|application|program|website|site|page|"
                    r"window|up|me|a|an)\b", " ", target)
    target = re.sub(r"\s+", " ", target).strip()
    if not target:
        return None
    if target in known_names:
        return target
    if fuzz is not None and len(target) >= 4:
        best = process.extractOne(target, known_names, scorer=fuzz.ratio)
        if best and best[1] >= 85:
            return best[0]
    return None


OPEN_VERBS = (r"(?:start|run|launch|load|fire up|bring up|pull up|switch to|"
              r"go to|take me to|navigate to|show me|use|get me|give me|visit|"
              r"open up)")


def rewrite(c, known_names=()):
    """Turn a cleaned sentence into a standard command, or return None."""
    known_names = list(known_names)

    # ---- notes: "write this down ...", "make a quick note ..."
    m = re.match(r"^(?:jot|write|note|put|type|scribble|save)\s+"
                 r"(?:this\s+|that\s+|it\s+)?down(?:\s+that)?\s*(.*)$", c)
    if m:
        return ("take a note " + m.group(1)).strip()
    m = re.match(r"^(?:make|create|save|keep|add|write|take|put|start)\s+"
                 r"(?:a\s+|an\s+)?(?:quick\s+|short\s+|new\s+)?note\b"
                 r"(?:\s+(?:that|saying|says|to self|about|of|for me))?\s*(.*)$", c)
    if m:
        return ("take a note " + m.group(1)).strip()
    m = re.match(r"^(?:remember|don'?t let me forget|do not let me forget|"
                 r"make sure i remember)\s+(?:that\s+|to\s+)?(.+)$", c)
    if m:
        what = m.group(1)
        if re.search(TIME_LOOK, what):   # has a time -> it is a reminder
            return "remind me to " + _tidy_time(what)
        return "take a note " + what
    m = re.match(r"^(?:what|which) (?:notes|things) did i (?:write|note|save)"
                 r"(?: down)?$|^(?:read|show|tell) me (?:back )?(?:what i "
                 r"(?:wrote|noted|saved)|my notes)$", c)
    if m:
        return "read my notes"

    # ---- reminders: "ping me in 10 minutes to stretch"
    m = re.match(r"^(?:wake me up|ping me|alert me|notify me|nudge me|buzz me|"
                 r"let me know|tell me|warn me)\s+(?=" + TIME_LOOK + r")(.+)$", c)
    if m:
        out = "remind me " + _tidy_time(m.group(1))
        if c.startswith("wake me up") and not re.search(r"\b(?:to|that|about)\b", out):
            out += " to wake up"
        return out
    m = re.match(r"^(?:set|start|create|make|add)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+|my\s+)?"
                 r"(?:new\s+)?(timer|alarm|reminder)\s+(?:for|in|at|after)\s+(.+)$", c)
    if m:
        kind, when = m.groups()
        when = _tidy_time("after " + when if re.match(NUM + r"\s*" + UNIT, when)
                          else when)
        if re.match(r"^\d", when):
            when = "at " + when
        if not re.search(r"\b(?:to|that|about)\b", when):
            label = {"timer": "check the timer", "alarm": "wake up"}.get(kind)
            if label:
                when += " to " + label
        return "remind me " + when
    if c.startswith("remind") and "after" in c:
        return _tidy_time(c)

    # ---- open apps: "fire up chrome", "go to youtube", "i want to use paint"
    m = re.match(r"^" + OPEN_VERBS + r"\s+(.+)$", c)
    if m and known_names:
        name = _match_known(m.group(1), known_names)
        if name:
            return "open " + name
    if known_names and len(c.split()) <= 2:       # just "chrome"
        name = _match_known(c, known_names)
        if name and not c.startswith("open"):
            return "open " + name
    return None


def interpret(command, known_names=()):
    """Layer 1+2. Always safe to call: returns the cleaned/rewritten command."""
    cleaned = clean(command)
    return rewrite(cleaned, known_names) or cleaned


# ------------------------------------------------ 3. last-chance guessing
# Searches and questions. Used ONLY when nothing else understood the sentence,
# so "what is the time" is never turned into a Wikipedia search.
def _explicit_knowledge(c):
    m = re.match(r"^(?:look up|find out about|find out|find|google|search for|"
                 r"search|i want to know about|i want to know|i wanna know about|"
                 r"i'?d like to know about)\s+(.+?)(?:\s+(?:online|on the internet|"
                 r"on google|on the web))?$", c)
    if m:
        return "search for " + m.group(1)
    m = re.match(r"^(?:explain|define|describe|teach me about|give me info on|"
                 r"give me information about|meaning of)\s+(.+)$", c)
    if m:
        return "tell me about " + m.group(1)
    m = re.match(r"^tell me (?:more |everything |something |a little |a bit )*"
                 r"(?:in detail )?about\s+(.+)$", c)
    if m:
        return "tell me about " + m.group(1)
    m = re.match(r"^(?:put on|i want to listen to|i want to hear|listen to|"
                 r"play me|play some|can i hear)\s+(.+)$", c)
    if m:
        return "play " + m.group(1) + " on youtube"
    return None


def _question_knowledge(c):
    m = re.match(r"^how (?:does|do|did)\s+(?:a\s+|an\s+|the\s+)?(.+?)\s+work$", c)
    if m:
        return "tell me about " + m.group(1)
    m = re.match(r"^how (?:do i|to|can i|should i|do you)\s+(.+)$", c)
    if m:
        return "search for how to " + m.group(1)
    m = re.match(r"^(?:what|who)(?:'s| is| are| was| were)\s+(?:a\s+|an\s+|the\s+)?(.+)$", c)
    if m and not re.match(r"^(?:my|your|today|up|new|going|wrong)\b", m.group(1)):
        return "tell me about " + m.group(1)
    return None


# Example sentences for each standard command. Add your own - the more
# examples, the better it understands.
INTENTS = {
    "tell me a joke": [
        "make me laugh", "say something funny", "i am bored", "i'm bored",
        "cheer me up", "got any jokes", "tell me something funny",
        "entertain me", "i need a laugh", "make me smile",
    ],
    "what time is it": [
        "what's the time now", "do you know what time it is", "got the time",
        "how late is it", "what is the time right now", "check the clock",
    ],
    "what is today's date": [
        "what's today", "which day is it today", "what day is it",
        "what is the date today", "do you know today's date",
    ],
    "battery status": [
        "how much charge is left", "is my laptop charged", "how much juice is left",
        "battery level", "is my battery low", "how much power do i have left",
    ],
    "cpu usage": [
        "is my pc slow", "how is my computer doing", "how much memory is being used",
        "how is my laptop performing", "is my computer running fine",
    ],
    "take a screenshot": [
        "grab my screen", "snap my screen", "save what is on my screen",
        "capture this screen", "screen grab", "copy my screen",
    ],
    "show desktop": [
        "minimize everything", "hide all windows", "go to the desktop",
        "clear the screen", "take me to the desktop",
    ],
    "volume up": [
        "it's too quiet", "i can't hear anything", "turn it up", "make it louder",
        "raise the sound", "i cannot hear you", "speak louder",
        "the volume is too low", "the sound is too low",
    ],
    "volume down": [
        "it's too loud", "turn it down", "make it quieter", "lower the sound",
        "that is too loud", "reduce the sound", "the volume is too high",
        "the sound is too loud",
    ],
    "mute": [
        "be quiet", "silence please", "turn the sound off", "turn off the sound",
        "no sound", "kill the volume",
    ],
    "read my notes": [
        "what did i write down", "show me what i noted", "what notes do i have",
        "read back my notes", "what did i save", "do i have any notes",
    ],
    "what are my reminders": [
        "what do i have to do", "what's coming up", "do i have anything scheduled",
        "am i forgetting something", "what is on my schedule", "what's pending",
        "do i have any reminders",
    ],
    "what can you do": [
        "what are your features", "how can you help me", "what are you capable of",
        "what commands do you know", "what can i ask you", "how do i use you",
    ],
    "who are you": [
        "what are you", "tell me about yourself", "what should i call you",
        "who made you", "introduce yourself",
    ],
    "how are you": [
        "how are you doing", "how is it going", "how do you feel", "are you okay",
        "how have you been",
    ],
    "thank you": [
        "that was helpful", "thanks a lot", "cheers", "great job", "well done",
        "that's great thanks", "awesome thanks", "cheers mate",
    ],
    "hello": [
        "good day", "what's up", "howdy", "greetings", "hey there", "hi there",
        "are you there", "you there",
    ],
    "stop": [
        "that's all", "goodbye", "see you later", "bye", "i'm done", "quit",
        "close the assistant", "that will be all", "shut down", "bye bye",
    ],
    "test the alarm": [
        "play the alarm sound", "check if the alarm works", "ring the alarm",
    ],
    "what can you open": [
        "which apps can you launch", "what programs can you start",
        "which apps are allowed", "list the apps", "what apps do you have",
    ],
}

_FLAT = [(phrase, intent) for intent, phrases in INTENTS.items()
         for phrase in phrases]
_EXACT = {phrase: intent for phrase, intent in _FLAT}

# Little words that carry no meaning. A match must share a real word.
STOP = {"the", "a", "an", "is", "are", "it", "its", "it's", "my", "me", "i",
        "i'm", "to", "you", "your", "of", "in", "on", "at", "do", "does", "can",
        "what", "what's", "how", "that", "this", "there", "so", "am", "be",
        "for", "and", "have", "has", "any", "some", "now", "right", "too",
        "much", "all", "up", "down", "off"}


def _words(text):
    return [w for w in re.findall(r"[a-z']+", text) if w not in STOP]


def _score(c, phrase):
    """How close is the sentence to an example? 0-100."""
    base = max(fuzz.token_sort_ratio(c, phrase), fuzz.ratio(c, phrase),
               fuzz.partial_ratio(c, phrase) if len(phrase) >= 9 else 0)
    wanted = _words(phrase)
    if wanted:                           # must share at least one real word
        if not any(fuzz.ratio(a, b) >= 88 for a in _words(c) for b in wanted):
            return 0
        return base
    return base if base >= 90 else 0     # phrases made of little words only


def _fuzzy_intent(c):
    """Find the standard command whose examples are closest to `c`."""
    if c in _EXACT:
        return _EXACT[c]
    if fuzz is None or len(c) < 4:
        return None
    scores = {}                          # best score per command
    for phrase, intent in _FLAT:
        score = _score(c, phrase)
        if score > scores.get(intent, 0):
            scores[intent] = score
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best[1] >= FUZZY_MIN_SCORE and best[1] - runner_up >= 3:
        return best[0]
    return None


# ----------------------------------------------------- 4. optional AI layer
LLM_PROMPT = """You convert one spoken sentence into ONE command for a desktop
voice assistant. Allowed commands (replace <...> with the real words):
take a note <text> | read my notes | remind me <when> to <task> |
what are my reminders | open <app> | tell me a joke | what time is it |
what is today's date | battery status | cpu usage | take a screenshot |
volume up | volume down | mute | show desktop | search for <query> |
play <song> on youtube | tell me about <topic> | start dictation | stop
Reply with ONLY the command, in lowercase. If none fits, reply NONE."""


def ask_llm(text):
    """Ask Claude what the sentence means. Returns a command or None."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not USE_LLM or not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        reply = client.messages.create(
            model=LLM_MODEL, max_tokens=60, system=LLM_PROMPT,
            messages=[{"role": "user", "content": text}])
        out = reply.content[0].text.strip().strip('"').strip().lower()
    except Exception as e:               # no internet, bad key, ... never crash
        print("AI fallback failed:", e)
        return None
    if not out or out == "none" or out.startswith(("delete", "clear", "remove")):
        return None                      # never let the AI delete things
    return out


def guess(command, known_names=()):
    """Layer 3+4. Called only when the assistant did not understand."""
    c = clean(command)
    return (_explicit_knowledge(c)
            or _fuzzy_intent(c)
            or _question_knowledge(c)
            or ask_llm(command))


# ------------------------------------------------------------ self test
if __name__ == "__main__":
    APPS = ["notepad", "calculator", "paint", "chrome", "command prompt",
            "file explorer", "settings", "vs code", "youtube", "google", "gmail"]
    print("Type a sentence to see how it is understood (empty line to quit).\n")
    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        step1 = interpret(line, APPS)
        print("  cleaned/rewritten ->", step1)
        print("  last-chance guess ->", guess(line, APPS))