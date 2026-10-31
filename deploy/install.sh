#!/usr/bin/env bash
# Cài Band Metronome lên VPS Oracle Always Free (Oracle Linux 8/9 hoặc Ubuntu/Debian).
#
#   sudo REPO_URL=git@github.com:YOU/band-metronome.git \
#        DOMAIN=ten-cua-ban.duckdns.org \
#        bash deploy/install.sh
#
# Idempotent: chạy lại sau khi `git push` để cập nhật code (không xoá dữ liệu).
set -euo pipefail

APP_DIR=/opt/band-metronome
DATA_DIR=/var/lib/band-metronome
ENV_FILE=/etc/band-metronome/env
RUN_USER=bandmetro
REPO_URL="${REPO_URL:?Set REPO_URL to your private git repo (SSH or https)}"
DOMAIN="${DOMAIN:-}"

[ "$(id -u)" = 0 ] || { echo "Chạy với sudo."; exit 1; }

if [ -f /etc/os-release ]; then . /etc/os-release; fi
case "${ID:-}" in
  ubuntu|debian) PM=apt ;;
  ol|rhel|fedora|centos|amzn|almalinux|rocky) PM=dnf ;;
  *) echo "Không nhận diện được distro ('$ID'). Xem các bước thủ công trong README."; exit 1 ;;
esac
echo ">> Distro: ${PRETTY_NAME:-?} -> $PM"

# ---------------------------------------------------------------- packages
install_pkgs() {
  if [ "$PM" = apt ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip git curl ca-certificates sqlite3
    apt-get install -y espeak-ng || echo "!! espeak-ng chưa cài -> app chạy bình thường, chỉ mất giọng đọc marker."
  else
    dnf install -y epel-release || true          # espeak-ng nằm ở EPEL trên RHEL/OL
    dnf install -y python3 python3-pip git curl tar sqlite espeak-ng \
      || dnf install -y python3 python3-pip git curl tar sqlite \
      || echo "!! espeak-ng chưa cài -> mất giọng đọc marker."
  fi
}
install_pkgs

# ---------------------------------------------------------------- caddy
if command -v caddy >/dev/null 2>&1; then
  echo ">> Caddy đã có, bỏ qua bước cài."
elif [ "$PM" = apt ]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
else
  dnf install -y dnf-plugins-core
  dnf copr enable -y @caddy/caddy
  dnf install -y caddy
fi

# ---------------------------------------------------------------- user + data
id -u "$RUN_USER" >/dev/null 2>&1 || \
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$RUN_USER"
mkdir -p "$DATA_DIR/output" /var/backups/band-metronome /etc/band-metronome
chown -R "$RUN_USER:$RUN_USER" "$DATA_DIR" /var/backups/band-metronome

# ---------------------------------------------------------------- code
if [ -d "$APP_DIR/.git" ]; then
  echo ">> Đã có repo, cập nhật."
  # --ff-only: từ chối nếu có commitlocal/tay-đeo đã sửa trong /opt -> báo lỗi chứ không đè
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" || true

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -U pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------- config
[ -f "$ENV_FILE" ] || { install -m 640 "$APP_DIR/deploy/env.production" "$ENV_FILE"; echo ">> Đã tạo $ENV_FILE (chỉnh trần nếu cần)."; }

if [ -n "$DOMAIN" ]; then
  sed "s/<ten>\.duckdns.org/${DOMAIN}/g; s|you@example.com|ops@${DOMAIN}|" \
    "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile.new
  if [ -f /etc/caddy/Caddyfile ] && ! grep -q duckdns /etc/caddy/Caddyfile; then
    cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.orig.$(date +%F)"
    echo ">> Đã lưu Caddyfile cũ -> /etc/caddy/Caddyfile.orig.*"
  fi
  mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile
  systemctl enable --now caddy && systemctl reload caddy
  echo ">> Caddy cấu hình cho https://$DOMAIN (chứng chỉ Let's Encrypt sẽ xin ở request đầu tiên)."
else
  echo "!! Không có DOMAIN -> bỏ qua Caddy. Chạy lại script với DOMAIN=... sau khi trỏ DuckDNS."
fi

# ---------------------------------------------------------------- service
install -m 644 "$APP_DIR/deploy/band-metronome.service" /etc/systemd/system/band-metronome.service
systemctl daemon-reload
systemctl enable --now band-metronome
systemctl --no-pager status band-metronome | head -12 || true

echo
echo "=========================== KIỂM TRA ==========================="
sleep 2
curl -fsS http://127.0.0.1:8000/healthz && echo
[ -n "$DOMAIN" ] && curl -fsS "https://$DOMAIN/healthz" && echo
cat <<EOF

Mở firewall (2 việc này làm ở web, không làm được từ shell):
  1. Oracle Cloud Console -> VCN -> Security List -> thêm Ingress TCP 80 + 443 (0.0.0.0/0).
  2. Firewall trong OS:
       $([ "$PM" = apt ] && echo "sudo ufw allow 80,443/tcp && sudo ufw reload" || echo "sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload")
Backup:      sudo bash deploy/backup.sh            (cron: xem đầu file)
Cập nhật code: sudo git -C $APP_DIR pull && sudo systemctl restart band-metronome
EOF
