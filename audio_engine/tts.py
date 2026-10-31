"""Giọng đọc marker offline bằng pyttsx3. Nếu không khả dụng thì bỏ qua êm."""
import os
import tempfile
import wave
import numpy as np

from .click_generator import SAMPLE_RATE

_cache = {}


def speak(text, sr=SAMPLE_RATE):
    """Trả về mảng float32 mono ở sample rate `sr`, hoặc None nếu TTS không dùng được."""
    key = (text, sr)                       # cache phân theo rate vì audio được resample
    if key in _cache:
        return _cache[key]
    audio = None
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 170)
        path = os.path.join(tempfile.gettempdir(), f"marker_{abs(hash(text))}.wav")
        engine.save_to_file(text, path)
        engine.runAndWait()
        audio = _trim(_read_wav(path, sr), sr)
        os.remove(path)
    except Exception as exc:  # thiếu engine, thiếu giọng, v.v.
        print(f"[tts] bỏ qua marker '{text}': {exc}")
    _cache[key] = audio
    return audio


def _read_wav(path, sr):
    with wave.open(path, "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    data = (data - 128) / 128 if width == 1 else data / float(np.iinfo(dtype).max)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if rate != sr:
        x = np.linspace(0, 1, len(data), endpoint=False)
        xi = np.linspace(0, 1, int(len(data) * sr / rate), endpoint=False)
        data = np.interp(xi, x, data).astype(np.float32)
    return data


def _trim(x, sr):
    idx = np.nonzero(np.abs(x) > 0.02 * max(np.abs(x).max(), 1e-9))[0]
    if len(idx) == 0:
        return x
    return x[max(idx[0] - int(0.005 * sr), 0): idx[-1] + 1]
