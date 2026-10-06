"""Chỉ dùng khi phát triển: đo thư viện ảnh trên "điện thoại + 3G nhanh" với máy chủ giả (dev/mock_gallery.py), cả Chromium và WebKit.

  python dev/perf_gallery.py --browser chromium|webkit [--runs 3] [--out /tmp/album-e/perf-chromium.json]

Mạng được giả lập Ở PHÍA MÁY CHỦ GIẢ (cùng một cách cho cả hai trình duyệt, WebKit không có CDP): băng thông chung 1.6 Mbps cho cả cổng
ứng dụng lẫn "CDN", 150 ms RTT cho mỗi request, kết nối TCP mới tốn 2 RTT (TCP+TLS — nên preconnect có tác dụng thật).
CPU 4× chậm đi chỉ có ở Chromium (CDP). Màn hình 390×844, DPR 3.

Mỗi cấu hình (CDN tắt / bật / bật + <link preconnect> trong HTML) chạy --runs lần với context mới (cache lạnh):
  A. mạng chậm: thời gian từ điều hướng tới khi mọi ô trong màn đầu đã có ảnh và đã vẽ; byte thumbnail tới lúc đó; byte sau khi cuộn 10 màn hình
  B. mạng nhanh + CPU 4×: lướt qua cả ~1.900 ảnh — số nút DOM, số ảnh đang giữ src, JS heap (Chromium) tối đa và sau GC
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--browser", default="chromium", choices=["chromium", "webkit"])
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--port", type=int, default=8824)
ap.add_argument("--kbps", type=int, default=1600)
ap.add_argument("--rtt", type=int, default=150)
ap.add_argument("--out")
a = ap.parse_args()
APP, CDN = a.port, a.port + 1
THUMB = re.compile(r"/i/[0-9a-f]{16}-[0-9a-f]{8}-(240|480)\.webp|/thumbs/")

PROBE = """
window.__fs = null;
new PerformanceObserver(l => { for (const e of l.getEntries()) if (e.name === 'first-contentful-paint') window.__fcp = e.startTime; }).observe({ type: 'paint', buffered: true });
(function loop() {
  if (window.__fs !== null) return;
  requestAnimationFrame(() => {
    const vh = innerHeight; let n = 0, ok = true;
    for (const b of document.querySelectorAll('#vg-all .tile')) {
      const r = b.getBoundingClientRect();
      if (r.bottom > 0 && r.top < vh) { n++; const i = b.firstChild; if (!(i.complete && i.naturalWidth > 0)) { ok = false; break; } }
    }
    if (n > 0 && ok) requestAnimationFrame(() => { window.__fs = performance.now(); window.__fsN = n; window.__fsNames = performance.getEntriesByType('resource').filter(e => e.responseEnd > e.startTime && e.responseEnd <= performance.now()).map(e => e.name); /* chỉ request đã tải xong (WebKit liệt kê cả request đang bay) */ });
    else loop();
  });
})();
"""
SETTLE = """() => { const vh = innerHeight; let n = 0;
  for (const b of document.querySelectorAll('#vg-all .tile')) { const r = b.getBoundingClientRect();
    if (r.bottom > 0 && r.top < vh) { n++; const i = b.firstChild; if (!(i.complete && i.naturalWidth > 0)) return false; } }
  return n > 0; }"""


def start_mock(extra):
    cmd = [sys.executable, os.path.join(HERE, "mock_gallery.py"), "--port", str(APP), "--cdn-port", str(CDN), "--kbps", str(a.kbps),
           "--rtt-ms", str(a.rtt), "--connect-rtts", "2"] + extra
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    line = pr.stdout.readline()  # chặn tới khi máy chủ giả báo sẵn sàng
    if not line.startswith("mock:"):
        pr.kill(); sys.exit("mock không chạy: " + line)
    return pr


def shape(b, kbps, rtt):
    import urllib.request
    urllib.request.urlopen(f"http://localhost:{APP}/__mock/shape?kbps={kbps}&rtt={rtt}").read()


def size_of(req):
    try:
        return req.sizes()["responseBodySize"]
    except Exception:
        return 0


def run_a(p, url):
    b = getattr(p, a.browser).launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
    ctx.add_init_script(PROBE)
    pg = ctx.new_page()
    fin, failed = [], []
    pg.on("requestfinished", lambda r: fin.append(r))
    pg.on("requestfailed", lambda r: failed.append(r.url))
    if a.browser == "chromium":
        ctx.new_cdp_session(pg).send("Emulation.setCPUThrottlingRate", {"rate": 4})
    pg.goto(url, wait_until="commit")
    pg.wait_for_function("window.__fs !== null", timeout=180000, polling=100)
    d = pg.evaluate("({ fs: window.__fs, fcp: window.__fcp || null, n: window.__fsN, names: window.__fsNames, man: (performance.getEntriesByName('gallery-manifest')[0] || {}).startTime || null, rend: (performance.getEntriesByName('gallery-rendered')[0] || {}).startTime || null })")
    marks_done = len(fin)
    t10 = 0
    for _ in range(10):
        pg.evaluate("scrollBy(0, innerHeight)")
        pg.wait_for_function(SETTLE, timeout=180000, polling=100)
    t10 = pg.evaluate("performance.now()")
    pg.wait_for_timeout(1500)  # chờ nốt phần nạp trước
    sizes = {}
    for r in fin:
        sizes.setdefault(r.url, []).append(size_of(r))
    fs_bytes = sum(sizes.get(u, [0])[0] for u in set(d["names"]) if THUMB.search(u))
    all_bytes = sum(sum(v) for u, v in sizes.items() if THUMB.search(u))
    static_bytes = sum(sum(v) for u, v in sizes.items() if not THUMB.search(u) and "/i/" not in u)
    pre = sum(1 for u in d["names"] if THUMB.search(u))
    out = dict(first_screen_ms=round(d["fs"]), fcp_ms=round(d["fcp"]) if d["fcp"] else None, manifest_ms=round(d["man"]) if d["man"] else None,
               rendered_ms=round(d["rend"]) if d["rend"] else None, tiles_in_first_screen=d["n"], first_screen_thumb_kb=round(fs_bytes / 1024, 1),
               first_screen_thumbs=pre, ten_screens_thumb_kb=round(all_bytes / 1024, 1), ten_screens_thumbs=sum(len(v) for u, v in sizes.items() if THUMB.search(u)),
               ten_screens_done_ms=round(t10), cancelled_thumbs=sum(1 for u in failed if THUMB.search(u)), page_static_kb=round(static_bytes / 1024, 1))
    ctx.close(); b.close()
    return out


def run_b(p, url):
    b = getattr(p, a.browser).launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    cdp = None
    if a.browser == "chromium":
        cdp = ctx.new_cdp_session(pg)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        cdp.send("Performance.enable")
    heap = lambda: next((m["value"] for m in cdp.send("Performance.getMetrics")["metrics"] if m["name"] == "JSHeapUsedSize"), 0) / 1048576 if cdp else None
    pg.goto(url)
    pg.wait_for_selector("#vg-all .tile img.on", timeout=60000)
    n_all = int(pg.inner_text("#count").split()[0].replace(".", ""))
    h0 = heap()
    total_h = pg.evaluate("document.documentElement.scrollHeight")
    step = pg.evaluate("innerHeight * 0.8")
    peak_dom = peak_img = 0
    peak_heap = 0
    y = 0
    seen = set()
    while True:
        pg.evaluate("y => scrollTo(0, y)", y)
        pg.wait_for_timeout(30)
        r = pg.evaluate("({ dom: document.getElementsByTagName('*').length, img: document.querySelectorAll('#vg-all img[src]').length, vis: [...document.querySelectorAll('#vg-all .tile')].filter(b => { const q = b.getBoundingClientRect(); return q.bottom > 0 && q.top < innerHeight; }).map(b => +b.dataset.i) })")
        peak_dom, peak_img = max(peak_dom, r["dom"]), max(peak_img, r["img"]); seen.update(r["vis"])
        if cdp:
            peak_heap = max(peak_heap, heap())
        if y >= total_h - pg.evaluate("innerHeight"):
            break
        y += step
    pg.wait_for_function(SETTLE, timeout=60000, polling=100)
    after_gc = None
    if cdp:
        cdp.send("HeapProfiler.collectGarbage")
        after_gc = round(heap(), 1)
    out = dict(photos=n_all, scrolled_past=len(seen), peak_dom_nodes=peak_dom, peak_imgs_with_src=peak_img, doc_height_px=total_h,
               heap_mb_start=round(h0, 1) if h0 else None, heap_mb_peak=round(peak_heap, 1) if cdp else None, heap_mb_after_gc=after_gc)
    ctx.close(); b.close()
    return out


CFGS = [("cdn-unset", ["--no-cdn"]), ("cdn-set", []), ("cdn-set+html-preconnect", ["--html-preconnect"])]
res = {}
with sync_playwright() as p:
    for name, extra in CFGS:
        pr = start_mock(extra)
        try:
            url = f"http://localhost:{APP}/"
            runs = [run_a(p, url) for _ in range(a.runs)]
            med = {k: (statistics.median([r[k] for r in runs if r[k] is not None]) if any(r[k] is not None for r in runs) else None) for k in runs[0]}
            shape(None, 0, 0)  # B: mạng nhanh, chỉ CPU 4×
            bb = run_b(p, url)
            res[name] = dict(a_median=med, a_runs=runs, b=bb)
            print(name, json.dumps(med, ensure_ascii=False), json.dumps(bb), flush=True)
        finally:
            pr.terminate(); pr.wait(10)
if a.out:
    with open(a.out, "w") as fh:
        json.dump(dict(browser=a.browser, kbps=a.kbps, rtt_ms=a.rtt, results=res), fh, indent=1, ensure_ascii=False)
