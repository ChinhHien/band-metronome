import os
import re
import time
import uuid
import sqlite3
import threading
import logging
import json
from contextlib import closing
from datetime import datetime
from flask import Flask, jsonify, render_template, request, send_file, abort
from werkzeug.middleware.proxy_fix import ProxyFix

from audio_engine import assemble_song, validate

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
# Chạy sau Caddy/nginx: tin X-Forwarded-* để lấy IP thật + scheme https
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_BODY_KB", "512")) * 1024

_out_env = os.environ.get("OUT_DIR", "output")
OUT = _out_env if os.path.isabs(_out_env) else os.path.join(BASE, _out_env)
HISTORY_DB = os.environ.get("HISTORY_DB") or os.path.join(BASE, "history.db")
os.makedirs(OUT, exist_ok=True)
NAME_RE = re.compile(r"^song_[0-9a-f]{12}\.wav$")

# --- Giới hạn cho app public (không cần đăng nhập) ---
MAX_SONG_SECONDS = float(os.environ.get("MAX_SONG_SECONDS", "1800"))   # 30 phút âm thanh/bài
MAX_JOBS = int(os.environ.get("MAX_JOBS", "2"))                        # render đồng thời
GENS_PER_HOUR = int(os.environ.get("GENS_PER_HOUR", "20"))             # theo IP
DISK_CAP_MB = int(os.environ.get("DISK_CAP_MB", "5120"))               # trần dung lượng output/

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("metronome")
START_TS = time.time()

# Job tổng hợp đang chạy (thread nền + polling tiến độ cho UI)
JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOBS = 0
RATE = {}                      # ip -> [timestamps của giờ gần nhất]
RATE_LOCK = threading.Lock()


def _owner():
    """Nhãn thiết bị (localStorage client) để mỗi trình duyệt chỉ thấy bài của mình."""
    key = request.headers.get("X-Band-Key", "")
    return key if re.fullmatch(r"[0-9a-f]{8,64}", key or "") else ""


# --- Lịch sử bài hát (sqlite) ---
def db():
    con = sqlite3.connect(HISTORY_DB, timeout=20, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")     # job nền + request cùng ghi
    con.execute("PRAGMA busy_timeout=20000")
    return con


def init_db():
    with closing(db()) as con, con:
        con.execute("""CREATE TABLE IF NOT EXISTS history(
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT    NOT NULL,
            filename   TEXT    NOT NULL,
            duration   REAL    NOT NULL,
            created_at TEXT    NOT NULL,
            sections   TEXT    NOT NULL)""")
        for ddl in ("ALTER TABLE history ADD COLUMN updated_at TEXT",
                    "ALTER TABLE history ADD COLUMN owner TEXT NOT NULL DEFAULT ''"):
            try:                                    # DB cũ chưa có cột
                con.execute(ddl)
            except sqlite3.OperationalError:
                pass
        con.execute("CREATE INDEX IF NOT EXISTS idx_owner ON history(owner, id DESC)")


init_db()


def _enforce_disk_cap():
    """Giữ WAV vĩnh viễn cho tới khi output/ vượt trần → xoá file lâu nhất trước."""
    files = [(os.path.getmtime(os.path.join(OUT, f)), os.path.getsize(os.path.join(OUT, f)), f)
             for f in os.listdir(OUT) if NAME_RE.match(f)]
    total = sum(sz for _, sz, _ in files)
    if total <= DISK_CAP_MB * 1024 * 1024:
        return
    for _, sz, f in sorted(files):                  # mtime tăng dần
        if total <= DISK_CAP_MB * 1024 * 1024:
            break
        try:
            os.remove(os.path.join(OUT, f))
            total -= sz
            log.info("disk cap: da xoa %s (%.1f MB)", f, sz / 1e6)
        except OSError:
            pass


def safe_path(filename):
    if not NAME_RE.match(filename):          # chặn directory traversal
        abort(404)
    p = os.path.join(OUT, filename)
    if not os.path.isfile(p):
        abort(404)
    return p


def _rate_ok(ip):
    now = time.time()
    with RATE_LOCK:
        if len(RATE) > 5000:                     # tránh phình bộ nhớ theo số IP
            for k in [k for k, v in RATE.items() if not v or now - v[-1] > 3600]:
                RATE.pop(k, None)
        hits = [t for t in RATE.get(ip, []) if now - t < 3600]
        if len(hits) >= GENS_PER_HOUR:
            RATE[ip] = hits
            return False
        hits.append(now)
        RATE[ip] = hits
        return True


def _estimate_seconds(sections):
    """Thời lượng xấp xỉ (bỏ bar đếm vào bị hút sang đoạn trước) — cùng công thức với UI."""
    total = 0.0
    for i, s in enumerate(sections):
        num = int(str(s.get("time_signature", "4/4")).split("/")[0])
        nxt = sections[i + 1] if i + 1 < len(sections) else None
        absorbed = bool(s.get("count_out") and nxt and nxt.get("count_in"))
        ci = 1 if (s.get("count_in") and not absorbed) else 0
        total += (int(s["bars"]) + ci) * num * 60 / float(s["bpm"])
    return total


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    with JOBS_LOCK:
        running = ACTIVE_JOBS
    return jsonify(ok=True, active_jobs=running, uptime_s=round(time.time() - START_TS))


def _prune_jobs():
    with JOBS_LOCK:
        for jid in [k for k, v in JOBS.items()
                    if v.get("finished_at") and time.time() - v["finished_at"] > 900]:
            JOBS.pop(jid, None)


def _release_slot():
    """Trả 1 slot trong bộ đếm render đồng thời.

    Hàm riêng là bắt buộc: `ACTIVE_JOBS -= 1` viết trong worker() lồng nhau sẽ
    thành phép gán biến cục bộ (UnboundLocalError) -> slot không bao giờ được trả,
    server kẹt 'đang bận' vĩnh viễn sau MAX_JOBS lần render.
    """
    global ACTIVE_JOBS
    with JOBS_LOCK:
        ACTIVE_JOBS -= 1


def _finish_job(job, song, filename, gen_ts, owner):
    try:
        _render_and_store(job, song, filename, gen_ts, owner)
    except Exception:
        log.exception("render that")
        job.update(error="Tổng hợp thất bại (lỗi nội bộ).")
    finally:
        job.update(done=True, finished_at=time.time())


def _render_and_store(job, song, filename, gen_ts, owner):
    try:
        info = assemble_song(
            song, os.path.join(OUT, filename),
            progress_cb=lambda frac, label: job.update(
                progress=round(min(frac, 1.0) * 100), label=label))
    except ValueError as e:
        job.update(error=str(e))
        return
    # KHÔNG thêm mục lịch sử mới — chỉ cập nhật bài đang mở (nếu caller gửi history_id):
    # ghi đè bản ghi wav (xoá file cũ), độ dài và cấu trúc đoạn. Tên bài chỉ đổi qua PATCH.
    hid = song.get("history_id")
    row = None
    try:
        with closing(db()) as con, con:
            row = con.execute("SELECT id, title, filename, owner FROM history WHERE id=?", (hid,)).fetchone() if hid else None
            if row and owner and row["owner"] in (owner, ""):
                con.execute("UPDATE history SET filename=?, duration=?, sections=?, updated_at=?, owner=? WHERE id=?",
                            (filename, info["duration"],
                             json.dumps(song.get("sections") or [], ensure_ascii=False), gen_ts, owner, row["id"]))
                if row["filename"] != filename:        # thay bản ghi cũ của chính bài này
                    old = os.path.join(OUT, row["filename"])
                    if NAME_RE.match(row["filename"]) and os.path.isfile(old):
                        try:
                            os.remove(old)
                        except OSError:
                            pass
            elif row:
                os.remove(os.path.join(OUT, filename))  # bài của thiết bị khác → huỷ bản vừa tạo
                job.update(error="Bài này thuộc về thiết bị khác. Bấm “＋ Tạo bài mới” rồi render lại.")
                return
    except sqlite3.Error as e:
        job.update(error=f"Lỗi ghi lịch sử: {e}")
        return
    _enforce_disk_cap()
    job.update(result=dict(filename=filename, history_id=row["id"] if row else None,
                           title=row["title"] if row else "",
                           stream=f"/stream/{filename}", download=f"/download/{filename}", **info),
                progress=100)


@app.post("/api/generate")
def generate():
    global ACTIVE_JOBS
    _prune_jobs()
    song = request.get_json(silent=True) or {}
    try:
        sections = validate(song)                    # lỗi cấu trúc → 400
    except ValueError as e:
        return jsonify(error=str(e)), 400
    est = _estimate_seconds(sections)
    if est > MAX_SONG_SECONDS:
        return jsonify(error=(f"Bài quá dài ({est / 60:.0f} phút). Giới hạn {MAX_SONG_SECONDS / 60:.0f} phút — "
                              "giảm số đoạn hoặc số bar.")), 400
    ip = request.remote_addr or "?"
    if not _rate_ok(ip):
        return jsonify(error=f"Bạn đã tạo metronome {GENS_PER_HOUR} lần trong 1 giờ. Hãy thử lại sau."), 429
    owner = _owner()
    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_JOBS:
            return jsonify(error="Máy chủ đang bận tổng hợp, chờ một chút rồi bấm lại."), 429
        ACTIVE_JOBS += 1
    job_id = uuid.uuid4().hex[:12]
    filename = f"song_{job_id}.wav"
    job = {"done": False, "progress": 0, "label": "đang khởi động", "finished_at": None}
    with JOBS_LOCK:
        JOBS[job_id] = job

    def worker():
        try:
            _finish_job(job, song, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), owner)
        finally:
            _release_slot()

    threading.Thread(target=worker, daemon=True).start()
    log.info("generate job=%s ip=%s sections=%d est=%.0fs", job_id, ip, len(sections), est)
    return jsonify(job_id=job_id)


@app.get("/api/progress/<job_id>")
def progress(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        snapshot = dict(job) if job else None
    if snapshot is None:
        abort(404)
    return jsonify(snapshot)


@app.get("/api/history")
def history_list():
    owner = _owner()
    with closing(db()) as con:
        rows = con.execute("SELECT id, title, filename, duration, created_at FROM history "
                           "WHERE owner=? OR owner='' ORDER BY id DESC LIMIT 100", (owner,)).fetchall()
    return jsonify([{"id": r["id"], "title": r["title"], "filename": r["filename"],
                     "duration": r["duration"], "created_at": r["created_at"],
                     "file_exists": bool(r["filename"]) and os.path.isfile(os.path.join(OUT, r["filename"]))}
                    for r in rows])


@app.post("/api/history")
def history_create():
    """Tạo bài nháp làm mục lịch sử mới (dùng nút "＋ Tạo bài mới")."""
    data = request.get_json(silent=True) or {}
    title = (str(data.get("title") or "").strip() or "Bài hát mới")[:120]
    with closing(db()) as con, con:
        cur = con.execute(
            "INSERT INTO history(title, filename, duration, created_at, sections, updated_at, owner) "
            "VALUES(?,?,?,?,?,?,?)",
            (title, "", 0.0, datetime.now().strftime("%Y-%m-%d %H:%M"), "[]", "", _owner()))
        return jsonify(id=cur.lastrowid), 201


def _row_for(hid, owner, write=False):
    """Dòng lịch sử thuộc về thiết bị này.

    Dòng ẩn danh (owner='') — tạo trước khi có cơ chế chủ — ai cũng đọc được, và
    thiết bị ghi đầu tiên sẽ nhận nó làm của mình thay vì bị chặn.
    """
    with closing(db()) as con:
        row = con.execute("SELECT id, title, filename, duration, sections, updated_at, owner "
                          "FROM history WHERE id=?", (hid,)).fetchone()
    if row is None or row["owner"] not in (owner, ""):
        abort(404)
    if write and not owner:
        abort(403)                              # không có device token → chỉ đọc
    if write and row["owner"] not in ("", owner):
        abort(403)                              # bài của thiết bị khác
    return row


@app.get("/api/history/<int:hid>")
def history_get(hid):
    row = _row_for(hid, _owner())
    try:
        sections = json.loads(row["sections"])
    except ValueError:
        sections = []
    ts = None
    if row["updated_at"]:
        try:
            ts = int(datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        except ValueError:
            ts = None
    return jsonify(id=row["id"], title=row["title"], filename=row["filename"],
                   duration=row["duration"], sections=sections, generated_ts=ts,
                   file_exists=bool(row["filename"]) and os.path.isfile(os.path.join(OUT, row["filename"])))


@app.patch("/api/history/<int:hid>")
def history_rename(hid):
    """Đổi tên bài hát (menu trong lịch sử)."""
    data = request.get_json(silent=True) or {}
    title = (str(data.get("title") or "").strip() or "Bài hát mới")[:120]
    owner = _owner()
    _row_for(hid, owner, write=True)
    with closing(db()) as con, con:
        con.execute("UPDATE history SET title=?, owner=? WHERE id=?", (title, owner, hid))
    return jsonify(ok=True, id=hid, title=title)


@app.delete("/api/history/<int:hid>")
def history_delete(hid):
    owner = _owner()
    row = _row_for(hid, owner, write=True)
    with closing(db()) as con, con:
        con.execute("DELETE FROM history WHERE id=? AND (owner=? OR owner='')", (hid, owner))
    p = os.path.join(OUT, row["filename"])     # dọn luôn file wav nếu còn
    if NAME_RE.match(row["filename"]) and os.path.isfile(p):
        try:
            os.remove(p)
        except OSError:
            pass  # file đang được phát trên Windows → _enforce_disk_cap sẽ dọn sau
    return jsonify(ok=True)


@app.get("/stream/<filename>")
def stream(filename):
    return send_file(safe_path(filename), mimetype="audio/wav", conditional=True)


@app.get("/download/<filename>")
def download(filename):
    raw = request.args.get("title", "metronome")
    clean = re.sub(r"[^\w\-]+", "_", raw).strip("._")[:50] or "metronome"
    return send_file(safe_path(filename), mimetype="audio/wav", as_attachment=True,
                     download_name=f"{clean}_metronome.wav")


@app.errorhandler(404)
def not_found(e):
    return jsonify(error="Không tìm thấy."), 404


@app.errorhandler(403)
def forbidden(e):
    return jsonify(error="Bài này thuộc về thiết bị khác."), 403


@app.errorhandler(413)
def too_large(e):
    return jsonify(error="Nội dung gửi lên quá lớn."), 413


if __name__ == "__main__":
    # local dev: python app.py (debug chỉ bật khi FLASK_DEBUG=1)
    # production: xem deploy/ (gunicorn + Caddy)
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")),
            debug=debug, threaded=True)
