import speech_recognition as sr

MIC_INDEX = 6

r = sr.Recognizer()
with sr.Microphone(device_index=MIC_INDEX) as source:
    print("Stay quiet for a second...")
    r.adjust_for_ambient_noise(source, duration=1)
    print("Speak now!")
    try:
        audio = r.listen(source, timeout=8, phrase_time_limit=6)
    except sr.WaitTimeoutError:
        print("Heard nothing. Check mic or index.")
        raise SystemExit

try:
    print("You said:", r.recognize_google(audio, language="en-IN"))
except sr.UnknownValueError:
    print("Could not understand the audio")
except sr.RequestError:
    print("No internet or API problem")