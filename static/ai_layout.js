/* UI: panel "AI đọc bố cục bài hát".
 * Độc lập với app.js — chỉ nói chuyện qua (1) /api/ai/* và (2) khung "Nhập JSON"
 * có sẵn của modal tạo bài. Xoá include trong index.html là hết, không để lại gì.
 * Key/base URL/model sống trong localStorage của máy này và đi kèm từng request;
 * server không giữ bản nào.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const panel = $("ai-panel");
  const modal = $("name-modal");
  const heading = $("name-heading");
  if (!panel || !modal || !heading) return;

  const runBtn = $("ai-run"), errBox = $("ai-error"), busy = $("ai-busy"), bar = $("ai-bar"),
        label = $("ai-label"), result = $("ai-result"), preview = $("ai-preview"),
        summary = $("ai-summary"), cfgBox = $("ai-cfg"), cfgOn = $("ai-cfg-on"),
        cfgSum = $("ai-cfg-sum"), cfgErr = $("ai-cfg-err"), body = $("ai-body"),
        baseIn = $("ai-cfg-base"), modelIn = $("ai-cfg-model"),
        keyIn = $("ai-cfg-key"), cancelBtn = $("ai-cfg-cancel");
  const DEV_KEY = "band-metronome-device";
  const SEC_KEY = "band-metronome-ai";
  let cfg = { enabled: false, max_upload_mb: 25 };
  let sec = null;                           // {base, model, key} — chỉ ở máy này
  let timer = null, last = null;

  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const showErr = (msg) => { errBox.textContent = msg || ""; errBox.hidden = !msg; };
  const showCfgErr = (msg) => { cfgErr.textContent = msg || ""; cfgErr.hidden = !msg; };
  const setBusy = (on, pct, text) => {
    busy.hidden = !on;
    bar.style.width = `${Math.max(2, Math.min(100, pct || 0))}%`;
    if (text) label.textContent = text;
  };
  const stopPolling = () => { clearTimeout(timer); timer = null; };

  /* ---- cấu hình của thiết bị ---- */
  function loadSec() {
    try { return JSON.parse(localStorage.getItem(SEC_KEY) || "null"); } catch { return null; }
  }
  function saveSec(obj) {
    try { localStorage.setItem(SEC_KEY, JSON.stringify(obj)); } catch { /* chế độ ẩn danh */ }
    sec = obj;
    renderCfg();
  }
  const printable = (v) => typeof v === "string" && /^[\x21-\x7e]+$/.test(v);

  /* header gửi lên server: token thiết bị (quyền sở hữu bài) + credential AI của máy này */
  function headers(creds) {
    const c = creds || sec;
    const h = {};
    try {
      const d = localStorage.getItem(DEV_KEY);
      if (d) h["X-Band-Key"] = d;
    } catch { /* private mode */ }
    if (c && printable(c.key) && printable(c.base) && printable(c.model)) {
      h["X-AI-Key"] = c.key;
      h["X-AI-Base"] = c.base;
      h["X-AI-Model"] = c.model;
    }
    return h;
  }

  function hostOf(url) {
    try { return new URL(url).hostname; } catch { return url; }
  }

  function showCfg(isEdit) {
    cfgBox.hidden = false;
    cancelBtn.hidden = !isEdit;
    showCfgErr("");
    setTimeout(() => baseIn.focus(), 0);
  }

  function hideCfg() {
    cfgBox.hidden = true;
    if (sec) setTimeout(() => $("ai-file").focus(), 0);
  }

  function renderCfg() {
    if (!sec) {
      cfgOn.hidden = true;
      body.hidden = true;
      showCfg(false);
      return;
    }
    cfgOn.hidden = false;
    body.hidden = false;
    cfgBox.hidden = true;
    cfgSum.textContent = `${sec.model} · ${hostOf(sec.base)} · key …${sec.key.slice(-4)}`;
    cfgSum.title = "Key chỉ nằm trong trình duyệt này";
  }

  async function checkAndSave() {
    const cand = {
      base: (baseIn.value || "").trim(),
      model: (modelIn.value || "").trim(),
      key: (keyIn.value || "").trim(),
    };
    if (!cand.base || !cand.model || !cand.key) return showCfgErr("Cần đủ base URL, model và key.");
    if (!/^https:\/\//i.test(cand.base)) return showCfgErr("Base URL phải là https://");
    showCfgErr("");
    cancelBtn.hidden = false;
    $("ai-cfg-save").disabled = true;
    $("ai-cfg-save").textContent = "Đang kiểm tra…";
    try {
      const r = await fetch("/api/ai/ping", { method: "POST", headers: headers(cand) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `Server từ chối (${r.status}).`);
      saveSec(cand);
      keyIn.value = "";
    } catch (e) {
      showCfgErr(e.message);
    } finally {
      $("ai-cfg-save").disabled = false;
      $("ai-cfg-save").textContent = "Kiểm tra & lưu";
    }
  }

  /* ---- nút ✨ cạnh tiêu đề modal, chi chèn vào khi server báo bật ---- */
  function addTrigger() {
    if ($("ai-open")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "ai-open";
    btn.className = "ai-chip";
    btn.title = "Để AI nghe bài hát và gợi ý cấu trúc đoạn";
    btn.textContent = "✨ AI";
    const row = document.createElement("div");
    row.className = "ai-head";
    heading.parentNode.insertBefore(row, heading);
    row.append(heading, btn);
    btn.addEventListener("click", open);
  }

  function open() {
    showErr("");
    result.hidden = true;
    setBusy(false);
    panel.hidden = false;
    renderCfg();
  }
  function close() {
    stopPolling();
    panel.hidden = true;
    runBtn.disabled = false;
    runBtn.textContent = "Phân tích";
  }

  /* ---- gọi backend + poll (job chạy nền, tránh timeout proxy) ---- */
  async function poll(jobId) {
    let miss = 0;
    const deadline = Date.now() + (cfg.timeout_s || 300) * 1000;
    for (;;) {
      await new Promise((ok) => { timer = setTimeout(ok, 700); });
      let r;
      try {
        r = await fetch(`/api/ai/job/${jobId}`, { headers: headers() });
      } catch { r = null; }
      if (!r || !r.ok) {
        if (++miss >= 5) throw new Error("Mất kết nối tới server phân tích.");
        continue;
      }
      miss = 0;
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      setBusy(true, j.progress || 0, j.label || "đang phân tích…");
      if (j.done) return j.result;
      if (Date.now() > deadline) throw new Error("Model trả lời quá lâu — thử lại với bài ngắn hơn.");
    }
  }

  /* ================= đo BPM bằng DSP trong trình duyệt =================
   * Model âm thanh KHÔNG đếm được nhịp (kiểm chứng 2026-11-02: cùng một tệp, nhiệt độ 0,
   * trả 60/91/139 cho bài 125 BPM). Nên pulse lấy từ phép đo trên PCM mà Web Audio tự giải
   * mã; model chỉ được quyền quyết cách ký hiệu tempo (đơn/đôi) trong tập giá trị đo được.
   */
  const M = { workSr: 8000, lpHz: 420, win: 0.024, hop: 0.012,
              lo: 55, hi: 200, step: 0.5, goodLo: 70, goodHi: 160 };
  let meter = null;                       // {seconds, bpm, alts:[], unstable, map:[]}
  let measuring = null;                   // promise để run() chờ nếu người dùng bấm vội

  async function decodeToPcm(file) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw new Error("trình duyệt không có Web Audio");
    const ac = new AC();
    try {
      return await ac.decodeAudioData(await file.arrayBuffer());
    } catch {
      throw new Error("trình duyệt không giải mã được tệp này");
    } finally {
      ac.close();
    }
  }

  /* Bao nét onset vùng trầm (<420 Hz): kick/snare là pulse, tránh hợp âm & hat gây nhân đôi. */
  function onsetEnvelope(buf) {
    const sr = buf.sampleRate, n = buf.length, ch = buf.numberOfChannels;
    const k = Math.max(1, Math.round(sr / M.workSr)), sr2 = sr / k;
    const win = Math.max(1, Math.round(sr2 * M.win)), hop = Math.max(1, Math.round(sr2 * M.hop));
    const frames = Math.max(1, Math.floor(Math.floor(n / k) - win) / hop + 1);
    const chan = [];
    for (let c = 0; c < ch; c++) chan.push(buf.getChannelData(c));
    const a = 1 - Math.exp(-2 * Math.PI * M.lpHz / sr2);
    const nov = new Float32Array(frames);
    const rms = new Float32Array(frames);
    let lp1 = 0, lp2 = 0;
    for (let f = 0; f < frames; f++) {
      const start = f * hop * k;
      let acc = 0;
      const end = Math.min(start + win * k, n);
      for (let i = start; i < end; i++) {
        let s = 0;
        for (let c = 0; c < ch; c++) s += chan[c][i];
        lp1 += a * (s / ch - lp1);                     // 1-pole x2 = bậc 2 ~420 Hz
        lp2 += a * (lp1 - lp2);
        acc += lp2 * lp2;
      }
      rms[f] = Math.sqrt(acc / Math.max(1, end - start));
      nov[f] = f ? Math.max(0, Math.log(rms[f] + 1e-7) - Math.log(rms[f - 1] + 1e-7)) : 0;
    }
    return { nov, rms, fps: sr2 / hop, seconds: n / sr };
  }

  /* Điểm "lưới nhịp": với mỗi BPM, pha nào bắt được nhiều onset nhất. */
  function scoreTempo(nov, fps, from, to) {
    const out = [];
    for (let bpm = M.lo; bpm <= M.hi + 1e-9; bpm += M.step) {
      const p = 60 * fps / bpm;
      let best = 0;
      for (let ph = 0; ph < p; ph++) {
        let s = 0, c = 0;
        for (let t = from + ph; t < to; t += p) { s += nov[Math.round(t)] || 0; c++; }
        if (c > 2) best = Math.max(best, s / c);
      }
      out.push({ bpm: +bpm.toFixed(1), sc: best });
    }
    return out;
  }

  function pickBpm(scores) {
    const top = scores.reduce((m, s) => Math.max(m, s.sc), 0);
    const strong = scores.filter((s) => s.sc >= 0.8 * top);
    const band = strong.filter((s) => s.bpm >= M.goodLo && s.bpm <= M.goodHi);
    if (!strong.length) return { bpm: 0, alts: [] };
    const win = (band.length ? band : strong).reduce((a, b) => (b.sc > a.sc ? b : a));
    const alts = [];
    for (const f of [0.5, 1, 2]) {
      const s = scores.find((x) => Math.abs(x.bpm - win.bpm * f) < M.step);
      if (s && s.bpm >= M.lo && s.bpm <= M.hi) alts.push({ bpm: s.bpm, sc: s.sc });
    }
    return { bpm: win.bpm, alts, strength: win.sc / (top || 1) };
  }

  async function measureTempo(file) {
    const env = onsetEnvelope(await decodeToPcm(file));
    if (env.seconds < 3 || env.nov.length < 40) throw new Error("audio quá ngắn để đo nhịp");
    const whole = pickBpm(scoreTempo(env.nov, env.fps, 0, env.nov.length));
    /* ba khúc: bài đổi nhịp thì không khoá một con số chung */
    const third = Math.floor(env.nov.length / 3);
    const parts = [0, 1, 2].map((i) => pickBpm(
      scoreTempo(env.nov, env.fps, i * third, i === 2 ? env.nov.length : (i + 1) * third)));
    const vals = parts.map((p) => p.bpm).filter(Boolean);
    const spread = vals.length ? Math.max(...vals) / Math.min(...vals) : 1;
    const unstable = spread > 1.15;
    const map = unstable ? parts.map((p, i) => ({ t0: +(i * env.seconds / 3).toFixed(1),
                                                  t1: +((i + 1) * env.seconds / 3).toFixed(1),
                                                  bpm: p.bpm })) : [];
    const median = vals.slice().sort((a, b) => a - b)[vals.length >> 1];
    if (!whole.bpm) throw new Error("không bắt được nhịp nào rõ");
    return { seconds: env.seconds, bpm: unstable ? median : whole.bpm, alts: whole.alts,
             unstable, map, spread, env };
  }

  /* ---------------- bằng chứng âm thanh cho ranh giới đoạn ----------------
   * Model tự đoán chỗ đổi đoạn; ở đây đo lại xem tại thời điểm đó có THẬT SỰ có
   * thay đổi năng lượng không. Năng lượng lấy theo từng GIÂY (bin 1 s để không bị
   * nhịp/alias của từng tiếng trống làm nhiễu), so mức trung bình 3 s mỗi bên,
   * z = |trước - sau| / độ dao động chung của bài (sàn 0.25 lần mức trung bình để
   * bài phẳng không biến nhiễu thành "có cấu trúc"). Ranh giới yếu khi z dưới cả
   * mốc 0.6 lẫn percentil 75 của bài — nghĩa là chỗ đó chẳng đáng chú ý hơn một
   * vị trí ngẫu nhiên khác trong bài.
   */
  function boundaryEvidence(env, sections) {
    if (!env || !env.rms || !sections.length) return null;
    const bin = Math.max(1, Math.round(env.fps));            // 1 giây / bin
    const lvl = [];
    for (let i = 0; i < env.rms.length; i += bin) {
      let s = 0, n = 0;
      for (let j = 0; j < bin && i + j < env.rms.length; j++) { s += env.rms[i + j] * env.rms[i + j]; n++; }
      lvl.push(Math.sqrt(s / Math.max(1, n)));
    }
    if (lvl.length < 9) return null;                          // bài quá ngắn thì không có cơ sở
    const mean = lvl.reduce((a, b) => a + b, 0) / lvl.length;
    const sd = Math.max(Math.sqrt(lvl.reduce((a, b) => a + (b - mean) ** 2, 0) / lvl.length), 0.25 * mean) || 1e-6;
    const half = 3;                                           // so 3 giây mỗi bên
    const avg = (from, to) => {
      let s = 0, n = 0;
      for (let i = Math.max(0, from); i < Math.min(lvl.length, to); i++) { s += lvl[i]; n++; }
      return n ? s / n : 0;
    };
    const z = (i) => (i - half < 0 || i + half > lvl.length) ? 0
      : Math.abs(avg(i - half, i) - avg(i, i + half)) / sd;
    const grid = [];
    for (let i = half; i <= lvl.length - half; i++) grid.push(z(i));
    grid.sort((a, b) => a - b);
    const thr = Math.max(0.6, grid[Math.floor(0.75 * (grid.length - 1))]);
    const tol = 1;                                            // dung sai ±1 bar-second

    const starts = [];
    let acc = 0;
    for (const sec of sections) {
      starts.push(acc);
      acc += sec.bars * Number(String(sec.time_signature).split("/")[0]) * 60 / Number(sec.bpm);
    }
    const flags = starts.map((t, i) => {
      if (i === 0) return false;                              // đầu bài không phải ranh giới
      if (t >= lvl.length - 1) return false;                  // ngoài phần đã đo
      let best = 0;
      const c = Math.round(t);
      for (let k = c - tol; k <= c + tol; k++) if (k > 0) best = Math.max(best, z(k));
      return best < thr;
    });
    return { flags, weak: flags.filter(Boolean).length, thr, seconds: lvl.length };
  }

  const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

  function renderMeter(state, err) {
    const box = $("ai-meter");
    if (!box) return;
    if (state === "busy") {
      box.hidden = false;
      box.innerHTML = '<p class="ai-note">đang đo nhịp…</p>';
      return;
    }
    if (!meter) {
      box.hidden = !err;
      box.innerHTML = err ? `<p class="ai-note err-hint">Chưa đo được nhịp (${esc(err)}) — `
        + "tempo sẽ do model tự quyết.</p>" : "";
      return;
    }
    box.hidden = false;
    const set = new Set();
    [meter.bpm / 2, meter.bpm, meter.bpm * 2].concat(meter.alts.map((a) => a.bpm))
      .forEach((v) => { if (v >= 25 && v <= 240) set.add(+v.toFixed(1)); });
    const opts = [...set].sort((a, b) => a - b);
    const chips = opts.map((v) => `<button type="button" class="ai-chip ai-alt`
      + (Math.abs(v - meter.bpm) < 0.3 ? " on" : "") + `" data-bpm="${v}" title="Chọn tempo này và giữ nguyên">`
      + `${v} BPM</button>`).join("");
    box.innerHTML = `<p class="ai-note"><b>Đo được ${meter.bpm} BPM</b> · ${fmtTime(meter.seconds)}`
      + (meter.unstable
        ? ` · nhịp đổi dọc bài (${meter.map.map((s) => `${s.bpm}@${fmtTime(s.t0)}`).join(" · ")})`
        : "") + "</p>"
      + `<div class="ai-alts">${chips}</div>`;
    box.querySelectorAll(".ai-alt").forEach((b) => b.addEventListener("click", () => {
      meter.bpm = parseFloat(b.dataset.bpm);
      meter.locked = true;
      renderMeter("done");
    }));
  }

  async function onFileChosen() {
    const file = $("ai-file").files[0];
    meter = null;
    measuring = null;
    if (!file) return renderMeter("idle");
    if (file.size > cfg.max_upload_mb * 1048576) {
      return renderMeter("idle", `vượt trần ${cfg.max_upload_mb} MB`);
    }
    renderMeter("busy");
    measuring = measureTempo(file).then((m) => {
      meter = m;
      renderMeter("done");
    }).catch((e) => {
      meter = null;
      renderMeter("idle", e.message);
    });
    await measuring;
  }

  async function run() {
    showErr("");
    result.hidden = true;
    if (!sec) { showCfg(true); return showCfgErr("Cấu hình model trước đã."); }
    const file = $("ai-file").files[0];
    if (!file) return showErr("Chọn tệp âm thanh trước.");
    if (file.size > cfg.max_upload_mb * 1024 * 1024) {
      return showErr(`Tệp vượt trần ${cfg.max_upload_mb} MB.`);
    }

    const form = new FormData();
    form.append("audio", file);
    if (measuring) {
      setBusy(true, 1, "đang đo nhịp…");
      try { await measuring; } catch { /* đã báo trong ô đo */ }
    }
    if (meter) {
      form.append("seconds", meter.seconds.toFixed(1));
      form.append("bpm", String(meter.bpm));
      if (meter.locked) {
        form.append("bpm_locked", "1");        // người dùng đã chọn: không cho model đổi
      } else {
        if (meter.alts.length) form.append("bpm_alts", JSON.stringify(meter.alts.map((a) => a.bpm)));
        if (meter.map.length) form.append("bpm_map", JSON.stringify(meter.map));
      }
    }

    runBtn.disabled = true;
    runBtn.textContent = "Đang phân tích…";
    setBusy(true, 3, "đang gửi yêu cầu…");
    try {
      const r = await fetch("/api/ai/analyze", { method: "POST", body: form, headers: headers() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `Server từ chối (${r.status}).`);
      last = await poll(d.job_id);
      renderResult(last);
    } catch (e) {
      showErr(e.message);
    } finally {
      stopPolling();
      setBusy(false);
      runBtn.disabled = false;
      runBtn.textContent = "Phân tích";
    }
  }

  /* ---- "cho mọi đoạn": ba ô đếm đều BẬT SẴN, kết quả của model bị ghi đè theo đó ----
   * Tắt ô nào thì áp giá trị ngược lại cho toàn bộ đoạn. read_marker chỉ có ý nghĩa
   * khi bật count_in nên nó bị vô hiệu theo. Ba cờ này chỉ thêm lời đọc, không thêm bar.
   */
  const BULK = ["count_in", "read_marker", "count_out"];

  function applyBulkTo(sections) {
    if (!sections) return;
    const ci = $("ai-all-count_in"), rm = $("ai-all-read_marker");
    BULK.forEach((k) => {
      const box = $(`ai-all-${k}`);
      if (box) sections.forEach((s) => { s[k] = box.checked; });
    });
    if (rm) rm.disabled = Boolean(ci && !ci.checked);
  }

  function renderResult(data) {
    const secs = (data && data.sections) || [];
    if (!secs.length) return showErr("Model không trả về đoạn nào.");
    applyBulkTo(secs);
    const ev = meter && meter.env ? boundaryEvidence(meter.env, secs) : null;
    summary.textContent = `${secs.length} đoạn · ${data.base?.bpm || "—"} BPM · ${data.base?.time_signature || "4/4"}`;
    preview.innerHTML = secs.map((s, i) => `<li${ev && ev.flags[i] ? ' title="Ranh giới không có thay đổi năng lượng đo được"' : ""}>`
      + `<b>${ev && ev.flags[i] ? "⚠ " : ""}${esc(s.name)}</b>
      <span>${Number(s.bpm)} BPM · ${s.bars} bar · ${esc(s.time_signature)}</span></li>`).join("");
    renderMeasure(data.measure, secs.length);
    result.hidden = false;
  }

  /* model đã quyết gì so với phép đo */
  function renderMeasure(ms, n) {
    const box = $("ai-measure");
    if (!box) return;
    box.hidden = false;
    if (!ms || !ms.pulse) {
      box.innerHTML = '<p class="ai-note err-hint">Không đo được nhịp — tempo do model tự đoán.</p>';
      return;
    }
    const cov = ms.coverage == null ? "" : ` · phủ ${ms.coverage}% thời lượng`;
    const src = ms.locked ? "bạn chọn" : "model chốt";
    box.innerHTML = `<p class="ai-note">${src} ${ms.chosen.join("/")} BPM `
      + `(pulse đo ${ms.pulse} BPM)${cov}.</p>`;
  }

  /* Phiên mới hoàn toàn: kết quả cũ, tệp đã chọn, phép đo — bấm ✨ AI lần kế tiếp là
   * lại từ đầu. Ba ô "cho mọi đoạn" trở lại mặc định BẬT. */
  function resetSession() {
    last = null;
    meter = null;
    measuring = null;
    const file = $("ai-file");
    if (file) file.value = "";
    BULK.forEach((k) => {
      const box = $(`ai-all-${k}`);
      if (box) { box.checked = true; box.disabled = false; }
    });
    renderMeter("idle");
    result.hidden = true;
    setBusy(false);
    showErr("");
  }

  /* Đưa cấu trúc vào khung "Nhập JSON" của modal — dùng lại đúng luồng đã có.
   * Tên bài là do người dùng gõ ở ô "TÊN BÀI HÁT", model không được đặt tên. */
  function apply() {
    if (!last) return;
    const importBox = $("import-box"), importToggle = $("name-import"), importText = $("import-text");
    if (!importBox || !importToggle || !importText) return close();
    const typed = ($("name-input")?.value || "").trim();
    importText.value = JSON.stringify({ app: "band-metronome", version: 1,
      title: typed || "Bài hát chưa có tiêu đề",
      base: last.base, sections: last.sections }, null, 2);
    if (importBox.hidden) importToggle.click();
    close();
    resetSession();
  }

  /* ---- gắn sự kiện + khóa panel khi modal đóng ---- */
  $("ai-close").addEventListener("click", close);
  $("ai-reject").addEventListener("click", () => { last = null; result.hidden = true; });
  $("ai-apply").addEventListener("click", apply);
  runBtn.addEventListener("click", run);
  $("ai-cfg-save").addEventListener("click", checkAndSave);
  keyIn.addEventListener("keydown", (e) => { if (e.key === "Enter") checkAndSave(); });
  cancelBtn.addEventListener("click", () => {
    showCfgErr("");
    if (sec) hideCfg(); else renderCfg();
  });
  $("ai-cfg-edit").addEventListener("click", () => {
    baseIn.value = sec.base;
    modelIn.value = sec.model;
    keyIn.value = sec.key;
    showCfg(true);
  });
  $("ai-cfg-clear").addEventListener("click", () => {
    if (!confirm("Xoá key và cấu hình khỏi trình duyệt này?")) return;
    try { localStorage.removeItem(SEC_KEY); } catch { /* private mode */ }
    sec = null;
    baseIn.value = modelIn.value = keyIn.value = "";
    renderCfg();
  });
  BULK.forEach((k) => {
    const box = $(`ai-all-${k}`);
    if (!box) return;
    box.addEventListener("change", () => {
      if (last) { applyBulkTo(last.sections); renderResult(last); }
      else applyBulkTo([]);              // không có kết quả thì chỉ cần cập nhật read_marker.disabled
    });
  });
  $("ai-file").addEventListener("change", onFileChosen);
  panel.addEventListener("click", (e) => { if (e.target === panel) close(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) { e.stopPropagation(); close(); }
  }, true);
  new MutationObserver(() => { if (modal.hidden) close(); })
    .observe(modal, { attributes: true, attributeFilter: ["hidden"] });

  sec = loadSec();
  renderMeter("idle");
  fetch("/api/ai/status", { headers: headers() })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d || !d.enabled) return;                     // server tắt -> không hiện nút
      cfg = Object.assign(cfg, d, { timeout_s: (d.timeout_s || 180) + 120 });
      addTrigger();
    })
    .catch(() => { /* server chưa có module -> im lặng */ });
})();
