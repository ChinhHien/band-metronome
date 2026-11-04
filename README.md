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
- Trước khi deploy, chạy `python smoke_test.py` (50 kiểm tra, offline, không cần API key): engine đếm bar,
  ước lượng thời lượng khớp file render, credential/guard SSRF, snap tempo, `/api/ai/*`, header CSP.

## Thiết kế
- Click được tạo theo **sample tuyệt đối** nên các đoạn nối nhau không bị lệch nhịp.
- *Đếm vào* và *Đếm chuyển* **không thêm bar** — chúng chỉ là lời đọc đặt trên bar đã có: đếm vào của một đoạn nằm ở bar cuối đoạn liền trước (đoạn đầu thì nằm ở bar dẫn), đếm chuyển nằm ở bar áp chót nếu đoạn sau có đếm vào, ngược lại ở bar cuối. Tổng số bar của file luôn khớp đúng số bar anh gõ cho bài gốc; muốn có bar riêng chỗ đổi tempo thì tự thêm bar thừa vào đầu đoạn.
- Nhịp ghép (6/8, 9/8, 12/8) nhấn phách 1 mạnh và mỗi nhóm 3 phách nhấn vừa.
- Render chạy ở **thread nền**, UI poll `/api/progress/<job_id>` (thay vì giữ một request dài) — chịu được proxy cắt kết nối và cho % tiến độ thật theo từng đoạn.
- `/stream` và `/download` chỉ nhận tên file dạng `song_<12 hex>.wav` để chống directory traversal.
- File WAV giữ vĩnh viễn cho tới khi `output/` vượt `DISK_CAP_MB` → xoá file lâu ngày nhất (LRU).
- App public, không tài khoản: mỗi trình duyệt là một `owner` (token `X-Band-Key` sinh ở localStorage),
  nên người lạ không đọc/đổi tên/xoá bài của nhau. Bài tạo trước khi có cơ chế này (`owner=''`) vẫn đọc được
  và **thiết bị nào sửa trước thì nhận luôn quyền sở hữu**.
- Mọi response mang `Content-Security-Policy` (xem `CSP` trong `app.py`): `script-src 'self'` — không có
  inline script lẫn `on*=` attribute, nên nội dung chèn vào HTML không tự chạy code được (quan trọng vì
  panel AI gửi key của người dùng theo từng request); `style-src` phải có `'unsafe-inline'` vì thanh
  tiến trình đặt màu/flex bằng thuộc tính `style=""`; font ngoài được phép đúng `fonts.googleapis.com`
  (CSS) và `fonts.gstatic.com` (woff); thêm `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`.

## Biến môi trường
`OUT_DIR`, `HISTORY_DB` (đường dẫn tương đối tính từ thư mục app, tuyệt đối cũng được), `FLASK_DEBUG`,
`MAX_SONG_SECONDS` (30 phút), `MAX_JOBS` (2 render đồng thời), `GENS_PER_HOUR` (20/IP),
`DISK_CAP_MB` (5 GB), `MAX_BODY_KB` (512). Mặc định an toàn — xem `deploy/env.production`.

## Tính năng AI đọc bố cục bài hát (`ai_layout/`)
Model "nghe" một bài hát (tải tệp lên) rồi trả về cấu trúc đoạn (Intro/Verse/Chorus…, BPM,
số bar, nhịp) để điền vào khung **Nhập JSON** của modal "Tạo bài hát mới".
Trong bảng kết quả có hàng **Cho mọi đoạn** với ba checkbox *Đếm vào / Đọc tên / Đếm chuyển* —
**mặc định cả ba đều BẬT** và được áp cho **mọi** đoạn model trả về; tắt ô nào thì toàn bộ đoạn bỏ cờ
đó. Ba cờ này không còn nằm trong contract của model (prompt cấm xuất `count_in`/`count_out`/`read_marker`),
và app không cộng thêm bar nào khi bật chúng — chỉ là lời đọc trên bar đã có. Panel cũng bỏ hết chữ
giải thích: không còn dòng hướng dẫn, chỉ số liệu (số đoạn, BPM, phủ % thời lượng) và nhãn ⚠ trên những
đoạn có ranh giới không có bằng chứng năng lượng (giải thích nằm trong tooltip của dòng đó).
Model **không trả tên bài**: trước đây nó đoán tên từ lời hát và ghi đè vào ô "TÊN BÀI HÁT", giờ contract
bỏ hẳn field `title` — tên là do người dùng gõ, để trống thì bài nhận "Bài hát chưa có tiêu đề"
(mặc định chung cho mọi bài không tên, cả trong `app.py` lẫn danh sách lịch sử). Bấm *Dùng cấu trúc này*
xong là panel tự reset (kết quả, tệp đã chọn, phép đo, ba ô "cho mọi đoạn" trở lại mặc định BẬT) —
mở ✨ AI lần sau là phiên mới.

Bật lên:
    pip install -r requirements.txt   # openai nằm trong đó; thiếu nó thì app vẫn chạy, chỉ mất /api/ai/*
    AI_LAYOUT_ENABLED=1 python app.py
    # (tuỳ chọn khi dev) cp ai.env.example ai.env  # để sẵn công tắc + trần, không có key

**Mỗi thiết bị dùng key riêng — server không giữ secret nào.** Người dùng bấm ✨ AI lần đầu sẽ thấy khối
*Cấu hình model* với đúng ba ô: Base URL, Model, API key (không gợi ý nhà cung cấp hay tên model nào cả).
Bấm *Kiểm tra & lưu* gọi `/api/ai/ping`
(một request văn bản 1 token, không có audio) để xác nhận key thật rồi mới ghi vào `localStorage` của
trình duyệt đó. Từ lúc này mỗi request `/api/ai/*` mang theo `X-AI-Key` / `X-AI-Base` / `X-AI-Model`;
server dựng client OpenAI từ ba giá trị này, dùng xong là bỏ — key **không** vào DB, không vào `JOBS`,
không vào log (`Creds.__repr__` cũng che key). Người khác truy cập cùng trang phải tự nhập key của họ,
và ai không nhập thì chỉ không dùng được tính năng AI, phần còn lại của app không ảnh hưởng.

`ai.env` (gitignore) giờ chỉ còn các giá trị vận hành: công tắc, trần dung lượng/thời lượng, nhiệt độ,
timeout, chống spam, allowlist host — **không còn `AI_API_KEY`/`AI_BASE_URL`/`AI_MODEL`**. Biến môi
trường thật luôn thắng file; production đặt chúng trong `deploy/env.production`.

Tách biệt có chủ đích:
- `ai_layout/` là package riêng, gắn vào app bằng **một** khối `try/except` trong `app.py` (thiếu
  `openai`, chưa bật công tắc, lỗi gì cũng chỉ ghi log `warning`, app chính vẫn chạy).
- Blueprint riêng `/api/ai/*` với job queue + rate limit riêng, không đụng `JOBS`/render/lịch sử.
- `/api/ai/status` trả cấu hình công khai (`enabled`, trần, `jobs_per_hour`, `allowed_hosts`) —
  không có field credential nào vì server chẳng có cái nào để trả.
- Trần an toàn: 25 MB / 20 phút âm thanh, 1 job đồng thời, 10 job/giờ/thiết bị (ping 30 lần/giờ);
  WAV được downmix 16 kHz mono bằng numpy (không FFmpeg). **Chỉ nhận tệp người dùng tải lên** — server
  không tải URL hộ ai nên hết đường SSRF qua file.
- `ai_layout/netguard.py` giữ đúng một việc: kiểm URL *do người dùng nhập* trước khi server gọi tới —
  bắt buộc `https://`, không userinfo, chỉ cổng 443, DNS phải trỏ ra internet (chặn private/loopback/
  link-local/reserved/multicast, kể cả `169.254.169.254`), và nếu `AI_ALLOWED_HOSTS` được đặt thì đúng
  host trong danh sách. Production nên khoá danh sách (xem `deploy/env.production`).
- Người dùng nhập base URL trỏ vào máy chủ của *chính họ* (Ollama/llama.cpp ở `127.0.0.1` hay `192.168.x`)
  sẽ bị chặn bởi thiết kế — server công khai không được phép đại diện cho mạng nội bộ. Muốn dùng model
  cục bộ thì chạy app ở mạng đó và tự nới guard, hoặc dùng `deploy/env.production` trên LAN.

Định dạng payload theo provider (đã kiểm chứng 2026-11-01 với Gemini):
- `input_audio.data` tự chọn dạng theo host: `dashscope*` nhận `data:audio/<fmt>;base64,...`, còn lại
  (Gemini/OpenAI/OpenRouter) nhận **base64 thuần** — Gemini trả `400 Failed to base64-decode audio data`
  nếu đưa data URL. Không cần biến cấu hình nữa vì provider nào cũng do người dùng chọn.
- Lý do lỗi thật của provider hiện nguyên trong panel (mở cả body dạng mảng của GCP):
  `Gọi model thất bại: Please pass a valid API key (code 400)`.
- Provider không nhận `response_format: json_object` (mỗi thiết bị một model khác nhau trong khi
  `AI_RESPONSE_FORMAT` là biến của server) → `_chat()` **tự retry một lần không có tham số đó**,
  `_extract()` bóc JSON khỏi ```chắn code; chỉ những lỗi nhắc `response_format`/`json_object`/`json_schema`
  mới kích hoạt retry, lỗi khác (sai key, hết quota) báo thẳng để người dùng thấy ngay.
- Bài >4 phút nên gửi MP3/OGG thay vì WAV: 20 phút WAV 16 kHz mono ≈ 37 MB raw ≈ 49 MB base64,
  vượt trần inline 20 MB của các gói free.

Định dạng được nhận (kiểm chứng 2026-11-01 — module tin **byte đầu tệp**, không tin phần mở rộng):
- Tệp: `wav/mp3/ogg/flac/m4a/webm/mp4` (danh sách cố định trong `ai_layout/config.py`). Một
  trang HTML hay file nhị phân đổi tên thành `.mp3` bị chặn ngay bằng 400 kèm lý do, không gửi lên model.
  Đuôi không khớp nội dung thật (vd. nội dung WAV đặt tên `.mp3`) cũng bị chặn; riêng `m4a`↔`mp4` được
  chấp nhận vì hai nhãn này dùng chung container.
- Link thì **không còn được hỗ trợ**: YouTube, trang nghe nhạc và URL động `?id=…` vốn đã dùng
  được ở đâu — muốn phân tích bài nào thì tải tệp audio của bài đó về rồi nạp.

Tempo: máy đo, model quyết cách ký hiệu (kiểm chứng 2026-11-02/03):
- Model âm thanh **không đếm được nhịp**. Cùng một tệp, `AI_TEMPERATURE=0`, ba lần gọi trả ba số khác
  nhau và đều sai 20-40% so với tempo thật (bài 125 BPM nhận 60/91/139). Nguyên nhân: audio bị cửa sổ
  hoá nên model mất đồng hồ thời gian, và yêu cầu "phủ hết bài" khiến model **tính ngược** BPM cho khớp
  độ dài nó tự đoán. Vì vậy con số nhịp đến từ phép đo, không từ model.
- Trình duyệt đo trên máy người dùng: `static/ai_layout.js` giải mã bằng Web Audio, hạ sample rate về
  ~8 kHz, lọc thông thấp ~420 Hz (bắt kick/snare), tính RMS theo khung 24 ms, tìm lưới pha nhịp
  55-200 BPM. Nghiệm thu: click-track 100 BPM đo ra 100; "EM OII — The Flob" đo 125 BPM/282,4 s;
  "RADIO — THEMÈO" đo 90 BPM/356,1 s (khớp tempo công bố).
- Panel hiện phép đo ("Đo được 125 BPM · 4:42") kèm các nút tempo **½× / 1× / 2×** của nó (họ quãng 8,
  chặn 25–240). Mặc định người dùng không cần đụng tới: việc chọn *cách ký hiệu* tempo — pulse hay nhịp
  đôi/nửa, cùng một pulse chỉ khác cách ghi — giao cho model, trong giới hạn `allowed_tempos()`. Prompt
  nói rõ "đây là sự thật về nhịp, bạn chỉ được chọn trong danh sách …". Sau khi model trả JSON,
  `snap_tempos()` nắn BPM từng đoạn về giá trị hợp lệ **gần nhất theo quãng 8**, nên không đoạn nào lọt
  khỏi tập đã đo; panel báo "model chốt 100 BPM (pulse đo 200 BPM) · phủ 98% thời lượng".
- Bấm vào một nút tempo = **khoá**: trình duyệt gửi thêm `bpm_locked=1`, server rút tập hợp lệ về đúng
  một giá trị (`analyze(..., locked=True)`), prompt đổi thành "TEMPO ĐÃ CHỌN = X … mọi đoạn PHẢI là X",
  `bpm_map` bị bỏ, và panel báo "bạn chọn X BPM". Ưu tiên: người dùng > phép đo > model.
- **Ranh giới đoạn có kiểm chứng năng lượng** (`boundaryEvidence()` trong `static/ai_layout.js`): model tự đoán chỗ đổi đoạn, nên sau khi có kết quả, trình duyệt đo mức năng lượng theo từng giây (trên envelope đã tính cho BPM) và so 3 giây mỗi phía tại đúng chỗ model cắt. Chỗ nào không "nhảy" năng lượng (z dưới cả mốc 0.6 lẫn percentil 75 của bài) thì tên đoạn bị đánh dấu ⚠, giải thích ngắn đặt trong `title` của dòng đó — không có dòng cảnh báo riêng. Không có phép đo (decode thất bại, bài <9 s) thì bỏ qua, không hiện gì.
- Số từ client là đầu vào không tin cậy: `seconds`/`bpm`/`bpm_alts`/`bpm_map` bị chặn khoảng và kiểm tra
  kiểu ngay ở `_measure_fields()`. `bpm_map` chỉ bật khi phép đo ở ba khúc lệch nhau >15% — khi đó mỗi
  đoạn chỉ được chọn trong octave family của khúc chứa nó.
- Ngoại lệ: tệp **ALAC** (Apple Lossless, thường là `.m4a` không nén) vừa không đo được vừa không gửi
  model được, vì cả Chromium lẫn Gemini đều không giải mã; xuất sang MP3/AAC rồi nạp. Trần base64
  inline của gói free là 20 MB — bài 4-6 phút nên dùng MP3.
- Bài dài có thể cần tăng `AI_TIMEOUT_S` (mặc định 180 s) vì toàn bộ tệp phải tải lên model.
- Đánh đổi đã biết: dọn dữ liệu duyệt web = mất cả key lẫn lịch sử bài (cùng cơ chế `localStorage`);
  key đi trên mạng mỗi request nên **chỉ chạy sau HTTPS**.

Tắt tính năng (một trong hai, hoặc cả hai):
1. `AI_LAYOUT_ENABLED=0` (hoặc xoá `ai.env`) → nút ✨ AI biến mất, `/api/ai/*` trả 503.
2. Đổi `{% include '_ai_layout.html' %}` ở cuối `templates/index.html` thành `{# _ai_layout.html #}`
   → page không còn dòng markup AI nào; xoá luôn thư mục `ai_layout/` nếu không cần nữa.

## Tự host trên Ubuntu + tên miền riêng (không dịch vụ ngoài)
Database là **SQLite file trên chính máy chủ đó** (`/var/lib/band-metronome/history.db`), WAV nằm trong
`/var/lib/band-metronome/output/` — không cần Postgres/MySQL/dịch vụ storage nào cả.

### Bước 0 — xác định máy có tới được từ internet không (làm TRƯỚC khi cấu hình DNS)
Thuê bao VN thường nằm sau **CGNAT**: có IP công cộng trong DNS vẫn không có ai gõ vào được.

    curl -4 https://ifconfig.co                  # IP mà internet nhìn thấy
    ip -4 route get 1.1.1.1 | head -1            # IP nội bộ của máy
    python3 -m http.server 8000 --bind 0.0.0.0   # rồi mở http://<IP-công-cộng>:8000 từ điện thoại 4G

- Vào được → **MODE A**: Caddy tự xin chứng chỉ Let's Encrypt, tên miền trỏ A record về IP đó.
- Không vào được / IP là `100.64.0.0/10`, `10.x`, `172.16-31.x` → **MODE B**: Cloudflare Tunnel
  (free, không cần thẻ, không cần mở port): đổi Name Server của `lichinhhien.id.vn` sang Cloudflare,
  thêm record `band-metronome` dạng *proxied*, chạy `cloudflared` trỏ vào `http://127.0.0.1:8000`.
- **`100.73.88.121` (Tailscale) không phải IP công cộng**: đặt A record trỏ vào đó thì chỉ máy đã login
  cùng tailnet truy cập được, và Let's Encrypt từ chối cấp chứng chỉ. Muốn lộ trình "chỉ người trong
  band dùng", dùng Tailscale Funnel thay vì tên miền.

### Bước 1 — chép code sang máy (từ Windows, đi qua Tailscale)

    cd /e/Downloads/band_metronome && tar czf - \
      --exclude venv --exclude output --exclude history.db --exclude 'history.db-*' \
      --exclude .git --exclude __pycache__ --exclude '*.pyc' --exclude ai.env band_metronome \
      | ssh <user>@100.73.88.121 'cat > /tmp/bm.tgz'

Trên máy Ubuntu:

    sudo mkdir -p /opt/band-metronome
    sudo tar xzf /tmp/bm.tgz -C /opt/band-metronome --strip-components=1

`venv/` của Windows **không** đi theo — Linux phải có venv riêng, script tự tạo.

### Bước 2 — cài đặt

    sudo DOMAIN=band-metronome.lichinhhien.id.vn bash /opt/band-metronome/deploy/setup-ubuntu.sh
    # MODE B (đằng sau Cloudflare Tunnel): sudo CADDY=0 bash deploy/setup-ubuntu.sh

Script làm: `apt` (python3-venv, sqlite3, rsync, **espeak-ng + libespeak1** để pyttsx3 đọc marker),
user `bandmetro`, `/var/lib/band-metronome`, venv + `pip install -r requirements.txt`,
`/etc/band-metronome/env`, systemd unit chạy `gunicorn -w 1 --threads 4`, Caddy tự HTTPS,
rồi **tự in ra kết quả kiểm tra TTS** (OK/FAIL) và `curl /healthz`.

Vì sao `-w 1` bắt buộc: hàng đợi job render + rate-limit nằm trong tiến trình; nhiều worker làm
`/api/progress` hỏi sai tiến trình. Muốn scale thì chuyển job sang Redis/RQ trước.

### Bước 3 — DNS (MatBao, chỉ MODE A)
Tạo bản ghi: Host `band-metronome`, Loại `A (Address)`, Giá trị = IP công cộng, TTL 300.
**Không sửa bản ghi `@`** — nó đang trỏ dịch vụ khác của anh (mail/hosting).

### Bước 4 — laptop không được ngủ

    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
    # gập nắp vẫn chạy: sửa /etc/systemd/logind.conf -> HandleLidSwitch=ignore, rồi
    sudo systemctl restart systemd-logind

### Nghiệm thu & bảo trì

    curl -fsS https://band-metronome.lichinhhien.id.vn/healthz   # {"ok":true,...}
    journalctl -u band-metronome -f          # log app      |  journalctl -u caddy -f
    sudo bash /opt/band-metronome/deploy/backup.sh             # + cron 03:17
    # cập nhật code: làm lại Bước 1 rồi  sudo systemctl restart band-metronome

### Điều phải chấp nhận khi tự host
- Mất điện / laptop tắt / router rớt = site chết (tự sống lại khi máy lên, không mất dữ liệu).
- IP công cộng thường là **động**: mỗi lần đổi phải sửa A record tay (MatBao không có API cập nhật DNS).
- Nhà mạng VN hay chặn 80/443 ở thuê bao gia đình → sẽ phải đi MODE B.
- Nút thắt thật là **băng thông upload** của mạng nhà: WAV 60 MB tải bởi khách ở xa mất 1–2 phút.
- Muốn bật tính năng AI trên server: thêm `AI_LAYOUT_ENABLED=1` vào `/etc/band-metronome/env`
  (key vẫn do từng thiết bị tự nhập, server không giữ secret nào).
