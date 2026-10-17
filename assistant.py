import re
import time
import threading
import sqlite3
import subprocess
import webbrowser
from datetime import datetime, timedelta

import speech_recognition as sr
import pyttsx3

LANGUAGE = "en-IN"   # Indian English. If recognition gets worse, try "en-US"

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


def speak(text):
    print("Assistant:", text)
    with _speak_lock:
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()


def listen():
    with sr.Microphone(device_index=MIC_INDEX) as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("Listening...")
        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=12)
        except sr.WaitTimeoutError:
            return ""
    try:
        text = recognizer.recognize_google(audio, language=LANGUAGE)
        print("You said:", text)
        return text.lower()
    except sr.UnknownValueError:
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


# ---------------- COMMAND HANDLER ----------------
def handle(command):
    """Returns False when the assistant should stop."""
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
    elif command.startswith("open ") or command.startswith("launch "):
        open_apps(command.split(" ", 1)[1])
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
        speak("Sorry, I did not understand")
    return True


def main():
    init_db()
    threading.Thread(target=reminder_checker, daemon=True).start()
    print("Using microphone index:", MIC_INDEX)
    speak("Hello, I am your voice assistant")
    running = True
    while running:
        command = listen()
        if command:
            running = handle(command)


if __name__ == "__main__":
    main()