import importlib

libs = ["speech_recognition", "pyttsx3", "pyaudio", "vosk", "apscheduler",
        "plyer", "customtkinter", "pygame", "psutil", "pyautogui",
        "dateparser", "wikipedia", "pyjokes", "pywhatkit", "sqlite3", "tkinter"]

for lib in libs:
    try:
        importlib.import_module(lib)
        print("OK      ", lib)
    except Exception as e:
        print("MISSING ", lib, "->", e)