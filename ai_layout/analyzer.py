"""Gọi model đa phương thức (OpenAI-compatible) để nghe và trích bố cục bài hát.

Key/base URL/model đến từ **thiết bị đang gọi** (header), không phải từ server —
xem `parse_creds()`. Kết quả luôn được nắn qua audio_engine.validate() và snap về
các tempo mà phép đo trên máy người dùng cho phép, nên bố cục trả về web chắc chắn
dựng lại được và không bao giờ đi lệch khỏi nhịp đã đo.
"""
import base64
import json
import math
import re
from dataclasses import dataclass

from audio_engine import validate

from . import config
from .netguard import GuardError, check_base_url, host_of

SYSTEM_PROMPT = """You are a music arranger for a band metronome app.
You will hear one song. Output ONLY a JSON object, no prose, no markdown:

{
  "base": {"bpm": <số>, "time_signature": "<x/y>", "sample_rate": 48000},
  "sections": [
    {"name": "<Intro|Verse|Pre-Chorus|Chorus|Post-Chorus|Bridge|Interlude|Solo|Outro|...>",
     "bpm": <số>, "bars": <số>, "time_signature": "<x/y>"}
  ]
}

Rules:
- sections: 1..100 items, in play order. Cover the WHOLE song, no gaps.
- bpm: 20..400 (integer, the real tempo of that section).
- bars: 1..128 integer, counted in the section's own time_signature.
- time_signature: one of 2/4 3/4 4/4 5/4 6/8 7/8 9/8 12/8.
- Không xuất count_in / count_out / read_marker: tuỳ chọn đếm là phần người dùng bật sẵn
  trên web, không phải phần việc của bạn.
- Name sections with standard band terms; number repeats (Verse 1, Verse 2, Chorus 1...).
- base must equal the first section's bpm and time_signature.
Be accurate about structure and bar counts; if unsure, prefer fewer, longer sections."""

# Model KHÔNG tự đo được nhịp (kiểm chứng 2026-11-02: cùng một tệp, nhiệt độ 0, trả
# 60/91/139 cho bài 125 BPM). Nhưng model nghe được *cách cảm nhận* nhịp, tức là
# chọn ký hiệu tempo (pulse hay nhịp đôi/nửa) — phần đó ta giao cho model quyết,
# trong giới hạn các giá trị mà phép đo cho phép.
_TEMPO_RULES = (0.5, 1.0, 2.0)


class AiError(RuntimeError):
    """Model trả về kết quả không dùng được (hoặc gọi model thất bại)."""


@dataclass(frozen=True)
class Creds:
    """Thông tin đăng nhập của riêng thiết bị đang gọi — sống đúng trong một request."""
    base_url: str
    model: str
    api_key: str
    host: str

    def __repr__(self):                    # không bao giờ để key lọt vào log/traceback
        return f"<Creds host={self.host} model={self.model} key=***>"


def parse_creds(headers, allowed_hosts=()):
    """Đọc X-AI-Base / X-AI-Model / X-AI-Key, kiểm tra rồi trả về Creds.

    Đây là dữ liệu khách đưa: chặn độ dài, chặn ký tự điều khiển, chỉ https,
    và không cho trỏ vào mạng nội bộ.
    """
    raw_key = headers.get("X-AI-Key", "").strip()
    base = headers.get("X-AI-Base", "").strip()
    model = headers.get("X-AI-Model", "").strip()
    if not (raw_key and base and model):
        raise AiError("Thiết bị này chưa cấu hình model — bấm “Cấu hình model” trong panel AI.")
    if not (8 <= len(raw_key) <= 256) or any(ord(c) < 33 or ord(c) > 126 for c in raw_key):
        raise AiError("API key không hợp lệ (cần 8-256 ký tự, không khoảng trắng).")
    if len(model) > 120 or any(ord(c) < 33 or ord(c) > 126 for c in model):
        raise AiError("Tên model không hợp lệ.")
    try:
        base = check_base_url(base, allowed_hosts)
    except GuardError as e:
        raise AiError(str(e)) from e
    return Creds(base_url=base, model=model, api_key=raw_key, host=host_of(base))


def _client(creds):
    try:
        from openai import OpenAI
    except ImportError as e:
        raise AiError("Thiếu thư viện `openai` — bật qua: pip install openai") from e
    return OpenAI(base_url=creds.base_url, api_key=creds.api_key,
                  timeout=config.TIMEOUT_S, max_retries=0)


def _audio_part(audio_bytes, fmt, host):
    b64 = base64.b64encode(audio_bytes).decode()
    # DashScope/Qwen-Omni đòi "data:audio/<fmt>;base64,...", còn Gemini/OpenAI từ chối
    # đúng dạng đó (lỗi 400 "Failed to base64-decode") -> chọn theo host, không cần cấu hình.
    data = f"data:audio/{fmt};base64,{b64}" if "dashscope" in host else b64
    return {"type": "input_audio", "input_audio": {"data": data, "format": fmt}}


def allowed_tempos(pulse, alts=(), bpm_map=()):
    """Các tempo hợp lệ = octave family của từng pulse đo được, chặn 25..240.

    `alts` là chính các octave mà trình duyệt đã chấm — chỉ nạp thêm nguyên giá trị,
    không nhân đôi tiếp (nếu không sẽ mở ra những số phi thực tế như 25 hay 400).
    """
    fam = set()
    if pulse:
        fam.add(round(float(pulse), 2))
    fam |= {round(float(seg["bpm"]), 2) for seg in (bpm_map or ()) if seg.get("bpm")}
    out = {round(p * f, 2) for p in fam for f in _TEMPO_RULES if 25 <= p * f <= 240}
    out |= {round(float(a), 2) for a in alts or () if a and 25 <= float(a) <= 240}
    return sorted(out)


def _segment_tempos(t, pulse, bpm_map, allowed):
    """Với pulse của khúc chứa thời điểm t, liệt kê các tempo hợp lệ quanh nó."""
    base = pulse
    for seg in bpm_map or ():
        if seg["t0"] <= t < seg["t1"]:
            base = seg["bpm"]
            break
    near = {a for a in allowed if base and any(abs(a - base * f) < 0.51 for f in _TEMPO_RULES)}
    return sorted(near) or allowed


def snap_tempos(sections, pulse, allowed, bpm_map=None):
    """Ép BPM mỗi đoạn về tempo hợp lệ *gần nhất* (tính theo quãng 8).

    Model được quyền chọn nhịp đôi/nhịp nửa, nhưng chỉ trong tập do phép đo đưa ra;
    số ngoài tập đó bị nắn lại. Trả về (số đoạn đã sửa, các tempo đã dùng).
    """
    if not allowed:
        return 0, sorted({s["bpm"] for s in sections})
    changed, t = 0, 0.0
    for s in sections:
        pool = _segment_tempos(t, pulse, bpm_map, allowed)
        want = float(s["bpm"])
        target = min(pool, key=lambda a: abs(math.log2(a / want))) if pool else want
        s["bpm"] = int(target) if float(target).is_integer() else target
        num = int(str(s["time_signature"]).split("/")[0])
        t += s["bars"] * num * 60 / target
        if abs(want - target) > 0.01:
            changed += 1
    return changed, sorted({s["bpm"] for s in sections})


def _implied_seconds(sections):
    total = 0.0
    for s in sections:
        num = int(str(s["time_signature"]).split("/")[0])
        total += s["bars"] * num * 60 / float(s["bpm"])
    return total


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def anchor_text(seconds, pulse, allowed, spread, locked=False):
    """Mẩu prompt neo thời lượng + tempo.

    locked=True khi người dùng tự chọn tempo trên web -> không còn là đề xuất nữa,
    mọi đoạn bắt buộc dùng đúng số đó. Không locked thì model được quyền chọn cách
    ký hiệu (pulse / nhịp đôi / nhịp nửa) trong tập do phép đo đưa ra.
    """
    if not pulse and not seconds:
        return ""
    lines = [""]
    if pulse and allowed:
        if locked:
            opts = ", ".join(f"{a:g}" for a in allowed)
            lines.append(f"TEMPO ĐÃ CHỌN = {pulse:g} BPM — người dùng chỉ định trực tiếp. "
                         f"Mọi `bpm` của các đoạn PHẢI là {opts}. Cấm chọn số khác, cấm "
                         "đổi tempo để khớp độ dài bài.")
        else:
            lines.append(f"TEMPO ĐO ĐƯỢC = {pulse:g} BPM (pulse) — đo bằng bộ đếm nhịp trên chính "
                         "tệp này, không phải ước lượng. Đây là sự thật về nhịp, không phải thứ để bàn.")
            opts = ", ".join(f"{a:g}" for a in allowed)
            lines.append("NHIỆM VỤ CỦA BẠN: nghe và TỰ QUYẾT cách ký hiệu tempo cho bài — chọn MỘT "
                         f"trong các giá trị hợp lệ sau: {opts}. Cùng một pulse, chỉ khác cách ghi "
                         "(nhịp đôi/nửa). Hãy cân nhắc thể loại và cảm giác nhịp: ballad chậm thường "
                         "ghi nửa pulse, trống nhanh gấp đôi thì ghi pulse. Cấm dùng số ngoài danh sách.")
            lines.append("Nếu bài thực sự đổi nhịp giữa chừng, chọn tempo cho *từng đoạn* trong cùng "
                         "danh sách hợp lệ đó.")
            if spread:
                lines.append(f"Pulse đo được khác nhau theo vị trí: {spread}.")
    if seconds:
        bpm_hint = f"{allowed[0]:g}" if len(allowed) == 1 else "tempo bạn đã chọn"
        fix = "sửa SỐ BAR" if len(allowed) == 1 else "sửa SỐ BAR hoặc chọn tempo khác trong danh sách hợp lệ"
        lines.append(f"ĐỘ DÀI BÀI = {seconds:.0f} giây. Tổng thời lượng các đoạn "
                     f"(bars × số phách × 60 / {bpm_hint}) phải xấp xỉ {seconds:.0f} giây; nếu chưa "
                     f"khớp thì {fix}, không bịa số. Đừng điền số bar lẻ (13, 15, 17) để lấp cho vừa — "
                     "hãy dùng 4/8/12/16.")
    return "\n".join(lines)


def _response_format():
    if config.RESPONSE_FORMAT == "none":
        return None
    return {"type": "json_object"}          # json_schema: provider không có strict schema thì hạ cấp


# Dấu hiệu provider từ chối tham số ép JSON. Không so rộng ("unsupported"…) để tránh
# retry vô ích khi lỗi là việc khác (sai key, hết quota).
_JSON_REJECT = ("response_format", "json_object", "json_schema")


def _chat(client, kwargs, say):
    """Gọi chat completions, tự hạ cấp nếu provider không tiêu được response_format.

    `AI_RESPONSE_FORMAT` là biến của server còn model là của *từng thiết bị*, nên không
    có cấu hình nào đúng cho mọi người — bắt lỗi rồi retry một lần không có tham số đó
    (hàm `_extract` đã biết bóc JSON khỏi ```chắn code và văn bản thừa).
    """
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as e:
        msg = _provider_error(e).lower()
        if "response_format" not in kwargs or not any(h in msg for h in _JSON_REJECT):
            raise
        say(0.3, "model không nhận response_format — gọi lại không dùng")
        return client.chat.completions.create(**{k: v for k, v in kwargs.items() if k != "response_format"})


def _extract(content):
    text = _FENCE.sub("", (content or "").strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AiError("Model không trả về JSON. Thử lại, hoặc đổi model hỗ trợ âm thanh.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise AiError(f"JSON của model không parse được: {e}") from e


# Model sai loại là lỗi người dùng gặp nhiều nhất: whisper chỉ nhận endpoint
# transcription, không có chat completions -> nói rõ phải dùng model chat nào.
_WRONG_KIND = (
    ("does not support chat completions",
     "Đây là model chỉ dành cho endpoint transcription (whisper…), không gọi được bằng chat. "
     "Cần model chat có khả năng nghe audio — dòng Gemini flash, gpt-4o-audio-preview, "
     "qwen-omni-turbo… chứ không phải model ASR."),
    ("unsupported modalities",
     "Model chat này không nhận đầu vào âm thanh — chọn model có hỗ trợ audio input."),
)


def _provider_error(e):
    """openai.APIStatusError chỉ có 'Error code: 400' — lý do thật nằm trong e.body.

    Ba dạng body đã gặp: GCP trả MẢNG ([{"error": {...}}]), OpenAI trả dict
    {"error": {...}}, một số proxy trả thẳng {"message", "type"} — moi cả ba, nếu
    không người dùng chỉ thấy "Error code: 400".
    """
    body = getattr(e, "body", None)
    if isinstance(body, list):
        body = next((x for x in body if isinstance(x, dict)), None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("status") or err)
            code = err.get("code") or getattr(e, "status_code", "")
            if code:
                msg = f"{msg} (code {code})"
        elif body.get("message"):
            msg = str(body["message"])
            if body.get("type"):
                msg = f"{msg} (type {body['type']})"
        else:
            msg = str(body)
    else:
        msg = str(e)[:300]
    low = msg.lower()
    for needle, hint in _WRONG_KIND:
        if needle in low:
            return f"{msg} — {hint}"
    return msg


def ping(creds):
    """Gọi nhẹ nhất có thể để xem key/URL/model có dùng được không (không gửi audio)."""
    try:
        resp = _client(creds).chat.completions.create(
            model=creds.model, messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=1, temperature=0)
    except Exception as e:
        raise AiError(f"Gọi model thất bại: {_provider_error(e)}") from e
    choice = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    return {"ok": True, "host": creds.host, "model": creds.model, "reply": choice[:20]}


def analyze(creds, audio_bytes=None, fmt="mp3", progress=None, seconds=0.0,
            pulse=0.0, alts=(), bpm_map=None, locked=False):
    """Trả về dict {base, sections, measure} sẵn cho khung "Nhập JSON" của web.

    Không có `title`: model từng đoán tên bài từ lời hát và sai/be tên người dùng gõ,
    nên tên bài giờ do người dùng nhập trên web (xem static/ai_layout.js `apply()`).
    `locked=True` khi người dùng tự chọn tempo: tập hợp lệ rút còn đúng một giá trị,
    model không còn quyền đổi cách ký hiệu.
    """
    say = progress or (lambda frac, label: None)
    allowed = [pulse] if (locked and pulse) else allowed_tempos(pulse, alts, bpm_map)
    bpm_map = () if locked else (bpm_map or ())
    spread = ", ".join(f"{seg['bpm']:g} BPM từ {seg['t0']:.0f}s đến {seg['t1']:.0f}s"
                       for seg in bpm_map) if len({s["bpm"] for s in bpm_map}) > 1 else ""

    say(0.1, "đang gửi âm thanh cho model")
    user_text = "Phân tích bố cục bài hát này." + anchor_text(seconds, pulse, allowed, spread, locked)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    _audio_part(audio_bytes, fmt, creds.host)]}]

    kwargs = dict(model=creds.model, messages=messages, temperature=config.TEMPERATURE,
                  max_tokens=config.MAX_TOKENS)
    if _response_format():
        kwargs["response_format"] = _response_format()

    say(0.25, f"model {creds.model} đang nghe")
    try:
        resp = _chat(_client(creds), kwargs, say)
    except AiError:
        raise
    except Exception as e:
        raise AiError(f"Gọi model thất bại: {type(e).__name__}: {_provider_error(e)}") from e

    say(0.8, "đang kiểm tra cấu trúc")
    raw = _extract(resp.choices[0].message.content)
    sections = validate({"sections": raw.get("sections")})   # lỗi -> ValueError (tiếng Việt)
    snapped, chosen = snap_tempos(sections, pulse, allowed, bpm_map)
    implied = _implied_seconds(sections)
    base = raw.get("base") or {}
    return {
        "base": {"bpm": sections[0]["bpm"],
                 "time_signature": base.get("time_signature") or sections[0]["time_signature"],
                 "sample_rate": int(base.get("sample_rate") or 48000)},
        "sections": sections,
        "measure": {"pulse": float(pulse or 0), "allowed": allowed, "chosen": chosen,
                    "snapped": snapped, "locked": bool(locked and pulse),
                    "seconds": float(seconds or 0),
                    "implied_seconds": round(implied, 1),
                    "coverage": round(implied / seconds * 100) if seconds else None},
    }
