"use strict";
// ---- ngưỡng điểm (cosine) — chỉnh ở đây. Khách không bao giờ thấy điểm; chủ nhà xem điểm ở /?tune=1 ----
const CONFIDENT_CUT = 0.40;  // ≥ mức này: "Ảnh của bạn"
const MAYBE_CUT = 0.28;      // từ mức này tới CONFIDENT_CUT: "Có thể cũng là bạn" (thu gọn)
// ---- ảnh selfie quá lớn thì thu về cạnh dài này trước khi gửi (nhanh hơn trên 4G, khỏi vướng giới hạn 40 MP của máy chủ) ----
const SEND_MAX_EDGE = 2000;
const SEND_RESIZE_ABOVE = 2400;

const $ = id => document.getElementById(id);
const TUNE = new URLSearchParams(location.search).get("tune") === "1";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const nf = new Intl.NumberFormat("vi-VN");
// Ảnh chụp liên thanh (burst) có chung `b`. Mặc định GỘP: mỗi chuỗi thành MỘT ô (khung đầu + huy hiệu "+N"), lightbox lướt qua từng khung,
// không khung nào bị ẩn hay xoá. Đặt false để hiện từng khung như ảnh riêng (?bursts=0 chỉ để so sánh/gỡ lỗi).
const COLLAPSE_BURSTS = new URLSearchParams(location.search).get("bursts") !== "0";

let file = null, selfieUrl = null, runId = 0, ctl = null;
let data = null;
let mode = "all";  // all: cả album · mine: kết quả tìm bằng selfie
let photos = [], galleryReady = false;

// ---------- địa chỉ ảnh + đường lùi: CDN → cùng nguồn gốc /i/ → cũ (/thumbs, /previews) ----------
let CDN = "";
const tq = x => x.tok ? "?t=" + encodeURIComponent(x.tok) : "";  // x.tok (không phải x.t: ở manifest `t` là giờ chụp)  // ảnh có token theo kết quả tìm (ảnh đang ẩn): /originals /thumbs /previews cần ?t=
const iUrl = (base, x, n) => `${base}/i/${x.id}-${x.v}-${n}.webp`;
function chain(x, kind) {
  const steps = [];
  if (x.v) {
    for (const base of CDN ? [CDN, ""] : [""]) {
      if (kind === "thumb") steps.push({ srcset: `${iUrl(base, x, 240)} 240w, ${iUrl(base, x, 480)} 480w`, src: iUrl(base, x, 480) });
      else steps.push({ src: iUrl(base, x, 2000) });
    }
  }
  steps.push({ src: (kind === "thumb" ? `/thumbs/${x.id}.jpg` : `/previews/${x.id}.jpg`) + tq(x) });
  return steps;
}
// gán bước đầu của đường lùi cho <img>; lỗi thì sang bước kế, hết đường thì onBad
function loadImg(img, steps, sizes, onBad) {
  let k = 0;
  const apply = () => {
    const s = steps[k];
    if (s.srcset) { img.sizes = sizes; img.srcset = s.srcset; } else img.removeAttribute("srcset");
    img.src = s.src;
  };
  img.onerror = () => { if (++k < steps.length) apply(); else onBad(); };
  apply();
}
function preconnect(origin) {
  if (!origin || [...document.querySelectorAll("link[rel=preconnect]")].some(l => l.href.replace(/\/$/, "") === origin)) return;  // backend có thể đã đặt sẵn trong HTML
  const l = document.createElement("link"); l.rel = "preconnect"; l.href = origin; document.head.append(l);
}

// hoa trang trí (~130 KB): chờ màn ảnh đầu hiện xong mới nạp, để không tranh băng thông với thumbnail trên mạng chậm
function loadDecor() {
  document.querySelectorAll("img.flower[data-src]").forEach(i => { i.onload = () => i.classList.add("on"); i.src = i.dataset.src; i.removeAttribute("data-src"); });
}
setTimeout(loadDecor, 6000);

// mỗi ảnh (id) chỉ xuất hiện đúng một lần. Giữ phần tử đầu tiên (giữ thứ tự manifest); nếu có `better` thì giữ phần tử tốt hơn
function uniqById(list, better) {
  const m = new Map();
  for (const x of list) { const o = m.get(x.id); if (!o || (better && better(x, o))) m.set(x.id, x); }
  return [...m.values()];
}

// Ô gộp: it.frames = mọi khung của chuỗi (theo thứ tự manifest), it.at = chỉ số khung đầu trong `flat` (bộ ảnh để lướt trong lightbox),
// f.head = ô chứa khung f. flat = các ô theo thứ tự, mỗi ô trải thành các khung của nó: mỗi id đúng một lần.
function stackTiles(items, seen = new Set()) {  // seen dùng chung giữa hai nhóm: một id không bao giờ ở cả hai
  const flat = [], out = [];
  for (const it of items) {
    it.frames = it.frames.filter(f => !seen.has(f.id));  // an toàn: một id không bao giờ ở hai ô
    if (!it.frames.length) continue;
    it.at = flat.length;
    for (const f of it.frames) { seen.add(f.id); f.head = it; flat.push(f); }
    out.push(it);
  }
  return { items: out, flat };
}
function collapse(list) {  // thư viện: gộp theo `b`, ô nằm ở vị trí của khung đầu tiên
  if (!COLLAPSE_BURSTS) return { items: list, flat: list };
  const heads = new Map(), items = [];
  for (const x of list) {
    const h = x.b && heads.get(x.b);
    if (h) h.frames.push(x);
    else { x.frames = [x]; items.push(x); if (x.b) heads.set(x.b, x); }
  }
  return stackTiles(items);
}
let burstFrames = new Map(), photoById = new Map();  // b -> mọi khung của chuỗi (từ manifest, theo thứ tự)

// ---------- thư viện ảnh: hàng ảnh căn đều tính bằng JS, chỉ dựng các hàng gần màn hình ----------
const WINDOW_VH = 2;   // dựng DOM trong ±2 màn hình quanh vùng nhìn thấy
const LOAD_VH = 1;     // chỉ nạp ảnh trong ±1 màn hình (sau khi màn đầu tải xong; trước đó chỉ ±0.2)
const DROP_VH = 1.6;   // ra khỏi ±1.6 màn hình thì bỏ src (giải phóng bộ nhớ giải mã, dừng tải dở)

// tỉ lệ khung hình an toàn: w/h = 0, thiếu hoặc quá cực đoan (panorama 1×100) không được làm vỡ bố cục
const arOf = x => { const r = x.w / x.h; return isFinite(r) && r > 0 ? Math.min(5, Math.max(0.2, r)) : 1.5; };

function layoutRows(items, W) {
  const H = W < 500 ? 135 : W < 900 ? 180 : 210, gap = W < 500 ? 4 : W < 900 ? 6 : 8;
  const rows = [], n = items.length;
  let i = 0, top = 0;
  while (i < n) {
    let j = i, sum = 0, h = 0, justify = true;
    while (j < n) {
      const ar = arOf(items[j]), hh = (W - gap * (j - i)) / (sum + ar);
      if (j > i && hh <= H && Math.abs(hh - H) > Math.abs(h - H)) break;  // thêm ảnh này làm hàng lệch xa chiều cao đích hơn
      sum += ar; h = hh; j++;
      if (hh <= H) break;
    }
    if (j >= n && h > H * 1.15) { h = H; justify = false; }  // hàng cuối còn trống nhiều: không kéo giãn
    const cnt = j - i, ws = [];
    let used = 0;
    for (let k = i; k < j; k++) { const w = Math.round(arOf(items[k]) * h); ws.push(w); used += w; }
    if (justify) ws[cnt - 1] += W - gap * (cnt - 1) - used;  // khít đúng bề ngang
    rows.push({ top, h: Math.round(h), i, j, ws });
    top += Math.round(h) + gap; i = j;
  }
  return { rows, height: rows.length ? top - gap : 0, gap };
}

const galleries = [];
class VGallery {
  constructor(root, kind, onOpen) {
    this.root = root; this.kind = kind; this.onOpen = onOpen;
    this.items = []; this.flat = []; this.rows = []; this.rowOf = []; this.mounted = new Map(); this.gap = 4;
    this.W = 0; this.active = false; this.warm = false; this.first = true; this.pending = 0;
    root.addEventListener("click", e => { const b = e.target.closest(".tile"); if (b) this.onOpen(this, +b.dataset.i, b); });
    new ResizeObserver(() => requestAnimationFrame(() => { if (this.active && this.root.clientWidth !== this.W) this.relayout(); })).observe(root);  // rAF: relayout đổi chiều cao root, không được làm lặp vòng quan sát
    galleries.push(this);
  }
  set(list) { this.put(collapse(list)); }
  put(c) { this.items = c.items; this.flat = c.flat; this.warm = false; this.first = true; this.relayout(true); }
  setActive(on) { this.active = on; if (on) this.relayout(); else this.unmountAll(); }
  unmountAll() { for (const r of this.mounted.values()) r.el.remove(); this.mounted.clear(); }
  relayout(fresh) {
    const W = this.root.clientWidth;
    if (!this.active || !W) return;
    let anchor = null;
    if (!fresh && this.rows.length && this.W) {  // giữ nguyên ảnh đang nhìn khi đổi bề ngang
      const y = -this.root.getBoundingClientRect().top;
      const r = this.rows[this.findRow(y)];
      if (r) anchor = { i: r.i, off: r.top - y };
    }
    this.W = W;
    const L = layoutRows(this.items, W);
    this.gap = L.gap; this.rows = L.rows; this.rowOf = new Array(this.items.length);
    this.rows.forEach((r, ri) => { for (let k = r.i; k < r.j; k++) this.rowOf[k] = ri; });
    this.root.style.height = L.height + "px";
    this.unmountAll();
    if (anchor) scrollTo(0, this.root.getBoundingClientRect().top + scrollY + this.rows[this.rowOf[anchor.i]].top - anchor.off);
    this.update();
  }
  findRow(y) {  // hàng đầu tiên có đáy ≥ y
    let lo = 0, hi = this.rows.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (this.rows[m].top + this.rows[m].h < y) lo = m + 1; else hi = m; }
    return lo;
  }
  update() {
    if (!this.active || !this.rows.length) return;
    const vh = innerHeight, y = -this.root.getBoundingClientRect().top;
    const a = this.findRow(y - WINDOW_VH * vh), hiY = y + vh + WINDOW_VH * vh;
    const keep = document.activeElement && this.root.contains(document.activeElement) ? this.rowOf[+document.activeElement.closest(".tile").dataset.i] : -1;
    let b = a;
    while (b + 1 < this.rows.length && this.rows[b + 1].top <= hiY) b++;
    for (const [ri, r] of this.mounted) if ((ri < a || ri > b) && ri !== keep) { r.el.remove(); this.mounted.delete(ri); }
    // DOM luôn theo đúng thứ tự hàng (Tab và trình đọc màn hình đi theo thứ tự DOM): hàng mới chèn trước hàng đã dựng có chỉ số lớn hơn gần nhất
    for (let ri = a; ri <= b; ri++) {
      if (this.mounted.has(ri)) continue;
      let next = null;
      for (const [k, r] of this.mounted) if (k > ri && (!next || k < next.k)) next = { k, el: r.el };
      const r = this.mountRow(ri);
      this.root.insertBefore(r.el, next ? next.el : null);
      this.mounted.set(ri, r);
    }
    // nạp / bỏ ảnh theo khoảng cách tới vùng nhìn thấy
    const L = this.warm ? LOAD_VH : 0.2;
    const lo1 = y - L * vh, hi1 = y + vh + L * vh, lo2 = y - DROP_VH * vh, hi2 = y + vh + DROP_VH * vh;
    for (const [ri, r] of this.mounted) {
      const row = this.rows[ri];
      if (row.top + row.h >= lo1 && row.top <= hi1) { if (!r.on) this.loadRow(r, row, y, vh); }
      else if (r.on && (row.top + row.h < lo2 || row.top > hi2)) this.dropRow(r);
    }
  }
  mountRow(ri) {
    const row = this.rows[ri], el = document.createElement("div");
    el.className = "vrow"; el.style.gap = this.gap + "px"; el.style.top = row.top + "px"; el.style.height = row.h + "px";
    for (let k = row.i, c = 0; k < row.j; k++, c++) {
      const x = this.items[k], b = document.createElement("button");
      b.type = "button"; b.className = "tile"; b.dataset.i = k; b.dataset.id = x.id;
      b.style.width = row.ws[c] + "px"; b.style.background = x.c || "";
      const nf2 = x.frames ? x.frames.length : 1;
      b.setAttribute("aria-label", `Mở ảnh ${k + 1} trên ${this.items.length}` + (nf2 > 1 ? `, gồm ${nf2} ảnh chụp liên tiếp` : ""));
      const img = document.createElement("img"); img.alt = ""; img.decoding = "async"; img.draggable = false;
      b.append(img);
      if (nf2 > 1) { b.classList.add("stacked"); const s = document.createElement("span"); s.className = "stack"; s.textContent = `+${nf2 - 1}`; s.setAttribute("aria-hidden", "true"); b.append(s); }
      if (TUNE && x.bbox) {
        const box = document.createElement("i"); box.className = "box"; placeBox(box, x.bbox, x.w, x.h);
        const sc = document.createElement("span"); sc.className = "score is-" + (x.score >= CONFIDENT_CUT ? "main" : "maybe"); sc.textContent = `${x.score.toFixed(3)} · ${x.name}`;
        b.append(box, sc);
      }
      el.append(b);
    }
    return { el, on: false };
  }
  loadRow(r, row, y, vh) {
    r.on = true;
    const inFirst = this.first && row.top < y + vh;  // hàng nằm trong màn đầu tiên
    const imgs = r.el.querySelectorAll("img");
    imgs.forEach((img, c) => {
      const k = row.i + c, x = this.items[k], tile = img.parentNode;
      img.loading = inFirst ? "eager" : "lazy";
      if (this.first && row.i === 0) img.fetchPriority = "high";
      img.onload = () => { img.classList.add("on"); x.ts = img.currentSrc; if (inFirst) this.settle(); };
      if (inFirst) this.pending++;
      tile.classList.remove("bad");
      loadImg(img, chain(x, "thumb"), row.ws[c] + "px", () => { tile.classList.add("bad"); if (inFirst) this.settle(); });
      img.dataset.f = inFirst ? "1" : "";
    });
    if (inFirst && !this.warmTimer) this.warmTimer = setTimeout(() => this.heat(), 3500);
  }
  settle() { if (this.pending > 0 && --this.pending === 0) this.heat(); }
  heat() {  // màn đầu xong (hoặc quá lâu): bắt đầu nạp trước phần sắp tới
    clearTimeout(this.warmTimer);
    this.first = false;
    loadDecor();
    if (!this.warm) { this.warm = true; this.update(); }
  }
  dropRow(r) {
    r.on = false;
    r.el.querySelectorAll("img").forEach(img => { img.onload = img.onerror = null; img.classList.remove("on"); img.removeAttribute("srcset"); img.removeAttribute("src"); });
  }
  reveal(i) {  // đưa ảnh i vào tầm nhìn (nếu cần) và trả focus về ô của nó
    const ri = this.rowOf[i], row = this.rows[ri];
    if (!row) return;
    const top = this.root.getBoundingClientRect().top + scrollY + row.top, vh = innerHeight;
    if (top < scrollY + 60 || top + row.h > scrollY + vh - 60) scrollTo(0, Math.max(0, top - (vh - row.h) / 2));
    this.update();
    const b = this.root.querySelector(`.tile[data-i="${i}"]`);
    if (b) b.focus({ preventScroll: true });
  }
}
let ticking = false;
const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; for (const g of galleries) g.update(); trackPill(); }); } };
addEventListener("scroll", onScroll, { passive: true });
addEventListener("resize", onScroll);
document.addEventListener("focusin", e => { if (e.target.classList && e.target.classList.contains("tile")) onScroll(); });

const gAll = new VGallery($("vg-all"), "all", (g, i, b) => openLb(g, i, b, false));
const gMain = new VGallery($("vg-main"), "main", (g, i, b) => openLb(g, i, b, true));
const gMaybe = new VGallery($("vg-maybe"), "maybe", (g, i, b) => openLb(g, i, b, true));

// ---------- tải danh sách ảnh (preload ở <head> dùng lại đúng request này) ----------
async function loadGallery() {
  $("empty").hidden = true; $("skeleton").hidden = false; if (mode === "all") $("all").hidden = false;
  let m;
  try {
    let r = await (window.__gallery || fetch("/api/gallery"));  // request đã bắt đầu ở <head>
    window.__gallery = null;                                     // thân chỉ đọc được một lần; "Thử lại" sẽ tải mới
    if (!r) r = await fetch("/api/gallery");
    if (!r.ok) throw new Error("http " + r.status);
    m = await r.json();
    if (!m || !Array.isArray(m.photos)) throw new Error("manifest sai dạng");
  } catch (e) {
    $("skeleton").hidden = true;
    showEmpty("Chưa tải được album", "Có vẻ mạng đang gián đoạn. Bạn thử lại nhé.", true);
    return;
  }
  performance.mark("gallery-manifest");
  CDN = String(m.cdn || "").replace(/\/+$/, "");
  preconnect(CDN);
  photos = uniqById(m.photos.map(p => ({ id: p[0], w: p[1], h: p[2], t: p[3], v: p[4], c: p[5], name: p[6], b: p[7] || null })));  // mỗi ảnh đúng một lần, kể cả khi manifest lỡ có dòng trùng
  burstFrames = new Map(); photoById = new Map(photos.map(p => [p.id, p]));
  for (const p of photos) if (p.b) { if (!burstFrames.has(p.b)) burstFrames.set(p.b, []); burstFrames.get(p.b).push(p); }
  galleryReady = true;
  $("skeleton").hidden = true;
  if (!photos.length) {
    $("count").textContent = "";
    $("cta-row").hidden = true; $("all").hidden = true; document.querySelector(".meta .sep").hidden = true;
    showEmpty("Album đang được cập nhật", "Ảnh cưới sẽ sớm có mặt ở đây. Bạn quay lại sau ít phút nhé.");
    return;
  }
  $("count").textContent = `${nf.format(photos.length)} ảnh`;
  gAll.set(photos);
  if (mode === "all") gAll.setActive(true);
  performance.mark("gallery-rendered");
  watchCta();
}
function showEmpty(t, s, retry) {
  loadDecor();
  if (retry) $("all").hidden = true;  // thẻ lỗi: bỏ vùng thư viện trống (min-height) phía trên
  $("empty").hidden = false; $("empty-t").textContent = t; $("empty-s").textContent = s; $("empty-retry").hidden = !retry;
}
$("empty-retry").onclick = loadGallery;
loadGallery();

// ---------- nút nổi "Tìm ảnh của bạn" / "Xem tất cả ảnh": hiện khi nút ở đầu trang đã cuộn khuất ----------
// Nút nổi không che ảnh: ẩn khi đang cuộn xuống và khi chân trang hiện ra; cuộn lên thì hiện lại.
let io = null, pillShown = false, pillAway = false, footSeen = false, lastY = 0;
function paintPill() { $("pill").hidden = !pillShown; $("pill").classList.toggle("away", pillAway || footSeen); }
function trackPill() {
  const y = scrollY;
  if (y > lastY + 6) pillAway = true; else if (y < lastY - 6) pillAway = false;
  lastY = y; paintPill();
}
new IntersectionObserver(es => { footSeen = es[es.length - 1].isIntersecting; paintPill(); }).observe(document.querySelector(".foot"));
function watchCta() {
  if (io) io.disconnect();
  pillAway = false;
  if (!galleryReady || !photos.length) { pillShown = false; paintPill(); return; }
  const target = mode === "all" ? $("cta-row") : $("finder");
  $("pill-find").hidden = mode !== "all"; $("pill-all").hidden = mode === "all";
  io = new IntersectionObserver(es => { pillShown = !es[es.length - 1].isIntersecting; paintPill(); });
  io.observe(target);
}
// "Xem tất cả ảnh": lấy lại đúng mục lịch sử đã đẩy ở enterMine (không đẩy thêm mục mới), popstate sẽ gọi showAll
const leaveMine = () => { if (history.state && history.state.mine) history.back(); else showAll(); };
$("pill-all").onclick = leaveMine;
$("b-all").onclick = leaveMine;

// ---------- chọn ảnh selfie ----------
$("in-file").onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) choose(f); };
// thả ảnh vào bất cứ đâu trên trang cũng nhận (và không để trình duyệt mở ảnh ra thay trang)
let dragN = 0;
addEventListener("dragenter", e => { e.preventDefault(); dragN++; document.body.classList.add("dragging"); });
addEventListener("dragleave", () => { if (--dragN <= 0) { dragN = 0; document.body.classList.remove("dragging"); } });
addEventListener("dragover", e => e.preventDefault());
addEventListener("drop", e => {
  e.preventDefault(); dragN = 0; document.body.classList.remove("dragging");
  const f = e.dataTransfer && e.dataTransfer.files[0]; if (f) choose(f);
});
$("b-retry").onclick = () => file && search();

function enterMine() {
  if (mode === "mine") return;
  mode = "mine";
  history.pushState({ mine: 1 }, "");  // nút Back của điện thoại quay về thư viện thay vì rời trang
  savedY = scrollY;
  gAll.setActive(false);
  $("all").hidden = true; $("finder").hidden = false; $("cta-row").hidden = true;
  watchCta();
}
let savedY = 0;
function showAll() {
  runId++; if (ctl) ctl.abort();
  clearResults();
  if (selfieUrl) URL.revokeObjectURL(selfieUrl);
  selfieUrl = null; file = null;
  mode = "all";
  $("finder").hidden = true; $("results").hidden = true; $("all").hidden = false; $("cta-row").hidden = !photos.length;
  gAll.setActive(true);
  scrollTo(0, savedY);  // về đúng chỗ đang xem trước khi tìm
  gAll.update(); watchCta();
}

function choose(f) {
  if (!f.type.startsWith("image/") && f.type !== "") { runId++; if (ctl) ctl.abort(); clearResults(); enterMine(); showError("Đây không phải ảnh", "Hãy chọn một tấm ảnh selfie (JPG hoặc PNG).", [], false, true); return; }
  clearResults();
  enterMine();
  if (selfieUrl) URL.revokeObjectURL(selfieUrl);
  file = f; selfieUrl = URL.createObjectURL(f);
  const img = $("selfie");
  $("selfie-box").hidden = true;
  $("selfie-fig").hidden = false;
  img.onerror = () => { $("selfie-fig").hidden = true; };  // trình duyệt không hiển thị được (vd. HEIC): vẫn gửi để máy chủ thử
  img.src = selfieUrl;
  $("finder").scrollIntoView({ block: "start" });
  search();
}

// ---------- gọi API ----------
async function prepare(f) {
  // giữ nguyên ảnh nếu vừa phải; quá lớn thì vẽ lại nhỏ hơn (trình duyệt tự áp EXIF khi vẽ <img>)
  const url = URL.createObjectURL(f);
  try {
    const im = new Image();
    im.src = url; await im.decode();
    const m = Math.max(im.naturalWidth, im.naturalHeight);
    if (m <= SEND_RESIZE_ABOVE) return f;
    const s = SEND_MAX_EDGE / m, c = document.createElement("canvas");
    c.width = Math.round(im.naturalWidth * s); c.height = Math.round(im.naturalHeight * s);
    c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
    const b = await new Promise(r => c.toBlob(r, "image/jpeg", 0.92));
    return b || f;
  } catch (e) { return f; }
  finally { URL.revokeObjectURL(url); }  // kể cả khi decode() lỗi (HEIC…)
}

async function search() {
  const my = ++runId;
  if (ctl) ctl.abort();
  ctl = new AbortController();
  clearResults();
  setLoading();
  const t0 = performance.now();
  try {
    const body = await prepare(file);
    if (my !== runId) return;
    // selfie CHỈ gửi tới đường dẫn tương đối cùng nguồn gốc với trang — không bao giờ tới CDN
    const r = await fetch("/api/search", { method: "POST", body, headers: { "Content-Type": "application/octet-stream" }, signal: ctl.signal });
    if (my !== runId) return;
    if (r.status === 422) {
      const e = await r.json().catch(() => ({}));
      if (my !== runId) return;
      if (e.error === "face_too_small") {
        showError("Khuôn mặt hơi nhỏ", "Khuôn mặt trong ảnh quá nhỏ để nhận ra bạn. Hãy chụp gần hơn hoặc dùng một tấm ảnh khác.",
          ["Đưa máy lại gần, để mặt chiếm phần lớn khung hình", "Chọn ảnh chỉ có một mình bạn"]);
      } else {
        showError("Chưa thấy khuôn mặt nào", "Chúng tôi không tìm thấy khuôn mặt trong ảnh này. Bạn thử một tấm selfie rõ hơn nhé.",
          ["Chụp nơi đủ sáng, nhìn thẳng vào máy", "Bỏ khẩu trang, kính râm hoặc tóc che mặt", "Đảm bảo ảnh không bị mờ"]);
      }
      return;
    }
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      if (my !== runId) return;
      if (r.status === 503) showError("Đang đông khách quá", "Nhiều người đang tìm ảnh cùng lúc. Bạn thử lại sau ít giây nhé.", [], true);
      else if (r.status === 413) showError("Ảnh quá lớn", e.detail || "Hãy chọn một ảnh nhỏ hơn.", []);
      else if (r.status === 400) showError("Không đọc được ảnh", e.detail || "Hãy thử một file JPG hoặc PNG khác.", []);
      else showError("Có chút trục trặc", "Chưa tìm được ảnh lúc này. Bạn thử lại sau ít phút nhé.", [], true);
      return;
    }
    data = await r.json();
    data.e2e = performance.now() - t0;
    showResults();
  } catch (err) {
    if (err.name === "AbortError" || my !== runId) return;
    showError("Chưa kết nối được", "Có vẻ mạng đang gián đoạn. Bạn kiểm tra kết nối rồi thử lại nhé.", [], true);
  }
}

// ---------- trạng thái ----------
function setLoading() {
  $("selfie-fig").classList.add("is-loading");
  $("finder").classList.remove("is-done");
  $("selfie-box").hidden = true;
  $("s-loading").hidden = false; $("s-msg").hidden = true; $("s-actions").hidden = true;
  $("s-multi").hidden = true;
}
function showError(title, text, tips, canRetry, noPreview) {
  if (noPreview) { $("selfie-fig").hidden = true; }
  $("selfie-fig").classList.remove("is-loading");
  $("finder").classList.remove("is-done");
  $("s-loading").hidden = true; $("s-msg").hidden = false; $("s-msg").classList.add("is-err");
  $("s-title").textContent = title; $("s-text").textContent = text;
  $("s-multi").hidden = true;
  const ul = $("s-tips"); ul.textContent = ""; ul.hidden = !tips.length;
  for (const t of tips || []) { const li = document.createElement("li"); li.textContent = t; ul.append(li); }
  $("b-retry").hidden = !canRetry || !file;
  $("s-actions").hidden = false;
  $("status").focus({ preventScroll: true });
  $("status").scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
}

function placeBox(box, bbox, w, h) {
  if (!w || !h) return;
  box.hidden = false;
  box.style.left = bbox[0] / w * 100 + "%"; box.style.top = bbox[1] / h * 100 + "%";
  box.style.width = (bbox[2] - bbox[0]) / w * 100 + "%"; box.style.height = (bbox[3] - bbox[1]) / h * 100 + "%";
}

function showResults() {
  const q = data.query;
  $("selfie-fig").classList.remove("is-loading");
  placeBox($("selfie-box"), q.bbox, q.width, q.height);
  $("s-loading").hidden = true; $("s-msg").hidden = false; $("s-msg").classList.remove("is-err");
  $("s-tips").hidden = true;
  const total = data.results.filter(x => x.score >= MAYBE_CUT).length;
  const none = !TUNE && total === 0;
  const empty = photos.length === 0;
  $("s-title").textContent = empty ? "Album đang được cập nhật" : none ? "Chưa tìm thấy ảnh của bạn" : "Đã tìm xong";
  if (empty) $("s-text").textContent = "Ảnh cưới chưa được đưa lên đầy đủ. Bạn quay lại sau ít phút nhé.";
  else if (none) $("s-text").textContent = "Chưa có ảnh nào khớp với khuôn mặt này. Có thể ảnh của bạn chưa được tải lên, hoặc bạn thử một tấm selfie khác rõ nét hơn.";
  else $("s-text").textContent = "Dưới đây là những khoảnh khắc có bạn.";
  if (q.n_faces > 1) {
    $("s-multi").textContent = `Ảnh bạn gửi có ${q.n_faces} khuôn mặt. Chúng tôi dùng khuôn mặt to và rõ nhất (khung bao quanh mặt). ` +
      "Nếu chưa đúng người, hãy thử ảnh chỉ có một mình bạn.";
    $("s-multi").hidden = false;
  } else $("s-multi").hidden = true;
  $("b-retry").hidden = true; $("s-actions").hidden = false;
  $("finder").classList.toggle("is-done", !none);  // gọn lại (ảnh selfie nhỏ cạnh lời nhắn) để kết quả lộ ra ngay dưới
  $("b-other").textContent = none ? "Thử ảnh khác" : "Chọn ảnh khác";

  $("results").hidden = none && !TUNE;
  if (!none || TUNE) renderResults();
  if (none) $("status").scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  else setTimeout(() => $("finder").scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" }), 250);
}

function clearResults() {
  // xoá sạch kết quả lượt trước (khách sau không thấy ảnh của khách trước)
  data = null; gMain.setActive(false); gMaybe.setActive(false); gMain.items = gMaybe.items = gMain.flat = gMaybe.flat = [];
  $("results").hidden = true; $("g-maybe").hidden = true; $("trunc").hidden = true;
  $("selfie-box").hidden = true;
  closeLb(true);
}

// ---------- kết quả: dùng lại đúng URL ảnh và cách dựng như thư viện ----------
// `b` theo manifest (nguồn đã dùng để dựng các khung của chuỗi) — nếu manifest cũ/mới lệch với kết quả tìm kiếm thì vẫn nhất quán
const fromHit = r => { const mp = photoById.get(r.id); return { id: r.id, tok: mp ? null : (r.t || null), w: r.w || r.width, h: r.h || r.height, v: r.v || null, c: r.c || null, name: r.name, b: (mp ? mp.b : r.b) || null, score: r.score, bbox: r.bbox }; };
// một ảnh = một ô: gộp theo id lấy điểm cao nhất, xếp điểm giảm dần. Chia nhóm SAU khi gộp nên hai nhóm không bao giờ trùng nhau
const hits = list => uniqById(list.map(fromHit), (x, o) => x.score > o.score).sort((a, b) => b.score - a.score);

// Kết quả: mỗi chuỗi liên thanh thành một ô, tính điểm theo khung khớp nhất; ô nằm ở nhóm cao hơn mà khung khớp nhất của nó đạt, không bao giờ ở cả hai nhóm.
// Lightbox của ô gồm MỌI khung của chuỗi (khách có thể ở khung bên cạnh; khung không khớp vẫn lưu được).
function burstTiles(res) {
  if (!COLLAPSE_BURSTS) return res;
  const byB = new Map(), out = [], copy = x => Object.assign({}, x, { frames: null, head: null, at: null });
  for (const h of res) {  // res: điểm giảm dần, mỗi id một lần
    if (!h.b) { const t = copy(h); t.frames = [t]; out.push(t); continue; }
    let t = byB.get(h.b);
    if (t) { t.hits.push(h); continue; }
    const all = burstFrames.get(h.b) || [h];                        // đủ khung từ manifest; không có thì chỉ các khung khớp
    t = copy(all[0]); t.score = h.score; t.hits = [h]; t.frames = all.map(copy);
    const best = t.frames.find(f => f.id === h.id); if (best) { best.score = h.score; best.bbox = h.bbox; }
    t.bbox = (t.frames[0].id === h.id) ? h.bbox : null;
    byB.set(h.b, t); out.push(t);
  }
  for (const t of byB.values()) if (!burstFrames.has(t.b)) t.frames = t.hits.map(copy);
  return out;
}
const countText = g => g.flat.length === g.items.length ? `${g.items.length} ảnh` : `${g.items.length} ảnh · ${g.flat.length} khung hình`;

function renderResults() {
  if (TUNE) return renderTune();
  const res = burstTiles(hits(data.results));
  const main = res.filter(x => x.score >= CONFIDENT_CUT), maybe = res.filter(x => x.score >= MAYBE_CUT && x.score < CONFIDENT_CUT);
  $("tune").hidden = true;
  $("g-main-t").textContent = "Ảnh của bạn";
  $("g-main").hidden = !main.length && !maybe.length;
  const noMain = !main.length;
  $("g-main-empty").hidden = !noMain;
  $("g-main-empty").textContent = "Chưa có ảnh nào thật chắc chắn, nhưng có vài ảnh có thể là bạn ở bên dưới.";
  $("g-main-t").hidden = noMain; $("g-main-n").hidden = noMain;
  const seenIds = new Set();  // ảnh đã ở nhóm chính thì không được xuất hiện lần nữa ở nhóm "có thể"
  gMain.put(COLLAPSE_BURSTS ? stackTiles(main, seenIds) : { items: main, flat: main }); gMain.setActive(!noMain);
  for (const f of gMain.flat) seenIds.add(f.id);
  const maybeLeft = COLLAPSE_BURSTS ? maybe : maybe.filter(x => !seenIds.has(x.id));
  $("g-main-n").textContent = main.length ? countText(gMain) : "";
  gMaybe.put(COLLAPSE_BURSTS ? stackTiles(maybeLeft, seenIds) : { items: maybeLeft, flat: maybeLeft });
  const hasMaybe = gMaybe.items.length > 0;
  $("g-maybe").hidden = !hasMaybe;
  if (hasMaybe) {
    $("maybe-n").textContent = `(${gMaybe.items.length})`;
    setMaybe(noMain);  // không có ảnh chắc chắn thì mở sẵn nhóm này
  }
  const cut = data.truncated && res.length && res[res.length - 1].score >= MAYBE_CUT;
  $("trunc").hidden = !cut;
  if (cut) $("trunc").textContent = `Đang hiển thị ${main.length + maybe.length} ảnh giống nhất.`;
}

function setMaybe(open) {
  $("maybe-toggle").setAttribute("aria-expanded", open);
  $("maybe-body").hidden = !open;
  gMaybe.setActive(open);
}
$("maybe-toggle").onclick = () => setMaybe($("maybe-toggle").getAttribute("aria-expanded") !== "true");

// ---------- chế độ chỉnh ngưỡng ?tune=1 ----------
function renderTune() {
  $("tune").hidden = false;
  $("g-maybe").hidden = true; gMaybe.setActive(false);
  $("g-main-empty").hidden = true; $("g-main-t").hidden = false; $("g-main-n").hidden = false;
  $("g-main").hidden = false;
  $("g-main-t").textContent = "Kết quả (có điểm)";
  const th = parseFloat($("th").value);
  $("thv").textContent = th.toFixed(2);
  const shown = burstTiles(hits(data.results.filter(x => x.score >= th)));
  $("tcount").textContent = `${shown.length} ô / ${data.results.length} ảnh có điểm ≥ 0.15`;
  $("tune-info").textContent = `Hai mức đang dùng cho khách: chắc chắn ≥ ${CONFIDENT_CUT.toFixed(2)} (nhãn xanh), có thể ≥ ${MAYBE_CUT.toFixed(2)} (nhãn vàng). ` +
    `Máy chủ ${data.timing_ms.total.toFixed(0)} ms, tổng ${data.e2e.toFixed(0)} ms.`;
  gMain.put(COLLAPSE_BURSTS ? stackTiles(shown) : { items: shown, flat: shown }); gMain.setActive(true);
  $("g-main-n").textContent = countText(gMain);
  const cut = data.truncated && shown.length && th <= data.results[data.results.length - 1].score;
  $("trunc").hidden = !cut;
  if (cut) $("trunc").textContent = `Có ${data.total_matches} ảnh khớp nhưng chỉ trả về ${data.results.length} ảnh điểm cao nhất.`;
}
$("th").value = String(CONFIDENT_CUT);
$("th").oninput = () => data && renderTune();


// ---------- lightbox ----------
const lb = $("lb"), lbImg = $("lb-img"), stageEl = $("lb-stage");
let cur = { list: [], i: 0, g: null, prefetch: false }, lbOpen = false;
const ZOOM_TAP = 2.5, ZOOM_MAX = 5;
// chia sẻ file chỉ hợp với điện thoại (cảm ứng); máy tính dùng nút Lưu ảnh
const shareApi = matchMedia("(pointer: coarse)").matches && !!navigator.canShare && !!navigator.share;

// prefetch: chỉ ở chế độ "ảnh của bạn" mới tải sẵn ảnh gốc cho nút Chia sẻ (cả album ~1.900 ảnh gốc thì không thể)
function openLb(g, i, opener, prefetch) {
  // "ảnh của bạn": bộ ảnh để lướt là nhóm chắc chắn rồi tới nhóm "có thể", đúng thứ tự hiển thị, mỗi id một lần
  const mine = prefetch && g !== gAll;
  const at = g.items[i].at != null ? g.items[i].at : i;  // ô gộp: mở ở khung đầu của chuỗi
  cur = mine ? { list: gMain.flat.concat(gMaybe.flat), i: at + (g === gMaybe ? gMain.flat.length : 0), g, prefetch } : { list: g.flat, i: at, g, prefetch };
  lbOpen = true; lb.hidden = false; document.body.classList.add("lb-open");
  history.pushState({ lb: 1 }, "");
  show(0);
  $("lb-close").focus({ preventScroll: true });
}
function closeLb(silent) {
  if (!lbOpen) return;
  ptrs.clear(); g = null;
  lbOpen = false; lb.hidden = true; document.body.classList.remove("lb-open");
  lbImg.removeAttribute("src"); lbImg.style.opacity = ""; resetZoom(); cancelNeighbours();
  clearTimeout(shareTimer); for (const e of blobs.values()) if (e.ctl) e.ctl.abort(); blobs.clear(); lastId = null;
  if (history.state && history.state.lb && !silent) { popIgnore++; history.back(); }
  else if (history.state && history.state.lb) history.replaceState(null, "");
  const x = cur.list[cur.i], head = x && (x.head || x);  // đã lướt xa thì cuộn tới ảnh đang xem, focus về đúng ô
  if (cur.g === gAll) { if (gAll.active) gAll.reveal(gAll.items.indexOf(head)); }
  else if (cur.g && mode === "mine" && cur.list.length && cur.list.length === gMain.flat.length + gMaybe.flat.length) {  // (kết quả đã bị dọn thì thôi)
    const inMaybe = cur.i >= gMain.flat.length, grp = inMaybe ? gMaybe : gMain;
    if (inMaybe && $("maybe-toggle").getAttribute("aria-expanded") !== "true") setMaybe(true);
    grp.reveal(grp.items.indexOf(head));
  }
}
const requestClose = () => closeLb(false);
let popIgnore = 0;  // popstate do chính closeLb gây ra bằng history.back()
addEventListener("popstate", () => {
  if (popIgnore > 0) { popIgnore--; return; }
  if (lbOpen) closeLb(true);
  else if (mode === "mine") showAll();  // Back từ kết quả → về thư viện; Back lần nữa mới rời trang
});

let nbImgs = [];
function cancelNeighbours() { for (const im of nbImgs) { im.onload = im.onerror = null; im.src = ""; } nbImgs = []; }  // src rỗng huỷ lượt tải đang bay
function loadFull(x, ok, fail) {  // ảnh 2000 px theo đường lùi CDN → /i/ → /previews
  const steps = chain(x, "preview");
  let k = 0;
  const im = new Image();
  im.onload = () => ok(im.src);
  im.onerror = () => { if (++k < steps.length) im.src = steps[k].src; else fail(); };
  im.src = steps[0].src;
}
function show(dir) {
  const x = cur.list[cur.i], n = cur.list.length;
  resetZoom();
  $("lb-count").textContent = `${nf.format(cur.i + 1)} / ${nf.format(n)}`;
  $("lb-prev").hidden = $("lb-next").hidden = n < 2;
  const hd = x.head, bl = $("lb-burst");  // khung thuộc chuỗi liên thanh: cho biết đang ở khung nào
  bl.hidden = !(hd && hd.frames.length > 1);
  if (!bl.hidden) bl.textContent = `Chụp liên tiếp ${hd.frames.indexOf(x) + 1}/${hd.frames.length}`;
  lbImg.alt = `Ảnh cưới số ${cur.i + 1} trên ${n}`;
  $("lb-save").href = "/originals/" + x.id + tq(x);
  const spin = $("lb-spin");
  lbImg.style.opacity = "";
  lbImg.src = x.ts || chain(x, "thumb")[0].src;  // ảnh nhỏ của ô lưới (đã có trong cache): hiện ngay, rồi thay bằng bản 2000 px
  spin.classList.add("on");
  loadFull(x, src => { if (lbOpen && cur.list[cur.i] === x) { lbImg.src = src; spin.classList.remove("on"); } },  // không gán src sau khi đã đóng
    () => { if (cur.list[cur.i] === x) spin.classList.remove("on"); });  // lỗi của ảnh cũ không được tắt vòng xoay của ảnh mới
  if (dir && !reduced) lbImg.animate([{ opacity: 0, transform: `translateX(${dir * 36}px)` }, { opacity: 1, transform: "none" }], { duration: 280, easing: "cubic-bezier(.2,.8,.2,1)" });
  cancelNeighbours();
  const saveData = navigator.connection && navigator.connection.saveData;  // tiết kiệm dữ liệu: không tải trước ảnh hàng xóm
  if (!saveData) for (const k of [cur.i - 1, cur.i + 1]) if (cur.list[k]) { const im = new Image(); im.src = chain(cur.list[k], "preview")[0].src; nbImgs.push(im); }
  prepareShare(x);
}
function go(d) {
  const n = cur.list.length, j = cur.i + d;
  if (n < 2 || j < 0 || j >= n) { if (n > 1 && !reduced) lbImg.animate([{ transform: `translateX(${-d * 14}px)` }, { transform: "none" }], { duration: 220 }); return; }
  cur.i = j; show(d);
}
$("lb-close").onclick = requestClose;
$("lb-prev").onclick = () => go(-1);
$("lb-next").onclick = () => go(1);
addEventListener("keydown", e => {
  if (!lbOpen) return;
  if (e.key === "Escape") { e.preventDefault(); requestClose(); }
  else if (e.key === "ArrowLeft") go(-1);
  else if (e.key === "ArrowRight") go(1);
  else if (e.key === "Tab") {  // giữ focus trong khung xem
    const f = [...lb.querySelectorAll("button, a[href]")].filter(el => !el.hidden && el.offsetParent !== null);
    const a = document.activeElement, first = f[0], last = f[f.length - 1];
    if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
    else if (!lb.contains(a)) { e.preventDefault(); first.focus(); }
  }
});

// ---------- zoom: chụm hai ngón / chạm đúp (nhấp đúp) / lăn chuột; kéo để di chuyển khi đã phóng ----------
let z = { s: 1, x: 0, y: 0 };
function applyZoom() {
  lbImg.style.transform = z.s === 1 && !z.x && !z.y ? "" : `translate(${z.x}px, ${z.y}px) scale(${z.s})`;
  lb.dataset.zoom = z.s.toFixed(2);
}
function zoomOut() { z = { s: 1, x: 0, y: 0 }; applyZoom(); }  // giữ transition của smooth(): thu về có hoạt ảnh
function resetZoom() { z = { s: 1, x: 0, y: 0 }; lbImg.style.transition = ""; applyZoom(); }
// vùng chứa ảnh (hộp của #lb-stage trừ padding) và phần ảnh thật sự hiển thị trong đó (object-fit: contain), toạ độ màn hình, lúc chưa zoom
function contentRect() {
  const r = stageEl.getBoundingClientRect(), cs = getComputedStyle(stageEl);
  const l = r.left + parseFloat(cs.paddingLeft), t = r.top + parseFloat(cs.paddingTop);
  const bw = r.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight), bh = r.height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  const nw = lbImg.naturalWidth || 1, nh = lbImg.naturalHeight || 1, k = Math.min(bw / nw, bh / nh), w = nw * k, h = nh * k;
  return { l: l + (bw - w) / 2, t: t + (bh - h) / 2, w, h, cx: l + bw / 2, cy: t + bh / 2, bw, bh };
}
function clampZoom() {
  const c = contentRect(), mx = Math.max(0, (c.w * z.s - c.bw) / 2), my = Math.max(0, (c.h * z.s - c.bh) / 2);
  z.x = Math.min(mx, Math.max(-mx, z.x)); z.y = Math.min(my, Math.max(-my, z.y));
}
function zoomAt(s, px, py) {  // giữ điểm (px, py) trên màn hình đứng yên
  const c = contentRect(), ox = px - c.cx, oy = py - c.cy, k = s / z.s;
  z.x = ox - (ox - z.x) * k; z.y = oy - (oy - z.y) * k; z.s = s;
  if (s <= 1.01) z = { s: 1, x: 0, y: 0 }; else clampZoom();
  applyZoom();
}
function smooth(fn) {
  if (!reduced) { lbImg.style.transition = "transform .25s cubic-bezier(.2,.8,.2,1)"; setTimeout(() => { lbImg.style.transition = ""; }, 280); }
  fn();
}

const ptrs = new Map();
let g = null, lastTap = { t: 0, x: 0, y: 0 };
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y), mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

function tap(e) {
  const now = performance.now(), c = contentRect();
  const inside = e.clientX >= c.l && e.clientX <= c.l + c.w && e.clientY >= c.t && e.clientY <= c.t + c.h;
  const dbl = now - lastTap.t < 320 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 40;
  lastTap = dbl ? { t: 0, x: 0, y: 0 } : { t: now, x: e.clientX, y: e.clientY };
  if (dbl) { smooth(() => (z.s > 1 ? zoomOut() : zoomAt(ZOOM_TAP, e.clientX, e.clientY))); return; }
  if (!inside && z.s === 1) requestClose();  // chạm vào vùng tối quanh ảnh: đóng (không đóng khi đang phóng)
}

stageEl.addEventListener("pointerdown", e => {
  if (e.pointerType === "mouse" && e.button !== 0) return;
  stageEl.setPointerCapture(e.pointerId);
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    lbImg.style.opacity = ""; applyZoom();  // bỏ dở cú vuốt đang kéo
    g = { mode: "pinch", d0: dist(a, b), s0: z.s, x0: z.x, y0: z.y, m0: mid(a, b) };
  } else if (ptrs.size === 1) {
    g = { mode: "one", x: e.clientX, y: e.clientY, t: performance.now(), axis: null, dx: 0, dy: 0, zx: z.x, zy: z.y, moved: false };
  }
});
stageEl.addEventListener("pointermove", e => {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (!g) return;
  if (g.mode === "pinch") {
    if (ptrs.size < 2) return;
    const [a, b] = [...ptrs.values()], m = mid(a, b), c = contentRect();
    const s = Math.min(ZOOM_MAX, Math.max(1, g.s0 * dist(a, b) / g.d0));
    const qx = (g.m0.x - c.cx - g.x0) / g.s0, qy = (g.m0.y - c.cy - g.y0) / g.s0;  // điểm ảnh nằm dưới trung điểm lúc bắt đầu
    z.s = s; z.x = (m.x - c.cx) - s * qx; z.y = (m.y - c.cy) - s * qy; clampZoom(); applyZoom();
    return;
  }
  const dx = e.clientX - g.x, dy = e.clientY - g.y; g.dx = dx; g.dy = dy;
  if (z.s > 1) {  // đang phóng: chỉ di chuyển ảnh, không đổi ảnh / không đóng
    if (Math.hypot(dx, dy) > 4) g.moved = true;
    z.x = g.zx + dx; z.y = g.zy + dy; clampZoom(); applyZoom();
    return;
  }
  if (!g.axis && Math.hypot(dx, dy) > 8) g.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
  if (!g.axis) return;
  g.moved = true;
  if (g.axis === "x") lbImg.style.transform = `translateX(${dx}px)`;
  else {
    const d = Math.max(dy, 0);
    lbImg.style.transform = `translateY(${d}px) scale(${1 - Math.min(d / 900, .15)})`;
    lbImg.style.opacity = String(1 - Math.min(d / 500, .6));
  }
});
function endPtr(e) {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.delete(e.pointerId);
  if (g && g.mode === "pinch") {
    if (ptrs.size < 2) {
      if (z.s < 1.05) resetZoom();
      g = null;
      if (ptrs.size === 1) { const [p] = [...ptrs.values()]; g = { mode: "one", x: p.x, y: p.y, t: performance.now(), axis: null, dx: 0, dy: 0, zx: z.x, zy: z.y, moved: true, noTap: true }; }  // sau chụm, nhấc ngón còn lại không phải là chạm
    }
    return;
  }
  if (!g) return;
  const p = g; g = null;
  const cancel = e.type === "pointercancel";  // hệ thống huỷ cử chỉ (cuộc gọi, cử chỉ hệ điều hành…): chỉ bật về, không đóng, không chuyển ảnh
  lbImg.style.opacity = "";
  if (z.s > 1) { if (!p.moved && !p.noTap && e.type === "pointerup") tap(e); return; }
  if (!p.axis) { if (!p.noTap && e.type === "pointerup" && Math.hypot(p.dx, p.dy) < 10 && performance.now() - p.t < 500) tap(e); return; }
  const v = Math.abs(p.dx) / Math.max(performance.now() - p.t, 1);
  if (p.axis === "y" && p.dy > 90 && !cancel) { requestClose(); }
  else if (p.axis === "x" && !cancel && (Math.abs(p.dx) > 60 || v > .5)) { lbImg.style.transform = ""; go(p.dx < 0 ? 1 : -1); }
  else {
    if (!reduced) lbImg.animate([{ transform: lbImg.style.transform, opacity: lbImg.style.opacity || 1 }, { transform: "none", opacity: 1 }], { duration: 220, easing: "ease-out" });
    lbImg.style.transform = "";
  }
}
stageEl.addEventListener("pointerup", endPtr);
stageEl.addEventListener("pointercancel", endPtr);
stageEl.addEventListener("wheel", e => {  // chụm trên bàn di chuột (ctrlKey) mới phóng/thu; hai ngón cuộn thường không phóng
  if (!e.ctrlKey) return;
  e.preventDefault();
  const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);  // dòng/trang (Firefox) quy về px
  zoomAt(Math.min(ZOOM_MAX, Math.max(1, z.s * Math.exp(-dy * 0.002))), e.clientX, e.clientY);
}, { passive: false });

// ---------- lưu / chia sẻ ----------
// Lưu ảnh: link tải ảnh gốc (Content-Disposition: attachment).
// Chia sẻ (điện thoại): navigator.share phải được gọi ngay trong cú bấm — nên tải sẵn ảnh gốc của ảnh đang xem
// (chỉ ảnh hiện tại + ảnh liền trước; dừng tải khi rời ảnh) và chỉ gọi share khi đã có file.
let toastT = 0;
function toast(t) { const el = $("lb-toast"); el.textContent = t; el.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove("on"), 2600); }
$("lb-save").addEventListener("click", () => toast("Đang tải ảnh gốc…"));

const blobs = new Map();  // id -> { state: loading|ready|error, pct, ctl, file, denied }
const SHARE_PREFETCH_MS = 1500;
let shareTimer = 0, lastId = null, sharing = false;

function prepareShare(x) {
  clearTimeout(shareTimer);
  if (!shareApi) { $("lb-share").hidden = true; return; }
  const prev = lastId;
  for (const [id, e] of blobs) if (id !== x.id && id !== prev) { if (e.ctl) e.ctl.abort(); blobs.delete(id); }
  const left = prev && prev !== x.id && blobs.get(prev);
  if (left && left.state === "loading") { left.ctl.abort(); blobs.delete(prev); }
  lastId = x.id;
  paintShare();
  const saveData = navigator.connection && navigator.connection.saveData;  // chế độ tiết kiệm dữ liệu: chỉ tải khi bấm. Cả album (cur.prefetch = false): luôn chỉ tải khi bấm
  if (cur.prefetch && !blobs.has(x.id) && !saveData) shareTimer = setTimeout(() => fetchBlob(x), SHARE_PREFETCH_MS);  // lướt nhanh qua nhiều ảnh thì không tải ảnh nào
}
async function fetchBlob(x) {
  const e = { state: "loading", pct: 0, ctl: new AbortController(), file: null, denied: false };
  blobs.set(x.id, e); paintShare();
  try {
    const r = await fetch("/originals/" + x.id + tq(x), { signal: e.ctl.signal });
    if (!r.ok) throw new Error("http " + r.status);
    const total = +r.headers.get("content-length") || 0, rd = r.body && r.body.getReader(), parts = [];
    if (rd) {
      for (let got = 0; ;) {
        const { done, value } = await rd.read();
        if (done) break;
        parts.push(value); got += value.length;
        if (total) { e.pct = Math.round(got / total * 100); if (cur.list[cur.i] === x) paintShare(); }
      }
    } else parts.push(new Uint8Array(await r.arrayBuffer()));
    const type = (r.headers.get("content-type") || "image/jpeg").split(";")[0];
    e.file = new File(parts, x.name, { type });
    if (!navigator.canShare({ files: [e.file] })) throw new Error("không chia sẻ được file này");
    e.state = "ready";
  } catch (err) {
    if (err.name === "AbortError") return;
    e.state = "error";
  }
  if (lbOpen && cur.list[cur.i] === x) paintShare();
}
function paintShare() {
  const btn = $("lb-share");
  if (!shareApi) { btn.hidden = true; return; }
  const x = cur.list[cur.i], e = x && blobs.get(x.id);
  btn.hidden = !!e && e.state === "error";  // không chia sẻ được thì chỉ còn nút Lưu ảnh
  const busy = !!e && e.state === "loading";
  btn.toggleAttribute("aria-busy", busy);
  $("lb-share-t").textContent = busy ? (e && e.pct ? `Đang chuẩn bị ${e.pct}%` : "Đang chuẩn bị…") : e && e.denied ? "Bấm lần nữa để chia sẻ" : "Chia sẻ";
}
$("lb-share").onclick = () => {
  const x = cur.list[cur.i], e = blobs.get(x.id);
  if (!e || e.state !== "ready") {  // chưa tải sẵn (đợi trước khi tải / chế độ tiết kiệm dữ liệu): tải ngay khi bấm, có tiến độ trên nút
    if (!e) { clearTimeout(shareTimer); fetchBlob(x); }
    toast("Đang chuẩn bị ảnh, xong bạn bấm Chia sẻ lần nữa nhé.");
    return;
  }
  if (sharing) return;  // chống bấm đúp
  sharing = true; e.denied = false; paintShare();
  let p;
  try { p = navigator.share({ files: [e.file] }); } catch (err) { p = Promise.reject(err); }  // gọi đồng bộ trong cú bấm
  p.then(() => {}, err => {
    if (err && err.name === "AbortError") return;  // người dùng tự đóng bảng chia sẻ
    if (err && err.name === "NotAllowedError") { e.denied = true; paintShare(); return; }  // giữ file, bấm lần nữa là chia sẻ ngay
    toast("Không chia sẻ được, bạn hãy dùng nút Lưu ảnh nhé.");
  }).finally(() => { sharing = false; });
};
