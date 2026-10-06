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
    queue.push({ f, li, st, fill, loaded: 0 }); total += f.size;
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
  x.open("POST", "/api/upload?name=" + encodeURIComponent(it.f.name));
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
    if (x.status === 200 && d.status === "ok") { st.textContent = "✓ " + fmt(it.f.size) + (d.renamed ? " (đổi tên: " + d.name + ")" : ""); st.className = "f-st ok"; doneN++; }
    else if (x.status === 200 && d.name) { st.textContent = "trùng với " + d.name; st.className = "f-st dup"; dupN++; }
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
  if (!run && timer) { clearInterval(timer); timer = null; refreshStats(); }
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
// Dữ liệu: GET /api/admin/photos → [id, w, h, t, v, c, name, b, hidden]; b = chuỗi liên thanh GỐC (không phải bản đã dồn của khách).
const vis = (() => {
  const grid = $("vgrid"), bar = $("abar"), toastEl = $("toast");
  const FILTERS = { all: () => true, shown: p => !p.hidden, hidden: p => p.hidden };
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
      for (const [id, w, h, t, v, c, name, b, hidden] of d.photos) {
        const p = { id, w, h, v, c, name, b: b || null, hidden: !!hidden };
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
    flat = P.filter(FILTERS[filter]); idx = new Map(flat.map((p, i) => [p.id, i]));
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
    filter = b.dataset.f; anchor = null; armed = false;
    for (const x of document.querySelectorAll(".fchip")) x.setAttribute("aria-pressed", x === b ? "true" : "false");
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
  function confirmBig(n, hidden) {
    const d = $("cf");
    $("cf-t").textContent = `${hidden ? "Ẩn" : "Hiện"} ${plural(n)}?`;
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
    $("pv-cap").textContent = `${p.name} · ${p.hidden ? "đang ẩn" : "đang hiện"}` + (all ? ` · khung ${all.indexOf(p) + 1}/${all.length} của chuỗi` : "");
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
  return { load };
})();
