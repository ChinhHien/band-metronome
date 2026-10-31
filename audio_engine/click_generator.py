"""Tạo click track theo sample (không dùng giây thập phân để tránh lệch nhịp)."""
import numpy as np

SAMPLE_RATE = 48000
RATES = {22050, 44100, 48000}          # các chuẩn đầu ra được hỗ trợ (mặc định: 48 kHz)


def _click(freq, amp, sr=SAMPLE_RATE, dur=0.03):
    n = int(sr * dur)
    t = np.arange(n) / sr
    return amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 180)


def accent_pattern(time_signature, beats):
    """Trả về danh sách mức nhấn (2=mạnh, 1=vừa, 0=nhẹ) cho từng phách."""
    num, den = (int(x) for x in time_signature.split("/"))
    group = 3 if (den == 8 and num % 3 == 0) else num   # nhịp ghép: nhấn mỗi 3 phách
    out = []
    for i in range(beats):
        if i % num == 0:
            out.append(2)
        elif den == 8 and group == 3 and i % 3 == 0:
            out.append(1)
        else:
            out.append(0)
    return out


def beats_per_bar(time_signature):
    return int(time_signature.split("/")[0])


def click_track(bpm, beats, time_signature="4/4", sr=SAMPLE_RATE):
    total = round(beats * 60 * sr / bpm)
    buf = np.zeros(total + sr // 10, dtype=np.float32)
    sounds = {2: _click(1000, 0.95, sr), 1: _click(950, 0.85, sr), 0: _click(900, 0.75, sr)}
    for i, level in enumerate(accent_pattern(time_signature, beats)):
        start = round(i * 60 * sr / bpm)         # mốc sample tuyệt đối, không cộng dồn sai số
        c = sounds[level]
        buf[start:start + len(c)] += c[: len(buf) - start]
    return buf[:total]
