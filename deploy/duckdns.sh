#!/usr/bin/env bash
# Cập nhật bản ghi A của subdomain DuckDNS về IP hiện tại của VM.
# Chạy:  DUCKDNS_DOMAIN=ten DUCKDNS_TOKEN=<token> bash deploy/duckdns.sh
#   cron: * * * * * root . /etc/band-metronome/duckdns.env && bash /opt/band-metronome/deploy/duckdns.sh
set -euo pipefail

ENV_FILE=/etc/band-metronome/duckdns.env
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

: "${DUCKDNS_DOMAIN:?Thiếu DUCKDNS_DOMAIN (vd: myband, KHÔNG gồm .duckdns.org)}"
: "${DUCKDNS_TOKEN:?Thiếu DUCKDNS_TOKEN (lấy ở duckdns.org, bên phải subdomain)}"

# &ip= (bỏ trống) = "gán IP của chính máy đang gọi" — đúng cho VM có IPv4 public cố định.
# DuckDNS trả "OK" nếu thành công, "KO" nếu token/sai tên.
res="$(curl -fsS --max-time 20 "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=")"
echo "$(date '+%F %T') duckdns -> $res"
[ "$res" = "OK" ] || exit 1
