"""
Voice Desktop Assistant - window (GUI)

Run:  python gui.py

Dark theme with soft pink, dark blue, purple, orange, gold and bright white.
  * a live sound wave for YOUR voice (pink / orange / gold)
  * a live sound wave for the ASSISTANT (purple / blue / pink)
  * a chat made of speech bubbles
  * panels for notes, reminders, dictations, timers and the command list
"""
import math
import queue
import random
import sqlite3
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import assistant
import skills

ctk.set_appearance_mode("dark")

# ------------------------------------------------------------------ colours
BG = "#0a0a1f"            # window (deep dark blue)
PANEL = "#121230"         # cards
PANEL_2 = "#1b1b40"       # inputs, tabs
LINE = "#2d2d5e"          # borders
TEXT = "#ffffff"          # bright white
MUTED = "#9a9fcc"

PINK = "#ff8fc7"          # soft pink  - your voice
PURPLE = "#a78bfa"        # purple     - assistant voice
BLUE = "#4c63ff"          # blue highlight
ORANGE = "#ff9f43"
GOLD = "#ffd166"
RED = "#ff5c7a"
RED_DARK = "#d63f5c"

USER_WAVE = [PINK, ORANGE, GOLD]
USER_WAVE_DIM = ["#5c3550", "#5c4026", "#5c4e2e"]
ASSIST_WAVE = [PURPLE, BLUE, PINK]
ASSIST_WAVE_DIM = ["#3d3465", "#242c66", "#5c3550"]

FONT = "Segoe UI"         # falls back to the system font if missing

STATUS = {                # key -> (text, colour)
    "idle": ("Ready", MUTED),
    "listening": ("Listening", PINK),
    "thinking": ("Thinking", ORANGE),
    "speaking": ("Speaking", PURPLE),
    "stopping": ("Stopping", RED),
    "waiting": ("Waiting for wake word", GOLD),
}

# kind -> (bubble colour, text colour, side)
BUBBLE = {
    "user": ("#5a2a55", "#ffffff", "right"),
    "assistant": ("#2a2468", "#ffffff", "left"),
    "dictation": ("#1c2d73", "#ffffff", "left"),
    "reminder": ("#5e3a12", "#fff3d6", "left"),
}

QUICK_COMMANDS = [
    ("Time", "what time is it"),
    ("Joke", "tell me a joke"),
    ("Screenshot", "take a screenshot"),
    ("Battery", "battery status"),
    ("Weather", "what's the weather"),
    ("Help", "what can you do"),
]


# --------------------------------------------------------------- sound wave
class Wave(tk.Canvas):
    """A smooth, layered sound wave. Louder sound = taller wave."""

    def __init__(self, master, title, bright, dim, height=84):
        super().__init__(master, height=height, bg=PANEL,
                         highlightthickness=0, bd=0)
        self.title, self.bright, self.dim = title, bright, dim
        self.level = 0.0
        self.phase = random.random() * 6.0

    def draw(self, target, active):
        self.level += (target - self.level) * 0.30          # smooth movement
        self.phase += 0.16 + self.level * 0.30
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20:
            return
        self.delete("all")
        mid = h / 2
        colours = self.bright if active else self.dim
        for i, colour in enumerate(colours):
            amp = (0.05 + self.level) * h * 0.46 * (1 - 0.22 * i)
            points = []
            for x in range(0, w + 1, 6):
                t = x / w
                edge = math.sin(math.pi * t) ** 1.4          # calm at the sides
                y = mid + (math.sin(t * (6 + 2 * i) * math.pi
                                    + self.phase * (1 + 0.35 * i))
                           * amp * edge)
                points += [x, y]
            self.create_line(*points, fill=colour, width=(4, 2, 1)[min(i, 2)],
                             smooth=True)
        self.create_text(14, 10, text=self.title, anchor="nw",
                         fill=(self.bright[0] if active else MUTED),
                         font=(FONT, 11, "bold"))


def blend(c1, c2, t):
    """Mix two #rrggbb colours (t=0 -> c1, t=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class MicButton(tk.Canvas):
    """A round button. A glow ring around it pulses with your voice (pink)
    or with the assistant's voice (purple)."""
    SIZE = 124

    def __init__(self, master, command):
        super().__init__(master, width=self.SIZE, height=self.SIZE, bg=PANEL,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.listening = False
        self.hover = False
        self.ring = 0.0
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: setattr(self, "hover", True))
        self.bind("<Leave>", lambda e: setattr(self, "hover", False))

    def draw(self, level, ring_colour):
        self.ring += (level - self.ring) * 0.30
        c = self.SIZE / 2
        self.delete("all")
        for k, fade in ((2, 0.80), (1, 0.55)):          # two soft rings
            r = 40 + k * 4 + self.ring * 7 * k
            self.create_oval(c - r, c - r, c + r, c + r, width=2,
                             outline=blend(ring_colour, PANEL, fade))
        fill = "#ffb3da" if self.hover else PINK
        self.create_oval(c - 38, c - 38, c + 38, c + 38, fill=fill, outline="")
        dark = "#2a0a24"
        if self.listening:                              # stop square
            self.create_rectangle(c - 12, c - 12, c + 12, c + 12,
                                  fill=dark, outline="")
        else:                                           # microphone
            self.create_line(c, c - 14, c, c + 2, width=17, capstyle="round",
                             fill=dark)
            self.create_arc(c - 17, c - 12, c + 17, c + 22, start=180,
                            extent=180, style="arc", width=3, outline=dark)
            self.create_line(c, c + 22, c, c + 29, width=3, fill=dark)
            self.create_line(c - 8, c + 29, c + 8, c + 29, width=3,
                             capstyle="round", fill=dark)


# ---------------------------------------------------------------------- app
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Voice Desktop Assistant")
        self.geometry("1180x800")
        self.minsize(980, 700)
        self.configure(fg_color=BG)

        self.events = queue.Queue()            # events coming from assistant.py
        self.continuous = threading.Event()    # True while listening is on
        self.wake = threading.Event()          # True while wake word is on
        self.mic_lock = threading.Lock()       # only one microphone job at a time
        self._last_text = {}

        # live values written by assistant.py (from other threads)
        self.status_key = "idle"
        self.raw_user = 0.0                    # real microphone loudness (if sent)
        self.peak = 900.0                      # auto-gain for quiet microphones
        self.pulse = 0.0                       # assistant voice pulse
        self._t = 0.0                          # time, for the fallback animation

        assistant.EVENT_HOOK = self._on_event
        assistant.CONFIRM_HOOK = self._ask_confirm   # Yes/No box for typed commands
        assistant.init_db()
        threading.Thread(target=assistant.reminder_checker, daemon=True).start()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._process_events)
        self.after(300, self._refresh_panels)
        self.after(500, self._refresh_timers)
        self.after(33, self._animate)

        self._bubble("assistant", "Hi! Tap the microphone and talk to me "
                                  "normally, or type below.")
        if assistant.MIC_INDEX is None:
            self._bubble("info", "No microphone found. You can still type.")

    # ------------------------------------------------------------- layout
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # ---------------- left: header, waves, chat, input ----------------
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, padx=(16, 8), pady=16, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Voice Assistant", text_color=TEXT,
                     font=ctk.CTkFont(FONT, 26, "bold")
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="Talk to it the way you talk to a person",
                     text_color=GOLD, font=ctk.CTkFont(FONT, 13)
                     ).grid(row=1, column=0, sticky="w")
        pill = ctk.CTkFrame(header, fg_color=PANEL_2, corner_radius=18,
                            border_width=1, border_color=LINE)
        pill.grid(row=0, column=1, rowspan=2, sticky="e")
        self.status = ctk.CTkLabel(pill, text="●  Ready", text_color=MUTED,
                                   font=ctk.CTkFont(FONT, 14, "bold"))
        self.status.pack(padx=16, pady=7)

        # wave card
        self.wave_card = ctk.CTkFrame(left, fg_color=PANEL, corner_radius=20,
                                      border_width=2, border_color=LINE)
        self.wave_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.wave_card.grid_columnconfigure(0, weight=1)
        self.user_wave = Wave(self.wave_card, "You", USER_WAVE, USER_WAVE_DIM)
        self.user_wave.grid(row=0, column=0, padx=16, pady=(12, 0), sticky="ew")
        self.assist_wave = Wave(self.wave_card, "Assistant",
                                ASSIST_WAVE, ASSIST_WAVE_DIM)
        self.assist_wave.grid(row=1, column=0, padx=16, pady=(0, 6), sticky="ew")

        controls = ctk.CTkFrame(self.wave_card, fg_color="transparent")
        controls.grid(row=2, column=0, pady=(0, 10))
        self.mic = MicButton(controls, self.toggle_listening)
        self.mic.grid(row=0, column=1, padx=8, rowspan=2)
        self.talk_btn = ctk.CTkButton(
            controls, text="One command", width=120, height=34,
            corner_radius=17, fg_color=PANEL_2, hover_color=LINE,
            text_color=TEXT, font=ctk.CTkFont(FONT, 13),
            command=self.talk_once)
        self.talk_btn.grid(row=0, column=2, padx=(10, 0), pady=(30, 0), sticky="n")
        self.wake_btn = ctk.CTkButton(
            controls, text="Wake word: Off", width=120, height=34,
            corner_radius=17, fg_color=PANEL_2, hover_color=LINE,
            text_color=TEXT, font=ctk.CTkFont(FONT, 13),
            command=self.toggle_wake)
        self.wake_btn.grid(row=0, column=0, padx=(0, 10), pady=(30, 0), sticky="n")
        self.listen_hint = ctk.CTkLabel(controls, text="Tap to start listening",
                                        text_color=MUTED,
                                        font=ctk.CTkFont(FONT, 12))
        self.listen_hint.grid(row=2, column=0, columnspan=3)

        # chat
        self.chat = ctk.CTkScrollableFrame(left, fg_color=PANEL,
                                           corner_radius=20)
        self.chat.grid(row=2, column=0, sticky="nsew", pady=(0, 10))

        quick = ctk.CTkFrame(left, fg_color="transparent")
        quick.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        for i, (label, command) in enumerate(QUICK_COMMANDS):
            quick.grid_columnconfigure(i, weight=1)
            ctk.CTkButton(quick, text=label, height=32, corner_radius=16,
                          fg_color=PANEL_2, hover_color=LINE, text_color=TEXT,
                          font=ctk.CTkFont(FONT, 13),
                          command=lambda c=command: self.run_text(c)
                          ).grid(row=0, column=i, padx=3, sticky="ew")

        entry_row = ctk.CTkFrame(left, fg_color="transparent")
        entry_row.grid(row=4, column=0, sticky="ew")
        entry_row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            entry_row, height=44, corner_radius=22, fg_color=PANEL_2,
            border_color=LINE, text_color=TEXT, font=ctk.CTkFont(FONT, 14),
            placeholder_text="Type anything, e.g. remind me to study at 6 pm")
        self.entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.entry.bind("<Return>", self.send_text)
        ctk.CTkButton(entry_row, text="Send", width=84, height=44,
                      corner_radius=22, fg_color=GOLD, hover_color="#ffe08f",
                      text_color="#2a1c00", font=ctk.CTkFont(FONT, 14, "bold"),
                      command=self.send_text).grid(row=0, column=1)

        # ------- right: notes / reminders / dictations / timers / commands -------
        right = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20)
        right.grid(row=0, column=1, padx=(8, 16), pady=16, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(
            right, fg_color=PANEL, corner_radius=16,
            segmented_button_fg_color=PANEL_2,
            segmented_button_selected_color=PURPLE,
            segmented_button_selected_hover_color="#8f75e6",
            segmented_button_unselected_color=PANEL_2,
            segmented_button_unselected_hover_color=LINE,
            text_color=TEXT)
        self.tabs.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.boxes = {}
        # tab -> list of (button text, command it runs)
        tab_buttons = {
            "Notes": [("Delete last note", "delete the last note"),
                      ("Delete all notes", "delete all notes")],
            "Dictations": [("Delete last dictation", "delete my last dictation"),
                           ("Delete all dictations", "delete all dictations")],
            "Timers": [("1 min", "set a timer for 1 minute"),
                       ("5 min", "set a timer for 5 minutes"),
                       ("10 min", "set a timer for 10 minutes"),
                       ("Cancel", "cancel all timers"),
                       ("Stopwatch", None),
                       ("Reset", "reset stopwatch")],
        }
        for name in ("Notes", "Reminders", "Dictations", "Timers", "Commands"):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            box = ctk.CTkTextbox(tab, wrap="word", fg_color=PANEL_2,
                                 text_color=TEXT, corner_radius=12,
                                 font=ctk.CTkFont(FONT, 16 if name == "Timers" else 13))
            box.grid(row=0, column=0, sticky="nsew")
            box.configure(state="disabled")
            self.boxes[name] = box
            if name in tab_buttons:
                row = ctk.CTkFrame(tab, fg_color="transparent")
                row.grid(row=1, column=0, pady=(6, 0), sticky="ew")
                for i, (label, command) in enumerate(tab_buttons[name]):
                    row.grid_columnconfigure(i, weight=1)
                    action = (self.toggle_stopwatch if command is None else
                              (lambda c=command: self.run_text(c)))
                    ctk.CTkButton(row, text=label, height=28, corner_radius=14,
                                  fg_color=PANEL_2, hover_color=LINE,
                                  text_color=TEXT, border_width=1,
                                  border_color=LINE, command=action
                                  ).grid(row=0, column=i, padx=2, sticky="ew")
        self._fill(self.boxes["Commands"], assistant.CHEAT_SHEET.strip())

    # ------------------------------------------------------ chat bubbles
    def _bubble(self, kind, text):
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=3)
        stamp = datetime.now().strftime("%H:%M")
        if kind == "info":                                   # small grey note
            ctk.CTkLabel(row, text=text, text_color=MUTED,
                         font=ctk.CTkFont(FONT, 12, slant="italic")
                         ).pack(pady=2)
        else:
            colour, text_colour, side = BUBBLE[kind]
            who = {"user": "You", "assistant": "Assistant",
                   "dictation": "Dictation", "reminder": "Reminder"}[kind]
            holder = ctk.CTkFrame(row, fg_color="transparent")
            holder.pack(side=side)
            ctk.CTkLabel(holder, text=f"{who}  {stamp}", text_color=MUTED,
                         font=ctk.CTkFont(FONT, 11)
                         ).pack(anchor="e" if side == "right" else "w", padx=6)
            box = ctk.CTkFrame(holder, fg_color=colour, corner_radius=16)
            box.pack(anchor="e" if side == "right" else "w")
            width = self.chat.winfo_width()
            wrap = max(240, (width if width > 100 else 560) - 170)
            ctk.CTkLabel(box, text=text, text_color=text_colour,
                         justify="left", anchor="w", wraplength=wrap,
                         font=ctk.CTkFont(FONT, 14)
                         ).pack(padx=14, pady=9)
        self.after(60, self._scroll_chat_down)

    def _scroll_chat_down(self):
        try:
            self.chat._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    # --------------------------------------------------- small helpers
    def _fill(self, box, text):
        """Replace a panel's text (only if it changed, so it does not flicker)."""
        if self._last_text.get(id(box)) == text:
            return
        self._last_text[id(box)] = text
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")

    def _set_status(self, key):
        text, colour = STATUS.get(key, STATUS["idle"])
        self.status.configure(text=f"●  {text}", text_color=colour)
        self.wave_card.configure(border_color=colour if key != "idle" else LINE)
        if key == "listening":
            self.peak = 900.0                  # re-tune for this room

    def _reset_listen_button(self):
        self.mic.listening = False
        self.listen_hint.configure(text="Tap to start listening")

    def _reset_wake_button(self):
        self.wake_btn.configure(text="Wake word: Off", fg_color=PANEL_2,
                                hover_color=LINE, text_color=TEXT)

    # -------------------------------------- events from assistant.py
    def _on_event(self, kind, text=""):
        """Called from other threads. Sound levels are stored directly (fast);
        everything else goes through a queue and is shown by the main thread."""
        if kind == "level_user":
            try:
                self.raw_user = float(text)
            except ValueError:
                pass
        elif kind == "level_assistant":
            try:
                self.pulse = float(text)
            except ValueError:
                pass
        else:
            if kind == "status":
                self.status_key = text
            self.events.put((kind, text))

    def _process_events(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "status":
                    self._set_status(text)
                elif kind == "reset_button":
                    self._reset_listen_button()
                elif kind == "reset_wake":
                    self._reset_wake_button()
                elif kind == "confirm":
                    prompt, box = text
                    box["ans"] = messagebox.askyesno("Please confirm", prompt,
                                                     parent=self)
                    box["done"].set()
                elif kind == "reminder":
                    self._bubble("reminder", text)
                    self.bell()
                elif kind in BUBBLE or kind == "info":
                    self._bubble(kind, text)
        except queue.Empty:
            pass
        self.after(50, self._process_events)

    # ------------------------------------------------------ animation
    def _animate(self):
        key = self.status_key
        self._t += 0.12
        if key == "listening":
            if self.raw_user > 0:              # real microphone level
                self.peak = max(self.peak * 0.997, self.raw_user, 700.0)
                user_target = min(1.0, (self.raw_user / self.peak) ** 0.85)
            else:                              # no level data: gentle motion
                user_target = 0.22 + 0.14 * math.sin(self._t * 1.7) ** 2
        else:
            user_target = 0.0
        self.pulse *= 0.80                     # each spoken word is a pulse
        if key == "speaking":
            fallback = 0.28 + 0.2 * math.sin(self._t * 2.3) ** 2
            assist_target = max(self.pulse, fallback)
        else:
            assist_target = 0.0
        try:
            self.user_wave.draw(user_target, key in ("listening", "waiting"))
            self.assist_wave.draw(assist_target, key == "speaking")
            if key == "speaking":
                self.mic.draw(assist_target, PURPLE)
            else:
                self.mic.draw(user_target, PINK)
        except tk.TclError:
            return                             # window is closing
        self.after(33, self._animate)

    # --------------------------------------------------- panels (auto)
    def _refresh_panels(self):
        try:
            self._fill(self.boxes["Notes"], self._notes_text())
            self._fill(self.boxes["Reminders"], self._reminders_text())
            self._fill(self.boxes["Dictations"], self._dictations_text())
        except Exception as e:
            print("Panel refresh error:", e)
        self.after(2000, self._refresh_panels)

    def _refresh_timers(self):
        try:
            lines = skills.timer_lines() + ["", ""] + skills.stopwatch_lines()
            self._fill(self.boxes["Timers"], "\n".join(lines))
        except Exception as e:
            print("Timer refresh error:", e)
        self.after(500, self._refresh_timers)

    def toggle_stopwatch(self):
        running = skills._sw["running"]
        self.run_text("stop the stopwatch" if running else "start the stopwatch")

    def _ask_confirm(self, prompt):
        """Called from a worker thread: shows a Yes/No box and waits."""
        box = {"ans": False, "done": threading.Event()}
        self.events.put(("confirm", (prompt, box)))
        box["done"].wait(120)
        return box["ans"]

    def _query(self, sql):
        db = sqlite3.connect(assistant.DB)
        try:
            return db.execute(sql).fetchall()
        finally:
            db.close()

    def _notes_text(self):
        rows = self._query(
            "SELECT text, created FROM notes ORDER BY id DESC LIMIT 50")
        if not rows:
            return "No notes yet.\n\nSay: take a note buy milk"
        return "\n".join(f"{n}. {text}\n    {created}\n"
                         for n, (text, created) in enumerate(rows, start=1))

    def _reminders_text(self):
        rows = assistant.pending_reminders()
        if not rows:
            return ("No pending reminders.\n\n"
                    "Say: remind me in 5 minutes to drink water")
        lines = []
        for number, (_, text, due) in enumerate(rows, start=1):
            when = datetime.strptime(due, assistant.DB_TIME_FORMAT)
            lines.append(f"{number}. {text}\n    {assistant.fmt_when(when)}\n")
        return "\n".join(lines)

    def _dictations_text(self):
        rows = self._query(
            "SELECT created, text FROM dictations ORDER BY id DESC LIMIT 30")
        if not rows:
            return "No dictations yet.\n\nSay: start dictation"
        out = []
        for n, (created, text) in enumerate(rows, start=1):
            preview = text if len(text) <= 160 else text[:160] + "..."
            out.append(f"{n}. {created}\n{preview}\n")
        return "\n".join(out)

    # ------------------------------------------------ microphone buttons
    def toggle_wake(self):
        if self.wake.is_set():                       # turn OFF
            self.wake.clear()
            self._set_status("stopping")
            self._reset_wake_button()
            return
        if self.continuous.is_set():                 # one mic mode at a time
            self.toggle_listening()
        self.wake.set()
        self.wake_btn.configure(text="Wake word: On", fg_color=PURPLE,
                                hover_color="#8f75e6", text_color="#150b2e")
        threading.Thread(target=self._wake_worker, daemon=True).start()

    def _wake_worker(self):
        with self.mic_lock:
            try:
                assistant.wake_loop(self.wake)
            except Exception as e:
                print("Wake word error:", e)
                self.events.put(("info", f"Wake word problem: {e}"))
                self.wake.clear()
        self.events.put(("reset_wake", ""))
        self.status_key = "idle"
        self.events.put(("status", "idle"))

    def toggle_listening(self):
        if self.continuous.is_set():                 # turn OFF
            self.continuous.clear()
            self._set_status("stopping")
            self._reset_listen_button()
        else:                                        # turn ON
            if self.wake.is_set():
                self.toggle_wake()                   # switch wake word off
                self.after(600, self.toggle_listening)
                return
            self.continuous.set()
            self.mic.listening = True
            self.listen_hint.configure(text="Listening - tap to stop")
            threading.Thread(target=self._continuous_loop, daemon=True).start()

    def _continuous_loop(self):
        with self.mic_lock:
            while self.continuous.is_set():
                try:
                    command = assistant.listen()
                    if not self.continuous.is_set():
                        break
                    if command and not assistant.handle(command):
                        self.continuous.clear()      # the user said "stop"
                        self.events.put(("reset_button", ""))
                except Exception as e:
                    print("Listening error:", e)
                    self.events.put(("info", f"Problem: {e}"))
                    self.continuous.clear()
                    self.events.put(("reset_button", ""))
            self.status_key = "idle"
            self.events.put(("status", "idle"))

    def talk_once(self):
        if self.continuous.is_set() or self.wake.is_set():
            return
        threading.Thread(target=self._talk_once_worker, daemon=True).start()

    def _talk_once_worker(self):
        if not self.mic_lock.acquire(blocking=False):
            return
        try:
            command = assistant.listen()
            if command:
                assistant.handle(command)
            else:
                self.events.put(("info", "I did not catch that. Try again."))
        except Exception as e:
            print("Listening error:", e)
            self.events.put(("info", f"Problem: {e}"))
        finally:
            self.mic_lock.release()
            self.status_key = "idle"
            self.events.put(("status", "idle"))

    # ------------------------------------------------------- typed commands
    def send_text(self, event=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.run_text(text)

    def run_text(self, text):
        self._bubble("user", text)
        threading.Thread(target=self._text_worker, args=(text.lower(),),
                         daemon=True).start()

    def _text_worker(self, command):
        try:
            if not assistant.handle(command, source="text"):   # "stop" / "exit"
                self.continuous.clear()
                self.events.put(("reset_button", ""))
        except Exception as e:
            print("Command error:", e)
            self.events.put(("info", f"Something went wrong: {e}"))

    # ------------------------------------------------------------------ close
    def _on_close(self):
        self.continuous.clear()
        self.wake.clear()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()