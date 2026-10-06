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

let file = null, selfieUrl = null, runId = 0, ctl = null;
let data = null;

// ---------- chọn ảnh ----------
for (const id of ["in-cam", "in-lib", "in-lib-touch"]) {
  $(id).onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) choose(f); };
}
const drop = $("drop");
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) choose(f); });
// thả ảnh ra ngoài ô cũng nhận (tránh trình duyệt mở ảnh ra thay trang)
addEventListener("dragover", e => e.preventDefault());
addEventListener("drop", e => e.preventDefault());
$("b-other").onclick = reset;
$("b-retry").onclick = () => file && search();

function reset() {
  runId++; if (ctl) ctl.abort();
  clearResults();
  if (selfieUrl) URL.revokeObjectURL(selfieUrl);
  selfieUrl = null; file = null;
  $("st-work").hidden = true; $("st-idle").hidden = false;
  $("stage").scrollIntoView({ block: "nearest" });
}

function choose(f) {
  if (!f.type.startsWith("image/") && f.type !== "") { runId++; if (ctl) ctl.abort(); clearResults(); showError("Đây không phải ảnh", "Hãy chọn một tấm ảnh selfie (JPG hoặc PNG).", [], false, true); return; }
  clearResults();
  if (selfieUrl) URL.revokeObjectURL(selfieUrl);
  file = f; selfieUrl = URL.createObjectURL(f);
  const img = $("selfie");
  $("selfie-box").hidden = true;
  $("selfie-fig").hidden = false;
  img.onerror = () => { $("selfie-fig").hidden = true; };  // trình duyệt không hiển thị được (vd. HEIC): vẫn gửi để máy chủ thử
  img.src = selfieUrl;
  $("st-idle").hidden = true; $("st-work").hidden = false;
  search();
}

// ---------- gọi API ----------
async function prepare(f) {
  // giữ nguyên ảnh nếu vừa phải; quá lớn thì vẽ lại nhỏ hơn (trình duyệt tự áp EXIF khi vẽ <img>)
  try {
    const url = URL.createObjectURL(f), im = new Image();
    im.src = url; await im.decode();
    const m = Math.max(im.naturalWidth, im.naturalHeight);
    if (m <= SEND_RESIZE_ABOVE) { URL.revokeObjectURL(url); return f; }
    const s = SEND_MAX_EDGE / m, c = document.createElement("canvas");
    c.width = Math.round(im.naturalWidth * s); c.height = Math.round(im.naturalHeight * s);
    c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
    URL.revokeObjectURL(url);
    const b = await new Promise(r => c.toBlob(r, "image/jpeg", 0.92));
    return b || f;
  } catch (e) { return f; }
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
    const r = await fetch("/api/search", { method: "POST", body, headers: { "Content-Type": "application/octet-stream" }, signal: ctl.signal });
    if (my !== runId) return;
    if (r.status === 422) {
      const e = await r.json().catch(() => ({}));
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
  $("selfie-box").hidden = true;
  $("s-loading").hidden = false; $("s-msg").hidden = true; $("s-actions").hidden = true;
  $("s-multi").hidden = true;
}
function showError(title, text, tips, canRetry, noPreview) {
  if (noPreview) { $("st-idle").hidden = true; $("st-work").hidden = false; $("selfie-fig").hidden = true; }
  $("selfie-fig").classList.remove("is-loading");
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
  const empty = data.indexed_photos === 0;
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
  $("b-other").textContent = "Chọn ảnh khác";
  if (none) $("b-other").textContent = "Thử ảnh khác";

  $("results").hidden = none && !TUNE;
  if (!none || TUNE) renderResults();
  if (none) $("status").scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  $("results").scrollIntoView && !none && setTimeout(() => $("results").scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" }), 250);
}

function clearResults() {
  // xoá sạch kết quả lượt trước (khách sau không thấy ảnh của khách trước)
  data = null; lists = { main: [], maybe: [] };
  $("grid-main").textContent = ""; $("grid-maybe").textContent = "";
  $("results").hidden = true; $("g-maybe").hidden = true; $("trunc").hidden = true;
  $("selfie-box").hidden = true;
  closeLb(true);
}

// ---------- lưới ảnh ----------
let lists = { main: [], maybe: [] };

function tile(x, list, i, kind) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "tile";
  b.setAttribute("aria-label", `Mở ảnh ${i + 1} trên ${list.length}`);
  const inn = document.createElement("span"); inn.className = "tile-in";
  inn.style.aspectRatio = `${x.width} / ${x.height}`;
  const img = document.createElement("img");
  img.src = "/" + x.thumb; img.loading = "lazy"; img.decoding = "async"; img.alt = `Ảnh cưới có bạn, số ${i + 1}`;
  const on = () => img.classList.add("on");
  img.onload = on; img.onerror = () => inn.classList.add("bad"); if (img.complete && img.naturalWidth) on();
  inn.append(img);
  if (TUNE) {
    const box = document.createElement("i"); box.className = "box"; placeBox(box, x.bbox, x.width, x.height); inn.append(box);
    const sc = document.createElement("span"); sc.className = "score is-" + kind; sc.textContent = `${x.score.toFixed(3)} · ${x.name}`; b.append(inn, sc);
  } else b.append(inn);
  b.onclick = () => openLb(list, i, b);
  return b;
}

function fill(grid, list, kind) {
  const frag = document.createDocumentFragment();
  list.forEach((x, i) => frag.append(tile(x, list, i, kind)));
  grid.textContent = ""; grid.append(frag); grid.dataset.n = list.length;
}

function renderResults() {
  const res = data.results;
  if (TUNE) return renderTune();
  lists.main = res.filter(x => x.score >= CONFIDENT_CUT);
  lists.maybe = res.filter(x => x.score >= MAYBE_CUT && x.score < CONFIDENT_CUT);
  $("tune").hidden = true;
  $("g-main-t").textContent = "Ảnh của bạn";
  $("g-main-n").textContent = lists.main.length ? `${lists.main.length} ảnh` : "";
  $("g-main").hidden = !lists.main.length && !lists.maybe.length;
  const noMain = !lists.main.length;
  $("g-main-empty").hidden = !noMain;
  $("g-main-empty").textContent = "Chưa có ảnh nào thật chắc chắn, nhưng có vài ảnh có thể là bạn ở bên dưới.";
  $("g-main-t").hidden = noMain; $("g-main-n").hidden = noMain;
  fill($("grid-main"), lists.main, "main");
  const hasMaybe = lists.maybe.length > 0;
  $("g-maybe").hidden = !hasMaybe;
  if (hasMaybe) {
    $("maybe-n").textContent = `(${lists.maybe.length})`;
    fill($("grid-maybe"), lists.maybe, "maybe");
    setMaybe(noMain);  // không có ảnh chắc chắn thì mở sẵn nhóm này
  }
  const cut = data.truncated && res.length && res[res.length - 1].score >= MAYBE_CUT;
  $("trunc").hidden = !cut;
  if (cut) $("trunc").textContent = `Đang hiển thị ${lists.main.length + lists.maybe.length} ảnh giống nhất.`;
}

function setMaybe(open) {
  $("maybe-toggle").setAttribute("aria-expanded", open);
  $("maybe-body").hidden = !open;
}
$("maybe-toggle").onclick = () => setMaybe($("maybe-toggle").getAttribute("aria-expanded") !== "true");

// ---------- chế độ chỉnh ngưỡng ?tune=1 ----------
function renderTune() {
  $("tune").hidden = false;
  $("g-maybe").hidden = true;
  $("g-main-empty").hidden = true; $("g-main-t").hidden = false; $("g-main-n").hidden = false;
  $("g-main").hidden = false;
  $("g-main-t").textContent = "Kết quả (có điểm)";
  const th = parseFloat($("th").value);
  $("thv").textContent = th.toFixed(2);
  const shown = data.results.filter(x => x.score >= th);
  lists.main = shown;
  $("count").textContent = `${shown.length} ảnh / ${data.results.length} ảnh có điểm ≥ 0.15`;
  $("g-main-n").textContent = `${shown.length} ảnh`;
  $("tune-info").textContent = `Hai mức đang dùng cho khách: chắc chắn ≥ ${CONFIDENT_CUT.toFixed(2)} (nhãn xanh), có thể ≥ ${MAYBE_CUT.toFixed(2)} (nhãn vàng). ` +
    `Chỉ mục ${data.indexed_photos} ảnh, ${data.indexed_faces} mặt. Máy chủ ${data.timing_ms.total.toFixed(0)} ms, tổng ${data.e2e.toFixed(0)} ms.`;
  const kinds = shown.map(x => x.score >= CONFIDENT_CUT ? "main" : "maybe");
  const frag = document.createDocumentFragment();
  shown.forEach((x, i) => frag.append(tile(x, shown, i, kinds[i])));
  $("grid-main").textContent = ""; $("grid-main").append(frag); $("grid-main").dataset.n = shown.length;
  const cut = data.truncated && shown.length && th <= data.results[data.results.length - 1].score;
  $("trunc").hidden = !cut;
  if (cut) $("trunc").textContent = `Có ${data.total_matches} ảnh khớp nhưng chỉ trả về ${data.results.length} ảnh điểm cao nhất.`;
}
$("th").value = String(CONFIDENT_CUT);
$("th").oninput = () => data && renderTune();


// ---------- lightbox ----------
const lb = $("lb"), lbImg = $("lb-img"), stageEl = $("lb-stage");
let cur = { list: [], i: 0, opener: null }, lbOpen = false;
const ZOOM_TAP = 2.5, ZOOM_MAX = 5;
// chia sẻ file chỉ hợp với điện thoại (cảm ứng); máy tính dùng nút Lưu ảnh
const shareApi = matchMedia("(pointer: coarse)").matches && !!navigator.canShare && !!navigator.share;

function openLb(list, i, opener) {
  cur = { list, i, opener };
  lbOpen = true; lb.hidden = false; document.body.classList.add("lb-open");
  history.pushState({ lb: 1 }, "");
  show(0);
  $("lb-close").focus({ preventScroll: true });
}
function closeLb(silent) {
  if (!lbOpen) return;
  ptrs.clear(); g = null;
  lbOpen = false; lb.hidden = true; document.body.classList.remove("lb-open");
  lbImg.removeAttribute("src"); lbImg.style.opacity = ""; resetZoom();
  clearTimeout(shareTimer); for (const e of blobs.values()) if (e.ctl) e.ctl.abort(); blobs.clear(); lastId = null;
  if (history.state && history.state.lb && !silent) history.back();
  else if (history.state && history.state.lb) history.replaceState(null, "");
  if (cur.opener && cur.opener.isConnected) cur.opener.focus({ preventScroll: true });
}
const requestClose = () => closeLb(false);
addEventListener("popstate", () => { if (lbOpen) closeLb(true); });

function show(dir) {
  const x = cur.list[cur.i], n = cur.list.length;
  resetZoom();
  $("lb-count").textContent = `${cur.i + 1} / ${n}`;
  $("lb-prev").hidden = $("lb-next").hidden = n < 2;
  lbImg.alt = `Ảnh cưới có bạn, số ${cur.i + 1} trên ${n}`;
  $("lb-save").href = "/originals/" + x.id;
  const spin = $("lb-spin");
  lbImg.style.opacity = "";
  lbImg.src = "/" + x.thumb;  // thumb đã có sẵn trong cache của lưới: hiện ngay, rồi thay bằng bản 2000 px
  spin.classList.add("on");
  const full = new Image();
  full.onload = () => { if (cur.list[cur.i] === x) { lbImg.src = full.src; spin.classList.remove("on"); } };
  full.onerror = () => { if (cur.list[cur.i] === x) spin.classList.remove("on"); };  // lỗi của ảnh cũ không được tắt vòng xoay của ảnh mới
  full.src = "/" + x.preview;
  if (dir && !reduced) lbImg.animate([{ opacity: 0, transform: `translateX(${dir * 36}px)` }, { opacity: 1, transform: "none" }], { duration: 280, easing: "cubic-bezier(.2,.8,.2,1)" });
  for (const k of [cur.i - 1, cur.i + 1]) if (cur.list[k]) new Image().src = "/" + cur.list[k].preview;
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
  if (dbl) { smooth(() => (z.s > 1 ? resetZoom() : zoomAt(ZOOM_TAP, e.clientX, e.clientY))); return; }
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
  lbImg.style.opacity = "";
  if (z.s > 1) { if (!p.moved && !p.noTap && e.type === "pointerup") tap(e); return; }
  if (!p.axis) { if (!p.noTap && e.type === "pointerup" && Math.hypot(p.dx, p.dy) < 10 && performance.now() - p.t < 500) tap(e); return; }
  const v = Math.abs(p.dx) / Math.max(performance.now() - p.t, 1);
  if (p.axis === "y" && p.dy > 90) { requestClose(); }
  else if (p.axis === "x" && (Math.abs(p.dx) > 60 || v > .5)) { lbImg.style.transform = ""; go(p.dx < 0 ? 1 : -1); }
  else {
    if (!reduced) lbImg.animate([{ transform: lbImg.style.transform, opacity: lbImg.style.opacity || 1 }, { transform: "none", opacity: 1 }], { duration: 220, easing: "ease-out" });
    lbImg.style.transform = "";
  }
}
stageEl.addEventListener("pointerup", endPtr);
stageEl.addEventListener("pointercancel", endPtr);
stageEl.addEventListener("wheel", e => {  // máy tính: lăn chuột để phóng/thu
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
  const saveData = navigator.connection && navigator.connection.saveData;  // chế độ tiết kiệm dữ liệu: chỉ tải khi bấm
  if (!blobs.has(x.id) && !saveData) shareTimer = setTimeout(() => fetchBlob(x), SHARE_PREFETCH_MS);  // lướt nhanh qua nhiều ảnh thì không tải ảnh nào
}
async function fetchBlob(x) {
  const e = { state: "loading", pct: 0, ctl: new AbortController(), file: null, denied: false };
  blobs.set(x.id, e); paintShare();
  try {
    const r = await fetch("/originals/" + x.id, { signal: e.ctl.signal });
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
