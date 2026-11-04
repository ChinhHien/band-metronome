"""Blueprint riêng cho tính năng AI đọc bố cục: /api/ai/*

Server chỉ là cầu nối: key/base URL/model do thiết bị gửi kèm từng request qua header
và **không bao giờ được lưu lại** (không vào DB, không vào log, không vào job dict).
Chỉ nhận tệp audio người dùng tải lên — không tải URL hộ ai, nên không có đường SSRF
cho tệp; SSRF chỉ còn ở ô base URL, và nó bị netguard chặn.

Tự chứa job queue + rate limit riêng (không đụng JOBS/MAX_JOBS của app.py) và trả 503
khi chưa bật AI_LAYOUT_ENABLED.
"""
import json
import logging
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from . import config
from .analyzer import AiError, analyze, parse_creds, ping
from .audio import AudioError, prepare

log = logging.getLogger("ai_layout")
bp = Blueprint("ai_layout", __name__, url_prefix="/api/ai")

JOBS = {}
LOCK = threading.Lock()
ACTIVE = 0
HITS = {}                       # (bucket, thiết bị) -> [ts], tách khỏi rate limit của app.py
HITS_LOCK = threading.Lock()


@bp.before_request
def _allow_big_uploads():
    """app.py đặt MAX_CONTENT_LENGTH ~512 KB cho JSON; route này cần MB cho tệp audio.

    Giới hạn chỉ nới riêng /api/ai/* (Werkzeug đọc request.max_content_length lúc
    parse form), các endpoint chính vẫn giữ trần chống body khổng lồ.
    """
    request.max_content_length = int(config.MAX_UPLOAD_MB * 1024 * 1024) + 64 * 1024
    return None


def _owner():
    key = request.headers.get("X-Band-Key", "")
    return key if len(key) in (8, 16, 32, 36, 64) else (request.remote_addr or "?")


def _rate_ok(bucket, limit):
    now = time.time()
    with HITS_LOCK:
        hits = [t for t in HITS.get((bucket, _owner()), []) if now - t < 3600]
        if len(hits) >= limit:
            HITS[(bucket, _owner())] = hits
            return False
        hits.append(now)
        HITS[(bucket, _owner())] = hits
        return True


def _error(e):
    """AiError/GuardError -> JSON cho panel. Key không bao giờ xuất hiện ở đây."""
    return jsonify(error=str(e) or "Yêu cầu không hợp lệ."), 400


def _measure_fields():
    """Đọc số đo BPM + thời lượng mà trình duyệt tính trên chính tệp đang tải lên.

    Số từ client là đầu vào không tin cậy: chặn khoảng, bỏ bảng tempo sai kiểu.
    `bpm_locked=1` nghĩa là con số đó do người dùng bấm chọn chứ không phải máy đo
    tự đưa ra -> server rút tập tempo hợp lệ về đúng một giá trị.
    """
    def num(key, lo, hi):
        try:
            v = float(request.form.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
        return v if lo <= v <= hi else 0.0

    seconds = num("seconds", 0.5, 12 * 3600)
    pulse = num("bpm", 25, 240)
    locked = request.form.get("bpm_locked") in ("1", "true", "yes")
    alts = []
    try:
        for a in (json.loads(request.form.get("bpm_alts") or "[]") or [])[:6]:
            v = float(a)
            if 25 <= v <= 240:
                alts.append(round(v, 2))
    except (TypeError, ValueError):
        alts = []
    segs = []
    try:
        for seg in (json.loads(request.form.get("bpm_map") or "[]") or [])[:12]:
            t0, t1 = float(seg.get("t0")), float(seg.get("t1"))
            b = float(seg.get("bpm"))
            if 0 <= t0 < t1 <= seconds and 25 <= b <= 240:
                segs.append({"t0": t0, "t1": t1, "bpm": round(b, 2)})
    except (TypeError, ValueError, AttributeError):
        segs = []
    return seconds, pulse, alts, segs, locked


def _run_job(job, creds, audio, fmt):
    global ACTIVE
    result, error = None, None
    try:
        result = analyze(creds, audio_bytes=audio, fmt=fmt,
                         seconds=job["seconds"], pulse=job["pulse"], alts=job["alts"],
                         bpm_map=job["bpm_map"], locked=job["locked"],
                         progress=lambda frac, label: job.update(
                             progress=round(min(max(frac, 0), 1) * 100), label=label))
    except (AiError, AudioError, ValueError) as e:
        error = str(e)
    except Exception:
        log.exception("ai layout job")            # creds tự ẩn trong repr, nhưng vẫn không log nó
        error = "Lỗi nội bộ khi phân tích (xem log server)."
    with LOCK:
        ACTIVE -= 1                 # nhả slot TRƯỚC khi báo done: client poll thấy xong là bấm lại được
    job.update(done=True, progress=100, **({"error": error} if error else {"result": result}))
    job.update(finished_at=time.time())


def _prune():
    with LOCK:
        for jid in [k for k, v in JOBS.items()
                    if v.get("finished_at") and time.time() - v["finished_at"] > 900]:
            JOBS.pop(jid, None)


@bp.get("/status")
def status():
    return jsonify(config.public_status())


@bp.post("/ping")
def config_ping():
    """Kiểm tra cấu hình của thiết bị: gọi model 1 token, không đụng tới audio."""
    if not config.ENABLED:
        return jsonify(error="Tính năng đang tắt (AI_LAYOUT_ENABLED=1)."), 503
    if not _rate_ok("ping", max(config.JOBS_PER_HOUR * 3, 5)):
        return jsonify(error="Thử cấu hình quá nhiều lần trong 1 giờ — chờ sau."), 429
    try:
        creds = parse_creds(request.headers, config.ALLOWED_HOSTS)
        return jsonify(ping(creds))
    except AiError as e:
        return _error(e)


@bp.post("/analyze")
def analyze_start():
    global ACTIVE
    if not config.ENABLED:
        return jsonify(error="Tính năng đang tắt (AI_LAYOUT_ENABLED=1 trong môi trường server)."), 503
    try:
        creds = parse_creds(request.headers, config.ALLOWED_HOSTS)
    except AiError as e:
        return _error(e)
    if not _rate_ok("analyze", config.JOBS_PER_HOUR):
        return jsonify(error=f"Đã dùng {config.JOBS_PER_HOUR} lần trong 1 giờ — thử lại sau."), 429
    _prune()

    upload = request.files.get("audio")
    if not (upload and upload.filename):
        return jsonify(error="Chọn tệp âm thanh trước."), 400

    max_bytes = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    seconds, pulse, alts, bpm_map, locked = _measure_fields()
    if seconds > config.MAX_AUDIO_SECONDS:
        return jsonify(error=f"Bài dài {seconds / 60:.1f} phút, trần phân tích là "
                             f"{config.MAX_AUDIO_SECONDS / 60:.0f} phút."), 400

    audio = upload.read(max_bytes + 1)
    if not audio:
        return jsonify(error="Tệp rỗng."), 400
    if len(audio) > max_bytes:
        return jsonify(error=f"Tệp vượt trần {config.MAX_UPLOAD_MB} MB."), 413
    try:
        audio, fmt, _ = prepare(audio, upload.filename, config.WAV_TARGET_SR, config.MAX_AUDIO_SECONDS)
    except AudioError as e:
        return jsonify(error=str(e)), 400

    with LOCK:
        if ACTIVE >= config.MAX_JOBS:
            return jsonify(error="Đang có một phân tích chạy — chờ xong rồi bấm lại."), 429
        ACTIVE += 1
        job = {"done": False, "progress": 2, "label": "đang khởi động",
               "seconds": seconds, "pulse": pulse, "alts": alts, "bpm_map": bpm_map,
               "locked": locked, "finished_at": None}
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job, creds, audio, fmt), daemon=True).start()
    # ghi host/model/số đo — không ghi key
    log.info("ai analyze job=%s host=%s model=%s bytes=%d do=%s@%.0fs map=%d", job_id,
             creds.host, creds.model, len(audio), pulse or "chưa đo", seconds, len(bpm_map))
    return jsonify(job_id=job_id), 202


@bp.get("/job/<job_id>")
def job_progress(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        internal = {"alts", "bpm_map", "finished_at"}
        snapshot = {k: v for k, v in job.items() if k not in internal} if job else None
    if snapshot is None:
        return jsonify(error="Không tìm thấy phiên phân tích."), 404
    return jsonify(snapshot)
