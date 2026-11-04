"""Chuẩn bị âm thanh trước khi gửi cho model — chỉ dùng stdlib + numpy.

Dự án cố ý không phụ thuộc FFmpeg, nên ở đây chỉ làm phần an toàn:
WAV stereo/nhiều kênh → mono 16-bit ở sample rate thấp (giảm ~6-12 lần
dung lượng, đủ cho model nghe nhịp). Tệp nén (mp3/ogg/m4a/...) gửi nguyên
bản vì model tự giải mã được chúng.

prepare() tin vào byte thật của tệp (sniff()) chứ không tin phần mở rộng:
một trang HTML đổi tên .mp3 sẽ bị chặn ngay ở đây thay vì gửi lên model.
"""
import io
import wave

import numpy as np

from .config import AUDIO_FORMATS


class AudioError(ValueError):
    """Không đọc/không hạ cỡ được tệp âm thanh."""


# Dấu hiệu trang web: tệp "audio" tải từ trình nhúng nhạc thường là HTML, không phải âm thanh
_HTML_HINTS = (b"<!doctype", b"<html", b"<head", b"<?xml")


def sniff(data):
    """Định dạng container thật của *data* theo magic bytes, hoặc None nếu không phải audio."""
    if len(data) < 12:
        return None
    head = data[:16]
    if head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1a\x45\xdf\xa3":                       # EBML: webm/matroska
        return "webm"
    if head[4:8] == b"ftyp":                                  # ISO-BMFF: mp4/m4a
        return "m4a" if b"m4a" in head[8:12].lower() else "mp4"
    if head[:3] == b"ID3":
        return "mp3"
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:          # frame sync MPEG-1/2/2.5
        return "mp3"
    return None


def looks_like_webpage(data):
    return data[:400].lower().lstrip().startswith(_HTML_HINTS)


def _fmt_hint():
    return "/".join(AUDIO_FORMATS)


def _decode_wav(data):
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width == 1:
        pcm = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        pcm = (pcm - 128) * (256 / 32768.0)                 # đưa về thang 16-bit
    elif width == 2:
        pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    elif width == 4:
        pcm = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 65536.0
    else:
        raise AudioError(f"Độ sâu mẫu {width * 8}-bit không được hỗ trợ.")
    if ch > 1:
        pcm = pcm[: len(pcm) - len(pcm) % ch].reshape(-1, ch).mean(axis=1)
    return pcm, sr


def _encode_wav(pcm, sr):
    clipped = np.clip(pcm, -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(clipped.tobytes())
    return buf.getvalue()


def _resample(pcm, src_sr, dst_sr):
    if src_sr == dst_sr or not len(pcm):
        return pcm
    n = max(1, int(round(len(pcm) * dst_sr / src_sr)))
    x = np.linspace(0, len(pcm) - 1, n)
    return np.interp(x, np.arange(len(pcm)), pcm).astype(np.float32)


def _dur(sec):
    return f"{sec:.0f} giây" if sec < 90 else f"{sec / 60:.1f} phút"


def prepare(data, filename, target_sr=16000, max_seconds=1200):
    """Trả về (bytes, format, giây) — format là cái model nhận trong input_audio.

    Từ chối sớm: đuôi ngoài danh sách, hoặc byte đầu không phải audio thật
    (HTML/PDF/nhị phân đổi tên .mp3) — model sẽ chỉ trả về lỗi khó hiểu.
    """
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if ext and ext not in AUDIO_FORMATS:
        raise AudioError(f"Định dạng “.{ext}” không được hỗ trợ — chỉ nhận {_fmt_hint()}.")

    real = sniff(data)
    if real is None:
        if looks_like_webpage(data):
            raise AudioError("Tệp này là trang HTML, không phải âm thanh — tải file audio về rồi nạp.")
        raise AudioError(f"Không nhận dạng được âm thanh qua header — chỉ nhận {_fmt_hint()}.")
    # m4a/mp4 dùng chung container ISO-BMFF nên nhãn nào cũng giải mã được
    if ext and ext != real and {ext, real} != {"m4a", "mp4"}:
        raise AudioError(f"Phần mở rộng “.{ext}” không khớp nội dung thật (header là “.{real}”) — "
                         "hãy đặt lại tên đúng hoặc xuất file đúng định dạng.")

    if real == "wav":
        try:
            pcm, sr = _decode_wav(data)
        except Exception:                                   # WAV lạ -> gửi nguyên bản
            return data, "wav", 0.0
        seconds = len(pcm) / (sr or 1)
        if seconds > max_seconds:
            raise AudioError(f"Bài quá dài ({_dur(seconds)}), trần {_dur(max_seconds)}.")
        return _encode_wav(_resample(pcm, sr, target_sr), target_sr), "wav", seconds

    return data, real, 0.0                                  # model tự đọc header để biết thời lượng
