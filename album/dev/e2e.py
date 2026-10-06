"""Chỉ dùng khi phát triển: lái trình duyệt thật qua đúng giao diện mới (tải lên → Xử lý → selfie → lưới kết quả →
lightbox → Lưu ảnh), chụp màn hình từng bước, kiểm không có request nào ra ngoài localhost và đo RSS đỉnh của server.

  python dev/e2e.py --photos f1.jpg f2.jpg ... --selfie s.jpg --out /tmp/shots [--url http://127.0.0.1:8000] [--pid PID]

Thoát mã ≠ 0 nếu một bước hỏng. Ảnh chụp có thể chứa ảnh khách: .gitignore đã loại *.png.
"""
import argparse
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--photos", nargs="+", required=True)
ap.add_argument("--selfie", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--pid", type=int, help="PID server để đo RSS đỉnh khi xử lý")
ap.add_argument("--reduced-motion", action="store_true", help="chạy với prefers-reduced-motion: reduce")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
shot = lambda pg, n: pg.screenshot(path=os.path.join(a.out, n), full_page=True)
host = urlparse(a.url).hostname

peak = [0.0]
stop = threading.Event()
external = []


def sample():
    while not stop.is_set():
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(a.pid)], capture_output=True, text=True).stdout.strip()
        if r:
            peak[0] = max(peak[0], int(r) / 1024)
        time.sleep(0.2)


def rss():
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(a.pid)], capture_output=True, text=True).stdout) / 1024


def watch(ctx):
    ctx.on("request", lambda r: external.append(r.url) if urlparse(r.url).hostname not in (host, "localhost", "127.0.0.1") and not r.url.startswith(("data:", "blob:")) else None)


def check(cond, msg):
    if not cond:
        print("HỎNG:", msg)
        sys.exit(1)


with sync_playwright() as p:
    b = p.chromium.launch()
    rm = "reduce" if a.reduced_motion else "no-preference"

    # ---- chủ nhà (desktop): tải lên → Xử lý ----
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm, accept_downloads=True)
    watch(ctx)
    pg = ctx.new_page()
    pg.goto(a.url + "/manage")
    pg.wait_for_function("document.getElementById('disk').textContent.includes('GB')")
    print("đĩa:", pg.inner_text("#disk"))
    shot(pg, "1-manage-empty.png")
    pg.set_input_files("#pick", a.photos)
    pg.wait_for_function("document.getElementById('upsum').textContent.includes('Hoàn tất')", timeout=300000)
    pg.wait_for_function("document.getElementById('n-orig').textContent !== '0' && document.getElementById('n-orig').textContent !== '–'")
    shot(pg, "2-upload-done.png")
    print("upload:", pg.inner_text("#upsum"), "|", pg.inner_text("#upchips").replace("\n", ", "))
    if a.pid:
        print(f"RSS trước khi xử lý: {rss():.0f} MB")
        threading.Thread(target=sample, daemon=True).start()
    pg.click("#proc")
    pg.wait_for_function("document.getElementById('pmsg').textContent.includes('Đang xử lý')")
    pg.wait_for_timeout(1500)
    shot(pg, "3-process-running.png")
    pg.wait_for_function("document.getElementById('pmsg').textContent.includes('Hoàn tất')", timeout=900000)
    stop.set()
    shot(pg, "4-process-done.png")
    print("process:", pg.inner_text("#ptiles").replace("\n", " "), "|", pg.inner_text("#pinfo"))
    if a.pid:
        print(f"RSS đỉnh khi xử lý: {peak[0]:.0f} MB; sau xử lý: {rss():.0f} MB")
    ctx.close()

    # ---- khách (điện thoại): selfie → lưới → lightbox (zoom, chạm vùng tối) → chia sẻ/Lưu ảnh ----
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                        reduced_motion=rm, accept_downloads=True)
    watch(ctx)
    # navigator.share giả: ghi lại việc có được gọi ĐỒNG BỘ trong cú bấm hay không (cờ __clickTask chỉ còn đúng trong task của click)
    ctx.add_init_script("""
      window.__shares = []; window.__deny = 0; window.__clickTask = false;
      document.addEventListener('click', () => { window.__clickTask = true; setTimeout(() => window.__clickTask = false, 0); }, true);
      Object.defineProperty(navigator, 'canShare', { value: () => true, configurable: true });
      Object.defineProperty(navigator, 'share', { configurable: true, value: d => {
        window.__shares.push({ sync: window.__clickTask, n: d.files.length, type: d.files[0].type, size: d.files[0].size });
        if (window.__deny > 0) { window.__deny--; return Promise.reject(new DOMException('x', 'NotAllowedError')); }
        return new Promise(r => setTimeout(r, 400)); } });
    """)
    originals = []
    ctx.on("request", lambda r: originals.append(r.url) if "/originals/" in r.url else None)
    pg = ctx.new_page()
    pg.goto(a.url + "/")
    pg.wait_for_selector("#st-idle")
    html = pg.content()
    check("/static/guest.js?v=" in html and "__V__" not in html, "URL tĩnh phải có ?v=<mã nội dung>")
    check(pg.evaluate("matchMedia('(pointer: coarse)').matches"), "giả lập điện thoại phải là pointer: coarse")
    check(pg.locator("label:has(#in-cam)").is_visible(), "nút Chụp ảnh selfie phải hiện trên điện thoại")
    check(pg.get_attribute("#in-cam", "capture") == "user", "input selfie phải có capture=user")
    pg.wait_for_timeout(1300 if not a.reduced_motion else 0)
    pg.screenshot(path=os.path.join(a.out, "5-guest-idle.png"))  # chỉ viewport: full_page làm mất giả lập cảm ứng (pointer: coarse) của playwright
    pg.set_input_files("#in-lib-touch", a.selfie)
    pg.wait_for_selector("#results:not([hidden])", timeout=60000)
    pg.wait_for_selector("#grid-main .tile img.on, #grid-maybe .tile img.on", timeout=30000)
    pg.wait_for_load_state("networkidle")
    n = pg.locator(".tile").count()
    check(n > 1, "phải có ít nhất 2 ảnh kết quả")
    check("điểm" not in pg.inner_text("#results").lower() and not pg.locator(".score").count(), "khách không được thấy điểm")
    print("kết quả:", pg.inner_text("#s-title"), "|", pg.inner_text("#g-main-n"), "|", n, "ô ảnh")
    pg.screenshot(path=os.path.join(a.out, "6-guest-results.png"))
    tile = pg.locator("#grid-main .tile, #grid-maybe .tile").first
    zoom = lambda: float(pg.get_attribute("#lb", "data-zoom"))
    rect = lambda: pg.evaluate("(() => { const i = document.getElementById('lb-img'), b = i.getBoundingClientRect(); const k = Math.min(b.width / i.naturalWidth, b.height / i.naturalHeight); const w = i.naturalWidth * k, h = i.naturalHeight * k; return { l: b.left + (b.width - w) / 2, t: b.top + (b.height - h) / 2, w, h, bl: b.left, bt: b.top, bw: b.width, bh: b.height }; })()")

    # -- lightbox: mở, mũi tên ẩn trên cảm ứng, zoom --
    tile.click()
    pg.wait_for_function("document.getElementById('lb-img').naturalWidth > 1000", timeout=30000)
    pg.wait_for_timeout(400 if not a.reduced_motion else 0)
    print("lightbox:", pg.inner_text("#lb-count"), pg.eval_on_selector("#lb-img", "i => i.naturalWidth + 'x' + i.naturalHeight"))
    check(not pg.is_visible("#lb-prev") and not pg.is_visible("#lb-next"), "mũi tên ‹ › phải ẩn trên cảm ứng")
    check(pg.evaluate("getComputedStyle(document.getElementById('lb')).backgroundColor") == "rgb(38, 28, 24)", "nền lightbox phải đặc #261c18")
    pg.screenshot(path=os.path.join(a.out, "7-guest-lightbox.png"))
    r = rect()
    cx, cy = r["l"] + r["w"] / 2, r["t"] + r["h"] / 2
    check(abs(zoom() - 1) < 0.01, "ban đầu chưa zoom")
    pg.touchscreen.tap(cx, cy)
    pg.wait_for_timeout(60)
    pg.touchscreen.tap(cx, cy)
    pg.wait_for_function("parseFloat(document.getElementById('lb').dataset.zoom) > 2.4")
    check(abs(zoom() - 2.5) < 0.05, "chạm đúp phải zoom ~2.5×, đang là %s" % zoom())
    pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(a.out, "7b-guest-lightbox-zoomed.png"))
    # vuốt ngang khi đang zoom: chỉ di chuyển ảnh, không đổi ảnh
    y = r["t"] + r["h"] / 2
    pg.mouse.move(300, y); pg.mouse.down(); pg.mouse.move(200, y, steps=6); pg.mouse.move(100, y, steps=6); pg.mouse.up()
    pg.wait_for_timeout(100)
    check(pg.inner_text("#lb-count").startswith("1 /"), "vuốt khi đang zoom không được đổi ảnh")
    check(zoom() > 2.4, "vuốt khi đang zoom không được thoát zoom")
    # vuốt xuống khi đang zoom: không đóng
    pg.mouse.move(200, 300); pg.mouse.down(); pg.mouse.move(200, 420, steps=8); pg.mouse.move(200, 600, steps=8); pg.mouse.up()
    pg.wait_for_timeout(200)
    check(pg.is_visible("#lb"), "vuốt xuống khi đang zoom không được đóng")
    # chạm đúp lần nữa: thu về
    pg.touchscreen.tap(cx, cy); pg.wait_for_timeout(60); pg.touchscreen.tap(cx, cy)
    pg.wait_for_function("parseFloat(document.getElementById('lb').dataset.zoom) < 1.01")
    # chụm hai ngón (CDP): phóng to quanh tâm
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx - 45, "y": cy, "id": 1}, {"x": cx + 45, "y": cy, "id": 2}]})
    for k in range(1, 6):
        d = 45 + 10 * k
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": cx - d, "y": cy, "id": 1}, {"x": cx + d, "y": cy, "id": 2}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    pg.wait_for_timeout(100)
    check(zoom() > 1.8, "chụm hai ngón phải zoom, đang là %s" % zoom())
    print("zoom: chạm đúp 2.5×, chụm →", round(zoom(), 2), "; vuốt/vuốt xuống bị bỏ qua khi đang zoom")
    # đổi ảnh bằng phím: zoom về 1 và sang ảnh 2
    pg.keyboard.press("ArrowRight")
    pg.wait_for_function("document.getElementById('lb-count').textContent.startsWith('2 /')")
    check(abs(zoom() - 1) < 0.01, "đổi ảnh phải đặt lại zoom")
    # N1: chụm (về ~1×) trong vùng tối rồi nhấc từng ngón nhanh — không được tính là chạm vùng tối để đóng
    r = rect(); y0 = r["t"] - 40
    check(y0 > r["bt"] + 10, "cần vùng tối phía trên ảnh để thử chụm trong vùng tối")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx - 45, "y": y0, "id": 1}, {"x": cx + 45, "y": y0, "id": 2}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": cx - 46, "y": y0, "id": 1}, {"x": cx + 46, "y": y0, "id": 2}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": [{"x": cx + 46, "y": y0, "id": 2}]})  # nhấc ngón 1, còn ngón 2
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    pg.wait_for_timeout(200)
    check(pg.is_visible("#lb") and abs(zoom() - 1) < 0.01, "chụm trong vùng tối rồi nhấc ngón không được đóng lightbox")
    print("chụm trong vùng tối + nhấc ngón: lightbox vẫn mở")
    # chạm vào vùng tối quanh ảnh: đóng
    pg.wait_for_function("document.getElementById('lb-img').naturalWidth > 1000", timeout=30000)
    r = rect()
    pt = (r["bl"] + r["bw"] / 2, r["t"] - 12) if r["t"] - 12 > r["bt"] + 4 else ((r["l"] - 8, r["bt"] + r["bh"] / 2) if r["l"] - 8 > r["bl"] else (4, r["bt"] + r["bh"] / 2))
    pg.touchscreen.tap(*pt)
    pg.wait_for_selector("#lb", state="hidden", timeout=5000)
    print("chạm vùng tối quanh ảnh tại", [round(v) for v in pt], "→ đã đóng")

    # -- chia sẻ: mạng chậm, gọi navigator.share đồng bộ trong cú bấm khi file đã sẵn --
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})  # các bước zoom ở trên đã kéo ảnh gốc vào cache; thử tải thật
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 30, "downloadThroughput": 500_000, "uploadThroughput": -1})
    originals.clear()
    tile.click()
    pg.wait_for_selector("#lb-share", state="visible", timeout=10000)
    pg.wait_for_function("document.getElementById('lb-share').hasAttribute('aria-busy')")
    busy = pg.text_content("#lb-share").strip()
    check("Đang chuẩn bị" in busy, "nút Chia sẻ phải hiện tiến độ khi chưa có file: %r" % busy)
    pg.click("#lb-share")  # chưa sẵn sàng: không được gọi share
    check(pg.evaluate("window.__shares.length") == 0, "chưa tải xong thì không được gọi navigator.share")
    pg.wait_for_function("document.getElementById('lb-share').textContent.trim() === 'Chia sẻ'", timeout=60000)
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
    check(len([u for u in originals if "/originals/" in u]) == 1, "chỉ tải trước ảnh gốc của ảnh đang xem: %s" % originals)
    pg.evaluate("window.__deny = 1")
    pg.click("#lb-share")
    sh = pg.evaluate("window.__shares")
    check(len(sh) == 1 and sh[0]["sync"] and sh[0]["size"] > 10000 and sh[0]["type"] == "image/jpeg", "share phải được gọi đồng bộ với file đã sẵn: %s" % sh)
    pg.wait_for_function("document.getElementById('lb-share').textContent.includes('Bấm lần nữa')")
    pg.click("#lb-share")  # gọi lại sau NotAllowedError: vẫn đồng bộ, dùng lại file đã giữ
    sh = pg.evaluate("window.__shares")
    check(len(sh) == 2 and sh[1]["sync"], "bấm lần nữa phải chia sẻ ngay: %s" % sh)
    pg.evaluate("document.getElementById('lb-share').click()")  # đang chia sẻ dở: bấm thêm bị bỏ qua
    check(len(pg.evaluate("window.__shares")) == 2, "đang chia sẻ dở mà bấm thêm thì không được gọi lại")
    pg.wait_for_timeout(600)
    pg.evaluate("document.getElementById('lb-share').click(); document.getElementById('lb-share').click()")  # bấm đúp
    check(len(pg.evaluate("window.__shares")) == 3, "bấm đúp phải chỉ gọi share một lần")
    print("chia sẻ: tiến độ hiện, share gọi đồng bộ, NotAllowedError → 'Bấm lần nữa', chống bấm đúp: OK")
    pg.wait_for_function("document.getElementById('lb-share').textContent.trim() === 'Chia sẻ'")

    with pg.expect_download(timeout=30000) as dl:
        pg.click("#lb-save")
    d = dl.value
    size = os.path.getsize(d.path())
    check(size > 10000, "file tải về phải là ảnh gốc")
    print("tải về:", d.suggested_filename, size, "byte")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#lb", state="hidden")

    # -- album trống: tiêu đề phải là "đang được cập nhật", không phải "chưa tìm thấy" --
    pg.goto(a.url + "/")
    pg.route("**/api/search", lambda r: r.fulfill(status=200, content_type="application/json", body='{"query":{"n_faces":1,"n_ignored":0,"bbox":[10,10,100,100],"width":200,"height":200,"det_score":0.9},"results":[],"total_matches":0,"truncated":false,"indexed_photos":0,"indexed_faces":0,"timing_ms":{"total":1}}'))
    pg.set_input_files("#in-lib-touch", a.selfie)
    pg.wait_for_function("document.getElementById('s-title').textContent.length > 0 && !document.getElementById('s-msg').hidden")
    check(pg.inner_text("#s-title") == "Album đang được cập nhật", "album trống: %r" % pg.inner_text("#s-title"))
    print("album trống:", pg.inner_text("#s-title"))
    ctx.close()

    # -- desktop: lightbox có mũi tên + nhấp đúp --
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm)
    watch(ctx)
    pg = ctx.new_page()
    pg.goto(a.url + "/")
    pg.set_input_files("#in-lib", a.selfie)
    pg.wait_for_selector("#grid-main .tile img.on, #grid-maybe .tile img.on", timeout=60000)
    pg.locator(".tile").first.click()
    pg.wait_for_function("document.getElementById('lb-img').naturalWidth > 1000", timeout=30000)
    check(pg.is_visible("#lb-prev") and pg.is_visible("#lb-next"), "desktop phải có mũi tên")
    pg.screenshot(path=os.path.join(a.out, "8-desktop-lightbox.png"))
    r = pg.evaluate("(() => { const b = document.getElementById('lb-img').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()")
    pg.mouse.dblclick(*r)
    pg.wait_for_function("parseFloat(document.getElementById('lb').dataset.zoom) > 2.4")
    print("desktop: mũi tên hiện, nhấp đúp zoom →", pg.get_attribute("#lb", "data-zoom"))
    ctx.close()

    # ---- không request nào ra ngoài ----
    print("request ngoài localhost:", external or "không có")
    check(not external, "có request ra ngoài: %s" % external)
    b.close()
print("E2E OK")
