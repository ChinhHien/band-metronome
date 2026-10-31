# Band Metronome

Soạn cấu trúc bài hát (intro, verse, chorus…) với BPM, số phách, loại nhịp riêng cho từng đoạn,
tạo file metronome WAV có giọng đọc tên đoạn, nghe thử và tải về.

## Chạy local
    python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    python app.py        # mở http://127.0.0.1:5000

- Linux cần `sudo apt install espeak-ng` để pyttsx3 đọc được marker. Không có TTS thì app vẫn chạy, chỉ bỏ qua giọng đọc.
- Windows: bật `FLASK_DEBUG=1` chỉ khi lập trình; production dùng waitress/gunicorn.
- Xuất WAV không cần FFmpeg.

## Thiết kế
- Click được tạo theo **sample tuyệt đối** nên các đoạn nối nhau không bị lệch nhịp.
- Mỗi đoạn = 1 bar đếm vào (đọc từng phách: “Verse, two, three, four” hoặc “one, two, three, four”) + số bar đã nhập. Giọng đọc được mix chồng lên click nên không lệch nhịp.
- Nhịp ghép (6/8, 9/8, 12/8) nhấn phách 1 mạnh và mỗi nhóm 3 phách nhấn vừa.
- Render chạy ở **thread nền**, UI poll `/api/progress/<job_id>` (thay vì giữ một request dài) — chịu được proxy cắt kết nối và cho % tiến độ thật theo từng đoạn.
- `/stream` và `/download` chỉ nhận tên file dạng `song_<12 hex>.wav` để chống directory traversal.
- File WAV giữ vĩnh viễn cho tới khi `output/` vượt `DISK_CAP_MB` → xoá file lâu ngày nhất (LRU).
- App public, không tài khoản: mỗi trình duyệt là một `owner` (token `X-Band-Key` sinh ở localStorage),
  nên người lạ không đọc/đổi tên/xoá bài của nhau. Bài tạo từ trước khi có cơ chế này (`owner=''`) chỉ đọc được.

## Biến môi trường
`OUT_DIR`, `HISTORY_DB` (đường dẫn tương đối tính từ thư mục app, tuyệt đối cũng được), `FLASK_DEBUG`,
`MAX_SONG_SECONDS` (30 phút), `MAX_JOBS` (2 render đồng thời), `GENS_PER_HOUR` (20/IP),
`DISK_CAP_MB` (5 GB), `MAX_BODY_KB` (512). Mặc định an toàn — xem `deploy/env.production`.

## Deploy miễn phí: Oracle Always Free + Caddy + DuckDNS
Toàn bộ host + DB + tên miền đều 0đ. DB là SQLite file tại `HISTORY_DB`, không cần dịch vụ riêng.

**Chuẩn bị (làm trên web, ~20 phút)**
1. Đăng ký Oracle Cloud Free Tier (cần thẻ tín dụng để xác minh, không trừ tiền) → chọn region gần Việt Nam (Singapore/Tokyo).
2. Tạo **Compute Instance**: ARM (A1.Flex) **2 OCPU / 12 GB RAM**, image **Ubuntu 22.04/24.04** hoặc **Oracle Linux 9**,
   **gắn IPv4 public** (không dùng Ephemeral Public IP), tải/lưu **private key SSH** ngay khi tạo.
3. VCN → Security List → Ingress: cho phép TCP **80** và **443** từ `0.0.0.0/0`.
4. Tạo repo **private** trên GitHub, push dự án này.
5. [duckdns.org](https://www.duckdns.org) → đăng nhập bằng GitHub/Google → **Add domain** → được `ten-cua-ban.duckdns.org` + **token** (giữ bí mật).

**Trên VM** (SSH vào bằng key ở bước 2)

    sudo apt update && sudo apt install -y git      # Oracle Linux: sudo dnf install -y git
    # repo private cần quyền đọc: dùng HTTPS + Personal Access Token, hoặc thêm Deploy Key vào repo
    sudo REPO_URL=https://x-access-token:github_pat_xxx@github.com/YOU/band-metronome.git \
         DOMAIN=ten-cua-ban.duckdns.org \
         bash deploy/install.sh

`install.sh` tạo user `bandmetro`, venv, `/etc/band-metronome/env`, systemd unit, Caddyfile tự HTTPS.
Firewall trong OS bật thêm nếu cần: `sudo ufw allow 80,443/tcp` (Ubuntu) hoặc
`sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload` (Oracle Linux). Sau đó trỏ DNS:

    sudo bash -c 'umask 077; printf "DUCKDNS_DOMAIN=ten-cua-ban\nDUCKDNS_TOKEN=...\n" > /etc/band-metronome/duckdns.env'
    sudo bash deploy/duckdns.sh                       # gán A record = IP VM
    echo '*/5 * * * * root bash /opt/band-metronome/deploy/duckdns.sh >/dev/null 2>&1' | sudo tee /etc/cron.d/duckdns

**Kiểm tra**: `curl -fsS https://ten-cua-ban.duckdns.org/healthz` → `{"ok":true,...}`; mở trình duyệt bấm *Tạo beat*.

**Vì sao `gunicorn -w 1`**: hàng đợi job + rate-limit nằm trong tiến trình, nhiều worker sẽ làm
`/api/progress` hỏi sai tiến trình. Muốn scale thì chuyển job sang Redis/RQ trước.

**Bảo trì**
- Cập nhật code: `sudo git -C /opt/band-metronome pull && sudo systemctl restart band-metronome`
  (hoặc chạy lại `install.sh`). Log: `journalctl -u band-metronome -f`.
- Backup: `sudo bash deploy/backup.sh` + cron 03:17 — dump `history.db` bằng SQLite backup API (an toàn với WAL), giữ 14 bản.
- Theo dõi sống/chết: `curl .../healthz` mỗi 5 phút (UptimeRobot free, cron, hoặc healthcheck của Caddy).

**Rủi ro cần biết của free tier**
- *Oracle reclaim*: instance Always Free để **không hoạt động** (CPU/mạng thấp) nhiều tuần có thể bị thu hồi.
  Giữ ấm bằng cách có người thật dùng; nếu nhận email cảnh báo, vào Console → *Instances* → **Reclaim/Resume**
  rồi chạy lại `install.sh`. Định kỳ `sudo dnf/apt upgrade` cũng giúp chứng minh instance đang dùng.
- Instance **dừng/hỏng** = mất site; toàn bộ dữ liệu phục hồi được từ `deploy/backup.sh` (xem cuối file backup.sh).
- Không có SLA, không CDN, băng thông chia sẻ; WAV 60 MB tải qua 480 Mbps (giới hạn thực tế của free tier) mất ~2 s.
- DuckDNS chỉ cho subdomain `.duckdns.org`, không có giao diện quản lý DNS; muốn domain riêng thì phải trả phí (~150k/năm cho `.xyz`/`.site` khuyến nghị tránh vì hay bị spam-flag, hoặc `.com` ~300k).
