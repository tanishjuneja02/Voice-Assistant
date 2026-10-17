import speech_recognition as sr
import audioop, os

MIC_INDEX = 6

r = sr.Recognizer()
with sr.Microphone(device_index=MIC_INDEX) as source:
    input("Press Enter, then speak for 5 seconds: ")
    print("Recording... speak now!")
    audio = r.record(source, duration=5)
    print("Done.")

raw = audio.get_raw_data()
print("Average loudness:", audioop.rms(raw, audio.sample_width))

with open("test.wav", "wb") as f:
    f.write(audio.get_wav_data())

print("Playing back your recording...")
os.startfile("test.wav")

try:
    print("Google heard:", r.recognize_google(audio, language="en-US"))
except sr.UnknownValueError:
    print("Google could not understand it")
except sr.RequestError:
    print("No internet or API problem")
