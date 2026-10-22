"use strict";
const $ = id => document.getElementById(id);
const num = (digits = 0) => new Intl.NumberFormat("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits });  // dấu phẩy thập phân, dấu chấm hàng nghìn
const n0 = num(0), n1 = num(1), n2 = num(2);
const fmt = n => n >= 1073741824 ? n2.format(n / 1073741824) + " GB" : n >= 1048576 ? n1.format(n / 1048576) + " MB" : n0.format(n / 1024) + " KB";
const dur = s => { s = Math.round(s); return s >= 60 ? Math.floor(s / 60) + " phút " + (s % 60) + " giây" : s + " giây"; };
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// ---- khoá chủ nhà (ALBUM_ADMIN_TOKEN): cookie HttpOnly do máy chủ đặt sau khi nhập mã ở trang đăng nhập (/manage trả trang đó khi chưa có cookie).
// Tải lên / Xử lý mà gặp 401 (cookie hết hạn) thì quay lại /manage — máy chủ sẽ trả trang đăng nhập.
const toLogin = () => location.reload();

let maxMB = 60;
const EV_LABEL = { "2026-10-04": "4/10", "2026-10-17": "17/10" }, EV_DEFAULT = "2026-10-04";  // danh sách cố định, khớp store.EVENTS; máy chủ vẫn kiểm lại
const evLabel = id => EV_LABEL[id] || id;
const upEvent = () => { const c = document.querySelector('input[name="up-ev"]:checked'); return c ? c.value : EV_DEFAULT; };
// gọi thoải mái từ mọi nơi nhưng gửi tối đa ~1 request/giây (có lượt cuối để số liệu luôn đúng)
let statsAt = 0, statsTimer = 0;
function refreshStats() {
  const wait = statsAt + 1000 - Date.now();
  if (wait > 0) { if (!statsTimer) statsTimer = setTimeout(() => { statsTimer = 0; refreshStats(); }, wait); return; }
  statsAt = Date.now(); loadStats();
}
async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    if (r.status === 401) return toLogin();
    const d = await r.json();
    maxMB = d.max_upload_mb;
    $("drop-hint").textContent = `JPG, PNG, WEBP · tối đa ${maxMB} MB mỗi ảnh`;
    $("n-orig").textContent = n0.format(d.originals); $("n-done").textContent = n0.format(d.photos);
    $("n-pend").textContent = n0.format(d.pending); $("n-faces").textContent = n0.format(d.faces);
    const used = 1 - d.disk_free / d.disk_total, low = d.disk_free / d.disk_total < 0.1;
    $("disk-bar").style.width = Math.max(used * 100, 1) + "%";
    $("disk-bar").parentElement.classList.toggle("low", low);
    $("disk").classList.toggle("low", low);
    $("disk").textContent = `Ổ đĩa: còn trống ${n1.format(d.disk_free / 2 ** 30)} GB / ${n1.format(d.disk_total / 2 ** 30)} GB` +
      ` · ảnh đã lưu ${fmt(d.stored_bytes)}` + (d.max_total_bytes == null ? "" : ` / hạn mức ${fmt(d.max_total_bytes)}`) + (low ? " — sắp hết chỗ" : "");
  } catch (e) {}
}

// ---- tải lên: mỗi file một request, 3 request song song, có tiến độ byte ----
let chipsKey = "", queue = [], active = 0, total = 0, sent = 0, doneN = 0, failN = 0, dupN = 0;
function addFiles(list) {
  for (const f of list) {
    const li = el("li"), name = el("span", "f-name", f.name), st = el("span", "f-st", "chờ…");
    const bar = el("div", "f-bar"), fill = el("i"); bar.append(fill);
    li.append(name, st, bar);
    $("files").prepend(li);
    if (f.size > maxMB * 1048576) {  // báo ngay ở trình duyệt, khỏi gửi rồi mới bị đóng kết nối
      st.textContent = `✗ quá lớn (tối đa ${maxMB} MB)`; st.className = "f-st err"; li.classList.add("done"); failN++; continue;
    }
    queue.push({ f, li, st, fill, loaded: 0, ev: upEvent() });  // ngày chốt lúc thêm vào hàng chờ
    total += f.size;
  }
  $("overall").hidden = false; pump();
}
function pump() {
  while (active < 3 && queue.length) { active++; upload(queue.shift()); }
  const busy = active || queue.length;
  $("upsum").textContent = `Đã gửi ${fmt(sent)} / ${fmt(total)}` + (busy ? "…" : ". Hoàn tất.");
  const pct = total ? Math.min(100, sent / total * 100) : 100;
  $("upbar").style.width = pct + "%"; $("upbar-wrap").setAttribute("aria-valuenow", Math.round(pct));
  const key = doneN + "/" + dupN + "/" + failN;
  if (key === chipsKey) return;  // số đếm không đổi thì không dựng lại
  chipsKey = key;
  const c = $("upchips"); c.textContent = "";
  if (doneN) c.append(el("span", "chip ok", `${doneN} ảnh xong`));
  if (dupN) c.append(el("span", "chip dup", `${dupN} trùng`));
  if (failN) c.append(el("span", "chip err", `${failN} lỗi`));
}
function upload(it, tries = 0) {
  const st = it.st, x = new XMLHttpRequest();
  const retry = () => {  // máy chủ bận (503), quá nhiều request (429), hết thời gian hoặc đứt kết nối: thử lại tối đa 4 lần, chậm dần
    if (tries >= 4) return false;
    sent -= it.loaded; it.loaded = 0; it.fill.style.width = "0"; st.textContent = "chờ thử lại…";
    setTimeout(() => upload(it, tries + 1), 2000 * (tries + 1)); return true;
  };
  x.open("POST", "/api/upload?name=" + encodeURIComponent(it.f.name) + "&event=" + encodeURIComponent(it.ev));
  // hết thời gian theo KHÔNG HOẠT ĐỘNG (không có tiến độ tải lên trong 60 s), không phải tổng thời gian: ảnh gốc 15–25 MB trên mạng yếu vẫn tải được
  let idle = 0;
  const stalled = () => { if (retry()) return; st.textContent = "✗ quá thời gian chờ"; st.className = "f-st err"; failN++; fin(); };
  const arm = () => { clearTimeout(idle); idle = setTimeout(() => { x.abort(); stalled(); }, 60000); };
  x.setRequestHeader("Content-Type", "application/octet-stream");
  x.upload.onprogress = e => { arm(); if (!e.lengthComputable) return; sent += e.loaded - it.loaded; it.loaded = e.loaded; const p = Math.round(e.loaded / e.total * 100); st.textContent = p + "%"; it.fill.style.width = p + "%"; pump(); };
  const fin = () => { sent += it.f.size - it.loaded; it.loaded = it.f.size; it.li.classList.add("done"); active--; pump(); refreshStats(); };
  x.onload = () => {
    clearTimeout(idle);
    let d = null; try { d = JSON.parse(x.responseText); } catch (e) {}
    if (!d || typeof d !== "object") d = x.status === 200 ? { detail: "máy chủ trả lời không đọc được" } : {};  // 200 mà không phải JSON: lỗi, không phải "trùng"
    if (x.status === 200 && d.status === "ok") { st.textContent = "✓ " + fmt(it.f.size) + (d.renamed ? " (đổi tên: " + d.name + ")" : "") + " · ngày " + evLabel(d.event || it.ev); st.className = "f-st ok"; doneN++; }
    else if (x.status === 200 && d.name) { st.textContent = "trùng với " + d.name + " (giữ nguyên ngày " + evLabel(d.event) + " của ảnh cũ)"; st.className = "f-st dup"; dupN++; }
    else if (x.status === 401) { toLogin(); return; }
    else if ((x.status === 503 || x.status === 429) && retry()) return;
    else { st.textContent = "✗ " + (d.detail || ("lỗi " + x.status)); st.className = "f-st err"; failN++; }
    fin();
  };
  x.onerror = () => { clearTimeout(idle); if (retry()) return; st.textContent = "✗ mất kết nối"; st.className = "f-st err"; failN++; fin(); };
  x.upload.onload = () => clearTimeout(idle);  // đã gửi xong: chờ máy chủ trả lời
  arm(); x.send(it.f);
}
$("pick").onchange = () => { addFiles($("pick").files); $("pick").value = ""; };
const drop = $("drop");
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => addFiles(e.dataTransfer.files));
addEventListener("dragover", e => e.preventDefault());
addEventListener("drop", e => e.preventDefault());

// ---- xử lý nền ----
let timer = null;
function showStatus(s) {
  const run = s.state === "running";
  $("proc").disabled = run;
  const show = s.state !== "idle";
  $("pbar-wrap").hidden = !show; $("ptiles").hidden = !show;
  const pct = s.total ? Math.min(100, s.done / s.total * 100) : (s.state === "done" ? 100 : 0);
  $("pbar").style.width = pct + "%"; $("pbar-wrap").setAttribute("aria-valuenow", Math.round(pct));
  const m = { idle: "", running: "Đang xử lý…", done: "Hoàn tất ✓", error: "Có lỗi — xem bên dưới" }[s.state];
  $("pmsg").textContent = m; $("pmsg").className = "pmsg " + (s.state === "done" ? "ok" : s.state === "error" ? "err" : "");
  $("p-done").textContent = `${s.done}/${s.total}`; $("p-faces").textContent = s.faces;
  $("p-failed").textContent = s.failed; $("p-rej").textContent = s.rejected.length;
  $("p-failed-w").classList.toggle("bad", s.failed > 0); $("p-rej-w").classList.toggle("warn", s.rejected.length > 0);
  $("pinfo").textContent = show ? dur(s.elapsed || 0) + (s.current ? ` · đang làm: ${s.current}` : "") : "";
  const err = (s.rejected.length ? "Đã loại (làm sập khi giải mã, nằm trong thư mục rejected/): " + s.rejected.join(", ") + "\n" : "") + s.errors.join("\n");
  $("errors").textContent = err.trim(); $("errors").hidden = !err.trim();
  if (run && !timer) timer = setInterval(poll, 1000);
  if (!run && timer) { clearInterval(timer); timer = null; refreshStats(); vis.reload(); }  // Xử lý xong (done/error): lưới ảnh + số đếm lấy lại từ máy chủ
}
async function poll() { try { const r = await fetch("/api/process"); if (r.status === 401) return toLogin(); showStatus(await r.json()); } catch (e) {} }
$("proc").onclick = async () => {
  const r = await fetch("/api/process", { method: "POST" });
  if (r.status === 401) return toLogin();
  const d = await r.json();
  showStatus(d);
  if (r.status === 409) $("pmsg").textContent = d.message;
  if (r.status === 200 && !timer) timer = setInterval(poll, 1000);
};
refreshStats(); poll();

// ---- Ẩn / hiện ảnh (hợp đồng F.4) ----
// Dữ liệu: GET /api/admin/photos → [id, w, h, t, v, c, name, b, hidden, ev]; b = chuỗi liên thanh GỐC (không phải bản đã dồn của khách).
const vis = (() => {
  const grid = $("vgrid"), bar = $("abar"), toastEl = $("toast");
  const FILTERS = { all: () => true, shown: p => !p.hidden, hidden: p => p.hidden };
  let evf = "all";   // lọc theo ngày, kết hợp (AND) với bộ lọc ẩn/hiện
  const pass = p => FILTERS[filter](p) && (evf === "all" || p.ev === evf);
  const P = [], byId = new Map(), bursts = new Map();   // bursts: b → mọi khung gốc
  let filter = "all", selectMode = false, armed = false, anchor = null, flat = [], idx = new Map(), cells = new Map(), unitByKey = new Map();
  const sel = new Set(), expanded = new Set();
  let chain = Promise.resolve(), toastTimer = 0, swallow = 0, pvPos = -1;
  const thumb = p => p.v ? `/i/${p.id}-${p.v}-240.webp` : "";
  const big = p => p.v ? `/i/${p.id}-${p.v}-2000.webp` : "";
  const plural = n => n0.format(n) + " ảnh";

  function counts() { let h = 0; for (const p of P) if (p.hidden) h++; return { all: P.length, shown: P.length - h, hidden: h }; }
  function paintCounts() {
    const c = counts();
    for (const k of ["all", "shown", "hidden"]) $("c-" + k).textContent = n0.format(c[k]);
    for (const e of Object.keys(EV_LABEL)) $("ce-" + e).textContent = n0.format(P.reduce((k, p) => k + (p.ev === e ? 1 : 0), 0));
    return c;
  }
  async function api(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { toLogin(); throw new Error("401"); }
    if (!r.ok) throw new Error("http " + r.status);
    return r.json();
  }

  // ---- dữ liệu ----
  async function load() {
    $("v-state").textContent = "Đang tải danh sách ảnh…"; $("v-state").hidden = false;
    try {
      const r = await fetch("/api/admin/photos");
      if (r.status === 401) return toLogin();
      if (!r.ok) throw new Error("http " + r.status);
      const d = await r.json();
      P.length = 0; byId.clear(); bursts.clear();
      for (const [id, w, h, t, v, c, name, b, hidden, ev] of d.photos) {
        const p = { id, w, h, v, c, name, b: b || null, hidden: !!hidden, ev: ev || EV_DEFAULT };
        P.push(p); byId.set(id, p);
        if (p.b) { if (!bursts.has(p.b)) bursts.set(p.b, []); bursts.get(p.b).push(p); }
      }
      for (const id of [...sel]) if (!byId.has(id)) sel.delete(id);
      setting(d.settings.search_includes_hidden);
      render();
    } catch (e) {
      if (e.message === "401") return;
      $("v-state").textContent = "Chưa tải được danh sách ảnh. Chạm để thử lại.";
      $("v-state").onclick = () => { $("v-state").onclick = null; load(); };
    }
  }

  // ---- dựng lưới: một ô cho mỗi chuỗi gốc (các khung khớp bộ lọc), ô lẻ cho ảnh không thuộc chuỗi ----
  function build() {
    flat = P.filter(pass); idx = new Map(flat.map((p, i) => [p.id, i]));
    const out = [], byB = new Map();
    for (const p of flat) {
      if (p.b) {
        let u = byB.get(p.b);
        if (!u) { u = { key: "b" + p.b, b: p.b, frames: [] }; byB.set(p.b, u); out.push(u); }
        u.frames.push(p);
      } else out.push({ key: p.id, frames: [p] });
    }
    return out;
  }
  function render() {
    paintCounts();
    const units = build();
    cells = new Map(); unitByKey = new Map();
    const frag = document.createDocumentFragment();
    for (const u of units) {
      const burst = u.b && u.frames.length > 1;
      if (burst && expanded.has(u.b)) {
        const box = el("div", "bopen"), head = el("div", "bopen-h"), g = el("div", "vgrid vgrid--in");
        const all = bursts.get(u.b);
        head.append(el("span", "bopen-t", `Chuỗi ${all.length} ảnh chụp liên tiếp` + (u.frames.length < all.length ? ` · đang xem ${u.frames.length}` : "")));
        const close = el("button", "bopen-x", "Thu gọn"); close.type = "button"; close.dataset.collapse = u.b; head.append(close);
        for (const p of u.frames) { const fu = { key: p.id, frames: [p], b: p.b, inBurst: true }; unitByKey.set(fu.key, fu); g.append(cell(fu, false)); }
        box.append(head, g); frag.append(box);
      } else { unitByKey.set(u.key, u); frag.append(cell(u, burst)); }
    }
    grid.replaceChildren(frag);
    const none = !flat.length;
    $("v-state").hidden = !none;
    if (none) $("v-state").textContent = filter === "hidden" ? "Chưa có ảnh nào đang ẩn." : filter === "shown" ? "Không còn ảnh nào đang hiện." : "Chưa có ảnh nào.";
    refresh();
  }
  function cell(u, burst) {
    const c = el("div", "cell"), b = el("button", "tile"), first = u.frames[0];
    c.dataset.k = u.key; b.type = "button";
    c.style.setProperty("--c", first.c || "#e9dccf");
    if (burst) c.classList.add("is-burst");
    if (first.v) { const im = new Image(); im.loading = "lazy"; im.decoding = "async"; im.alt = ""; im.draggable = false; im.src = thumb(first); b.append(im); }
    else b.append(el("span", "noimg", "chưa có ảnh nhỏ"));
    const chk = el("i", "chk"); chk.setAttribute("aria-hidden", "true");
    const tags = el("span", "tags"); b.append(chk, tags);
    c.append(b);
    if (burst) {
      const o = el("button", "bopen-btn"); o.type = "button"; o.dataset.open = u.b;
      o.setAttribute("aria-label", `Mở chuỗi ${bursts.get(u.b).length} ảnh`); o.append(el("span", null, "Mở chuỗi"));
      c.append(o);
    }
    cells.set(u.key, { u, c, b, tags, burst });
    return c;
  }
  // trạng thái từng ô (chọn / ẩn / huy hiệu) — gọi lại khi chọn đổi mà không dựng lại lưới
  function refresh() {
    for (const e of cells.values()) {
      const { u, c, b, tags, burst } = e;
      const n = u.frames.reduce((k, p) => k + (sel.has(p.id) ? 1 : 0), 0), st = n === 0 ? "none" : n === u.frames.length ? "all" : "part";
      c.dataset.sel = st; c.classList.toggle("selectable", selectMode);
      const hid = u.frames.reduce((k, p) => k + (p.hidden ? 1 : 0), 0), all = u.b ? bursts.get(u.b) : u.frames;
      const allHid = all.reduce((k, p) => k + (p.hidden ? 1 : 0), 0);
      c.classList.toggle("is-hidden", hid === u.frames.length);
      tags.textContent = "";
      const evs = new Set(u.frames.map(p => p.ev));   // chuỗi gốc có thể gồm khung của hai ngày
      tags.append(el("span", "tag tag--ev", evs.size === 1 ? evLabel(first(u).ev) : [...evs].map(evLabel).join(" + ")));
      if (burst) tags.append(el("span", "tag tag--n", "×" + u.frames.length));
      if (allHid && allHid < all.length && burst) tags.append(el("span", "tag tag--mix", `${allHid}/${all.length} ẩn`));
      else if (hid === u.frames.length) tags.append(el("span", "tag tag--hid", "Đã ẩn"));
      else if (hid) tags.append(el("span", "tag tag--mix", `${hid}/${u.frames.length} ẩn`));
      const name = burst ? `Chuỗi ${u.frames.length} ảnh` : first(u).name;
      b.setAttribute("aria-label", name + (hid === u.frames.length ? ", đang ẩn" : hid ? `, ${hid} ảnh đang ẩn` : ", đang hiện") + (selectMode ? (st === "all" ? ", đã chọn" : st === "part" ? ", chọn một phần" : ", chưa chọn") : ""));
      if (selectMode) b.setAttribute("aria-pressed", st === "all" ? "true" : st === "part" ? "mixed" : "false"); else b.removeAttribute("aria-pressed");
    }
    const n = sel.size;
    let sh = 0, hd = 0; for (const id of sel) { if (byId.get(id).hidden) hd++; else sh++; }
    $("abar-n").textContent = n ? `${plural(n)} đã chọn` : "Chưa chọn ảnh nào";
    $("a-hide").disabled = !sh; $("a-show").disabled = !hd;
    $("a-hide").textContent = sh ? `Ẩn (${n0.format(sh)})` : "Ẩn"; $("a-show").textContent = hd ? `Hiện (${n0.format(hd)})` : "Hiện";
    $("a-clear").disabled = !n;
    $("a-range").setAttribute("aria-pressed", armed ? "true" : "false");
    $("a-range").textContent = armed ? "Chạm ảnh cuối của đoạn" : "Chọn đến đây";
    $("a-range").classList.toggle("on", armed);
    $("v-mode").textContent = selectMode ? "Xong" : "Chọn"; $("v-mode").setAttribute("aria-pressed", selectMode ? "true" : "false");
    $("v-all").hidden = !selectMode; $("v-help").hidden = !selectMode;
    bar.hidden = !selectMode;
    document.body.classList.toggle("has-bar", selectMode);
    if (selectMode) document.documentElement.style.setProperty("--bar-h", bar.offsetHeight + "px");
  }
  const first = u => u.frames[0];

  // ---- chọn ----
  function setMode(on) {
    selectMode = on; armed = false;
    if (!on) { sel.clear(); anchor = null; }
    refresh();
  }
  const span = u => [idx.get(u.frames[0].id), idx.get(u.frames[u.frames.length - 1].id)];
  function toggle(u) {
    const all = u.frames.every(p => sel.has(p.id));
    for (const p of u.frames) all ? sel.delete(p.id) : sel.add(p.id);
    anchor = [u.frames[0].id, u.frames[u.frames.length - 1].id];
  }
  function rangeTo(u) {
    const a = anchor && anchor.map(id => idx.get(id));
    if (!a || a[0] == null) { toggle(u); return; }
    const t = span(u), lo = Math.min(a[0], t[0]), hi = Math.max(a[1], t[1]);
    for (let i = lo; i <= hi; i++) sel.add(flat[i].id);
    anchor = [u.frames[0].id, u.frames[u.frames.length - 1].id];
  }
  grid.addEventListener("click", e => {
    if (swallow > Date.now()) { e.preventDefault(); return; }
    const op = e.target.closest("[data-open]"), cl = e.target.closest("[data-collapse]");
    if (op) { expanded.add(op.dataset.open); render(); focusB(op.dataset.open); return; }
    if (cl) { expanded.delete(cl.dataset.collapse); render(); return; }
    const c = e.target.closest(".cell"); if (!c) return;
    const u = unitByKey.get(c.dataset.k); if (!u) return;
    if (selectMode || e.shiftKey) {
      if (!selectMode) selectMode = true;
      if (armed || (e.shiftKey && anchor)) rangeTo(u); else toggle(u);
      armed = false; refresh();
    } else preview(u.frames[0]);
  });
  function focusB(b) { const f = grid.querySelector(".bopen-x"); if (f) f.focus(); }
  grid.addEventListener("contextmenu", e => e.preventDefault());
  grid.addEventListener("dragstart", e => e.preventDefault());
  // nhấn giữ = vào chế độ chọn và chọn ô đó; cuộn/di chuyển thì huỷ
  let lp = 0, lpAt = null;
  grid.addEventListener("pointerdown", e => {
    const c = e.target.closest(".cell"); if (!c || e.target.closest("[data-open]") || e.button) return;
    lpAt = [e.clientX, e.clientY]; clearTimeout(lp);
    lp = setTimeout(() => {
      const u = unitByKey.get(c.dataset.k); if (!u) return;
      swallow = Infinity; selectMode = true; armed = false;
      for (const p of u.frames) sel.add(p.id);
      anchor = [u.frames[0].id, u.frames[u.frames.length - 1].id];
      if (navigator.vibrate) try { navigator.vibrate(15); } catch (_) {}
      refresh();
    }, 480);
  });
  const stopLP = () => { clearTimeout(lp); lpAt = null; if (swallow === Infinity) swallow = Date.now() + 350; };  // chỉ nuốt cú chạm ngay sau khi nhả tay
  grid.addEventListener("pointermove", e => { if (lpAt && Math.hypot(e.clientX - lpAt[0], e.clientY - lpAt[1]) > 8) stopLP(); });
  for (const t of ["pointerup", "pointercancel", "pointerleave"]) grid.addEventListener(t, stopLP);
  addEventListener("scroll", stopLP, { passive: true });

  $("v-mode").onclick = () => setMode(!selectMode);
  $("v-all").onclick = () => { for (const p of flat) sel.add(p.id); refresh(); };
  $("a-clear").onclick = () => { sel.clear(); armed = false; anchor = null; refresh(); };
  $("a-range").onclick = () => { armed = !armed && !!anchor; if (!anchor) toast("Chạm một ảnh trước, rồi chạm “Chọn đến đây” và ảnh cuối của đoạn."); refresh(); };
  for (const b of document.querySelectorAll(".fchip")) b.onclick = () => {
    const isEv = b.dataset.e != null, group = isEv ? "#evf .fchip" : ".filters:not(#evf) .fchip";
    if (isEv) evf = b.dataset.e; else filter = b.dataset.f;
    anchor = null; armed = false;
    for (const id of [...sel]) if (!pass(byId.get(id))) sel.delete(id);  // chỉ giữ các khung còn nhìn thấy ở bộ lọc mới
    for (const x of document.querySelectorAll(group)) x.setAttribute("aria-pressed", x === b ? "true" : "false");
    render();
  };
  addEventListener("keydown", e => { if (e.key === "Escape" && selectMode && !document.querySelector("dialog[open]")) setMode(false); });

  // ---- ẩn / hiện: lạc quan, quay lại khi lỗi, hoàn tác ----
  function toast(text, undo) {
    clearTimeout(toastTimer);
    $("toast-t").textContent = text; $("toast-undo").hidden = !undo; $("toast-undo").onclick = undo ? () => { toastEl.hidden = true; undo(); } : null;
    toastEl.hidden = false;
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, undo ? 10000 : 5000);
  }
  function confirmBig(n, hidden, text) {
    const d = $("cf");
    $("cf-t").textContent = text || `${hidden ? "Ẩn" : "Hiện"} ${plural(n)}?`;
    return new Promise(res => {
      const done = v => { d.close(); res(v); };
      $("cf-yes").onclick = () => done(true); $("cf-no").onclick = () => done(false); d.oncancel = () => res(false);
      d.showModal();
    });
  }
  async function apply(list, hidden, isUndo) {
    const ch = list.filter(p => p.hidden !== hidden);
    if (!ch.length) return;
    if (!isUndo && ch.length > 200 && !(await confirmBig(ch.length, hidden))) return;
    for (const p of ch) p.hidden = hidden;
    if (!isUndo) sel.clear();
    anchor = null; armed = false;
    render();
    const ids = ch.map(p => p.id), want = counts();
    const word = hidden ? "Đã ẩn" : "Đã hiện";
    const undo = () => apply(ch, !hidden, true);
    toast(isUndo ? `Đã hoàn tác (${plural(ch.length)})` : `${word} ${plural(ch.length)}`, isUndo ? null : undo);
    chain = chain.then(async () => {
      let ok = true;
      for (let i = 0; i < ids.length && ok; i += 5000) {
        try {
          const d = await api("/api/admin/visibility", { ids: ids.slice(i, i + 5000), hidden });
          if (i + 5000 >= ids.length && (d.counts.hidden !== want.hidden || d.counts.all !== want.all)) load();  // lệch với máy chủ → tải lại cho đúng
        } catch (e) {
          if (e.message === "401") return;
          ok = false;
          for (const p of ch) { p.hidden = !hidden; if (!isUndo) sel.add(p.id); }  // trả lại cả lựa chọn để bấm lại được ngay
          render();
          toast(`Chưa lưu được — ${plural(ch.length)} giữ như cũ. Bạn thử lại nhé.`);
        }
      }
    });
    return chain;
  }
  // ---- chuyển ngày hàng loạt: cùng khuôn với ẩn / hiện (lạc quan, quay lại khi lỗi, hoàn tác) ----
  async function applyEvent(list, ev, isUndo) {
    const prev = list.filter(p => p.ev !== ev).map(p => [p, p.ev]);
    if (!prev.length) { if (!isUndo) toast(`Các ảnh đã chọn đã ở ngày ${evLabel(ev)}.`); return; }
    if (!isUndo && prev.length > 200 && !(await confirmBig(prev.length, null, `Chuyển ${plural(prev.length)} sang ngày ${evLabel(ev)}?`))) return;
    for (const [p] of prev) p.ev = ev;
    if (!isUndo) sel.clear();
    anchor = null; armed = false;
    render();
    const undo = () => applyEvent2(prev.map(([p, e]) => [p, e]));
    toast(isUndo ? `Đã hoàn tác (${plural(prev.length)})` : `Đã chuyển ${plural(prev.length)} sang ngày ${evLabel(ev)}`, isUndo ? null : undo);
    chain = chain.then(async () => {
      let saved = 0;   // số ảnh máy chủ đã nhận (các mảnh đã commit không được hoàn lại)
      try { for (; saved < prev.length; saved += 5000) await api("/api/admin/event", { ids: prev.slice(saved, saved + 5000).map(([p]) => p.id), event: ev }); }
      catch (e) {
        if (e.message === "401") return;
        const failed = prev.slice(saved);   // chỉ mảnh lỗi và các mảnh sau nó quay về ngày cũ
        for (const [p, old] of failed) p.ev = old;
        render();
        toast(saved ? `Chỉ lưu được một phần: ${plural(saved)} đã chuyển, ${plural(failed.length)} giữ nguyên ngày cũ. Bạn thử lại nhé.`
          : `Chưa lưu được — ${plural(prev.length)} giữ nguyên ngày cũ. Bạn thử lại nhé.`);
        load();   // đồng bộ lại với máy chủ cho chắc
      }
    });
    return chain;
  }
  async function applyEvent2(pairs) {   // hoàn tác: mỗi ảnh về đúng ngày trước đó (gộp theo ngày)
    const by = new Map();
    for (const [p, e] of pairs) { if (!by.has(e)) by.set(e, []); by.get(e).push(p); }
    for (const [e, ps] of by) await applyEvent(ps, e, true);
  }
  for (const b of document.querySelectorAll("[data-ev]")) if (b.id.startsWith("a-ev-")) b.onclick = () => applyEvent(chosen(), b.dataset.ev);
  const chosen = () => [...sel].map(id => byId.get(id)).filter(Boolean);
  $("a-hide").onclick = () => apply(chosen(), true);
  $("a-show").onclick = () => apply(chosen(), false);

  // ---- xem ảnh (chế độ thường) ----
  const pv = $("pv");
  function preview(p) {
    pvPos = idx.has(p.id) ? idx.get(p.id) : -1;
    showPv(p); if (!pv.open) pv.showModal();
  }
  function showPv(p) {
    $("pv-img").src = big(p) || thumb(p) || ""; $("pv-img").alt = p.name;
    const all = p.b ? bursts.get(p.b) : null;
    $("pv-cap").textContent = `${p.name} · ngày ${evLabel(p.ev)} · ${p.hidden ? "đang ẩn" : "đang hiện"}` + (all ? ` · khung ${all.indexOf(p) + 1}/${all.length} của chuỗi` : "");
    $("pv-tog").textContent = p.hidden ? "Hiện ảnh này" : "Ẩn ảnh này";
    $("pv-tog").onclick = async () => { await apply([p], !p.hidden); if (pv.open && byId.get(p.id) === p) showPv(p); };
    $("pv-prev").hidden = $("pv-next").hidden = pvPos < 0 || flat.length < 2;
  }
  const step = d => { if (pvPos < 0 || !flat.length) return; pvPos = (pvPos + d + flat.length) % flat.length; showPv(flat[pvPos]); };
  $("pv-prev").onclick = () => step(-1); $("pv-next").onclick = () => step(1);
  $("pv-close").onclick = () => pv.close();
  pv.addEventListener("click", e => { if (e.target === pv) pv.close(); });
  pv.addEventListener("keydown", e => { if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1); });
  pv.addEventListener("close", () => { $("pv-img").removeAttribute("src"); });

  // ---- cài đặt tìm bằng selfie ----
  function setting(on) {
    const cb = $("set-incl"); cb.checked = on; cb.disabled = false;
    $("set-note").textContent = on
      ? "Đang bật: khách vẫn tìm thấy ảnh của mình, kể cả ảnh đang ẩn. Lưu ý: nhờ vậy khách có thể đoán được ảnh nào đang ẩn."
      : "Đang tắt: ảnh đang ẩn được giấu hoàn toàn khỏi khách, cả khi họ tìm bằng selfie.";
  }
  $("set-incl").onchange = async () => {
    const cb = $("set-incl"), want = cb.checked; cb.disabled = true;
    try { const d = await api("/api/admin/settings", { search_includes_hidden: want }); setting(d.settings.search_includes_hidden); toast(want ? "Đã bật: ảnh ẩn vẫn có thể hiện khi khách tìm bằng selfie." : "Đã tắt: ảnh ẩn không hiện khi khách tìm bằng selfie."); }
    catch (e) { if (e.message !== "401") { setting(!want); toast("Chưa lưu được cài đặt. Bạn thử lại nhé."); } }
  };

  addEventListener("resize", () => { if (selectMode) document.documentElement.style.setProperty("--bar-h", bar.offsetHeight + "px"); });
  load();
  // sau Xử lý: chờ các yêu cầu ẩn/hiện/chuyển ngày đang bay xong (chain) rồi mới tải lại → không đè lên thay đổi lạc quan; giữ bộ lọc, lựa chọn (load tự bỏ ảnh đã mất)
  const reload = () => { chain = chain.then(() => load()); };
  return { load, reload };
})();

// ---- video: băm sha256 tăng dần → init → gửi mảnh tuần tự (nối lại từ offset máy chủ) → poster → finish ----
const VID = (() => {
  const CHUNK_START = 8 * 1048576, CHUNK_MIN = 1048576, POSTER_MAX = 2 * 1048576;
  let busy = false, titleTouched = false, cancelled = false, inflight = null, finSent = false;  // inflight: CHỈ request của lượt tải lên (không lẫn với đổi ngày ở danh sách)
  const fileEl = $("vf-file"), posterEl = $("vf-poster"), titleEl = $("vf-title"), goEl = $("vf-go"), msgEl = $("vf-msg"), infoEl = $("vf-info");
  const stem = n => n.replace(/\.[^.]*$/, "");
  const evName = v => v ? "ngày " + evLabel(v) : "không gán ngày";
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const mmss = s => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const say = (t, err) => { msgEl.textContent = t; msgEl.classList.toggle("vf-err", !!err); };
  const bar = p => { $("vf-bar-wrap").hidden = false; $("vf-bar").style.width = Math.min(100, p) + "%"; $("vf-bar-wrap").setAttribute("aria-valuenow", String(Math.round(p))); };
  class Fail extends Error {}
  const chk = () => {
    if (!cancelled) return;
    if (finSent) { list(); throw new Fail("Đã huỷ khi đang công bố: video có thể đã được đăng. Danh sách bên dưới vừa được làm mới để xem kết quả thật."); }
    throw new Fail("Đã huỷ. Phiên tải vẫn được giữ: chọn lại đúng file này để tiếp tục.");
  };
  const META = "vidmeta:";  // máy chủ không trả lại tiêu đề/ngày của phiên đang tải; ghi nhớ ở trình duyệt này để báo lại khi nối lại
  const metaGet = sha => { try { return JSON.parse(localStorage.getItem(META + sha)); } catch (e) { return null; } };
  const metaSet = (sha, m) => { try { localStorage.setItem(META + sha, JSON.stringify(m)); } catch (e) {} };
  const metaDel = sha => { try { localStorage.removeItem(META + sha); } catch (e) {} };
  const freeze = on => { for (const x of [fileEl, posterEl, titleEl, $("vf-ev")]) x.disabled = on; $("vf-cancel").hidden = !on; };
  $("vf-cancel").onclick = () => { cancelled = true; if (inflight) inflight.abort(); };
  titleEl.oninput = () => { titleTouched = true; };
  fileEl.onchange = () => {
    const f = fileEl.files[0];
    if (f && !titleTouched) titleEl.value = stem(f.name).slice(0, 120);
    goEl.disabled = !f || busy; say(""); infoEl.textContent = "";
  };
  addEventListener("beforeunload", e => { if (busy) { e.preventDefault(); e.returnValue = ""; } });

  async function call(method, path, body, ctype, timeoutMs, up) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), timeoutMs || 30000);
    if (up) inflight = ctl;
    try {
      const r = await fetch(path, { method, body, signal: ctl.signal, headers: ctype ? { "Content-Type": ctype } : {} });
      if (r.status === 401) { toLogin(); throw new Fail("Phiên đăng nhập đã hết hạn."); }
      let j = {}; try { j = await r.json(); } catch (e) {}
      return { status: r.status, j };
    } finally { clearTimeout(t); if (inflight === ctl) inflight = null; }
  }
  const jcall = (method, path, obj, up) => call(method, path, obj === undefined ? undefined : JSON.stringify(obj), obj === undefined ? null : "application/json", 0, up);

  function frameJpeg(file) {   // khung hình ở ~1 s → JPEG ≤ 2 MiB (null nếu trình duyệt không giải mã được)
    return new Promise(res => {
      const v = document.createElement("video"), url = URL.createObjectURL(file);
      const done = b => { URL.revokeObjectURL(url); v.removeAttribute("src"); v.load(); res(b); };
      const to = setTimeout(() => done(null), 15000);
      v.muted = true; v.preload = "auto"; v.playsInline = true;
      v.onerror = () => { clearTimeout(to); done(null); };
      v.onloadedmetadata = () => { v.currentTime = Math.min(1, (v.duration || 2) / 2); };
      v.onseeked = async () => {
        clearTimeout(to);
        try {
          let w = v.videoWidth, h = v.videoHeight;
          if (!w || !h) return done(null);
          const sc = Math.min(1, 1280 / Math.max(w, h)); w = Math.round(w * sc); h = Math.round(h * sc);
          const c = document.createElement("canvas"); c.width = w; c.height = h; c.getContext("2d").drawImage(v, 0, 0, w, h);
          for (const q of [0.85, 0.7, 0.5]) {
            const b = await new Promise(r => c.toBlob(r, "image/jpeg", q));
            if (b && b.size <= POSTER_MAX) return done(b);
          }
        } catch (e) {}
        done(null);
      };
      v.src = url;
    });
  }

  async function sendChunks(uid, file, offset, chunkMax, t0) {
    let size = Math.min(CHUNK_START, chunkMax), fails = 0, stuck = 0;
    const from = offset;
    while (offset < file.size) {
      chk();
      const end = Math.min(file.size, offset + size), prev = offset;
      let r;
      try { r = await call("PUT", `/api/admin/video/${uid}/chunk?off=${offset}`, file.slice(offset, end), "application/octet-stream", 30000 + size / 20000 * 1000, true); }
      catch (e) {
        if (e instanceof Fail) throw e;
        chk();
        size = Math.max(CHUNK_MIN, size >> 1);   // timeout / mạng đứt: mảnh nhỏ hơn
        if (++fails > 8) throw new Fail("Mạng không ổn định, đã thử lại nhiều lần. Chọn lại file để tiếp tục.");
        say(`Mạng chập chờn, thử lại (${fails})…`, true); await sleep(Math.min(8000, 500 * 2 ** fails)); chk(); continue;
      }
      if (r.status === 200 && typeof r.j.offset === "number") { offset = r.j.offset; fails = 0; }
      else if (r.status === 409 && typeof r.j.offset === "number") offset = r.j.offset;
      else if (r.status === 507) throw new Fail("Ổ đĩa máy chủ không đủ chỗ cho video này.");
      else if (r.status === 404) throw new Fail("Phiên tải đã hết hạn. Chọn lại file để bắt đầu lại.");
      else if (r.status >= 500 || r.status === 429) { if (++fails > 8) throw new Fail("Máy chủ lỗi (" + r.status + ")."); await sleep(Math.min(8000, 500 * 2 ** fails)); }
      else throw new Fail((r.j && (r.j.detail || r.j.error)) || "Lỗi tải mảnh (" + r.status + ").");
      if (offset > prev) stuck = 0;
      else if (offset < file.size && r.status < 500 && ++stuck >= 8) throw new Fail("Máy chủ và trình duyệt không thống nhất được vị trí tải (đã thử 8 lần). Phiên tải vẫn được giữ: chọn lại đúng file này để thử lại.");
      else if (stuck) { say(`Đồng bộ lại vị trí với máy chủ (${stuck}/8)…`, true); await sleep(Math.min(2000, 200 * 2 ** stuck)); chk(); }
      const pct = offset / file.size * 100, el = (Date.now() - t0) / 1000, rate = (offset - from) / Math.max(el, 0.5);
      bar(pct);
      infoEl.textContent = `Đã gửi ${fmt(offset)} / ${fmt(file.size)} (${n1.format(pct)}%)` + (rate > 0 && offset < file.size ? ` · còn khoảng ${mmss((file.size - offset) / rate)}` : "");
    }
  }

  async function start() {
    const file = fileEl.files[0];
    if (!file || busy) return;
    busy = true; cancelled = false; finSent = false; goEl.disabled = true; freeze(true); infoEl.textContent = ""; infoEl.classList.remove("vf-err"); say("");
    const title = titleEl.value.trim().slice(0, 120) || stem(file.name), ev = $("vf-ev").value || null;  // chốt ngay: từ đây các ô bị khoá
    let sha = "";
    try {
      if (!/\.mp4$/i.test(file.name)) throw new Fail("Chỉ nhận file .mp4.");
      let poster = posterEl.files[0] || null;
      if (poster && poster.size > POSTER_MAX) throw new Fail("Ảnh poster quá lớn (tối đa 2 MB).");
      say("Đang tính mã kiểm tra của video…");
      sha = await Sha256.file(file, done => { bar(done / file.size * 100); infoEl.textContent = `Tính mã kiểm tra: ${n1.format(done / file.size * 100)}%`; }, undefined, { get aborted() { return cancelled; } });
      if (!poster) { say("Đang lấy khung hình làm poster…"); poster = await frameJpeg(file); }
      if (!poster) throw new Fail("Không tự lấy được poster từ video này — hãy chọn thêm một ảnh poster (.jpg).");
      chk();
      say("Đang khởi tạo phiên tải…");
      const ini = await jcall("POST", "/api/admin/video/init", { name: file.name, size: file.size, sha256: sha, ev, title }, true);
      if (ini.status === 409 && ini.j.error === "duplicate") throw new Fail("Video này đã được đăng rồi.");
      if (ini.status === 409) throw new Fail(ini.j.error === "publishing" ? "Video này đang được công bố, chờ chút rồi thử lại." : "Đã có phiên tải khác cho cùng mã nhưng khác kích thước.");
      if (ini.status === 429) throw new Fail("Đang có quá nhiều phiên tải dở dang. Hoàn tất hoặc chờ phiên cũ hết hạn.");
      if (ini.status === 507) throw new Fail(ini.j.message || "Ổ đĩa máy chủ không đủ chỗ.");
      if (ini.status !== 200) throw new Fail((ini.j && ini.j.detail) || "Không khởi tạo được (" + ini.status + ").");
      const { uid } = ini.j;
      let kept = metaGet(sha);
      if (kept && (kept.uid !== uid || !(kept.exp > Date.now() / 1000))) { metaDel(sha); kept = null; }  // phiên khác / hết hạn: bỏ gợi ý cũ
      if (ini.j.offset > 0 || kept) {
        const kt = kept ? kept.title : null, ke = kept ? kept.ev : undefined;
        say(`Tiếp tục bản đang tải: ${kept ? `trình duyệt này nhớ lúc đầu bạn chọn “${kt}”, ${evName(ke)}` : "(trình duyệt này không nhớ tiêu đề/ngày đã chọn lúc đầu)"} — từ ${n1.format(ini.j.offset / file.size * 100)}%. Tiêu đề giữ như lần đầu` +
          (kept && ke !== ev ? `; ngày bạn chọn lúc này (${evName(ev)}) sẽ được áp dụng sau khi công bố.` : "."));
      } else say("Đang tải lên…");
      if (!kept) metaSet(sha, { uid, exp: ini.j.expires, title, ev });
      await sendChunks(uid, file, ini.j.offset, ini.j.chunk_max || CHUNK_START, Date.now());
      say("Đang gửi poster…");
      const pr = await call("PUT", `/api/admin/video/${uid}/poster`, poster, "image/jpeg", 60000, true);
      if (pr.status !== 200) throw new Fail((pr.j && (pr.j.detail || pr.j.error)) || "Không gửi được poster (" + pr.status + ").");
      say("Đang kiểm tra và công bố…"); bar(100);
      let fin;
      for (let i = 0; ; i++) {
        chk();
        finSent = true;
        try { fin = await call("POST", `/api/admin/video/${uid}/finish`, undefined, null, 600000, true); } catch (e) { chk(); if (e instanceof Fail || i >= 3) throw e; await sleep(2000); chk(); continue; }
        if (fin.status >= 500 && i < 3) { await sleep(2000); chk(); continue; }
        break;
      }
      if (fin.status === 422) throw new Fail("Video không hợp lệ: " + (fin.j.reason || "không đọc được MP4") + ". Cần H.264 + AAC và “faststart”.");
      if (fin.status === 409 && fin.j.error === "duplicate") throw new Fail("Video này đã được đăng rồi.");
      if (fin.status !== 200) throw new Fail((fin.j && (fin.j.detail || fin.j.error)) || "Không công bố được (" + fin.status + ").");
      metaDel(sha);
      let note = "", dayErr = "";
      if ((fin.j.ev || null) !== ev) {   // phiên cũ giữ ngày khác với lựa chọn lúc này: áp dụng ngày đã chọn
        let ok = false;
        try { ok = (await jcall("POST", `/api/admin/video/${fin.j.id}/event`, { ev })).status === 200; } catch (e) {}
        if (ok) { note = ` Ngày đã đặt theo lựa chọn hiện tại: ${evName(ev)}.`; fin.j.ev = ev; }
        else dayErr = `Đã đăng video, nhưng chưa đổi được ngày sang ${evName(ev)} (hiện: ${evName(fin.j.ev)}). Dùng ô chọn ngày trong danh sách bên dưới để sửa.`;
      }
      if (fin.j.title !== title) note += ` Tiêu đề giữ như lần tải đầu: “${fin.j.title}” (chưa có chức năng sửa tiêu đề).`;
      say(`Xong: “${fin.j.title}” (${mmss(fin.j.duration)}, ${evName(fin.j.ev)}). Khách thấy ngay trong tab Video.${note}`);
      infoEl.textContent = dayErr; infoEl.classList.toggle("vf-err", !!dayErr); fileEl.value = posterEl.value = ""; titleEl.value = ""; titleTouched = false;
      list();
    } catch (e) {
      say(e instanceof Fail ? e.message : "Lỗi không mong đợi: " + (e && e.message || e), true);
    } finally {
      busy = false; inflight = null; freeze(false); goEl.disabled = !fileEl.files[0];
    }
  }
  goEl.onclick = start;

  // ---- danh sách video đã đăng (lấy từ /api/gallery: video đã công bố luôn công khai) + đổi ngày ----
  async function list() {
    let rows = [];
    try { const r = await fetch("/api/gallery", { cache: "no-cache" }); rows = (await r.json()).videos || []; } catch (e) { return; }
    $("vl-empty").hidden = rows.length > 0;
    $("vlist").replaceChildren(...rows.map(r => {
      const [id, v, pv, , , d, title, ev] = r, li = el("li"), img = el("img"), info = el("div", "vi"), sel = el("select");
      img.src = `/v/${id}-${pv}.jpg`; img.alt = ""; img.loading = "lazy";
      info.append(el("b", null, title || "(không tiêu đề)"), el("span", "sum", `Thời lượng ${mmss(d)}`));
      sel.setAttribute("aria-label", "Ngày của video " + title);
      for (const [val, lab] of [["", "Không gán ngày"], ["2026-10-04", "Ngày 4/10"], ["2026-10-17", "Ngày 17/10"]]) {
        const o = el("option", null, lab); o.value = val; sel.append(o);
      }
      sel.value = ev || ""; let cur = sel.value;
      sel.onchange = async () => {
        const want = sel.value || null;
        if (!confirm(`Chuyển video “${title}” sang ${evName(want)}? Khách sẽ thấy thay đổi ngay và không có nút hoàn tác (muốn quay lại thì chọn lại ngày cũ).`)) { sel.value = cur; return; }
        sel.disabled = true;
        try {
          const rr = await jcall("POST", `/api/admin/video/${id}/event`, { ev: want });
          if (rr.status !== 200) throw new Error();
          cur = sel.value; say(`Đã chuyển “${title}” sang ${evName(want)}.`);
        } catch (e) { sel.value = cur; say("Chưa đổi được ngày của video. Bạn thử lại nhé.", true); }
        sel.disabled = false;
      };
      li.append(img, info, sel);
      return li;
    }));
  }
  list();
  return { list };
})();
