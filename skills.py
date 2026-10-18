"""
skills.py - the new features: close apps, weather, timers + stopwatch,
smart notes, music keys, typing, Wi-Fi / brightness / lock.

assistant.py calls init() once to give this file its helper functions
(speak, listen, ...). That avoids importing assistant.py from here.
"""
import ctypes
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import config

# filled in by init()
_speak = print
_listen = lambda: ""
_alert = print
_confirm = lambda prompt: False
DB = "assistant.db"
_started = False


def init(speak, listen, alert, confirm, db):
    global _speak, _listen, _alert, _confirm, DB, _started
    _speak, _listen, _alert, _confirm, DB = speak, listen, alert, confirm, db
    if not _started:
        _started = True
        threading.Thread(target=_timer_loop, daemon=True).start()


def _words(command):
    return re.findall(r"[a-z0-9']+", command.lower())


# =====================================================================
# 1. CLOSE APPS  (taskkill, small approved list)
# =====================================================================
CLOSABLE = {
    "notepad": ["notepad.exe"],
    "calculator": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    "paint": ["mspaint.exe", "PaintStudio.View.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "vs code": ["Code.exe"],
    "settings": ["SystemSettings.exe"],
}
CLOSE_ALIASES = {
    "note pad": "notepad", "calc": "calculator", "windows calculator": "calculator",
    "mspaint": "paint", "ms paint": "paint", "crome": "chrome",
    "google chrome": "chrome", "microsoft edge": "edge", "code": "vs code",
    "vscode": "vs code", "visual studio code": "vs code",
}
# Never closed: closing these could kill the assistant's own window or taskbar
PROTECTED = {"command prompt", "cmd", "terminal", "file explorer", "explorer",
             "files", "python", "powershell"}
CLOSE_FILLERS = {"close", "quit", "the", "my", "app", "application", "please",
                 "window", "windows", "program"}


def _hidden():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}   # no flash


def _taskkill(exe, force=False):
    cmd = ["taskkill", "/IM", exe, "/T"] + (["/F"] if force else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           **_hidden())
    except Exception as e:
        print("taskkill error:", e)
        return 1, str(e)
    return r.returncode, (r.stdout + r.stderr)


def close_app(name):
    name = " ".join(w for w in name.split() if w not in CLOSE_FILLERS)
    name = CLOSE_ALIASES.get(name, name)
    if not name:
        return
    if name in PROTECTED:
        _speak(f"I do not close {name}, because it could close me or your "
               "taskbar")
        return
    if name not in CLOSABLE:
        _speak(f"Sorry, {name} is not in my close list")
        return
    closed, need_force = False, False
    for exe in CLOSABLE[name]:
        code, out = _taskkill(exe)
        if code == 0:
            closed = True
        elif code != 128 and "/F" in out:        # 128 = not running
            need_force = True
    if closed:
        _speak(f"Closed {name}")
    elif need_force:
        if _confirm(f"{name} did not close normally. Force close it? "
                    "Unsaved work will be lost."):
            for exe in CLOSABLE[name]:
                _taskkill(exe, force=True)
            _speak(f"Force closed {name}")
        else:
            _speak("Okay, I left it open")
    else:
        _speak(f"{name} is not running")


def close_apps(text):
    for part in re.split(r"\band\b|,", text):
        if part.strip():
            close_app(part.strip())


# =====================================================================
# 2. WEATHER  (Open-Meteo, free, no key)
# =====================================================================
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
       45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle",
       55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
       77: "snow grains", 80: "light rain showers", 81: "rain showers",
       82: "heavy rain showers", 85: "snow showers", 86: "snow showers",
       95: "a thunderstorm", 96: "a thunderstorm with hail",
       99: "a thunderstorm with hail"}
RAINY = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
_geo_cache = {}


def _get_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(city):
    key = city.lower()
    if key not in _geo_cache:
        data = _get_json(
            "https://geocoding-api.open-meteo.com/v1/search?name="
            + urllib.parse.quote(city) + "&count=1&language=en&format=json")
        results = data.get("results") or []
        if not results:
            return None
        r = results[0]
        _geo_cache[key] = (r["latitude"], r["longitude"], r["name"])
    return _geo_cache[key]


def fetch_weather(city):
    place = geocode(city)
    if place is None:
        return None
    lat, lon, name = place
    unit = "fahrenheit" if config.TEMP_UNIT == "fahrenheit" else "celsius"
    data = _get_json(
        "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,precipitation_sum"
        "&timezone=auto&forecast_days=3&temperature_unit=%s" % (lat, lon, unit))
    data["place"] = name
    return data


def _deg(value):
    return f"{round(value)} degrees" + (" Fahrenheit" if
                                        config.TEMP_UNIT == "fahrenheit" else "")


def describe_weather(data, day=0, rain_question=False):
    """Pure function: Open-Meteo JSON -> a sentence to speak."""
    place, d, cur = data["place"], data["daily"], data["current"]
    when = ("today", "tomorrow", "the day after tomorrow")[day]
    prob = d["precipitation_probability_max"][day] or 0
    mm = d["precipitation_sum"][day] or 0
    code = d["weather_code"][day]
    desc = WMO.get(code, "mixed weather")

    if rain_question:
        if day == 0 and cur["weather_code"] in RAINY:
            return (f"Yes, it is raining in {place} right now, with a "
                    f"{round(prob)} percent chance for the day")
        if prob >= 50:
            return (f"Yes, rain is likely {when} in {place}, about "
                    f"{round(prob)} percent chance. Take an umbrella")
        if prob >= 20:
            return (f"There is a small chance of rain {when} in {place}, "
                    f"about {round(prob)} percent")
        return f"No, rain is unlikely {when} in {place}, only {round(prob)} percent"

    hi, lo = d["temperature_2m_max"][day], d["temperature_2m_min"][day]
    if day == 0:
        text = (f"It is {_deg(cur['temperature_2m'])} in {place} right now, "
                f"feels like {_deg(cur['apparent_temperature'])}, "
                f"{WMO.get(cur['weather_code'], 'mixed weather')}. "
                f"Today's high is {round(hi)} and low is {round(lo)}")
    else:
        text = (f"{when.capitalize()} in {place}: {desc}, high {round(hi)}, "
                f"low {round(lo)}")
    if prob >= 30:
        text += f". {round(prob)} percent chance of rain"
    return text


def parse_weather_request(command):
    """-> (city or None, day offset, rain_question)"""
    day = 1 if "tomorrow" in command else 0
    rain = bool(re.search(r"\b(rain|raining|umbrella|shower|showers)\b", command))
    city = None
    m = re.search(r"\b(?:in|for|at)\s+([a-z][a-z .'-]*?)"
                  r"(?:\s+(?:today|tomorrow|tonight|now|right now))?\s*$", command)
    if m and m.group(1).strip() not in ("the morning", "the evening",
                                        "the afternoon", "general", "my area"):
        city = m.group(1).strip()
    return city, day, rain


def say_weather(command):
    city, day, rain = parse_weather_request(command)
    try:
        data = fetch_weather(city or config.DEFAULT_CITY)
    except Exception as e:
        print("Weather error:", e)
        _speak("Sorry, I could not reach the weather service")
        return
    if data is None:
        _speak(f"Sorry, I could not find a place called {city}")
        return
    _speak(describe_weather(data, day, rain))


def weather_brief():
    """One short sentence for the daily briefing."""
    try:
        data = fetch_weather(config.DEFAULT_CITY)
        return describe_weather(data) if data else None
    except Exception as e:
        print("Weather error:", e)
        return None


# =====================================================================
# 3. TIMERS + STOPWATCH   (countdown is shown in the GUI)
# =====================================================================
WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
            "forty five": 45, "fifty": 50, "sixty": 60, "half": 0.5,
            "quarter": 0.25}
UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}
DUR_RE = re.compile(
    r"(\d+(?:\.\d+)?|forty five|[a-z]+)\s*(hours?|hrs?|minutes?|mins?|"
    r"seconds?|secs?)\b")


def parse_duration(text):
    """'5 minutes' / '1 hour 30 minutes' / 'half an hour' -> seconds or None."""
    t = text.lower()
    t = re.sub(r"\bhalf an hour\b", "30 minutes", t)
    t = re.sub(r"\ban hour and a half\b", "90 minutes", t)
    t = re.sub(r"\ba minute and a half\b", "90 seconds", t)
    t = re.sub(r"(\d+)\s+and a half\s+(hours?|minutes?)",
               lambda m: f"{int(m.group(1)) * 2 + 1} half-{m.group(2)}", t)
    t = re.sub(r"(\d+) half-hours?", lambda m: f"{int(m.group(1)) * 30} minutes", t)
    t = re.sub(r"(\d+) half-minutes?", lambda m: f"{int(m.group(1)) * 30} seconds", t)
    total = 0
    for m in DUR_RE.finditer(t):
        amount = m.group(1)
        try:
            amount = float(amount) if amount[0].isdigit() else WORD_NUM[amount]
        except (KeyError, ValueError):
            continue
        total += amount * UNIT_SECONDS[m.group(2)[0]]
    if total == 0:
        m = re.search(r"\b(?:for|of)\s+(\d+)\s*$", t)      # "timer for 5"
        if m:
            total = int(m.group(1)) * 60
    return int(total) or None


def fmt_clock(seconds):
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_spoken(seconds):
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h} hour" + ("s" if h != 1 else ""))
    if m:
        parts.append(f"{m} minute" + ("s" if m != 1 else ""))
    if s or not parts:
        parts.append(f"{s} second" + ("s" if s != 1 else ""))
    return " ".join(parts)


_timers = []          # dicts: id, label, total, end
_timer_lock = threading.Lock()
_next_timer_id = 1


def _timer_loop():
    while True:
        due = []
        now = time.time()
        with _timer_lock:
            for t in list(_timers):
                if t["end"] <= now:
                    _timers.remove(t)
                    due.append(t)
        for t in due:
            try:
                _alert(f"Your {fmt_spoken(t['total'])} timer is done")
            except Exception as e:
                print("Timer alert error:", e)
        time.sleep(0.25)


def start_timer(seconds):
    global _next_timer_id
    with _timer_lock:
        t = {"id": _next_timer_id, "total": seconds,
             "end": time.time() + seconds}
        _next_timer_id += 1
        _timers.append(t)
    _speak(f"Timer set for {fmt_spoken(seconds)}")


def timer_lines():
    """Text for the GUI countdown panel."""
    now = time.time()
    with _timer_lock:
        lines = [f"⏱  Timer {t['id']}  ({fmt_spoken(t['total'])}):   "
                 f"{fmt_clock(t['end'] - now)} left" for t in _timers]
    return lines or ["No timers running.", "", "Say: set a timer for 5 minutes"]


def timers_active():
    with _timer_lock:
        return len(_timers)


def timer_left():
    now = time.time()
    with _timer_lock:
        if not _timers:
            _speak("You have no timers running")
            return
        for t in _timers:
            _speak(f"Timer {t['id']} has {fmt_spoken(t['end'] - now)} left")


def cancel_timer(command):
    with _timer_lock:
        if not _timers:
            msg = "You have no timers running"
        else:
            m = re.search(r"\b(?:number\s*)?(\d+)\b", command)
            if re.search(r"\b(all|every)\b", command):
                _timers.clear()
                msg = "All timers cancelled"
            elif m and any(t["id"] == int(m.group(1)) for t in _timers):
                _timers[:] = [t for t in _timers if t["id"] != int(m.group(1))]
                msg = f"Timer {m.group(1)} cancelled"
            elif len(_timers) == 1:
                _timers.clear()
                msg = "Timer cancelled"
            else:
                msg = ("Which timer? Say cancel timer and its number, "
                       "or cancel all timers")
    _speak(msg)


def timer_command(command):
    if re.search(r"\b(cancel|stop|delete|remove|clear|end)\b", command):
        cancel_timer(command)
    elif re.search(r"\b(left|remaining|how much|how long|status|check)\b", command):
        timer_left()
    else:
        seconds = parse_duration(command)
        if seconds is None:
            _speak("For how long? Say, for example, set a timer for 5 minutes")
            seconds = parse_duration(_listen() or "")
            if seconds is None:
                _speak("Sorry, I did not catch the time. No timer set")
                return
        start_timer(seconds)


# ---- stopwatch ----
_sw = {"running": False, "start": 0.0, "elapsed": 0.0, "laps": []}
_sw_lock = threading.Lock()


def _sw_now():
    return _sw["elapsed"] + (time.time() - _sw["start"] if _sw["running"] else 0)


def stopwatch_line():
    with _sw_lock:
        state = ("running" if _sw["running"] else
                 "stopped" if _sw["elapsed"] else "ready")
        return f"⏲  Stopwatch ({state}):   {fmt_clock(_sw_now())}"


def stopwatch_lines():
    with _sw_lock:
        laps = list(_sw["laps"])
    return [stopwatch_line()] + [f"     lap {i}: {fmt_clock(x)}"
                                 for i, x in enumerate(laps, 1)]


def stopwatch_command(command):
    with _sw_lock:
        if re.search(r"\b(reset|clear|restart)\b", command):
            _sw.update(running=False, start=0.0, elapsed=0.0, laps=[])
            msg = "Stopwatch reset"
            if "restart" in command:
                _sw.update(running=True, start=time.time())
                msg = "Stopwatch restarted"
        elif re.search(r"\blap\b", command):
            if _sw["running"]:
                _sw["laps"].append(_sw_now())
                msg = f"Lap {len(_sw['laps'])}: {fmt_spoken(_sw_now())}"
            else:
                msg = "The stopwatch is not running"
        elif re.search(r"\b(stop|pause|end|finish)\b", command):
            if _sw["running"]:
                _sw["elapsed"] = _sw_now()
                _sw["running"] = False
                msg = f"Stopped at {fmt_spoken(_sw['elapsed'])}"
            else:
                msg = "The stopwatch is not running"
        elif re.search(r"\b(start|begin|resume|go)\b", command):
            if _sw["running"]:
                msg = "The stopwatch is already running"
            else:
                _sw.update(running=True, start=time.time())
                msg = "Stopwatch started"
        else:                                    # "how long on the stopwatch"
            msg = ("The stopwatch is at " + fmt_spoken(_sw_now())
                   if _sw["running"] or _sw["elapsed"]
                   else "The stopwatch has not been started")
    _speak(msg)


# =====================================================================
# 5. SMART NOTES  (search + delete one)
# =====================================================================
NOTE_NOISE = {"what", "did", "i", "my", "the", "a", "an", "about", "on", "for",
              "note", "notes", "noted", "write", "wrote", "written", "save",
              "saved", "jot", "down", "search", "find", "look", "up", "do",
              "have", "any", "me", "tell", "show", "regarding", "that", "says",
              "number", "delete", "remove", "erase", "last", "latest", "first",
              "second", "third", "recent", "one", "please", "is", "there"}
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
       "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def all_notes():
    """Newest first. Number 1 is the newest (same order as the GUI list)."""
    db = sqlite3.connect(DB)
    try:
        return db.execute(
            "SELECT id, text, created FROM notes ORDER BY id DESC").fetchall()
    finally:
        db.close()


def keywords(command):
    return [w for w in _words(command) if w not in NOTE_NOISE]


def matching_notes(command):
    """-> list of (number, id, text, created) of notes matching the keywords."""
    keys = keywords(command)
    numbered = [(n, *row) for n, row in enumerate(all_notes(), 1)]
    if not keys:
        return []
    hits = [r for r in numbered if all(k in r[2].lower() for k in keys)]
    return hits or [r for r in numbered if any(k in r[2].lower() for k in keys)]


def search_notes(command):
    hits = matching_notes(command)
    topic = " ".join(keywords(command))
    if not hits:
        _speak(f"I found no notes about {topic}" if topic
               else "What should I search for in your notes?")
        return
    _speak(f"I found {len(hits)} note" + ("s" if len(hits) != 1 else "")
           + f" about {topic}")
    for number, _, text, created in hits[:3]:
        _speak(f"Number {number}: {text}")
    if len(hits) > 3:
        _speak("The rest are shown on screen in the Notes tab")


def delete_note(command):
    """'delete note about milk' / 'delete note number 2' / 'delete the last note'."""
    notes = all_notes()
    if not notes:
        _speak("You have no notes")
        return
    m = (re.search(r"\b(?:number|no)\s*(\d+|one|two|three|four|five|six)\b", command)
         or re.search(r"\b(first|second|third|fourth|fifth)\b", command))
    chosen = None
    if m:
        word = m.group(1)
        n = int(word) if word.isdigit() else ORD[word]
        if not 1 <= n <= len(notes):
            _speak(f"You only have {len(notes)} notes")
            return
        chosen = notes[n - 1]
    elif re.search(r"\b(last|latest|recent|newest)\b", command):
        chosen = notes[0]
    elif keywords(command):
        hits = matching_notes(command)
        if not hits:
            _speak("I found no note like that")
            return
        if len(hits) > 1:
            _speak("I found more than one. Say: delete note number, and the number")
            for number, _, text, _ in hits[:4]:
                _speak(f"Number {number}: {text}")
            return
        chosen = hits[0][1:]
    elif len(notes) == 1:
        chosen = notes[0]
    else:
        _speak("Which note? Say: delete note about milk, or delete note number 2")
        return
    db = sqlite3.connect(DB)
    db.execute("DELETE FROM notes WHERE id=?", (chosen[0],))
    db.commit()
    db.close()
    _speak(f"Deleted the note: {chosen[1]}")


def note_command(command):
    """Returns True if this was a smart-note command."""
    if re.search(r"\b(?:what|anything)\b.*\b(?:did i|have i)\b.*"
                 r"\b(?:note|write|written|save|saved|jot)\b", command) \
            or re.search(r"\b(?:search|find|look)\b.*\bnotes?\b", command) \
            or re.search(r"\bdo i have (?:a |any )?notes?\b", command):
        search_notes(command)
        return True
    if re.search(r"\b(?:delete|remove|erase)\b", command) \
            and re.search(r"\bnote\b", command):                 # singular
        delete_note(command)
        return True
    if re.search(r"\b(?:delete|remove|erase|clear)\b", command) \
            and re.search(r"\bnotes\b", command):
        if re.search(r"\b(?:all|every|everything)\b", command) or \
                not keywords(command):
            if _confirm("Delete all your notes?"):
                db = sqlite3.connect(DB)
                db.execute("DELETE FROM notes")
                db.commit()
                db.close()
                _speak("All notes deleted")
            else:
                _speak("Okay, I kept your notes")
        else:
            delete_note(command)
        return True
    return False


# =====================================================================
# 6. MUSIC  (media keys - works with Spotify, YouTube, VLC ...)
# =====================================================================
def _press(key):
    try:
        import pyautogui
        pyautogui.press(key)
        return True
    except Exception as e:
        print("Media key error:", e)
        _speak("Sorry, I could not press the media key")
        return False


MUSIC_RE = re.compile(
    r"^(?:please )?(?:"
    r"(play|resume|pause|skip|next|previous)"
    r"(?: the| this| my)?(?: music| song| songs| track| tracks| playback| it)?"
    r"|(stop) (?:the |my )?(?:music|song|track|playback)"
    r"|(?:play|go to) (?:the )?(next|previous) (?:song|track)"
    r"|(next|previous|last) (?:song|track)"
    r"|(skip) (?:this )?(?:song|track))$")


def music_command(command):
    """Returns True if handled."""
    m = MUSIC_RE.match(command.strip())
    if not m:
        return False
    word = next((g for g in m.groups() if g), "")
    if word in ("play", "resume", "pause", "stop"):
        if _press("playpause"):
            _speak("Okay")
    elif word in ("next", "skip"):
        if _press("nexttrack"):
            _speak("Next track")
    elif word in ("previous", "last"):
        if _press("prevtrack"):
            _speak("Previous track")
    return True


# =====================================================================
# 7. TYPE INTO ANY APP  (pyautogui)
# =====================================================================
def type_into_app(text, delay=2.0):
    try:
        import pyautogui
    except Exception:
        _speak("Sorry, typing is not available")
        return
    _speak("Typing in 2 seconds. Click where you want the text")
    time.sleep(delay)
    pyautogui.write(text, interval=0.01)


def type_phrase(phrase):
    """Used by typing mode. Returns the number of characters typed."""
    import pyautogui
    pieces = re.split(r"\b(new paragraph|new line|next line)\b", phrase,
                      flags=re.I)
    count = 0
    for piece in pieces:
        low = piece.lower()
        if low == "new paragraph":
            pyautogui.press("enter", presses=2)
            count += 2
        elif low in ("new line", "next line"):
            pyautogui.press("enter")
            count += 1
        elif piece.strip():
            out = piece.strip() + " "
            pyautogui.write(out, interval=0.005)
            count += len(out)
    return count


def backspace(n):
    import pyautogui
    pyautogui.press("backspace", presses=n, interval=0.002)


# =====================================================================
# 9. WI-FI, BRIGHTNESS, LOCK  (risky ones ask first)
# =====================================================================
def _powershell(script, timeout=25):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=timeout, **_hidden())
    return r.returncode, (r.stdout + r.stderr).strip()


WIFI_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$wifi = $radios | ? { $_.Kind -eq 'WiFi' }
if (-not $wifi) { Write-Output 'NOWIFI'; exit 2 }
Await ($wifi.SetStateAsync('%STATE%')) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
Write-Output 'OK'
"""


def set_wifi(on):
    """Radio on/off through the Windows radio API (no admin needed)."""
    try:
        code, out = _powershell(WIFI_PS.replace("%STATE%", "On" if on else "Off"))
        if "OK" in out:
            return True
        print("Wi-Fi script said:", out)
    except Exception as e:
        print("Wi-Fi error:", e)
    try:                                         # backup: needs admin rights
        r = subprocess.run(
            ["netsh", "interface", "set", "interface", "name=Wi-Fi",
             "admin=" + ("enabled" if on else "disabled")],
            capture_output=True, text=True, timeout=15, **_hidden())
        return r.returncode == 0
    except Exception as e:
        print("netsh error:", e)
        return False


def wifi_status():
    try:
        r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                           capture_output=True, text=True, timeout=10,
                           **_hidden())
    except Exception:
        return None
    state = re.search(r"^\s*State\s*:\s*(.+)$", r.stdout, re.M)
    ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", r.stdout, re.M)
    if not state:
        return "Wi-Fi is turned off or not available"
    if state.group(1).strip().lower() == "connected" and ssid:
        return f"Wi-Fi is connected to {ssid.group(1).strip()}"
    return f"Wi-Fi state is {state.group(1).strip()}"


def wifi_command(command):
    if re.search(r"\b(off|disable|disconnect|stop)\b", command):
        if _confirm("Turning Wi-Fi off will also stop my speech recognition, "
                    "because it needs the internet. Turn Wi-Fi off?"):
            _speak("Wi-Fi turned off" if set_wifi(False)
                   else "Sorry, I could not turn Wi-Fi off")
        else:
            _speak("Okay, Wi-Fi stays on")
    elif re.search(r"\b(on|enable|connect|start)\b", command):
        _speak("Wi-Fi turned on" if set_wifi(True)
               else "Sorry, I could not turn Wi-Fi on")
    else:
        _speak(wifi_status() or "Sorry, I could not read the Wi-Fi status")


def get_brightness():
    try:
        import screen_brightness_control as sbc
        return int(sbc.get_brightness()[0])
    except Exception:
        pass
    try:
        code, out = _powershell("(Get-WmiObject -Namespace root/WMI -Class "
                                "WmiMonitorBrightness).CurrentBrightness")
        return int(out.split()[0])
    except Exception:
        return None


def set_brightness(level):
    level = max(0, min(100, int(level)))
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(level)
        return True
    except Exception as e:
        print("sbc error:", e)
    try:
        code, _ = _powershell(
            "(Get-WmiObject -Namespace root/WMI -Class "
            "WmiMonitorBrightnessMethods).WmiSetBrightness(1,%d)" % level)
        return code == 0
    except Exception:
        return False


def brightness_command(command):
    current = get_brightness()
    m = re.search(r"\b(\d{1,3})\b", command)
    if m:
        target = int(m.group(1))
    elif re.search(r"\b(max|maximum|full|highest)\b", command):
        target = 100
    elif re.search(r"\b(min|minimum|lowest)\b", command):
        target = 10
    elif re.search(r"\b(up|increase|raise|brighter|higher|more)\b", command):
        target = (current if current is not None else 50) + 20
    elif re.search(r"\b(down|decrease|lower|dim|darker|less|reduce)\b", command):
        target = (current if current is not None else 50) - 20
    else:
        _speak("Brightness is " + (f"{current} percent" if current is not None
                                   else "unknown on this screen"))
        return
    target = max(5, min(100, target))
    _speak(f"Brightness set to {target} percent" if set_brightness(target)
           else "Sorry, I could not change the brightness on this screen")


def lock_pc():
    if not _confirm("Lock the computer now?"):
        _speak("Okay, not locking")
        return
    _speak("Locking the computer")
    try:
        ctypes.windll.user32.LockWorkStation()
    except Exception:
        try:
            subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        except Exception as e:
            print("Lock error:", e)
            _speak("Sorry, I could not lock the computer")


# =====================================================================
# ROUTER  - called by assistant.handle(); returns True when it handled it
# =====================================================================
def handle_skill(command):
    c = command.strip()
    if note_command(c):
        return True
    if re.search(r"\btimers?\b|\bcount ?down\b", c) and "remind" not in c:
        timer_command(c)
        return True
    if re.search(r"\bstop ?watch\b", c):
        stopwatch_command(c)
        return True
    m = re.match(r"^(?:close|quit|shut down|shutdown) (.+)$", c)
    if m and not re.search(r"\b(?:computer|pc|laptop|system)\b", m.group(1)):
        close_apps(m.group(1))
        return True
    if music_command(c):
        return True
    m = re.match(r"^type (.+)$", c)
    if m:
        type_into_app(m.group(1))
        return True
    if re.search(r"\bwi-?\s?fi\b|\bwireless\b", c):
        wifi_command(c)
        return True
    if re.search(r"\bbrightness\b|\bbrighter\b|\bdim the screen\b", c):
        brightness_command(c)
        return True
    if re.search(r"\block (?:my |the |this )?(?:pc|computer|laptop|screen|"
                 r"system|windows)\b|^lock$", c):
        lock_pc()
        return True
    if re.search(r"\bweather\b|\bforecast\b|\bwill it rain\b|\braining\b|"
                 r"\bumbrella\b|\bhow (?:hot|cold) is it\b|"
                 r"\btemperature (?:today|outside|in)\b", c):
        say_weather(c)
        return True
    return False