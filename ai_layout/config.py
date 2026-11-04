"""Cấu hình vận hành cho module "đọc bố cục bài hát bằng AI" (ai_layout).

Không còn bất kỳ secret nào ở đây: API key / base URL / model là do **từng thiết bị**
điền trên web và gửi kèm từng request (xem routes.py). File `ai.env` ở gốc dự án
(chỉ là biến môi trường, không có key) đặt trần dung lượng, thời lượng, số job
và công tắc `AI_LAYOUT_ENABLED`. Biến môi trường thật luôn thắng file.

Module này không import gì từ app.py: xoá thư mục ai_layout/ và bỏ comment
dòng include trong templates/index.html là tính năng biến mất hoàn toàn.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.environ.get("AI_LAYOUT_ENV") or os.path.join(ROOT, "ai.env")


def _read_env_file(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass                             # chưa tạo file -> dùng hết giá trị mặc định
    return out


_FILE = _read_env_file(ENV_FILE)


def _get(key, default=""):
    return os.environ.get(key) or _FILE.get(key) or default


def _flag(key, default="0"):
    return _get(key, default).lower() in ("1", "true", "yes", "on")


def _num(key, default, cast=float):
    try:
        return cast(_get(key, str(default)))
    except ValueError:
        return cast(default)


# --- công tắc: TẮT theo mặc định để không bao giờ lộ ra production ---
ENABLED = _flag("AI_LAYOUT_ENABLED")

# --- chất lượng gọi model (KHÔNG phải thông tin đăng nhập) ---
TEMPERATURE = _num("AI_TEMPERATURE", 0.2)
MAX_TOKENS = _num("AI_MAX_TOKENS", 4000, int)
# json_object | json_schema | none — provider không hỗ trợ thì hạ về none
RESPONSE_FORMAT = _get("AI_RESPONSE_FORMAT", "json_object")
TIMEOUT_S = _num("AI_TIMEOUT_S", 180)

# --- âm thanh gửi cho model ---
MAX_UPLOAD_MB = _num("AI_MAX_UPLOAD_MB", 25)
MAX_AUDIO_SECONDS = _num("AI_MAX_ANALYZE_SECONDS", 1200)
# WAV tự hạ xuống mono ở SR thấp (không cần FFmpeg); format khác gửi nguyên bản
WAV_TARGET_SR = _num("AI_WAV_TARGET_SR", 16000, int)

# --- chống spam ---
MAX_JOBS = _num("AI_MAX_JOBS", 1, int)
JOBS_PER_HOUR = _num("AI_JOBS_PER_HOUR", 10, int)
# hostname được phép gọi tới, phân tách bằng dấu phẩy. Rỗng = mọi host công khai qua https
# (vẫn bị netguard chặn IP nội bộ/b_metadata). Đặt list khi muốn khoá chặt production.
ALLOWED_HOSTS = [h.strip().lower() for h in _get("AI_ALLOWED_HOSTS").split(",") if h.strip()]

AUDIO_FORMATS = ("wav", "mp3", "ogg", "flac", "m4a", "webm", "mp4")


def public_status():
    """Những gì frontend cần — server không còn key nào để mà lộ."""
    return dict(
        enabled=bool(ENABLED),
        max_upload_mb=MAX_UPLOAD_MB,
        timeout_s=TIMEOUT_S,
        jobs_per_hour=JOBS_PER_HOUR,
        formats=list(AUDIO_FORMATS),
        allowed_hosts=ALLOWED_HOSTS,
    )
