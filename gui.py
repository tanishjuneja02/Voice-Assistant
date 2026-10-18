"""
Voice Desktop Assistant - window (GUI)

Run:  python gui.py

The window uses the same brain as assistant.py. It adds buttons, a live
conversation log, and panels for notes, reminders and dictations.
"""
import queue
import sqlite3
import threading
from datetime import datetime

import customtkinter as ctk

import assistant

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

GREEN, GREEN_DARK = "#2e9e5b", "#237a46"
RED, RED_DARK = "#d6453d", "#a8352f"

STATUS = {
    "idle": ("Idle", "#9aa0a6"),
    "listening": ("Listening...", "#34c759"),
    "thinking": ("Thinking...", "#ff9f0a"),
    "speaking": ("Speaking...", "#0a84ff"),
    "stopping": ("Stopping...", "#ff453a"),
}

LOG_STYLE = {                       # kind -> (label, colour)
    "user": ("You", "#7ee787"),
    "assistant": ("Assistant", "#79c0ff"),
    "dictation": ("Dictation", "#d2a8ff"),
    "reminder": ("Reminder", "#ffa657"),
    "info": ("Info", "#8b949e"),
}

QUICK_COMMANDS = [
    ("Time", "what time is it"),
    ("Joke", "tell me a joke"),
    ("Screenshot", "take a screenshot"),
    ("Battery", "battery status"),
    ("Help", "what can you do"),
]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Voice Desktop Assistant")
        self.geometry("1100x700")
        self.minsize(900, 600)

        self.events = queue.Queue()            # events coming from assistant.py
        self.continuous = threading.Event()    # True while "Start Listening" is on
        self.mic_lock = threading.Lock()       # only one microphone job at a time
        self._last_text = {}

        assistant.EVENT_HOOK = lambda kind, text="": self.events.put((kind, text))
        assistant.init_db()
        threading.Thread(target=assistant.reminder_checker, daemon=True).start()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_events)
        self.after(300, self._refresh_panels)

        self._log("assistant", "Hello! Press Start Listening, or type a command below.")
        if assistant.MIC_INDEX is None:
            self._log("info", "No microphone was found. You can still type commands.")

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # ---------- left side: conversation ----------
        left = ctk.CTkFrame(self, corner_radius=12)
        left.grid(row=0, column=0, padx=(14, 7), pady=14, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, padx=14, pady=(14, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Voice Desktop Assistant",
                     font=ctk.CTkFont(size=22, weight="bold")
                     ).grid(row=0, column=0, sticky="w")
        self.status = ctk.CTkLabel(header, text="● Idle", text_color="#9aa0a6",
                                   font=ctk.CTkFont(size=15, weight="bold"))
        self.status.grid(row=0, column=1, sticky="e")

        buttons = ctk.CTkFrame(left, fg_color="transparent")
        buttons.grid(row=1, column=0, padx=14, pady=6, sticky="ew")
        buttons.grid_columnconfigure((0, 1), weight=1)
        self.listen_btn = ctk.CTkButton(
            buttons, text="Start Listening", height=46,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=GREEN, hover_color=GREEN_DARK,
            command=self.toggle_listening)
        self.listen_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.talk_btn = ctk.CTkButton(
            buttons, text="Tap to Talk (one command)", height=46,
            font=ctk.CTkFont(size=15), fg_color="#3a3f47",
            hover_color="#4a505a", command=self.talk_once)
        self.talk_btn.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        self.log_box = ctk.CTkTextbox(left, wrap="word",
                                      font=ctk.CTkFont(size=14))
        self.log_box.grid(row=2, column=0, padx=14, pady=6, sticky="nsew")
        for kind, (_, colour) in LOG_STYLE.items():
            self.log_box.tag_config(kind, foreground=colour)
        self.log_box.configure(state="disabled")

        quick = ctk.CTkFrame(left, fg_color="transparent")
        quick.grid(row=3, column=0, padx=14, pady=(0, 6), sticky="ew")
        for i, (label, command) in enumerate(QUICK_COMMANDS):
            quick.grid_columnconfigure(i, weight=1)
            ctk.CTkButton(quick, text=label, height=30, fg_color="#2b3139",
                          hover_color="#3a424c",
                          command=lambda c=command: self.run_text(c)
                          ).grid(row=0, column=i, padx=3, sticky="ew")

        entry_row = ctk.CTkFrame(left, fg_color="transparent")
        entry_row.grid(row=4, column=0, padx=14, pady=(0, 14), sticky="ew")
        entry_row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            entry_row, height=38,
            placeholder_text="Type a command and press Enter...")
        self.entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.entry.bind("<Return>", self.send_text)
        ctk.CTkButton(entry_row, text="Send", width=80, height=38,
                      command=self.send_text).grid(row=0, column=1)

        # ---------- right side: notes / reminders / dictations / commands ----------
        right = ctk.CTkFrame(self, corner_radius=12)
        right.grid(row=0, column=1, padx=(7, 14), pady=14, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(right)
        self.tabs.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.boxes = {}
        for name in ("Notes", "Reminders", "Dictations", "Commands"):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            box = ctk.CTkTextbox(tab, wrap="word", font=ctk.CTkFont(size=13))
            box.grid(row=0, column=0, sticky="nsew")
            box.configure(state="disabled")
            self.boxes[name] = box
        self._fill(self.boxes["Commands"], assistant.CHEAT_SHEET.strip())

    # ------------------------------------------------------------ small helpers
    def _fill(self, box, text):
        """Replace a panel's text (only if it changed, so it does not flicker)."""
        if self._last_text.get(id(box)) == text:
            return
        self._last_text[id(box)] = text
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")

    def _log(self, kind, text):
        label, _ = LOG_STYLE[kind]
        stamp = datetime.now().strftime("%H:%M")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {label}: ", kind)
        self.log_box.insert("end", text + "\n")
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def _set_status(self, key):
        text, colour = STATUS.get(key, STATUS["idle"])
        self.status.configure(text=f"● {text}", text_color=colour)

    def _reset_listen_button(self):
        self.listen_btn.configure(text="Start Listening",
                                  fg_color=GREEN, hover_color=GREEN_DARK)

    # ------------------------------------------- events from assistant.py (safe)
    def _process_events(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "status":
                    self._set_status(text)
                elif kind == "reset_button":
                    self._reset_listen_button()
                elif kind == "reminder":
                    self._log("reminder", text)
                    self.bell()
                elif kind in LOG_STYLE:
                    self._log(kind, text)
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    # ---------------------------------------------------------- panels (auto)
    def _refresh_panels(self):
        try:
            self._fill(self.boxes["Notes"], self._notes_text())
            self._fill(self.boxes["Reminders"], self._reminders_text())
            self._fill(self.boxes["Dictations"], self._dictations_text())
        except Exception as e:
            print("Panel refresh error:", e)
        self.after(2000, self._refresh_panels)

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
        return "\n".join(f"• {text}\n    {created}\n" for text, created in rows)

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
            "SELECT created, text FROM dictations ORDER BY id DESC LIMIT 10")
        if not rows:
            return "No dictations yet.\n\nSay: start dictation"
        out = []
        for created, text in rows:
            preview = text if len(text) <= 160 else text[:160] + "..."
            out.append(f"{created}\n{preview}\n")
        return "\n".join(out)

    # ------------------------------------------------------- microphone buttons
    def toggle_listening(self):
        if self.continuous.is_set():                 # turn OFF
            self.continuous.clear()
            self._set_status("stopping")
            self._reset_listen_button()
        else:                                        # turn ON
            self.continuous.set()
            self.listen_btn.configure(text="Stop Listening",
                                      fg_color=RED, hover_color=RED_DARK)
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
            self.events.put(("status", "idle"))

    def talk_once(self):
        if self.continuous.is_set():
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
            self.events.put(("status", "idle"))

    # ------------------------------------------------------- typed commands
    def send_text(self, event=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.run_text(text)

    def run_text(self, text):
        self._log("user", text)
        threading.Thread(target=self._text_worker, args=(text.lower(),),
                         daemon=True).start()

    def _text_worker(self, command):
        try:
            if not assistant.handle(command):         # "stop" / "exit"
                self.continuous.clear()
                self.events.put(("reset_button", ""))
        except Exception as e:
            print("Command error:", e)
            self.events.put(("info", f"Something went wrong: {e}"))

    # ------------------------------------------------------------------ close
    def _on_close(self):
        self.continuous.clear()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()