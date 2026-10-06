"use strict";
const $ = id => document.getElementById(id);
const fmt = n => n >= 1073741824 ? (n / 1073741824).toFixed(2) + " GB" : n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : (n / 1024).toFixed(0) + " KB";
const dur = s => s >= 60 ? Math.floor(s / 60) + " phút " + Math.round(s % 60) + " giây" : s.toFixed(0) + " giây";
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let maxMB = 60;
async function refreshStats() {
  try {
    const d = await (await fetch("/api/stats")).json();
    maxMB = d.max_upload_mb;
    $("drop-hint").textContent = `JPG, PNG, WEBP · tối đa ${maxMB} MB mỗi ảnh`;
    $("n-orig").textContent = d.originals; $("n-done").textContent = d.photos;
    $("n-pend").textContent = d.pending; $("n-faces").textContent = d.faces;
    const used = 1 - d.disk_free / d.disk_total, low = d.disk_free / d.disk_total < 0.1;
    $("disk-bar").style.width = Math.max(used * 100, 1) + "%";
    $("disk-bar").parentElement.classList.toggle("low", low);
    $("disk").classList.toggle("low", low);
    $("disk").textContent = `Ổ đĩa: còn trống ${(d.disk_free / 2 ** 30).toFixed(1)} GB / ${(d.disk_total / 2 ** 30).toFixed(1)} GB` +
      ` · ảnh đã lưu ${fmt(d.stored_bytes)} / hạn mức ${fmt(d.max_total_bytes)}` + (low ? " — sắp hết chỗ" : "");
  } catch (e) {}
}

// ---- tải lên: mỗi file một request, 3 request song song, có tiến độ byte ----
let queue = [], active = 0, total = 0, sent = 0, doneN = 0, failN = 0, dupN = 0;
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
  const c = $("upchips"); c.textContent = "";
  if (doneN) c.append(el("span", "chip ok", `${doneN} ảnh xong`));
  if (dupN) c.append(el("span", "chip dup", `${dupN} trùng`));
  if (failN) c.append(el("span", "chip err", `${failN} lỗi`));
}
function upload(it, tries = 0) {
  const st = it.st, x = new XMLHttpRequest();
  const retry = () => {  // máy chủ bận (503) hoặc đứt kết nối: thử lại tối đa 4 lần, chậm dần
    if (tries >= 4) return false;
    sent -= it.loaded; it.loaded = 0; it.fill.style.width = "0"; st.textContent = "chờ thử lại…";
    setTimeout(() => upload(it, tries + 1), 2000 * (tries + 1)); return true;
  };
  x.open("POST", "/api/upload?name=" + encodeURIComponent(it.f.name));
  x.setRequestHeader("Content-Type", "application/octet-stream");
  x.upload.onprogress = e => { sent += e.loaded - it.loaded; it.loaded = e.loaded; const p = Math.round(e.loaded / e.total * 100); st.textContent = p + "%"; it.fill.style.width = p + "%"; pump(); };
  const fin = () => { sent += it.f.size - it.loaded; it.loaded = it.f.size; it.li.classList.add("done"); active--; pump(); refreshStats(); };
  x.onload = () => {
    let d = {}; try { d = JSON.parse(x.responseText); } catch (e) {}
    if (x.status === 200 && d.status === "ok") { st.textContent = "✓ " + fmt(it.f.size) + (d.renamed ? " (đổi tên: " + d.name + ")" : ""); st.className = "f-st ok"; doneN++; }
    else if (x.status === 200) { st.textContent = "trùng với " + d.name; st.className = "f-st dup"; dupN++; }
    else if (x.status === 503 && retry()) return;
    else { st.textContent = "✗ " + (d.detail || ("lỗi " + x.status)); st.className = "f-st err"; failN++; }
    fin();
  };
  x.onerror = () => { if (retry()) return; st.textContent = "✗ mất kết nối"; st.className = "f-st err"; failN++; fin(); };
  x.send(it.f);
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
async function poll() { try { showStatus(await (await fetch("/api/process")).json()); } catch (e) {} }
$("proc").onclick = async () => {
  const r = await fetch("/api/process", { method: "POST" });
  const d = await r.json();
  showStatus(d);
  if (r.status === 409) $("pmsg").textContent = d.message;
  if (r.status === 200 && !timer) timer = setInterval(poll, 1000);
};
refreshStats(); poll();
