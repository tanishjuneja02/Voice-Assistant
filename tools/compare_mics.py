import speech_recognition as sr
import audioop

CANDIDATES = [1, 6]
r = sr.Recognizer()

for i in CANDIDATES:
    print(f"\n=== Testing mic index {i} ===")
    try:
        with sr.Microphone(device_index=i) as source:
            input("Press Enter, then say: 'hello this is my voice assistant project' ")
            print("Recording 5 seconds... speak now!")
            audio = r.record(source, duration=5)
    except Exception as e:
        print("Could not open this mic:", e)
        continue

    raw = audio.get_raw_data()
    print("Average loudness:", audioop.rms(raw, audio.sample_width))
    print("Peak:", audioop.max(raw, audio.sample_width), "(32767 = clipping)")

    with open(f"test_mic_{i}.wav", "wb") as f:
        f.write(audio.get_wav_data())
    print(f"Saved test_mic_{i}.wav")

    try:
        print("Google heard:", r.recognize_google(audio, language="en-US"))
    except sr.UnknownValueError:
        print("Google could not understand it")
    except sr.RequestError:
        print("No internet or API problem")