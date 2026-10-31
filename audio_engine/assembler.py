"""Ghép các section thành một file WAV.

Cấu trúc bài = [1 bar click] + [các đoạn].
Mỗi đoạn = [1 bar đếm vào nếu bật "Đếm vào"] + N bar click.
  - Bar đếm vào: nếu bật "Đọc tên" → phách 1 nói tên đoạn, các phách sau
    "two, three, four"; nếu tắt → chỉ đọc "one two three four".
  - Bar click đầu bài (trước đoạn đầu tiên) chỉ có tiếng click, lấy BPM + nhịp
    của đoạn đầu tiên.
  - "Đếm chuyển" (count_out): chỉ hiệu lực nếu đoạn KẾ TIẾP bật "Đếm vào".
    Khi đó bar kế cuối của đoạn này đọc "one two three four",
    bar CUỐI CÙNG của đoạn này chính là bar đếm vào của đoạn kế (đọc tên đoạn kế),
    và đoạn kế không tạo bar đếm vào riêng.
  Ví dụ Verse 4 bar → Chorus 4 bar (Chorus bật đếm vào):
    bar 3 verse: "one two three four" (đếm chuyển) — bar 4 verse: "Chorus two three four" (đếm vào).
Giọng đọc được MIX lên click nên không làm lệch nhịp."""
import wave
import numpy as np

from .click_generator import SAMPLE_RATE, RATES, beats_per_bar, click_track
from .tts import speak

SIGS = {"2/4", "3/4", "4/4", "5/4", "6/8", "7/8", "9/8", "12/8"}
NUMBERS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def validate(song):
    sections = song.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("Bài hát cần ít nhất một đoạn.")
    if len(sections) > 100:
        raise ValueError("Tối đa 100 đoạn.")
    clean = []
    for i, s in enumerate(sections, 1):
        try:
            bpm, bars = float(s["bpm"]), int(s["bars"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Đoạn {i}: thiếu hoặc sai BPM / số bar.")
        if not 20 <= bpm <= 400:
            raise ValueError(f"Đoạn {i}: BPM phải từ 20 đến 400.")
        if not 0 <= bars <= 128:
            raise ValueError(f"Đoạn {i}: số bar phải từ 0 đến 128.")
        ts = str(s.get("time_signature", "4/4"))
        if ts not in SIGS:
            raise ValueError(f"Đoạn {i}: nhịp {ts} không được hỗ trợ.")
        clean.append({"name": str(s.get("name") or f"Section {i}")[:60], "bpm": bpm, "bars": bars,
                      "time_signature": ts,
                      "count_in": bool(s.get("count_in")),
                      "count_out": bool(s.get("count_out")),
                      "read_marker": bool(s.get("read_marker", True))})
    return clean


def _fit(clip, max_len, sr=SAMPLE_RATE):
    """Nén từ dài vừa trong một phách (tăng tốc tối đa 1.5x, quá nữa thì cắt) và fade-out."""
    if len(clip) > max_len:
        speed = min(len(clip) / max_len, 1.5)
        n = int(len(clip) / speed)
        clip = np.interp(np.linspace(0, len(clip) - 1, n), np.arange(len(clip)), clip).astype(np.float32)[:max_len]
    f = min(int(0.01 * sr), len(clip))
    if f:
        clip = clip.copy()
        clip[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
    return clip


def _speak_words(track, beat, beat_offset, words, warnings, sr=SAMPLE_RATE):
    """Mix spoken *words* (one per beat) onto *track* starting at beat *beat_offset*."""
    for j, word in enumerate(words):
        voice = speak(word, sr)
        if voice is None:
            warnings.append(f"Không đọc được “{word}” (chưa có engine TTS).")
            continue
        voice = _fit(voice, int(beat), sr=sr)
        start = round((beat_offset + j) * beat)
        n = min(len(voice), len(track) - start)
        track[start:start + n] += 0.9 * voice[:n]


def _marker_words(name, num, speak_name=True):
    """Bar đếm vào: phách 1 = tên đoạn (nếu bật đọc tên), sau đó two three four…
    Khi tắt "Đọc tên", đọc đủ "one two three four"."""
    if not speak_name:
        return NUMBERS[:num]
    return [name if j == 0 else NUMBERS[j] for j in range(num)]


def assemble_song(song, out_path, progress_cb=None):
    sections = validate(song)
    # Chuẩn đầu ra chọn trong UI: 22050 / 44100 / 48000 Hz (vi phạm → fallback 44100)
    sr = int(song.get("sample_rate") or SAMPLE_RATE)
    if sr not in RATES:
        sr = SAMPLE_RATE
    parts, warnings = [], []

    total_steps = len(sections) + 1

    def _progress(done, label):
        if progress_cb:
            progress_cb(done / total_steps, label)

    # Bar lead: LUÔN LUÔN đúng 1 bar, chỉ tiếng click, lấy BPM + nhịp của đoạn đầu tiên.
    lead = sections[0]
    lead_num = beats_per_bar(lead["time_signature"])
    parts.append(click_track(lead["bpm"], lead_num, lead["time_signature"], sr=sr))
    _progress(1, "bar dẫn")

    # Các đoạn thật (bar lead không phải một session, không lặp lại trong vòng lặp)
    for idx, s in enumerate(sections):
        num = beats_per_bar(s["time_signature"])
        beat = 60 * sr / s["bpm"]

        next_s = sections[idx + 1] if idx < len(sections) - 1 else None
        # Đếm chuyển chỉ hiệu lực khi đoạn kế bật đếm vào:
        # bar kế cuối đọc "one two three four", bar cuối = bar đếm vào của đoạn kế.
        count_out_active = bool(s.get("count_out") and next_s and next_s.get("count_in"))
        # Bar đếm vào của đoạn này bị hút vào bar cuối đoạn trước khi đoạn trước bật đếm chuyển.
        prev_s = sections[idx - 1] if idx > 0 else None
        absorbed = bool(prev_s and prev_s.get("count_out") and s.get("count_in"))
        has_count_in = bool(s.get("count_in")) and not absorbed

        track = click_track(s["bpm"], (s["bars"] + (1 if has_count_in else 0)) * num, s["time_signature"], sr=sr)

        # --- Bar đếm vào (bật "Đọc tên": "Intro 2 3 4"; tắt: "one two three four") ---
        if has_count_in:
            _speak_words(track, beat, 0, _marker_words(s["name"], num, s["read_marker"]), warnings, sr)

        total_bars = s["bars"] + (1 if has_count_in else 0)
        if count_out_active:
            co_beat = (total_bars - 2) * num if total_bars >= 2 else 0
            _speak_words(track, beat, co_beat, NUMBERS[:num], warnings, sr)
            last_beat = (total_bars - 1) * num
            _speak_words(track, beat, last_beat,
                         _marker_words(next_s["name"], beats_per_bar(next_s["time_signature"]),
                                       next_s.get("read_marker", True)), warnings, sr)

        parts.append(track)
        _progress(idx + 2, s["name"])
    _progress(len(sections) + 1, "ghi file WAV")
    mix = np.concatenate(parts)
    peak = np.max(np.abs(mix)) or 1.0
    pcm = (mix / max(peak, 1.0) * 32767 * 0.95).astype("<i2")
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return {"duration": len(mix) / sr, "sample_rate": sr, "warnings": list(dict.fromkeys(warnings))[:3]}
