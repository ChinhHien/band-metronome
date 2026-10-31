#!/usr/bin/env bash
# Sao lưu lịch sử bài hát (sqlite) + gương (mirror) thư mục WAV.
#   cron (hàng ngày 03:17):
#   17 3 * * * root bash /opt/band-metronome/deploy/backup.sh >> /var/log/band-metronome-backup.log 2>&1
set -euo pipefail

DATA_DIR="${DATA_DIR:-/var/lib/band-metronome}"
DB="${HISTORY_DB:-$DATA_DIR/history.db}"
OUT="${OUT_DIR:-$DATA_DIR/output}"
BK="${BK_DIR:-/var/backups/band-metronome}"
KEEP_DB="${KEEP_DB:-14}"          # giữ 14 bản dump lịch sử
PY="${PY:-/opt/band-metronome/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

mkdir -p "$BK/db" "$BK/output"
# flock: không cho 2 lần backup chạy chồng nhau (cron retry / chạy tay)
exec 9>"$BK/.lock"; flock -n 9 || { echo "backup đang chạy, thoát."; exit 0; }

STAMP="$(date +%F_%H%M%S)"

# 1) lịch sử bài hát — bản nhỏ (vài trăm KB), phục hồi được 100% tên bài/cấu trúc đoạn.
#    Dùng API backup của sqlite (an toàn với WAL + job nền đang ghi), không copy file thô.
"$PY" - "$DB" "$BK/db/history-$STAMP.sqlite" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)                     # snapshot nhất quán, không cần app dừng
print(f"ok db -> {dst}")
PY
chmod 600 "$BK/db/history-$STAMP.sqlite"

# 2) file WAV — mirror tăng dần (rsync) nếu có rsync; không có thì bỏ qua vì WAV
#    luôn render lại được từ cấu trúc đoạn trong history.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --human-readable "$OUT/" "$BK/output/" && echo "ok output mirror"
else
  echo "(không có rsync -> bỏ qua mirror WAV; apt/dnf install rsync để bật)"
fi

# 3) xoá bản dump cũ, giữ KEEP_DB gần nhất
ls -1t "$BK"/db/history-*.sqlite 2>/dev/null | tail -n +$((KEEP_DB + 1)) | while read -r f; do
  rm -f "$f"
done

# 4) báo dung lượng để phát hiện trần DISK_CAP_MB phình to bất thường
du -sh "$BK/db" "$BK/output" "$OUT" 2>/dev/null || true

# Phục hồi:  cp /var/backups/band-metronome/db/history-<stamp>.sqlite /var/lib/band-metronome/history.db
#            systemctl restart band-metronome
