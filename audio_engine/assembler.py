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

    # Bar lead: LUÔN LUÔN đúng 1 bar, lấy BPM + nhịp của đoạn đầu tiên. Bar này không
    # thuộc bài hát nên nó kiêm luôn bar đếm vào của đoạn đầu — không cộng thêm bar.
    lead = sections[0]
    lead_num = beats_per_bar(lead["time_signature"])
    lead_track = click_track(lead["bpm"], lead_num, lead["time_signature"], sr=sr)
    if lead.get("count_in"):
        _speak_words(lead_track, 60 * sr / lead["bpm"], 0,
                     _marker_words(lead["name"], lead_num, lead["read_marker"]), warnings, sr)
    parts.append(lead_track)
    _progress(1, "bar dẫn")

    # Các đoạn thật. Đếm vào / đếm chuyển KHÔNG BAO GIỜ thêm bar: chúng là lời đọc đặt
    # trên bar cuối (và bar kế cuối) của đoạn liền trước, nên tổng số bar của file luôn
    # khớp số bar anh đã gõ cho bài gốc. Ai muốn có bar riêng để đổi tempo thì tự thêm
    # bar thừa vào đầu đoạn đó.
    for idx, s in enumerate(sections):
        num = beats_per_bar(s["time_signature"])
        beat = 60 * sr / s["bpm"]
        track = click_track(s["bpm"], s["bars"] * num, s["time_signature"], sr=sr)

        next_s = sections[idx + 1] if idx < len(sections) - 1 else None
        cue_in = bool(next_s and next_s.get("count_in"))
        last = s["bars"] - 1                      # bar cuối của đoạn này

        if cue_in:
            if last >= 0:
                # đếm vào cho đoạn kế, đếm theo số phách của bar hiện tại
                _speak_words(track, beat, last * num,
                             _marker_words(next_s["name"], num,
                                           next_s.get("read_marker", True)), warnings, sr)
            else:
                warnings.append(f'Đoạn “{s["name"]}” không có bar nào để đếm vào cho đoạn sau.')
        if s.get("count_out"):
            co = last - 1 if cue_in else last     # nhường bar cuối cho đếm vào nếu đoạn sau cần
            if co >= 0:
                _speak_words(track, beat, co * num, NUMBERS[:num], warnings, sr)
            else:
                warnings.append(f'Đoạn “{s["name"]}” chỉ có 1 bar nên không còn bar để đếm chuyển.')

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
