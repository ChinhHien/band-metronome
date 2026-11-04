#!/usr/bin/env python
"""Smoke test cho Band Metronome — chạy offline, không gọi network, không cần key.

    python smoke_test.py            # chạy toàn bộ, in PASS/FAIL, exit 1 nếu có lỗi

Bao phủ những chỗ đã hỏng thật trong quá trình phát triển:
  1. engine: Đếm vào / Đếm chuyển không cộng bar, lời đọc rơi đúng bar mượn;
  2. `_estimate_seconds` (chặn MAX_SONG_SECONDS) phải khớp đúng thời lượng file render;
  3. module AI: credential theo thiết bị, guard SSRF cho base URL, snap tempo,
     prompt khoá/mở, payload theo host, đường retry khi provider bỏ `response_format`;
  4. /api/ai/*: chặn thiếu key, thiếu tệp, job không bao giờ chứa credential, kill switch;
  5. header an toàn (CSP) trên trang chính.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import wave
from types import SimpleNamespace as NS

FAILED = []
PASSED = []


def chk(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"   -> {extra}"))


# --------------------------------------------------------------------- 1+2. engine
import audio_engine.assembler as A
from audio_engine import validate


def render_track(sections, sr=22050):
    """Render ra file tạm, trả (duration, [(bar_trong_track, lời đọc)]) theo thứ tự track."""
    tracks, cues = [], []
    real_track, real_speak = A.click_track, A._speak_words
    real_speak_fn = A.speak

    def click_track(bpm, total_beats, ts, sr=A.SAMPLE_RATE):
        num = int(str(ts).split("/")[0])
        tracks.append({"bars": total_beats / num, "num": num})
        return real_track(bpm, total_beats, ts, sr=sr)

    def spy(track, beat, beat_offset, words, warnings, sr=A.SAMPLE_RATE):
        cues.append((len(tracks) - 1, beat_offset // tracks[-1]["num"], " ".join(words)))
        return []

    A.click_track, A._speak_words, A.speak = click_track, spy, lambda w, srr: None
    try:
        path = os.path.join(TMP, "smoke.wav")
        info = A.assemble_song({"sample_rate": sr, "sections": sections}, path)
        with wave.open(path) as w:
            real = w.getnframes() / w.getframerate()
    finally:
        A.click_track, A._speak_words, A.speak = real_track, real_speak, real_speak_fn
    return info, cues, real


def sec(name, bars=4, bpm=120, ts="4/4", ci=False, co=False):
    return {"name": name, "bpm": bpm, "bars": bars, "time_signature": ts,
            "count_in": ci, "count_out": co, "read_marker": True}


def case(label, sections, expect_bars, expect_cues, warn=None):
    info, cues, real = render_track(sections)
    got_bars = real / 2.0                                # mọi ca dùng 4/4 @120 BPM -> 1 bar = 2 s
    chk(f"engine/{label}: {expect_bars} bar", abs(got_bars - expect_bars) < 0.01, f"{got_bars} bar")
    got = sorted((b, w) for _, b, w in cues)
    chk(f"engine/{label}: lời đọc đúng bar", got == sorted(expect_cues), f"{got} != {sorted(expect_cues)}")
    if warn:
        chk(f"engine/{label}: cảnh báo", any(warn in x for x in info["warnings"]), info["warnings"])
    return info, sections


TMP = tempfile.mkdtemp(prefix="band-smoke-")
try:
    case("không cờ", [sec("S1"), sec("S2")], 9, [])
    case("chỉ đếm chuyển S1", [sec("S1", co=True), sec("S2")], 9, [(3, "one two three four")])
    case("chuyển+đếm vào", [sec("S1", co=True), sec("S2", ci=True)], 9,
         [(2, "one two three four"), (3, "S2 two three four")])
    case("chỉ đếm vào S2", [sec("S1"), sec("S2", ci=True)], 9, [(3, "S2 two three four")])
    case("đếm vào đoạn đầu -> bar dẫn", [sec("S1", ci=True), sec("S2")], 9, [(0, "S1 two three four")])
    case("1 bar hai cờ", [sec("S1", bars=1, ci=True, co=True), sec("S2", ci=True)], 6,
         [(0, "S1 two three four"), (0, "S2 two three four")], warn="1 bar")

    # 2. chặn MAX_SONG_SECONDS khớp file thật (kể cả khi bật đếm vào khắp nơi)
    import app as APP
    big = [sec(f"P{i}", bars=9, ci=True, co=True) for i in range(10)]
    big[-1]["count_out"] = False
    info, _, real = render_track(big)
    est = APP._estimate_seconds(validate({"sections": big}))
    chk(f"estimate khớp file render ({real:.2f}s)", abs(est - real) < 0.05, f"est={est:.2f}")

    # ------------------------------------------------------------------- 3. AI module
    from ai_layout.analyzer import (AiError, allowed_tempos, anchor_text, _audio_part,
                                    _chat, _provider_error, parse_creds, snap_tempos)
    from ai_layout.netguard import GuardError, check_base_url

    GOOD = {"X-AI-Base": "https://api.openai.com/v1", "X-AI-Model": "m", "X-AI-Key": "sk-smoke-12345678"}
    c = parse_creds(GOOD)
    chk("creds/parse hợp lệ", c.host == "api.openai.com" and "sk-smoke" not in repr(c), repr(c))
    for bad, why in [({"X-AI-Base": "http://a.com"}, "http"),
                     ({"X-AI-Base": "https://169.254.169.254"}, "metadata"),
                     ({"X-AI-Base": "https://localhost"}, "loopback"),
                     ({"X-AI-Base": "https://a.com:8443"}, "port"),
                     ({"X-AI-Key": "short"}, "key cụt"),
                     ({"X-AI-Key": "sk co space"}, "key space")]:
        try:
            parse_creds(dict(GOOD, **bad))
            chk(f"creds/chặn {why}", False, "không bị chặn")
        except AiError:
            chk(f"creds/chặn {why}", True)
    try:
        check_base_url("https://api.openai.com/v1", ["example.com"])
        chk("creds/allowlist", False)
    except GuardError:
        chk("creds/allowlist", True)

    chk("tempo/allowed 125", allowed_tempos(125) == [62.5, 125], allowed_tempos(125))
    chk("tempo/allowed 100", allowed_tempos(100, [50, 200]) == [50, 100, 200], allowed_tempos(100, [50, 200]))
    rows = [{"bpm": 96, "bars": 4, "time_signature": "4/4"}, {"bpm": 62.5, "bars": 4, "time_signature": "4/4"}]
    changed, chosen = snap_tempos(rows, 100, allowed_tempos(100))
    chk("tempo/snap về quãng 8 gần nhất", [r["bpm"] for r in rows] == [100, 50] and changed == 2,
        (rows, changed))
    chk("tempo/không đo thì không sửa", snap_tempos([{"bpm": 88, "bars": 1, "time_signature": "4/4"}],
                                                   0, [])[1] == [88])
    tk, to = anchor_text(120, 100, [100.0], "", True), anchor_text(120, 100, [50, 100, 200], "", False)
    chk("prompt/khoá: TEMPO ĐÃ CHỌN", "TEMPO ĐÃ CHỌN = 100" in tk and "TỰ QUYẾT" not in tk, tk)
    chk("prompt/mở: model tự quyết trong danh sách", "TỰ QUYẾT" in to and "50, 100, 200" in to)
    chk("payload/DashScope data URL", _audio_part(b"x", "mp3", "dashscope.aliyuncs.com")
        ["input_audio"]["data"].startswith("data:audio/mp3;base64,"))
    chk("payload/Gemini base64 thuần", not _audio_part(b"x", "mp3", "generativelanguage.googleapis.com")
        ["input_audio"]["data"].startswith("data:"))
    chk("lỗi/GCP dạng mảng", _provider_error(NS(body=[{"error": {"code": 400, "message": "Please pass a valid API key"}}],
                                              status_code=400)).startswith("Please pass a valid API key"))
    whisper_err = NS(body={"error": {"message": "The model `whisper-large-v3` does not support chat completions"}})
    chk("lỗi/OpenAI dict+hint", "does not support chat completions" in _provider_error(whisper_err)
        and "transcription" in _provider_error(whisper_err))

    calls = []

    def client_fail_json(**kw):
        class Com:
            def create(self, **kwargs):
                calls.append("response_format" in kwargs)
                if "response_format" in kwargs:
                    e = Exception("Error code: 400")
                    e.body = {"error": {"message": "Invalid model with response_format json_object"}}
                    raise e
                return NS(chose="ok")
        return NS(chat=NS(completions=Com()))
    _chat(client_fail_json(), {"model": "m", "response_format": {"type": "json_object"}},
          lambda f, l: None)
    chk("retry/hạ cấp khi provider bỏ response_format", calls == [True, False], calls)

    # --------------------------------------------------------------------- 4. routes
    import flask
    import ai_layout.routes as R
    from ai_layout import config

    seen = {}
    real_analyze, real_ping = R.analyze, R.ping
    R.analyze = lambda *a, **kw: (seen.update(kw), {"base": {}, "sections": [], "measure": {}})[1]
    R.ping = lambda creds: {"ok": True, "host": creds.host, "model": creds.model, "reply": "x"}
    flask_app = flask.Flask(__name__)
    flask_app.register_blueprint(R.bp)
    cl = flask_app.test_client()
    config.ENABLED = True
    R.MAX_JOBS = 5

    buf = io.BytesIO()
    w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b"\x00\x00" * 8000)
    raw = buf.getvalue(); w.close()

    st = cl.get("/api/ai/status").get_json()
    chk("routes/status không lộ credential", st["enabled"] is True and "model" not in st and "api_key" not in st, st)
    chk("routes/phân tích thiếu key -> 400", "chưa cấu hình" in
        (cl.post("/api/ai/analyze", data={}).get_json()["error"] or "").lower())
    chk("routes/ping thiếu model -> 400", cl.post("/api/ai/ping",
        headers={k: v for k, v in GOOD.items() if k != "X-AI-Model"}).status_code == 400)
    chk("routes/thiếu tệp -> 400",
        cl.post("/api/ai/analyze", headers=GOOD, data={}).status_code == 400)
    rr = cl.post("/api/ai/analyze", headers=GOOD,
                 data={"audio": (io.BytesIO(raw), "s.wav"), "bpm": "100", "seconds": "1",
                       "bpm_locked": "1"}, content_type="multipart/form-data")
    time.sleep(0.4)
    jid = (rr.get_json() or {}).get("job_id")
    chk("routes/202 + locked tới analyze", rr.status_code == 202 and seen.get("locked") is True
        and seen.get("pulse") == 100.0, (rr.status_code, seen))
    chk("routes/job không chứa key", not any("sk-smoke" in str(v) for v in R.JOBS.get(jid, {}).values()))
    snap = cl.get(f"/api/ai/job/{jid}").get_json()
    chk("routes/snapshot giấu alts/bpm_map", "alts" not in snap and "bpm_map" not in snap, snap)
    config.ENABLED = False
    chk("routes/tắt công tắc -> 503", cl.post("/api/ai/analyze", headers=GOOD, data={}).status_code == 503)
    config.ENABLED = True
    R.analyze, R.ping = real_analyze, real_ping

    # --------------------------------------------------------------------- 5. CSP
    csp = APP.CSP
    for need in ("default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'",
                 "https://fonts.googleapis.com", "https://fonts.gstatic.com", "img-src 'self' data:",
                 "connect-src 'self'", "frame-ancestors 'none'"):
        chk(f"CSP cho phép {need}", need in csp, csp)
    chk("CSP không cho inline script", "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
