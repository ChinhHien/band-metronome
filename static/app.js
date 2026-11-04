const COLORS = ["--c0", "--c1", "--c2", "--c3", "--c4"];
const SIGS = ["2/4", "3/4", "4/4", "5/4", "6/8", "7/8", "9/8", "12/8"];
/* Gợi ý tên đoạn (datalist trên ô "Tên đoạn" — vẫn tự gõ được tên khác) */
const SECTION_NAMES = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Post-Chorus", "Refrain",
  "Interlude", "Bridge", "Solo", "Breakdown", "Outro", "Coda"];
/* Chuẩn đầu ra (sample rate) — phải khớp audio_engine.click_generator.RATES. Mặc định 48 kHz. */
const RATES = [
  ["22050", "22.05 kHz · nhẹ"],
  ["44100", "44.1 kHz · chuẩn CD"],
  ["48000", "48 kHz · chuẩn video"],
];
const $ = (id) => document.getElementById(id);

/* Nhãn thiết bị: app public không đăng nhập, mỗi trình duyệt chỉ thấy bài của mình */
const DEVICE_KEY = "band-metronome-device";
const deviceId = (() => {
  try {
    let d = localStorage.getItem(DEVICE_KEY);
    if (!d || !/^[0-9a-f]{8,64}$/.test(d)) {
      d = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(16) + Math.random().toString(16).slice(2))
        .replace(/-/g, "");
      localStorage.setItem(DEVICE_KEY, d);
    }
    return d;
  } catch { return "00000000"; }
})();
const api = (url, opts = {}) => fetch(url, { ...opts, headers: { "X-Band-Key": deviceId, ...(opts.headers || {}) } });
const list = $("sections");
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}`;

/* Đọc cấu hình toàn bài từ ô nhập ở header (không hardcode giá trị nào) */
const baseBpm = () => Number($("base-bpm").value) || null;
const baseSig = () => $("base-sig").value || null;
const setBase = (bpm, sig, rate) => {
  if (bpm) $("base-bpm").value = bpm;
  if (sig && SIGS.includes(sig)) $("base-sig").value = sig;
  if (rate && RATES.some(([v]) => String(rate) === v)) $("base-srate").value = String(rate);
};
/* BPM chưa hợp lệ/không có thì hiển thị "—" (ô nhập để trống là null, không phải 0) */
const bpmLabel = (s) => (Number(s.bpm) >= 20 && Number(s.bpm) <= 400 ? s.bpm : "—");

/* Tên bài giờ nằm trên mục lịch sử (bài đang mở) — đổi qua menu chuột phải */
let activeTitle = "Bài hát chưa có tiêu đề";

/* Tạo đoạn mới theo cấu hình toàn bài.
   "Đếm vào" và "Đếm chuyển" chỉ quyết định LỜI ĐỌC trên bar đã có, không thêm bar:
   đếm vào của đoạn nằm ở bar cuối đoạn trước (hoặc bar dẫn nếu là đoạn đầu),
   đếm chuyển nằm ở bar áp chót khi đoạn sau có đếm vào, nếu không thì ở bar cuối.
   "Đọc tên" (read_marker) bật/tắt việc đọc tên đoạn: bật → "Intro 2 3 4",
   tắt → chỉ "one two three four". */
const makeSection = (name) => ({
  name,
  bpm: baseBpm(),
  bars: 4,
  time_signature: baseSig() || SIGS[2],
  count_in: true,
  count_out: true,   /* mặc định bật: đếm chuyển ở cuối đoạn, độc lập với đoạn sau */
  read_marker: true,
});

/* Các session thật — bar lead (1 bar click) do engine tự thêm trước đoạn đầu,
   lấy BPM + nhịp từ ô ở trên. Session đầu ĐƯỢC PHÉP có đếm vào ("Intro 2 3 4"). */
let sections = [makeSection("Intro")];

/* Thời lượng một đoạn = đúng số bar của nó, không cộng bar đếm nào cả. */
const secs = (s) => {
  const num = Number(s.time_signature.split("/")[0]);
  const bpm = Number(s.bpm);
  return (Number(s.bars) * num * 60) / (bpm >= 20 && bpm <= 400 ? bpm : 120);
};
const noBpm = () => sections.filter((s) => !(Number(s.bpm) >= 20 && Number(s.bpm) <= 400)).length;

let dragSrc = null;

/* === Tự lưu nháp (localStorage): mất trang/F5 không mất cấu trúc đang soạn === */
const DRAFT_KEY = "band-metronome-drafts";
const draftBucket = () => { try { return JSON.parse(localStorage.getItem(DRAFT_KEY)) || {}; } catch { return {}; } };
const draftOf = (id) => draftBucket()[id === null || id === undefined ? "new" : String(id)];
const dropDraft = (id) => {
  const b = draftBucket(), k = String(id);
  if (b[k]) { delete b[k]; localStorage.setItem(DRAFT_KEY, JSON.stringify(b)); }
};
/* "Có nháp chưa tạo metronome" chỉ khi người dùng thực sự sửa kể từ lần render gần
   nhất. Tự lưu lúc F5/beforeunload phải giữ nguyên cờ này, nếu không mọi bài đã tạo
   xong đều bị hỏi khôi phục nháp sau khi tải lại trang. */
let unsaved = false;
function saveDraft() {
  try {
    const b = draftBucket();
    b[activeHistoryId ?? "new"] = {
      ts: Date.now(), dirty: unsaved, activeTitle,
      state: { sections, base_bpm: baseBpm(), base_sig: baseSig(), base_rate: $("base-srate").value },
    };
    Object.keys(b).filter((k) => k !== "__open" && k !== String(activeHistoryId ?? "new"))
      .forEach((k) => delete b[k]);             /* bài đã sinh/xoá → nháp cũ không cần giữ */
    b.__open = activeHistoryId ?? "new";
    localStorage.setItem(DRAFT_KEY, JSON.stringify(b));
  } catch { /* localStorage đầy / bị chặn → bỏ qua, không chặn thao tác */ }
}
let draftTimer = null;
const scheduleDraft = () => { unsaved = true; clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 500); };
const afterChange = () => { drawTimeline(); scheduleDraft(); };
window.addEventListener("beforeunload", () => saveDraft());

function loadDraftState(d) {
  const st = d.state || {};
  sections = Array.isArray(st.sections) && st.sections.length ? st.sections : sections;
  setBase(st.base_bpm, st.base_sig, st.base_rate);
  activeTitle = d.activeTitle || activeTitle;
  unsaved = Boolean(d.dirty);
  render();
}

/* === Thanh tổng: mỗi đoạn 1 ô theo đúng tỉ lệ thời gian, tên + BPM + nhịp === */
function drawTimeline() {
  const total = sections.reduce((a, s, i) => a + secs(s, i), 0);
  const w = $("timeline").clientWidth || 860;
  $("timeline").innerHTML = sections.map((s, i) => {
    const d = secs(s, i);
    const px = d / (total || 1) * w;
    const name = String(s.name || "").trim();
    const cell = `<div style="flex:${d};background:var(${COLORS[i % COLORS.length]})" `
      + `title="${esc(name || "Chưa đặt tên")}, ${bpmLabel(s)} BPM, ${s.bars} bar">`;
    /* đoạn không có tên -> chỉ là ô màu trống; thông tin vẫn xem được khi rê chuột */
    if (!name) return `${cell}</div>`;
    const meta = px >= 60 ? `<span class="tl-meta">${bpmLabel(s)} · ${esc(s.time_signature)}</span>` : "";
    const nm = px >= 34 ? `<span class="tl-name">${esc(name)}</span>` : "";
    return `${cell}${nm}${meta}</div>`;
  }).join("");
  const miss = noBpm();
  $("total").textContent = sections.length
    ? `${sections.length} đoạn, tổng ${fmt(total)}`
      + (miss ? ` · ${miss} đoạn chưa có BPM hợp lệ` : "")
    : "Chưa có đoạn nào. Bấm “Thêm đoạn” để bắt đầu.";
}

function render() {
  list.innerHTML = "";
  sections.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "sec";
    if (!(Number(s.bpm) >= 20 && Number(s.bpm) <= 400)) li.classList.add("bad");
    li.style.setProperty("--c", `var(${COLORS[i % COLORS.length]})`);
    li.sectionData = s;
    li.innerHTML = `
      <div class="bar"></div>
      <div class="drag-handle" draggable="true" aria-label="Kéo để di chuyển" title="Kéo để di chuyển">≡</div>
      <div class="fields">
        <label>Tên đoạn<input class="name" data-k="name" list="name-suggestions" value="${esc(s.name)}"></label>
        <div class="row">
          <label>BPM<input type="number" data-k="bpm" min="20" max="400" value="${s.bpm ?? ""}"></label>
          <label>Nhịp<select data-k="time_signature">${SIGS.map(
            (t) => `<option ${t === s.time_signature ? "selected" : ""}>${t}</option>`).join("")}</select></label>
          <label>Số bar<input type="number" data-k="bars" min="1" max="128" value="${s.bars}"></label>
        </div>
        <div class="checks">
          <label class="check"><input type="checkbox" data-k="count_in" ${s.count_in ? "checked" : ""}>Đếm vào</label>
          <label class="check"><input type="checkbox" data-k="read_marker" ${s.read_marker !== false ? "checked" : ""} ${s.count_in ? "" : "disabled"} title="Trong bar đếm vào: đọc tên đoạn hay chỉ đếm one two three four">Đọc tên</label>
          <label class="check"><input type="checkbox" data-k="count_out" ${s.count_out ? "checked" : ""}>Đếm chuyển</label>
        </div>
      </div>
      <div class="mv">
        <button data-a="up" class="mv-b" aria-label="Đưa đoạn lên" ${i === 0 ? "disabled" : ""}>▲</button>
        <button data-a="down" class="mv-b" aria-label="Đưa đoạn xuống" ${i === sections.length - 1 ? "disabled" : ""}>▼</button>
        <button data-a="del" class="del" aria-label="Xoá đoạn">✕</button>
      </div>`;
    li.querySelectorAll("[data-k]").forEach((el) =>
      el.addEventListener("input", (e) => {
        const k = el.dataset.k;
        s[k] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
        if (k === "bpm") li.classList.toggle("bad", !(Number(s.bpm) >= 20 && Number(s.bpm) <= 400));
        if (k === "count_in") {
          const rm = el.closest(".fields").querySelector("[data-k='read_marker']");
          if (rm) rm.disabled = !s.count_in;    /* không đếm vào → không đọc tên */
        }
        afterChange();
      }));
    /* ↑/↓: sắp xếp bằng cảm ứng (drag chỉ chạy trên desktop) */
    li.querySelectorAll(".mv [data-a]").forEach((b) => b.addEventListener("click", () => {
      const a = b.dataset.a;
      if (a === "del") sections.splice(i, 1);
      else {
        const j = a === "up" ? i - 1 : i + 1;
        if (j < 0 || j >= sections.length) return;
        [sections[i], sections[j]] = [sections[j], sections[i]];
      }
      render();
      scheduleDraft();
    }));

    /* ---- Drag & drop reorder ---- */
    const handle = li.querySelector(".drag-handle");
    handle.addEventListener("dragstart", (e) => {
      dragSrc = li;
      li.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      const img = new Image();
      img.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1' height='1'%3E%3C/svg%3E";
      e.dataTransfer.setDragImage(img, 0, 0);
    });
    handle.addEventListener("dragend", () => {
      li.classList.remove("dragging");
      if (dragSrc) {
        sections = [...list.querySelectorAll("li.sec")].map((el) => el.sectionData);
        render();
        scheduleDraft();
      }
      dragSrc = null;
    });
    li.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (!dragSrc || dragSrc === li) return;
      const rect = li.getBoundingClientRect();
      const after = e.clientY - rect.top > rect.height / 2;
      list.insertBefore(dragSrc, after ? li.nextSibling : li);
    });
    list.appendChild(li);
  });
  drawTimeline();
}

/* Bar lead (1 bar click trước session đầu) lấy BPM + nhịp từ ô ở trên */
$("base-bpm").addEventListener("input", () => {
  if (sections[0]) sections[0].bpm = baseBpm();
  scheduleDraft();
});
$("base-sig").addEventListener("change", () => {
  if (sections[0]) sections[0].time_signature = baseSig();
  scheduleDraft();
});
$("base-srate").addEventListener("change", scheduleDraft);
let resizeT = null;
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(drawTimeline, 150); });

$("add").onclick = () => {
  const bpm = baseBpm(), sig = baseSig();
  if (!bpm || !sig) {
    $("status").className = "err";
    $("status").textContent = "Hãy nhập BPM và chọn loại nhịp trước khi thêm đoạn.";
    return;
  }
  $("status").textContent = "";
  sections.push(makeSection(`Section ${sections.length}`));
  render();
};

/* === Điều khiển phát: thanh tổng quát hiển thị progress + tua bằng bấm/kéo === */
const preview = $("preview");

function updateProgress() {
  const bar = $("tl-progress");
  if (!preview.src || !preview.duration || Number.isNaN(preview.duration)) {
    bar.hidden = true;
    $("tl-wrap").classList.remove("seekable");
    return;
  }
  bar.hidden = false;
  $("tl-wrap").classList.add("seekable");
  bar.style.width = `${(preview.currentTime / preview.duration) * 100}%`;
}
preview.addEventListener("emptied", updateProgress);

/* Nút phát/dừng + nhãn thời gian (thay cho thanh native ẩn đi) */
function setPlayIcon() { $("play-btn").textContent = preview.paused ? "▶" : "❚❚"; }
function timeLabel() {
  if (!preview.src || !preview.duration || Number.isNaN(preview.duration))
    $("now-time").textContent = "0:00 / 0:00";
  else
    $("now-time").textContent = `${fmt(preview.currentTime)} / ${fmt(preview.duration)}`;
}
$("play-btn").addEventListener("click", () =>
  preview.paused ? preview.play().catch(() => {}) : preview.pause());
preview.addEventListener("play", setPlayIcon);
preview.addEventListener("pause", setPlayIcon);
preview.addEventListener("timeupdate", () => { updateProgress(); timeLabel(); });
preview.addEventListener("loadedmetadata", () => { updateProgress(); timeLabel(); });
preview.addEventListener("emptied", timeLabel);

/* Bấm / giữ kéo trên thanh tổng quát để tua bản ghi */
function seekFromEvent(e) {
  if (!preview.src || !preview.duration || Number.isNaN(preview.duration)) return;
  const rect = $("tl-wrap").getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  preview.currentTime = frac * preview.duration;
  updateProgress();
}
let seeking = false;
$("tl-wrap").addEventListener("pointerdown", (e) => {
  seeking = true;
  $("tl-wrap").classList.add("seeking");      /* tua thủ công → không áp transition mượt */
  $("tl-wrap").setPointerCapture(e.pointerId);
  seekFromEvent(e);
});
$("tl-wrap").addEventListener("pointermove", (e) => { if (seeking) seekFromEvent(e); });
$("tl-wrap").addEventListener("pointerup", () => {
  seeking = false;
  $("tl-wrap").classList.remove("seeking");
});

/* === Phím tắt phát: Space play/pause · ←/→ tua 5s (bỏ qua khi đang gõ) === */
document.addEventListener("keydown", (e) => {
  if (!$("gate").hidden || !$("name-modal").hidden) return;
  const t = e.target;
  if (t && t.nodeType === 1 && (t.closest("input, textarea, select") || t.isContentEditable)) return;
  if (!preview.src) return;
  if (e.code === "Space") {
    e.preventDefault();
    preview.paused ? preview.play().catch(() => {}) : preview.pause();
  } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    preview.currentTime = Math.max(0, Math.min(preview.duration || 0,
      preview.currentTime + (e.key === "ArrowRight" ? 5 : -5)));
    updateProgress(); timeLabel();
  }
});

$("generate").onclick = async () => {
  const btn = $("generate"), st = $("status"), bar = $("gen-progress"), fill = $("gen-progress-bar");
  if (!sections.length) { st.className = "err"; st.textContent = "Chưa có đoạn nào để tạo."; return; }
  if (noBpm()) { st.className = "err"; st.textContent = "Còn đoạn chưa có BPM hợp lệ (20–400) — điền BPM rồi hãy tạo."; return; }
  btn.disabled = true; st.className = ""; st.textContent = "Đang khởi động…"; bar.hidden = false; fill.style.width = "2%";
  try {
    /* KHÔNG thêm mục lịch sử — chỉ cập nhật bài đang mở (history_id), nếu có */
    const r = await api("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history_id: activeHistoryId, sample_rate: Number($("base-srate").value), sections }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "Không tạo được file.");
    let res = d;
    if (d.job_id) {                         /* backend trả job → poll tiến độ thật */
      const deadline = Date.now() + 300000; /* trần 5 phút chờ, không treo vô hạn */
      let miss = 0;
      for (;;) {
        await new Promise((ok) => setTimeout(ok, 400));
        const pr = await api(`/api/progress/${d.job_id}`).catch(() => null);
        if (!pr || !pr.ok) {
          if (pr && pr.status === 404) throw new Error("Không tìm thấy phiên tổng hợp — server có thể vừa khởi động lại, hãy bấm lại.");
          if (++miss >= 5) throw new Error("Mất kết nối khi đang tổng hợp.");
          continue;
        }
        miss = 0;
        const j = await pr.json();
        if (j.error) throw new Error(j.error);
        if (j.label) { st.textContent = `Đang tổng hợp ${j.progress}% — ${j.label}`; fill.style.width = `${Math.max(2, j.progress)}%`; }
        if (j.done) { res = j.result; break; }
        if (Date.now() > deadline) throw new Error("Tổng hợp quá lâu, đã huỷ chờ. Hãy rút ngắn bài rồi thử lại.");
      }
    }
    if (!res || !res.stream) throw new Error("Không nhận được kết quả từ server.");
    bar.hidden = true;
    if (res.title) activeTitle = res.title;
    $("result").hidden = false;
    preview.src = res.stream;
    preview.play().catch(() => {});
    $("dl").href = `${res.download}?title=${encodeURIComponent(activeTitle)}`;
    st.className = "ok"; st.textContent = `Đã tạo xong ở ${(res.sample_rate / 1000)} kHz, dài ${fmt(res.duration)}.`
      + (res.warnings.length ? " " + res.warnings.join(" ") : "");
    unsaved = false;
    saveDraft();                                /* nháp giờ khớp bản ghi → không hỏi khôi phục nữa */
    if (activeHistoryId) loadHistory();   /* làm mới meta của bài đang mở */
  } catch (e) {
    st.className = "err"; st.textContent = e.message;
  } finally {
    btn.disabled = false; bar.hidden = true;
  }
};

/* === JSON cấu trúc bài: dán vào lúc tạo bài mới + xuất từ menu chuột phải lịch sử === */
const jsonName = (title) => `${(title || "bai-hat").replace(/[^\w\-]+/g, "_").slice(0, 50)}_structure.json`;

function downloadJson(title, data) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  a.download = jsonName(title);
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/* {title?, base?, sections:[{name,bpm,bars,time_signature,count_in,count_out,read_marker}]} */
const songJson = (title, secs, rate) => ({
  app: "band-metronome", version: 1, title,
  base: { bpm: secs[0]?.bpm ?? null, time_signature: secs[0]?.time_signature ?? "4/4",
          sample_rate: Number(rate) || Number($("base-srate").value) },
  sections: secs,
});

function parseSongJson(text) {
  if (!text.trim()) throw new Error("Chưa dán nội dung JSON vào khung.");
  const data = JSON.parse(text);
  const src = Array.isArray(data.sections) ? data.sections : Array.isArray(data) ? data : null;
  if (!src || !src.length) throw new Error("JSON không có mảng sections hợp lệ.");
  if (src.length > 100) throw new Error("Tối đa 100 đoạn.");
  /* đoạn bỏ trống time_signature → lấy theo base, cuối cùng mới đến 4/4 */
  const baseTs = SIGS.includes(String(data.base?.time_signature)) ? String(data.base.time_signature) : "4/4";
  const out = src.map((s, i) => {
    const bpm = Number(s.bpm), bars = Math.round(Number(s.bars));
    const ts = SIGS.includes(String(s.time_signature)) ? String(s.time_signature) : baseTs;
    if (!(bpm >= 20 && bpm <= 400)) throw new Error(`Đoạn ${i + 1}: BPM phải từ 20 đến 400.`);
    if (!(bars >= 1 && bars <= 128)) throw new Error(`Đoạn ${i + 1}: số bar phải từ 1 đến 128.`);
    return { name: String(s.name || `Section ${i + 1}`).slice(0, 60), bpm, bars, time_signature: ts,
             count_in: Boolean(s.count_in), count_out: Boolean(s.count_out),
             read_marker: s.read_marker !== false };
  });
  return { sections: out, base: data.base || null, title: data.title ? String(data.title).slice(0, 120) : "" };
}

/* Xuất JSON của một bài trong lịch sử (menu chuột phải) */
async function exportHistoryJson(id) {
  const st = $("status");
  try {
    const d = await (await api(`/api/history/${id}`)).json();
    if (!Array.isArray(d.sections) || !d.sections.length) {
      st.className = "err"; st.textContent = "Bài này chưa có cấu trúc đoạn nào để xuất.";
      return;
    }
    downloadJson(d.title || "Bài hát chưa có tiêu đề", songJson(d.title || "", d.sections, d.sample_rate));
    st.className = "ok"; st.textContent = `Đã xuất cấu trúc của “${d.title || "Bài hát chưa có tiêu đề"}” ra JSON.`;
  } catch { st.className = "err"; st.textContent = "Không xuất được JSON."; }
}

/* === Lịch sử bài hát (sqlite) === */
let activeHistoryId = null;

const histItemHTML = (h) => `
  <li class="hist ${h.id === activeHistoryId ? "active" : ""}">
    <button data-hid="${h.id}" class="hist-open" title="Bấm mở bài · chuột phải / ⋮: đổi tên / tải / xoá">
      <span class="hist-title">${esc(h.title || "Bài hát chưa có tiêu đề")}</span>
      <span class="hist-meta">${esc(h.created_at)} · ${fmt(h.duration)}${h.file_exists ? "" : h.filename ? " · file đã xoá" : " · nháp"}</span>
    </button>
    <button data-hid="${h.id}" class="hist-kebab" aria-label="Tuỳ chọn bài hát">⋮</button>
  </li>`;

let histItems = [];                       /* danh sách đã tải, để lọc tại chỗ không gọi lại server */
/* bỏ dấu + thường hoá để "dat phuong" vẫn tìm ra "Đất phương nam" */
const fold = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .toLowerCase().replace(/đ/g, "d").trim();

function renderHistoryList() {
  const q = fold($("hist-search-input").value);
  const shown = q ? histItems.filter((h) => fold(h.title).includes(q)) : histItems;
  $("history-list").innerHTML = shown.map(histItemHTML).join("")
    + (shown.length || !q ? "" : '<li class="hist-empty">Không tìm thấy kết quả nào</li>');
}

async function loadHistory() {
  try {
    histItems = await (await api("/api/history")).json();
    renderHistoryList();
  } catch { /* im lặng khi chưa có server */ }
}
$("hist-search-input").addEventListener("input", renderHistoryList);

/* Nạp bản ghi của một bài vào thanh phát — KHÔNG tự phát: mở bài từ lịch sử là để
   người dùng tự bấm ▶ (phím Space cũng được). */
function showRecording(src) {
  $("result").hidden = false;
  preview.src = src;
  $("dl").href = `${src.replace("/stream/", "/download/")}?title=${encodeURIComponent(activeTitle)}`;
  setPlayIcon();
  timeLabel();
}
function stopAudio() {
  preview.pause();
  preview.removeAttribute("src");
  preview.load();
  $("result").hidden = true;
  $("tl-progress").hidden = true;
  $("tl-wrap").classList.remove("seekable");
  updateProgress();
}

/* Ô BPM/Nhịp/Xuất ở header không có cột riêng trong DB: BPM + Nhịp suy ra từ đoạn
   đầu (bar lead cũng lấy theo đoạn đầu), sample rate đọc từ bài vừa mở. */
const syncBase = (secs, rate) => {
  const f = secs && secs[0];
  if (!f) return;
  setBase(Number(f.bpm) || null, f.time_signature, rate);
};

/* Chữ ký cấu trúc đã chuẩn hoá: server trả bpm dạng 96.0, nháp lưu 96 → so trực tiếp sẽ lệch */
const secKey = (secs) => JSON.stringify((secs || []).map((s) => [
  String(s.name ?? ""), Number(s.bpm) || 0, Number(s.bars) || 0, String(s.time_signature ?? ""),
  Boolean(s.count_in), Boolean(s.count_out), s.read_marker !== false,
]));

/* Mở lại một bài từ lịch sử: khôi phục soạn thảo + phát bản ghi nếu còn */
async function openHistory(id) {
  const st = $("status");
  try {
    const d = await (await api(`/api/history/${id}`)).json();
    const dr = draftOf(d.id);               /* nháp chưa sinh nằm ở localStorage */
    const hasDraft = dr && dr.dirty && secKey(dr.state?.sections) !== secKey(d.sections)
      && (!d.filename || dr.ts > (d.generated_ts || 0));
    /* đang mở đúng bài này với nháp của nó (tự khôi phục lúc tải trang) → khỏi hỏi lại */
    if (hasDraft && (activeHistoryId === d.id || confirm(`“${d.title || "Bài hát chưa có tiêu đề"}” có nháp chưa tạo metronome (lưu lúc ${new Date(dr.ts).toLocaleString()}). Khôi phục nháp?`))) {
      activeHistoryId = d.id;
      loadDraftState(dr);
      if (!baseBpm()) syncBase(sections, d.sample_rate);
    } else {
      dropDraft(d.id);
      if (Array.isArray(d.sections) && d.sections.length) {
        sections = d.sections;
        syncBase(sections, d.sample_rate);
        unsaved = false;                    /* vừa nạp đúng bản trên server → không còn nháp */
      }
      activeHistoryId = d.id;
      activeTitle = d.title || "Bài hát chưa có tiêu đề";
      render();
    }
    loadHistory();
    if (d.file_exists) {
      showRecording(`/stream/${d.filename}`);
      st.className = "ok"; st.textContent = `Đã mở lại “${activeTitle}” từ lịch sử.`;
    } else {
      stopAudio();
      st.className = ""; st.textContent = "";
    }
  } catch { st.className = "err"; st.textContent = "Không tải được bài từ lịch sử."; }
}

/* === Modal nhập tên (+ dán JSON khi tạo bài mới) === */
let nameCallback = null;
const importBox = $("import-box");
const setImportOpen = (open) => {
  importBox.hidden = !open;
  $("name-import").textContent = open ? "Ẩn JSON ▴" : "Nhập JSON ▾";
  if (!open) $("import-err").hidden = true;
};
function openNameModal({ heading, value = "", requireText, okLabel = "Xác nhận", allowImport = false, onOk }) {
  $("name-heading").textContent = heading;
  $("name-input").value = value;
  $("name-ok").textContent = okLabel;
  $("name-hint").textContent = requireText ? "Hãy đặt tên cho bài hát." : "";
  $("name-hint").hidden = true;
  $("name-import").hidden = !allowImport;
  $("import-text").value = "";
  setImportOpen(false);
  nameCallback = onOk;
  $("name-modal").hidden = false;
  setTimeout(() => $("name-input").focus(), 0);
}
function closeNameModal() { $("name-modal").hidden = true; nameCallback = null; }
$("name-import").addEventListener("click", () => {
  setImportOpen(importBox.hidden);
  if (!importBox.hidden) $("import-text").focus();
});
$("name-ok").addEventListener("click", () => {
  const v = $("name-input").value.trim();
  if (!v && $("name-hint").textContent) { $("name-hint").hidden = false; $("name-input").focus(); return; }
  let imported = null;
  if (!importBox.hidden) {
    const err = $("import-err");
    try {
      imported = parseSongJson($("import-text").value);
      err.hidden = true;
    } catch (e) {
      err.hidden = false;
      err.textContent = `JSON chưa hợp lệ: ${e.message}`;
      $("import-text").focus();
      return;
    }
  }
  nameCallback && nameCallback(v || "Bài hát chưa có tiêu đề", imported);
  closeNameModal();
});
$("name-cancel").addEventListener("click", closeNameModal);
$("name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("name-ok").click(); });
$("name-modal").addEventListener("keydown", (e) => { if (e.key === "Escape") closeNameModal(); });

/* Tạo bài mới: đặt tên (+ dán cấu trúc JSON) → mục lịch sử tạo luôn, nút “Tạo metronome” chỉ ghi đè */
function createDraft() {
  openNameModal({
    heading: "Tạo bài hát mới", requireText: false, okLabel: "Tạo mới", allowImport: true,
    onOk: async (name, imported) => {
      const st = $("status");
      try {
        const r = await api("/api/history", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: name, sections: imported ? imported.sections : [] }) });
        if (!r.ok) throw new Error((await r.json()).error || "");
        const d = await r.json();
        activeHistoryId = d.id;
        activeTitle = name;
        if (imported) {
          sections = imported.sections;
          syncBase(sections, imported.base?.sample_rate);
        } else {
          sections = [makeSection("Intro")];
        }
        dropDraft("new");                   /* nháp của bài chưa lưu trước đó không còn liên quan */
        unsaved = false;                    /* cấu trúc vừa tạo đã nằm trên DB → chưa có gì phải khôi phục */
        $("gate").hidden = true;
        stopAudio();
        render();
        loadHistory();
        st.className = "ok"; st.textContent = `Đã tạo “${name}”${imported ? ` với ${sections.length} đoạn từ JSON` : ""} — dựng các đoạn rồi bấm “Tạo metronome”.`;
      } catch (e) {
        st.className = "err"; st.textContent = e.message ? `Không tạo được bài mới: ${e.message}` : "Không tạo được bài mới.";
      }
    },
  });
}
$("gate-new").addEventListener("click", createDraft);
$("hist-new").addEventListener("click", createDraft);

/* Đổi tên một bài trong lịch sử */
async function renameHistory(id) {
  let current = "Bài hát chưa có tiêu đề";
  try { current = (await (await api(`/api/history/${id}`)).json()).title || current; } catch {}
  openNameModal({
    heading: "Đổi tên bài hát", value: current,
    onOk: async (name) => {
      const st = $("status");
      const r = await api(`/api/history/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: name }) });
      if (!r.ok) { st.className = "err"; st.textContent = "Không đổi được tên."; return; }
      if (id === activeHistoryId) activeTitle = name;
      loadHistory(); showGateList();
      st.className = "ok"; st.textContent = `Đã đổi tên thành “${name}”.`;
    },
  });
}

/* Tải xuống một bài trong lịch sử */
async function downloadHistory(id) {
  const st = $("status");
  try {
    const d = await (await api(`/api/history/${id}`)).json();
    if (!d.file_exists) {
      st.className = "err"; st.textContent = "Bản ghi âm thanh đã bị xoá — bấm “Tạo metronome” để tạo lại.";
      return;
    }
    const a = document.createElement("a");
    a.href = `/download/${d.filename}?title=${encodeURIComponent(d.title || "Bài hát chưa có tiêu đề")}`;
    document.body.appendChild(a); a.click(); a.remove();
  } catch { st.className = "err"; st.textContent = "Không tải được file."; }
}

/* === Menu chuột phải trên mục lịch sử: đổi tên / tải / xoá === */
let ctxTarget = null;
const ctx = $("ctx-menu");

function showCtx(x, y) {
  ctx.hidden = false;
  const r = ctx.getBoundingClientRect();
  ctx.style.left = `${Math.max(8, Math.min(x, window.innerWidth - r.width - 8))}px`;
  ctx.style.top = `${Math.min(y, window.innerHeight - r.height - 8)}px`;
}
document.addEventListener("contextmenu", (e) => {
  const item = e.target.closest(".hist-open");
  if (!item) return;
  e.preventDefault();
  ctxTarget = item;
  showCtx(e.clientX, e.clientY);
});
/* Nút ⋮: cùng menu nhưng dùng được trên điện thoại (không có chuột phải) */
document.addEventListener("click", (e) => {
  const k = e.target.closest(".hist-kebab");
  if (!k) return;
  e.stopPropagation();
  ctxTarget = k.closest(".hist").querySelector(".hist-open");
  const r = k.getBoundingClientRect();
  showCtx(r.left - 150, r.bottom + 6);
});
ctx.addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]");
  if (!act || !ctxTarget) return;
  const id = Number(ctxTarget.dataset.hid);
  ctx.hidden = true;
  if (act.dataset.act === "rename") renameHistory(id);
  else if (act.dataset.act === "download") downloadHistory(id);
  else if (act.dataset.act === "export") exportHistoryJson(id);
  else if (act.dataset.act === "delete") deleteHistory(id);
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#ctx-menu") && !e.target.closest(".hist-kebab")) ctx.hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { ctx.hidden = true; closeNameModal(); }
});

async function deleteHistory(id) {
  const st = $("status");
  try {
    const r = await api(`/api/history/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error();
    if (id === activeHistoryId) { activeHistoryId = null; stopAudio(); }
    dropDraft(id); dropDraft("new");
    saveDraft();
    loadHistory(); showGateList();
    st.className = "ok"; st.textContent = "Đã xoá bài khỏi lịch sử.";
  } catch { st.className = "err"; st.textContent = "Không xoá được bài."; }
}

/* === Màn hình chờ khi vừa vào trang === */
async function showGateList() {
  if ($("gate").hidden) return;
  let items = [];
  try { items = await (await api("/api/history")).json(); } catch {}
  $("gate-list").innerHTML = items.map(histItemHTML).join("");
  $("gate-empty").hidden = items.length > 0;
}
/* Chọn bài trong panel / màn hình chờ (một handler chung) */
document.addEventListener("click", (e) => {
  if (!e.target.closest(".hist-open")) return;
  $("gate").hidden = true;
  openHistory(Number(e.target.closest(".hist-open").dataset.hid));
});
async function showGate() {
  $("gate").hidden = false;
  await showGateList();
}

/* Đổ lựa chọn loại nhịp cho ô ở header */
$("base-sig").innerHTML = SIGS.map((t) => `<option ${t === SIGS[2] ? "selected" : ""}>${t}</option>`).join("");
/* Gợi ý tên đoạn cho ô "Tên đoạn" của mọi session */
$("name-suggestions").innerHTML = SECTION_NAMES.map((n) => `<option value="${esc(n)}"></option>`).join("");
/* Chuẩn đầu ra mặc định: 48 kHz */
$("base-srate").innerHTML = RATES.map(([v, t]) => `<option value="${v}" ${v === "48000" ? "selected" : ""}>${t}</option>`).join("");

loadHistory();
render();
{                                   /* khôi phục phiên làm việc trước (nháp chưa sinh) */
  const b = draftBucket();
  const open = b.__open && b.__open !== "new" && b[b.__open]?.dirty ? b[b.__open] : null;
  if (open) {
    activeHistoryId = Number(b.__open);
    loadDraftState(open);
    $("gate").hidden = true;
    const st = $("status");
    st.className = "ok"; st.textContent = `Đã khôi phục nháp chưa tạo metronome (${new Date(open.ts).toLocaleString()}).`;
  } else if (b.new) {
    loadDraftState(b.new);
  }
}
showGate();   /* vào trang phải chọn bài cũ hoặc tạo bài mới */
