import array
import math
import os
import random
import re
import time
import threading
import sqlite3
import subprocess
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import speech_recognition as sr
import pyttsx3

import nlu          # understands normal sentences (see nlu.py)

LANGUAGE = "en-IN"   # Indian English. If recognition gets worse, try "en-US"

DICTATION_DIR = "dictations"      # dictation text files are saved here
AUTO_OPEN_DICTATION = True        # open the saved file in Notepad automatically
SCREENSHOT_DIR = "screenshots"    # screenshots are saved here

recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = 1.2   # allow short pauses inside a sentence


# ---------------- MICROPHONE ----------------
def find_mic():
    """Automatically find the laptop microphone."""
    skip = ("hands-free", "headset", "output", "mapper",
            "speakers", "headphones", "stereo mix")
    for i, name in enumerate(sr.Microphone.list_microphone_names()):
        n = name.lower()
        if "microphone" in n and not any(s in n for s in skip):
            try:
                with sr.Microphone(device_index=i):
                    return i
            except Exception:
                continue
    return None


MIC_INDEX = find_mic()


# ---------------- SPEAK / LISTEN ----------------
_speak_lock = threading.Lock()   # stops two voices talking at once
_com = threading.local()

EVENT_HOOK = None                # the window (gui.py) sets this to get updates


def emit(kind, text=""):
    """Tell the window (if there is one) what is happening."""
    if EVENT_HOOK:
        try:
            EVENT_HOOK(kind, text)
        except Exception:
            pass


def ensure_com():
    """The Windows voice needs COM started once in every thread."""
    if getattr(_com, "ready", False):
        return
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    _com.ready = True


def speak(text):
    print("Assistant:", text)
    emit("assistant", text)
    emit("status", "speaking")
    ensure_com()
    with _speak_lock:
        engine = pyttsx3.init()

        def on_word(name, location=0, length=4):
            # one pulse per spoken word -> drives the assistant's sound wave
            try:
                pulse = min(1.0, 0.4 + (length or 4) * 0.07 + random.random() * 0.25)
            except Exception:
                pulse = 0.7
            emit("level_assistant", f"{pulse:.2f}")

        try:
            engine.connect("started-word", on_word)
        except Exception:
            pass                       # the wave just stays calm
        engine.say(text)
        engine.runAndWait()
    emit("status", "idle")


class _LevelStream:
    """Wraps the microphone stream and reports how loud you are, so the
    window can draw your voice as a moving wave."""

    def __init__(self, stream):
        self._stream = stream

    def read(self, size):
        data = self._stream.read(size)
        try:
            samples = array.array("h", data)          # 16-bit audio
            if samples:
                rms = math.sqrt(sum(x * x for x in samples) / len(samples))
                emit("level_user", f"{rms:.1f}")
        except Exception:
            pass
        return data

    def close(self):
        self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def listen():
    with sr.Microphone(device_index=MIC_INDEX) as source:
        source.stream = _LevelStream(source.stream)
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("Listening...")
        emit("status", "listening")
        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=12)
        except sr.WaitTimeoutError:
            emit("status", "idle")
            return ""
    emit("status", "thinking")
    try:
        text = recognizer.recognize_google(audio, language=LANGUAGE)
        print("You said:", text)
        emit("user", text)
        return text.lower()
    except sr.UnknownValueError:
        emit("status", "idle")
        return ""
    except sr.RequestError:
        speak("I cannot reach the internet")
        return ""


# ---------------- DATABASE ----------------
DB = "assistant.db"


def init_db():
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS notes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        created TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS reminders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        due TEXT,
        done INTEGER DEFAULT 0)""")
    db.execute("""CREATE TABLE IF NOT EXISTS dictations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file TEXT,
        text TEXT,
        created TEXT)""")
    db.commit()
    db.close()


# ---------------- VOICE NOTES ----------------
NOTE_PREFIXES = ("take a note", "make a note", "add a note",
                 "write a note", "note down", "note")


def extract_note(command):
    """Return the note text if the command is a 'take a note' command, else None."""
    for prefix in NOTE_PREFIXES:
        if command == prefix or command.startswith(prefix + " "):
            text = command[len(prefix):].strip()
            if text.startswith("that "):
                text = text[5:]
            return text
    return None


def save_note(text):
    if not text:
        speak("What should I write in the note?")
        return
    db = sqlite3.connect(DB)
    db.execute("INSERT INTO notes(text, created) VALUES(?, ?)",
               (text, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    db.close()
    speak("Note saved")


def read_notes():
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT text, created FROM notes ORDER BY id DESC LIMIT 5").fetchall()
    db.close()
    if not rows:
        speak("You have no notes")
        return
    speak(f"You have {len(rows)} recent notes")
    for number, (text, created) in enumerate(rows, start=1):
        print(f"  {number}. [{created}] {text}")
        speak(text)


def delete_notes():
    db = sqlite3.connect(DB)
    db.execute("DELETE FROM notes")
    db.commit()
    db.close()
    speak("All notes deleted")


# ---------------- REMINDERS ----------------
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60,
}
UNITS = {
    "second": "seconds", "sec": "seconds",
    "minute": "minutes", "min": "minutes",
    "hour": "hours", "hr": "hours",
    "day": "days",
}

# Patterns that find the time part inside a sentence
DAY = r"(?:today|tomorrow)"
AMPM = r"(?:am|pm)\b"
CLOCK = (r"(?:(?:at|for)\s+\d{1,2}(?::\d{2})?(?:\s*" + AMPM + r")?"
         r"|\d{1,2}(?::\d{2})?\s*" + AMPM +
         r"|\d{1,2}:\d{2})")
REL = r"in (?:\d+|[a-z]+) (?:second|sec|minute|min|hour|hr|day)s?\b"
WHEN_RE = re.compile(r"\b(?:" + REL + r"|in \d+\s*$|(?:" + DAY + r"\s+)?" + CLOCK +
                     r"(?:\s+" + DAY + r")?|" + DAY + r")")
REMIND_RE = re.compile(r"\b(?:remind me|reminders?)\b")
REL_FULL = re.compile(
    r"^(?:in )?(\d+|[a-z]+) (second|sec|minute|min|hour|hr|day)s?$")
REL_BARE = re.compile(r"^in (\d+)$")          # "in 5" -> 5 minutes
FALLBACK_WORDS = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|"
    r"afternoon|evening|night|tonight|noon|midnight|next|week|weekend)\b")
TIME_FULL = re.compile(
    r"^(?:(today|tomorrow)\s+)?(?:(?:at|for)\s+)?(\d{1,2})(?::(\d{2}))?"
    r"\s*(am|pm)?(?:\s+(today|tomorrow))?$")


def normalize_speech(text):
    """'5 P.M.' -> '5pm', lowercase, trimmed."""
    text = text.lower().strip()
    text = re.sub(r"(\d)\s*([ap])\.?\s?m\b\.?", r"\1\2m", text)
    # Google often writes 1:35 pm as "135 pm" -> put the colon back
    return re.sub(r"\b(\d{1,2})(\d{2})(?=\s*[ap]m\b)", r"\1:\2", text)


def parse_when(when_text, now=None):
    """Turn 'in 5 minutes' / 'at 5pm' / 'tomorrow at 9am' into a datetime."""
    now = now or datetime.now()
    when_text = normalize_speech(when_text).strip(" .")

    # 1) relative: "in 5 minutes", "5 minutes", "in an hour", "in 5"
    m = REL_BARE.match(when_text)
    if m:
        return now + timedelta(minutes=int(m.group(1)))
    m = REL_FULL.match(when_text)
    if m:
        amount = m.group(1)
        amount = int(amount) if amount.isdigit() else NUMBER_WORDS.get(amount)
        if amount:
            return now + timedelta(**{UNITS[m.group(2)]: amount})
        return None

    # 2) clock time: "at 5pm", "1:20pm", "tomorrow at 9am", "1:20" (no am/pm)
    m = TIME_FULL.match(when_text)
    if m:
        day = m.group(1) or m.group(5)
        hour, minute, ampm = int(m.group(2)), int(m.group(3) or 0), m.group(4)
        if minute > 59:
            return None
        if ampm:
            if not 1 <= hour <= 12:
                return None
            candidates = [hour % 12 + (12 if ampm == "pm" else 0)]
        elif hour == 0 or hour > 12:          # 24-hour clock, e.g. 13:45
            if hour > 23:
                return None
            candidates = [hour]
        else:                                 # no am/pm: try both
            candidates = [hour % 12, hour % 12 + 12]
        base = now + timedelta(days=1) if day == "tomorrow" else now
        times = [base.replace(hour=c, minute=minute, second=0, microsecond=0)
                 for c in candidates]
        if day == "tomorrow":
            return min(times)
        future = [t for t in times if t > now]
        return min(future) if future else min(times) + timedelta(days=1)

    # 3) a few word-based times, e.g. "tomorrow morning", "next monday"
    if not FALLBACK_WORDS.search(when_text):
        return None
    try:
        import dateparser
        when = dateparser.parse(re.sub(r"^(at|on|for) ", "", when_text),
                                settings={"PREFER_DATES_FROM": "future"})
    except Exception:
        when = None
    if when is not None and now < when <= now + timedelta(days=60):
        return when
    return None


def parse_when_loose(text):
    """Like parse_when, but finds the time inside a longer answer."""
    text = normalize_speech(text)
    when = parse_when(text)
    if when is None:
        m = WHEN_RE.search(text)
        if m:
            when = parse_when(m.group(0))
    return when


def split_reminder(rest):
    """Split 'to study at 5pm' into (when_text, task). Either can be None."""
    rest = rest.strip()
    m = WHEN_RE.search(rest)
    when_text = m.group(0) if m else None
    task = (rest[:m.start()] + " " + rest[m.end():]) if m else rest
    task = re.sub(r"\s+", " ", task).strip(" ,.")
    task = re.sub(r"^(to|that)(\s+|$)", "", task)
    task = re.sub(r"\s+(at|in|on|for)$", "", task).strip()
    task = re.sub(r"^(at|in|on|for)$", "", task)
    return when_text, (task or None)


def fmt_time(dt):
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_when(dt):
    today = datetime.now().date()
    if dt.date() == today:
        return fmt_time(dt)
    if dt.date() == today + timedelta(days=1):
        return "tomorrow at " + fmt_time(dt)
    return f"{dt.day} {dt.strftime('%B')} at " + fmt_time(dt)


def set_reminder(command):
    command = normalize_speech(command)
    m = REMIND_RE.search(command)
    rest = command[m.end():] if m else command
    when_text, task = split_reminder(rest)

    # ask for whatever is missing
    if not task:
        speak("What should I remind you about?")
        answer = normalize_speech(listen())
        if not answer:
            speak("Sorry, I did not catch that. Reminder cancelled")
            return
        w, t = split_reminder(answer)
        task = t or answer
        when_text = when_text or w

    when = parse_when(when_text) if when_text else None
    attempts = 0
    while when is None and attempts < 2:
        if attempts == 0 and not when_text:
            speak("When should I remind you?")
        else:
            speak("Sorry, I did not understand the time. Please say it "
                  "again, like: 5 pm, or in 10 minutes")
        answer = normalize_speech(listen())
        attempts += 1
        if answer:
            m2 = REMIND_RE.search(answer)
            w, t = split_reminder(answer[m2.end():] if m2 else answer)
            when = parse_when(w) if w else None
            if when and t:
                task = t                 # you repeated the whole sentence
            if when is None:
                when = parse_when_loose(answer)
    if when is None:
        speak("Sorry, I could not understand the time. Reminder cancelled")
        return

    db = sqlite3.connect(DB)
    db.execute("INSERT INTO reminders(text, due) VALUES(?, ?)",
               (task, when.strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()
    db.close()
    speak(f"Okay, I will remind you to {task}, {fmt_when(when)}")


DB_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
STOP_WORDS = {"the", "and", "for", "that", "this", "with", "reminder",
              "reminders", "remind", "delete", "remove", "cancel", "clear",
              "please", "tomorrow", "today", "change", "edit", "update",
              "rename", "timer"}
NUMBER_ONLY = r"\d+|one|two|three|four|five|six|first|second|third|fourth|fifth"


def pending_reminders():
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT id, text, due FROM reminders WHERE done=0 "
        "ORDER BY due, id").fetchall()
    db.close()
    return rows


def list_reminders():
    rows = pending_reminders()
    if not rows:
        speak("You have no pending reminders")
        return
    word = "reminder" if len(rows) == 1 else "reminders"
    speak(f"You have {len(rows)} pending {word}")
    for number, (_, text, due) in enumerate(rows, start=1):
        when = datetime.strptime(due, DB_TIME_FORMAT)
        speak(f"Number {number}: {text}, {fmt_when(when)}")


def clear_reminders():
    db = sqlite3.connect(DB)
    db.execute("DELETE FROM reminders WHERE done=0")
    db.commit()
    db.close()
    speak("All pending reminders cleared")


def delete_one_reminder(rid, text):
    db = sqlite3.connect(DB)
    db.execute("DELETE FROM reminders WHERE id=?", (rid,))
    db.commit()
    db.close()
    speak(f"Deleted the reminder to {text}")


def same_clock(due, when):
    """True if a saved due-time has the same hour and minute (am/pm ignored)."""
    d = datetime.strptime(due, DB_TIME_FORMAT)
    return (d.hour % 12, d.minute) == (when.hour % 12, when.minute)


def find_reminders(command, rows):
    """Pick reminders by number, 'last', or by words / time in the sentence.
    Returns (chosen_list, number_out_of_range)."""
    m = (re.search(r"\b(?:number|no)\s*(\d+|one|two|three|four|five|six)\b",
                   command)
         or re.search(r"\b(first|second|third|fourth|fifth)\b", command))
    if m:
        word = m.group(1)
        n = int(word) if word.isdigit() else ORDINALS[word]
        if 1 <= n <= len(rows):
            return [rows[n - 1]], False
        return [], True

    if re.search(r"\b(last|latest|recent)\b", command):
        return [max(rows, key=lambda r: r[0])], False

    said = set(re.findall(r"[a-z]+", command))
    chosen = []
    for r in rows:
        key = [w for w in re.findall(r"[a-z]+", r[1].lower())
               if len(w) > 2 and w not in STOP_WORDS]
        if key and all(w in said for w in key):
            chosen.append(r)

    t = WHEN_RE.search(command)
    when = parse_when(t.group(0)) if t else None
    if when:
        chosen = [r for r in (chosen or rows) if same_clock(r[2], when)]
    return chosen, False


def delete_reminders(command):
    """'delete all reminders' / 'delete reminder number 2' /
    'delete the study reminder' / 'delete the last reminder'."""
    command = normalize_speech(command)
    rows = pending_reminders()
    if not rows:
        speak("You have no pending reminders")
        return

    if re.search(r"\b(all|every|everything)\b", command):
        clear_reminders()
        return

    chosen, out_of_range = find_reminders(command, rows)
    if out_of_range:
        speak(f"You only have {len(rows)} reminders")
    elif len(chosen) == 1:
        delete_one_reminder(chosen[0][0], chosen[0][1])
    elif len(chosen) > 1:
        speak("I found more than one match. Say: delete reminder number, "
              "and the number")
        list_reminders()
    elif re.search(r"\breminders\b", command):    # "delete the reminders"
        clear_reminders()
    else:
        speak("Which reminder should I delete? Say: delete reminder number "
              "2, or: delete all reminders")
        list_reminders()


# ---------------- EDIT REMINDERS ----------------
EDIT_RE = re.compile(
    r"^(?:(?:can|could) you |please |i want to |i wanna |i would like to |"
    r"let me )*(?:change|edit|update|modify|rename|reschedule|postpone|"
    r"shift|move)\b")


def wants_new_reminder(command):
    """'remind me ...', 'set a reminder ...', 'reminder to study at 5pm' ->
    True.  Just 'the first reminder' -> False (we show the list instead)."""
    text = normalize_speech(command)
    return bool(
        re.search(r"\b(remind me|want|get|need|make|set|add|create|give|"
                  r"schedule|new)\b", text)
        or WHEN_RE.search(text)
        or re.search(r"reminder to\b", text))


def is_edit_reminder(command):
    return bool(EDIT_RE.match(command)) and bool(
        re.search(r"remind|timer", command))


def edit_reminder(command):
    """Change the time, the name, or both.
    'change reminder number 1 to 6 pm'
    'rename the study reminder to physics homework'
    'change reminder number 2 to call mom at 7 pm'   (both)
    'edit my reminder'  (it asks you step by step)"""
    command = normalize_speech(command)
    rows = pending_reminders()
    if not rows:
        speak("You have no pending reminders")
        return

    # split "<which reminder>" from "<new value>" at the first " to "
    m = re.search(r"\s(?:to|into|with|as)\s", command)
    target_part = command[:m.start()] if m else command
    new_part = command[m.end():].strip() if m else ""

    # 1) which reminder?
    chosen, out_of_range = find_reminders(target_part, rows)
    if out_of_range:
        speak(f"You only have {len(rows)} reminders")
        return
    if len(chosen) > 1:
        speak("I found more than one match. Say: change reminder number, "
              "and the number")
        list_reminders()
        return
    if not chosen and len(rows) == 1:
        chosen = [rows[0]]                    # only one reminder exists
    if not chosen:
        speak("Which reminder do you want to change? Say its number")
        list_reminders()
        answer = normalize_speech(listen())
        if not answer:
            speak("Sorry, I did not catch that. Nothing changed")
            return
        if re.fullmatch(NUMBER_ONLY, answer):
            answer = "number " + answer
        chosen, out_of_range = find_reminders(answer, rows)
        if out_of_range or len(chosen) != 1:
            speak("Sorry, I could not find that reminder. Nothing changed")
            return
    rid, old_text, old_due = chosen[0]

    # 2) what to change?
    want_time = re.search(
        r"\b(time|reschedule|postpone|shift|move)\b", command)
    want_name = re.search(
        r"\b(rename|name|task|title|text|message|called)\b", command)
    when_text = new_text = None

    def read_new(text):
        if want_name and not want_time:
            return None, re.sub(r"^(to|that|called)\s+", "", text).strip(" ,.")
        if want_time and not want_name:
            return text, None
        w, t = split_reminder(text)
        if w is None and t and re.fullmatch(r"\d{1,2}(:\d{2})?", t):
            return t, None                    # just "5" -> a time
        return w, t

    if new_part:
        when_text, new_text = read_new(new_part)
    if not when_text and not new_text:
        speak("What should I change it to? Say the new time, the new name, "
              "or both")
        answer = normalize_speech(listen())
        if not answer:
            speak("Sorry, I did not catch that. Nothing changed")
            return
        when_text, new_text = read_new(answer)

    # 3) apply
    new_due = old_due
    if when_text:
        when = parse_when(when_text)
        if when is None:
            speak("Sorry, I could not understand the time. Nothing changed")
            return
        new_due = when.strftime(DB_TIME_FORMAT)
    new_text = new_text or old_text

    db = sqlite3.connect(DB)
    db.execute("UPDATE reminders SET text=?, due=?, done=0 WHERE id=?",
               (new_text, new_due, rid))
    db.commit()
    db.close()
    speak(f"Done. Reminder to {new_text}, "
          f"{fmt_when(datetime.strptime(new_due, DB_TIME_FORMAT))}")


def make_alert_wav(beeps=3, beep_ms=300, gap_ms=150, volume=0.9, rate=22050):
    """Build the alarm sound in memory (no sound file needed)."""
    import io
    import math
    import struct
    import wave
    frames = bytearray()
    fade = int(rate * 0.01)                      # fade in/out stops clicks
    for b in range(beeps):
        freq = 800 + 200 * b                     # rising tones: 800, 1000, 1200 Hz
        n = int(rate * beep_ms / 1000)
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            value = volume * env * math.sin(2 * math.pi * freq * i / rate)
            frames += struct.pack("<h", int(value * 32767))
        frames += b"\x00\x00" * int(rate * gap_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


_ALERT_WAV = None


def play_alert_sound():
    """Plays through the normal Windows audio (same path as the voice)."""
    global _ALERT_WAV
    try:
        import winsound
    except ImportError:
        print("\a")
        return
    try:
        if _ALERT_WAV is None:
            _ALERT_WAV = make_alert_wav()
        winsound.PlaySound(_ALERT_WAV, winsound.SND_MEMORY)
        return
    except Exception as e:
        print("Alert sound problem:", e)
    try:                                         # old-style backup beep
        for _ in range(3):
            winsound.Beep(1000, 250)
    except Exception:
        pass


def show_popup(text):
    try:
        from plyer import notification
        notification.notify(title="Voice Assistant Reminder",
                            message=text, timeout=10)
    except Exception:
        pass


def alert(text):
    """What happens when a reminder is due: popup + alarm sound + voice."""
    print(f"\n*** REMINDER: {text} ***")
    emit("reminder", text)
    threading.Thread(target=show_popup, args=(text,), daemon=True).start()
    play_alert_sound()
    speak(f"Reminder: {text}")


def reminder_checker():
    """Runs in the background and fires reminders when they are due."""
    try:
        import pythoncom
        pythoncom.CoInitialize()        # lets the voice work inside a thread
    except Exception:
        pass
    while True:
        try:
            db = sqlite3.connect(DB)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = db.execute(
                "SELECT id, text FROM reminders WHERE done=0 AND due<=?",
                (now,)).fetchall()
            for rid, text in rows:
                db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
                db.commit()
                alert(text)
            db.close()
        except Exception as e:
            print("Reminder checker error:", e)
        time.sleep(3)


# ---------------- DICTATION (speech-to-text) ----------------
SPOKEN_PUNCTUATION = [
    (r"\bnew paragraph\b", "\n\n"),
    (r"\b(?:new line|next line)\b", "\n"),
    (r"\bfull stop\b", "."),
    (r"\bcomma\b", ","),
    (r"\bquestion mark\b", "?"),
    (r"\b(?:exclamation mark|exclamation point)\b", "!"),
    (r"\bcolon\b", ":"),
]
STOP_DICT = re.compile(
    r"\b(?:stop|end|finish|complete)\s+(?:the\s+|my\s+)?dictat\w*"
    r"|\bend of dictation\b", re.I)
CANCEL_DICT = re.compile(
    r"\b(?:cancel|discard|abort)\s+(?:the\s+|my\s+)?dictat\w*", re.I)
UNDO_DICT = re.compile(
    r"(?:delete|remove|undo|scratch)(?: that| the last \w+| last \w+)?", re.I)
DICTATION_RE = re.compile(r"dictat|voice typing|speech.to.text")


def apply_spoken_punctuation(text):
    """'hello comma how are you question mark' -> 'hello , how are you ?'"""
    for pattern, symbol in SPOKEN_PUNCTUATION:
        text = re.sub(pattern, symbol, text, flags=re.I)
    return text


def tidy_dictation(text):
    """Clean spacing and capital letters in the finished text."""
    text = re.sub(r"[ \t]+([.,?!:;])", r"\1", text)      # no space before . , ?
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\bi\b", "I", text)                   # i -> I, i'm -> I'm
    text = re.sub(r"(^|[.?!]\s+|\n)([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip()


def process_dictation_phrase(phrase, parts):
    """Handle one spoken phrase. Returns 'continue', 'stop' or 'cancel'."""
    phrase = phrase.strip()
    if CANCEL_DICT.search(phrase):
        return "cancel"
    if UNDO_DICT.fullmatch(phrase.lower()):
        if parts:
            print("   (removed:", parts.pop().strip(), ")")
        return "continue"
    m = STOP_DICT.search(phrase)
    content = phrase[:m.start()] if m else phrase
    content = apply_spoken_punctuation(content).strip(" \t")
    if content:                               # keeps new lines / paragraphs
        parts.append(content)
    return "stop" if m else "continue"


def save_dictation(text):
    os.makedirs(DICTATION_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(DICTATION_DIR, f"dictation_{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    db = sqlite3.connect(DB)
    db.execute("INSERT INTO dictations(file, text, created) VALUES(?, ?, ?)",
               (path, text, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    db.close()
    return path


def open_file(path):
    try:
        os.startfile(os.path.abspath(path))      # opens in Notepad on Windows
    except Exception as e:
        print("Could not open the file:", e)


def dictate():
    """Listen continuously, turn speech into text, save it to a .txt file."""
    speak("Dictation started. Speak now. Say stop dictation when you are "
          "finished.")
    parts = []
    outcome = "stop"
    quiet_rounds = 0
    old_pause = recognizer.pause_threshold
    recognizer.pause_threshold = 1.5             # allow longer pauses
    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            source.stream = _LevelStream(source.stream)
            recognizer.adjust_for_ambient_noise(source, duration=0.7)
            print("\n--- DICTATING --- (say 'stop dictation' to finish)")
            emit("status", "listening")
            while True:
                try:
                    audio = recognizer.listen(source, timeout=10,
                                              phrase_time_limit=30)
                    phrase = recognizer.recognize_google(audio,
                                                         language=LANGUAGE)
                except (sr.WaitTimeoutError, sr.UnknownValueError):
                    quiet_rounds += 1
                    if quiet_rounds >= 3:        # about 30 seconds of nothing
                        outcome = "silence"
                        break
                    continue
                except sr.RequestError:
                    outcome = "offline"
                    break
                quiet_rounds = 0
                print("  >", phrase)
                emit("dictation", phrase)
                action = process_dictation_phrase(phrase, parts)
                if action != "continue":
                    outcome = action
                    break
    finally:
        recognizer.pause_threshold = old_pause

    if outcome == "cancel":
        speak("Dictation cancelled. Nothing was saved")
        return
    text = tidy_dictation(" ".join(parts))
    if not text:
        speak("I did not hear anything to save")
        return
    if outcome == "silence":
        speak("I did not hear you for a while, so I stopped")
    elif outcome == "offline":
        speak("I lost the internet connection. Saving what I have")
    path = save_dictation(text)
    print("\n----- YOUR TEXT -----\n" + text + "\n---------------------")
    speak(f"Dictation saved. {len(text.split())} words")
    print("Saved to:", path)
    if AUTO_OPEN_DICTATION:
        open_file(path)


def read_last_dictation():
    db = sqlite3.connect(DB)
    row = db.execute(
        "SELECT text FROM dictations ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    if not row:
        speak("You have no dictations yet")
        return
    speak("Your last dictation says")
    speak(row[0][:600])


def open_last_dictation():
    db = sqlite3.connect(DB)
    row = db.execute(
        "SELECT file FROM dictations ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    if not row or not os.path.exists(row[0]):
        speak("I could not find a saved dictation")
        return
    speak("Opening your last dictation")
    open_file(row[0])


# ---------------- APPROVED APPLICATIONS ----------------
# Only the apps and websites listed here can ever be opened.
APPROVED_APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "chrome": "chrome",
    "command prompt": "cmd",
    "file explorer": "explorer",
    "settings": "ms-settings:",
    "vs code": "code",
}

APPROVED_SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
}

# Words Google often hears differently
ALIASES = {
    "crome": "chrome",
    "calc": "calculator",
    "cmd": "command prompt",
    "terminal": "command prompt",
    "explorer": "file explorer",
    "files": "file explorer",
    "visual studio code": "vs code",
    "vscode": "vs code",
    "code": "vs code",
    "you tube": "youtube",
    "mspaint": "paint",
    "note pad": "notepad",
}


def open_app(name):
    fillers = ("open", "launch", "the", "my", "app", "application", "please")
    name = " ".join(w for w in name.split() if w not in fillers)
    name = ALIASES.get(name, name)
    if not name:
        return

    if name in APPROVED_APPS:
        speak(f"Opening {name}")
        subprocess.Popen(f'start "" {APPROVED_APPS[name]}', shell=True)
    elif name in APPROVED_SITES:
        speak(f"Opening {name}")
        webbrowser.open(APPROVED_SITES[name])
    else:
        speak(f"Sorry, {name} is not in the approved list")


def open_apps(text):
    """Handles 'paint and file explorer', 'chrome as well', etc."""
    text = re.sub(r"\b(as well|also|too)\b", "", text)
    for part in re.split(r"\band\b|,", text):
        open_app(part.strip())


def list_approved():
    names = list(APPROVED_APPS) + list(APPROVED_SITES)
    speak("I can open " + ", ".join(names))


def is_list_request(command):
    """True for phrases like 'what can you open', 'list that I can open'."""
    asking = any(w in command for w in
                 ("list", "what", "which", "things", "show", "tell"))
    about_apps = any(w in command for w in
                     ("open", "approved", "access", "apps"))
    return asking and about_apps


# ---------------- EXTRA COMMANDS ----------------
CHEAT_SHEET = """
===================== VOICE COMMANDS =====================
 Notes      take a note ...  |  read my notes  |  delete all notes
 Reminders  remind me in 5 minutes to ...  |  what are my reminders
            change reminder number 1 to 6 pm  |  delete reminder number 1
 Apps       open notepad / calculator / chrome ...  |  what can you open
 Dictation  start dictation ... stop dictation  |  read my last dictation
 Info       what time is it  |  today's date  |  battery status  |  cpu usage
 Web        search google for ...  |  play ... on youtube  |  tell me about ...
 System     take a screenshot  |  volume up / down  |  mute  |  show desktop
 Fun        tell me a joke  |  test the alarm
 Stop       stop
 Tip        you can also talk normally: "can you remind me to study at 6",
            "i'm bored", "how much battery is left", "jot down buy milk"
==========================================================
"""


def classify_extra(command):
    """Decide which extra command this is. Returns (name, argument)."""
    c = command.strip()

    if (re.fullmatch(r"help|help me", c)
            or re.search(r"\b(?:what can you do|your features|show commands|"
                         r"list commands|list of commands)\b", c)):
        return "help", None
    if re.search(r"\b(?:who are you|your name|introduce yourself)\b", c):
        return "intro", None
    if re.search(r"\bhow are you\b", c):
        return "how_are_you", None
    if re.search(r"\bthank(?:s| you)\b", c):
        return "thanks", None
    if len(c.split()) <= 4 and re.search(
            r"\b(?:hello|hi|hey|namaste|good (?:morning|afternoon|evening))\b", c):
        return "greet", None

    m = re.match(r"(?:search(?: on)? (?:google|the web|web)(?: for)?|"
                 r"google search(?: for)?|search for|search|google) (.+)$", c)
    if m:
        return "google", m.group(1).strip()
    m = re.match(r"play (.+?)(?: on youtube| in youtube| youtube)?$", c)
    if m:
        return "youtube", m.group(1).strip()
    m = re.match(r"(?:tell me about|who is|who was|who are|wikipedia|"
                 r"search wikipedia for|what is the meaning of) (.+)$", c)
    if m:
        return "wiki", m.group(1).strip()

    if re.search(r"\bwhat(?:'s| is)? the time\b|\btime is it\b|"
                 r"\bcurrent time\b|\btell me the time\b|^time$", c):
        return "time", None
    if re.search(r"today'?s date|\bthe date\b|\bwhat day\b|\bcurrent date\b|"
                 r"\bdate today\b|^date$", c):
        return "date", None
    if re.search(r"\bbattery\b", c):
        return "battery", None
    if re.search(r"\b(?:cpu|ram|processor|system status|memory usage|"
                 r"performance)\b", c):
        return "system", None
    if re.search(r"\bscreen ?shot\b|\bscreen capture\b|"
                 r"\bcapture (?:the |my )?screen\b", c):
        return "screenshot", None
    if re.search(r"\b(?:volume|mute|unmute|louder|quieter)\b", c):
        return "volume", c
    if re.search(r"\bjokes?\b", c):
        return "joke", None
    if re.search(r"\bshow (?:the )?desktop\b|"
                 r"\bminimi[sz]e (?:all )?(?:the )?windows\b", c):
        return "desktop", None
    return None, None


def time_message(now):
    return f"It is {fmt_time(now)}"


def date_message(now):
    return f"Today is {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}"


def battery_message(percent, plugged, secs_left=None):
    percent = round(percent)
    if plugged:
        msg = f"Battery is at {percent} percent and charging"
    else:
        msg = f"Battery is at {percent} percent and not charging"
        if secs_left and secs_left > 0:
            h, m = divmod(secs_left // 60, 60)
            msg += f", about {h} hours {m} minutes left"
        if percent < 20:
            msg += ". Battery is low, please plug in the charger"
    return msg


def system_message(cpu, ram):
    return (f"CPU usage is {round(cpu)} percent "
            f"and memory usage is {round(ram)} percent")


def clean_summary(text):
    """Remove (brackets) so the voice reads smoothly."""
    return re.sub(r"\s*\([^)]*\)", "", text).strip()


# ---- actions ----
def say_time():
    speak(time_message(datetime.now()))


def say_date():
    speak(date_message(datetime.now()))


def say_battery():
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        speak("I could not find a battery on this computer")
        return
    speak(battery_message(b.percent, b.power_plugged, b.secsleft))


def say_system():
    try:
        import psutil
        speak(system_message(psutil.cpu_percent(interval=1),
                             psutil.virtual_memory().percent))
    except Exception:
        speak("Sorry, I could not read the system status")


def take_screenshot():
    try:
        import pyautogui
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(SCREENSHOT_DIR, f"screenshot_{stamp}.png")
        pyautogui.screenshot().save(path)
        print("Saved to:", path)
        speak("Screenshot saved")
    except Exception as e:
        print("Screenshot error:", e)
        speak("Sorry, I could not take the screenshot")


LEVEL_WORDS = {
    "zero": 0, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "half": 50, "full": 100, "max": 100, "maximum": 100,
}


def parse_volume_level(command):
    """'set volume to 50' / 'volume to fifty percent' -> 50, else None."""
    m = re.search(r"\b(\d{1,3})\b", command)
    if m:
        return min(100, int(m.group(1)))
    for word in sorted(LEVEL_WORDS, key=len, reverse=True):
        if re.search(r"\b" + word + r"\b", command):
            return LEVEL_WORDS[word]
    return None


def change_volume(command):
    try:
        import pyautogui
    except Exception:
        speak("Sorry, volume control is not available")
        return
    if re.search(r"\b(?:mute|unmute)\b", command):
        pyautogui.press("volumemute")
        speak("Done")
        return

    relative = re.search(r"\b(?:up|down|increase|decrease|raise|lower|reduce|"
                         r"louder|quieter|more|less|higher)\b", command)
    level = parse_volume_level(command)
    if level is not None and (not relative or re.search(r"\bto\b", command)):
        pyautogui.press("volumedown", presses=50, interval=0.01)   # to zero
        pyautogui.press("volumeup", presses=round(level / 2), interval=0.01)
        speak(f"Volume set to about {level} percent")
    elif re.search(r"\b(?:up|increase|raise|louder|higher|more)\b", command):
        pyautogui.press("volumeup", presses=5, interval=0.02)
        speak("Volume increased")
    elif re.search(r"\b(?:down|decrease|lower|reduce|quieter|less)\b", command):
        pyautogui.press("volumedown", presses=5, interval=0.02)
        speak("Volume decreased")
    else:
        speak("Say volume up, volume down, mute, or set volume to 50 percent")


def tell_joke():
    try:
        import pyjokes
        speak(pyjokes.get_joke())
    except Exception:
        speak("Why do programmers prefer dark mode? Because light attracts bugs")


def show_desktop():
    try:
        import pyautogui
        pyautogui.hotkey("win", "d")
        speak("Showing the desktop")
    except Exception:
        speak("Sorry, I could not do that")


def search_google(query):
    speak(f"Searching Google for {query}")
    webbrowser.open("https://www.google.com/search?q=" + quote_plus(query))


def play_youtube(query):
    speak(f"Playing {query} on YouTube")
    try:
        import pywhatkit
        pywhatkit.playonyt(query)
    except Exception:                      # no internet / library problem
        webbrowser.open("https://www.youtube.com/results?search_query="
                        + quote_plus(query))


def wiki_summary(topic):
    try:
        import wikipedia
        try:
            text = wikipedia.summary(topic, sentences=2)
        except wikipedia.DisambiguationError as e:
            text = wikipedia.summary(e.options[0], sentences=2)
        text = clean_summary(text)
        print("\n" + text + "\n")
        speak(text)
    except Exception as e:
        if e.__class__.__name__ == "PageError":
            speak(f"Sorry, I could not find {topic} on Wikipedia")
        else:
            speak("Sorry, I could not reach Wikipedia")


def greet():
    hour = datetime.now().hour
    part = ("Good morning" if hour < 12 else
            "Good afternoon" if hour < 17 else "Good evening")
    speak(random.choice([f"{part}! How can I help you?",
                         "Hello! I am ready to help",
                         "Hi there! What can I do for you?"]))


def tell_features():
    print(CHEAT_SHEET)
    speak("I can take voice notes, set and edit reminders, open approved "
          "apps, do dictation, search Google, play YouTube, tell the time "
          "and battery, take screenshots, control the volume, and tell "
          "jokes. The full list is shown on the screen")


def handle_extras(command):
    """Runs an extra command. Returns True if it was one."""
    name, arg = classify_extra(command)
    if name is None:
        return False
    actions = {
        "help": tell_features,
        "intro": lambda: speak("I am your voice controlled desktop "
                               "assistant, built with Python"),
        "how_are_you": lambda: speak("I am doing great, thank you for asking"),
        "thanks": lambda: speak("You are welcome"),
        "greet": greet,
        "google": lambda: search_google(arg),
        "youtube": lambda: play_youtube(arg),
        "wiki": lambda: wiki_summary(arg),
        "time": say_time,
        "date": say_date,
        "battery": say_battery,
        "system": say_system,
        "screenshot": take_screenshot,
        "volume": lambda: change_volume(arg),
        "joke": tell_joke,
        "desktop": show_desktop,
    }
    actions[name]()
    return True


# ---------------- COMMAND HANDLER ----------------
def known_names():
    """App and website names the language layer may turn into 'open ...'."""
    return list(APPROVED_APPS) + list(APPROVED_SITES) + list(ALIASES)


def handle(command, _retry=False):
    """Returns False when the assistant should stop."""
    # Turn normal speech ("could you please open up notepad") into a command
    # this file knows ("open notepad"). See nlu.py.
    command = nlu.interpret(command, known_names())
    note_text = extract_note(command)

    if note_text is not None:
        save_note(note_text)
    elif is_edit_reminder(command):
        edit_reminder(command)
    elif "remind" in command:
        if re.search(r"\b(delete|clear|cancel|remove)\b", command):
            delete_reminders(command)
        elif (not re.match(r"(can you |could you )?(please )?remind me", command)
              and (re.search(r"\b(list|show|what|read|tell|pending|how many)\b",
                             command)
                   or re.search(r"\breminders\b", command))):
            list_reminders()
        elif wants_new_reminder(command):
            set_reminder(command)
        else:
            list_reminders()
    elif "read" in command and "note" in command:
        read_notes()
    elif "delete" in command and "note" in command:
        delete_notes()
    elif DICTATION_RE.search(command):
        if re.search(r"\b(stop|end|cancel)\b", command):
            speak("Dictation is not running")
        elif re.search(r"\b(read|say)\b", command):
            read_last_dictation()
        elif re.search(r"\b(open|show|last|previous|latest)\b", command):
            open_last_dictation()
        else:
            dictate()
    elif command.startswith("open ") or command.startswith("launch "):
        open_apps(command.split(" ", 1)[1])
    elif handle_extras(command):
        pass
    elif is_list_request(command):
        list_approved()
    elif (re.search(r"\b(test|check)\b", command)
          and re.search(r"\b(sound|beep|alarm|alert)\b", command)):
        speak("Playing the alert sound")
        play_alert_sound()
        speak("Did you hear it?")
    elif "stop" in command or "exit" in command:
        speak("Goodbye")
        return False
    else:
        # Nothing matched. Make one last attempt with fuzzy matching (and the
        # optional AI), then give up politely.
        guessed = None if _retry else nlu.guess(command, known_names())
        if guessed and guessed != command:
            print("(Understood as:", guessed + ")")
            emit("info", f"Understood as: {guessed}")
            return handle(guessed, _retry=True)
        speak("Sorry, I did not understand. Say 'what can you do' to hear my features")
    return True


def main():
    init_db()
    threading.Thread(target=reminder_checker, daemon=True).start()
    print("Using microphone index:", MIC_INDEX)
    print(CHEAT_SHEET)
    speak("Hello, I am your voice assistant")
    running = True
    while running:
        command = listen()
        if command:
            running = handle(command)


if __name__ == "__main__":
    main()