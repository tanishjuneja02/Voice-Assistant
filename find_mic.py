import speech_recognition as sr
import audioop

CANDIDATES = [1, 6, 12, 19, 23]

print("Keep talking or clapping during the test...\n")

for i in CANDIDATES:
    try:
        with sr.Microphone(device_index=i) as source:
            chunks = int(source.SAMPLE_RATE / source.CHUNK * 2)  # about 2 seconds
            loudest = 0
            for _ in range(chunks):
                data = source.stream.read(source.CHUNK)
                loudest = max(loudest, audioop.rms(data, source.SAMPLE_WIDTH))
            print(f"Index {i}: loudness = {loudest}")
    except Exception as e:
        print(f"Index {i}: failed ({e})")