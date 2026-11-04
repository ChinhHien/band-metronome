#!/usr/bin/env bash
# Cài Band Metronome trên Ubuntu server (laptop/máy để chạy 24/7).
# Code phải đã nằm ở /opt/band-metronome (bước chép code trong README).
#
#   sudo DOMAIN=band-metronome.lichinhhien.id.vn bash deploy/setup-ubuntu.sh
#   sudo CADDY=0 bash deploy/setup-ubuntu.sh        # khi đặt sau Cloudflare Tunnel (TLS ở edge)
#
# Idempotent: chạy lại sau khi chép code mới để cập nhật (không đụng dữ liệu).
set -euo pipefail

APP_DIR=/opt/band-metronome
DATA_DIR=/var/lib/band-metronome
ENV_FILE=/etc/band-metronome/env
RUN_USER=bandmetro
DOMAIN="${DOMAIN:-}"
WITH_CADDY="${CADDY:-1}"

[ "$(id -u)" = 0 ] || { echo "Chạy bằng sudo."; exit 1; }
if [ -f /etc/os-release ]; then . /etc/os-release; fi
[ "${ID:-ubuntu}" = "ubuntu" ] || echo "!! Script viết cho Ubuntu (đã thử trên ${PRETTY_NAME:-?}); apt có thể khác."

echo ">> 1/6 gói hệ thống"
apt-get update -y
# espeak-ng = tiếng đọc marker; libespeak1 cung cấp libespeak.so.1 mà pyttsx3 nạp bằng ctypes
apt-get install -y python3 python3-venv python3-pip curl ca-certificates sqlite3 rsync \
  espeak-ng libespeak1 || apt-get install -y python3 python3-venv python3-pip curl ca-certificates sqlite3 rsync espeak-ng

if [ ! -f "$APP_DIR/app.py" ]; then
  echo "!! Không thấy $APP_DIR/app.py — chép code lên trước (README, bước 2)."
  exit 1
fi

echo ">> 2/6 user + thư mục dữ liệu"
id -u "$RUN_USER" >/dev/null 2>&1 || \
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$RUN_USER"
mkdir -p "$DATA_DIR/output" /var/backups/band-metronome /etc/band-metronome
chown -R "$RUN_USER:$RUN_USER" "$DATA_DIR" /var/backups/band-metronome
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

echo ">> 3/6 venv + requirements"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -U pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo ">> 4/6 biến môi trường"
if [ ! -f "$ENV_FILE" ]; then
  install -m 640 "$APP_DIR/deploy/env.production" "$ENV_FILE"
  echo "   đã tạo $ENV_FILE — sửa trần (MAX_SONG_SECONDS / DISK_CAP_MB) nếu cần, rồi chạy lại script."
fi

echo ">> 5/6 systemd (gunicorn 1 worker/4 thread)"
install -m 644 "$APP_DIR/deploy/band-metronome.service" /etc/systemd/system/band-metronome.service
systemctl daemon-reload
systemctl enable --now band-metronome
sleep 2
systemctl --no-pager status band-metronome | head -8 || true

echo ">> 6/6 Caddy (HTTPS)"
if [ "$WITH_CADDY" != 1 ]; then
  echo "   CADDY=0 -> bỏ Caddy. Chạy cloudflared trỏ vào http://127.0.0.1:8000."
elif [ -z "$DOMAIN" ]; then
  echo "   !! Không có DOMAIN -> bỏ Caddy. Chạy lại: sudo DOMAIN=... bash $0"
else
  command -v caddy >/dev/null 2>&1 || {
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y && apt-get install -y caddy
  }
  sed "s|band-metronome\.lichinhhien\.id\.vn|${DOMAIN}|g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile.new
  mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy
  echo "   Caddy đang phục vụ https://${DOMAIN} (Let's Encrypt tự xin khi port 80 tới được máy này)."
fi

echo
echo "================ KIỂM TRA ================"
curl -fsS http://127.0.0.1:8000/healthz && echo
echo "-- TTS (giọng đọc marker):"
cd "$APP_DIR" && sudo -u "$RUN_USER" "$APP_DIR/venv/bin/python" -c \
  "from audio_engine.tts import speak
a = speak('Verse')
print('TTS OK —', len(a), 'sample' if a is not None else 'TTS FAIL: thiếu libespeak1 (apt install libespeak1)')" \
  2>&1 | tail -3
cat <<EOF
-- Firewall trong máy (nếu bật ufw):
     sudo ufw allow 80,443/tcp && sudo ufw reload
-- Cho phép router/ISP tới được máy: xem README mục 'mở port / CGNAT'.
-- Log: journalctl -u band-metronome -f        Backup: sudo bash deploy/backup.sh
EOF
