"""Chỉ dùng khi phát triển: lái trình duyệt thật qua giao diện album mới (mở thẳng thư viện → cuộn hết → lightbox →
tìm bằng selfie → quay lại tất cả → Lưu ảnh), chụp màn hình từng bước và kiểm các điều bất biến.

  # với máy chủ giả (dev/mock_gallery.py --admin-token test-token; và một bản không token ở cổng 8822 cho --open-url):
  python dev/e2e.py --url http://localhost:8820 --selfie /tmp/album-test/photos/wedding_couple.jpg --out /tmp/album-e/shots --mock --manage-token test-token --open-url http://localhost:8822
  # với backend thật (đã có ảnh được xử lý): bỏ --mock; thêm --photos f1.jpg ... nếu muốn tải lên + Xử lý trước

--browser chromium|webkit (zoom bằng hai ngón chỉ chạy ở chromium). Thoát mã ≠ 0 nếu một bước hỏng. Ảnh chụp có thể chứa ảnh khách: .gitignore đã loại *.png.
"""
import argparse
import json
import os
import sys
import urllib.request
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8820")
ap.add_argument("--selfie", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--photos", nargs="*", help="tải lên + Xử lý trước (backend thật, thư mục dữ liệu trống)")
ap.add_argument("--mock", action="store_true", help="đang chạy dev/mock_gallery.py: kiểm thêm log của 'CDN', đường lùi và /manage có mã")
ap.add_argument("--manage-token", default="", help="máy chủ (--url) chạy với token này: thử trang đăng nhập /manage (dùng mã giả, vd. test-token)")
ap.add_argument("--open-url", default="", help="máy chủ KHÔNG đặt token: thử /manage mở thẳng")
ap.add_argument("--browser", default="chromium", choices=["chromium", "webkit"])
ap.add_argument("--reduced-motion", action="store_true")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
APP = urlparse(a.url)
APP_ORIGIN = f"{APP.scheme}://{APP.netloc}"
webkit = a.browser == "webkit"
tag = "" if not webkit else "wk-"
def shot(pg, n, full=False):
    pg.wait_for_timeout(450)  # để hiệu ứng hiện dần (ảnh .25s, lightbox .25s) xong, ảnh chụp mới đúng như người dùng thấy
    pg.screenshot(path=os.path.join(a.out, tag + n), full_page=full)
external, selfie_posts = [], []
allowed = {APP.netloc}


def check(cond, msg):
    if not cond:
        print("HỎNG:", msg)
        sys.exit(1)


def watch(ctx):
    def on_req(r):
        u = urlparse(r.url)
        if r.url.startswith(("data:", "blob:")):
            return
        external.append(r.url)  # lọc ở cuối: nguồn gốc CDN chỉ biết sau khi manifest về
        try:
            body = r.post_data_buffer  # thân Blob có thể không đọc được ở vài trình duyệt; khi đọc được thì kiểm luôn
        except Exception:
            body = None
        if r.method in ("POST", "PUT", "PATCH") or (body and len(body) > 100):  # mọi request mang thân (selfie/ảnh tải lên): chỉ được tới đúng nguồn gốc của trang
            check(f"{u.scheme}://{u.netloc}" == APP_ORIGIN, "request có thân (selfie) gửi tới nguồn gốc khác ứng dụng: " + r.url)
        if r.method == "POST" and u.path == "/api/search":
            selfie_posts.append(r.url)
            check(f"{u.scheme}://{u.netloc}" == APP_ORIGIN and r.url == APP_ORIGIN + "/api/search", "POST /api/search phải là đường dẫn tương đối cùng nguồn gốc: " + r.url)
    ctx.on("request", on_req)


def mock_log():
    with urllib.request.urlopen(a.url + "/__mock/log") as f:
        return json.load(f)


def vis_tile(pg, root):
    """(chỉ số, x, y) của một ô ảnh đang nằm trọn trong màn hình"""
    return pg.evaluate("""root => { const vh = innerHeight;
      for (const b of document.querySelectorAll(root + ' .tile')) { const r = b.getBoundingClientRect();
        if (r.top > 70 && r.bottom < vh - 70 && r.left >= 0) return [+b.dataset.i, r.left + r.width / 2, r.top + r.height / 2]; } return null; }""", root)


def settle(pg, root="#vg-all", timeout=30000):
    """mọi ô đang nhìn thấy đã có ảnh"""
    pg.wait_for_function("""root => { const vh = innerHeight; let n = 0;
      for (const b of document.querySelectorAll(root + ' .tile')) { const r = b.getBoundingClientRect();
        if (r.bottom > 0 && r.top < vh) { n++; const i = b.firstChild; if (!(i.complete && i.naturalWidth > 0) && !b.classList.contains('bad')) return false; } }
      return n > 0; }""", arg=root, timeout=timeout)


SHARE_STUB = """
  window.__shares = []; window.__deny = 0; window.__clickTask = false;
  document.addEventListener('click', () => { window.__clickTask = true; setTimeout(() => window.__clickTask = false, 0); }, true);
  Object.defineProperty(navigator, 'canShare', { value: () => true, configurable: true });
  Object.defineProperty(navigator, 'share', { configurable: true, value: d => {
    window.__shares.push({ sync: window.__clickTask, n: d.files.length, type: d.files[0].type, size: d.files[0].size });
    if (window.__deny > 0) { window.__deny--; return Promise.reject(new DOMException('x', 'NotAllowedError')); }
    return new Promise(r => setTimeout(r, 400)); } });
"""

with sync_playwright() as p:
    b = getattr(p, a.browser).launch()
    rm = "reduce" if a.reduced_motion else "no-preference"

    # ---------- (tuỳ chọn) chủ nhà tải ảnh lên + Xử lý trên backend thật ----------
    if a.photos:
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm)
        watch(ctx)
        pg = ctx.new_page()
        pg.goto(a.url + "/manage")
        pg.wait_for_function("document.getElementById('disk').textContent.includes('GB')")
        pg.set_input_files("#pick", a.photos)
        pg.wait_for_function("document.getElementById('upsum').textContent.includes('Hoàn tất')", timeout=300000)
        pg.click("#proc")
        pg.wait_for_function("document.getElementById('pmsg').textContent.includes('Hoàn tất')", timeout=900000)
        print("đã tải lên + xử lý:", pg.inner_text("#ptiles").replace("\n", " "))
        ctx.close()

    # ---------- khách (điện thoại) ----------
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                        reduced_motion=rm, accept_downloads=True)
    watch(ctx)
    ctx.add_init_script(SHARE_STUB)
    reqs = []
    ctx.on("request", lambda r: reqs.append((r.method, r.url)))
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    if a.mock:
        urllib.request.urlopen(urllib.request.Request(a.url + "/__mock/reset", method="POST", data=b""))

    # 1. mở là thấy thư viện, chưa cần selfie
    pg.goto(a.url + "/")
    html = pg.content()
    check("/static/guest.js?v=" in html and "__V__" not in html and "__CDN_PRECONNECT__" not in html.replace("<!--__CDN_PRECONNECT__-->", ""), "URL tĩnh phải có ?v=<mã nội dung>")
    check(pg.evaluate("matchMedia('(pointer: coarse)').matches"), "giả lập điện thoại phải là pointer: coarse")
    pg.wait_for_selector("#vg-all .tile img.on", timeout=30000)
    settle(pg)
    n_all = int(pg.inner_text("#count").split()[0].replace(".", ""))
    check(n_all > 0, "phải có ảnh trong album")
    check(pg.is_visible("#cta-find") and pg.text_content("#cta-find").strip() == "Tìm ảnh của bạn", "nút Tìm ảnh của bạn phải hiện ở đầu trang")
    check(pg.text_content("h1").strip() == "Khắc Doãn & Đông Tiên", "đầu trang phải mang bộ nhận diện thiệp")
    check(not [u for m, u in reqs if "/api/search" in u], "chưa chọn selfie thì không được gọi /api/search")
    check(len([u for m, u in reqs if u.split("?")[0].endswith("/api/gallery")]) == 1, "manifest chỉ được tải 1 lần (bắt đầu từ <head>): %s" % [u for m, u in reqs if "/api/gallery" in u])
    cdn = pg.evaluate("document.querySelector('link[rel=preconnect]') ? document.querySelector('link[rel=preconnect]').href.replace(/\\/$/, '') : ''")
    cdn_img = pg.evaluate("document.querySelector('#vg-all .tile img').currentSrc")
    if cdn:
        allowed.add(urlparse(cdn).netloc)
        check(cdn_img.startswith(cdn + "/i/"), "ảnh nhỏ phải đi từ CDN khi có: " + cdn_img)
    first = pg.evaluate("document.querySelector('#vg-all .tile img').fetchPriority")
    check(first == "high", "ảnh hàng đầu phải fetchpriority=high: " + first)
    check(pg.evaluate("document.querySelector('#vg-all .tile img').decoding") == "async", "decoding=async")
    check(pg.evaluate("[...document.querySelectorAll('#vg-all .tile img')].every(i => i.getAttribute('sizes') || !i.srcset)"), "srcset cần sizes")
    pg.wait_for_timeout(1200 if not a.reduced_motion else 0)
    shot(pg, "1-gallery-first-load.png")
    print(f"thư viện: {n_all} ảnh, màn đầu đã đủ ảnh; ảnh nhỏ từ {cdn_img.split('/i/')[0] or 'cùng nguồn gốc'}")

    # 2. cuộn hết toàn bộ ảnh
    seen, max_dom, max_img = set(), 0, 0
    total_h = pg.evaluate("document.documentElement.scrollHeight")
    y, step, shot_mid = 0, pg.evaluate("innerHeight * 0.8"), False
    while True:
        pg.evaluate("y => scrollTo(0, y)", y)
        pg.wait_for_timeout(35)
        r = pg.evaluate("""() => { const vh = innerHeight, s = []; for (const b of document.querySelectorAll('#vg-all .tile')) { const q = b.getBoundingClientRect(); if (q.bottom > 0 && q.top < vh) s.push(+b.dataset.i); }
          return { s, dom: document.querySelectorAll('#vg-all .tile').length, img: document.querySelectorAll('#vg-all .tile img[src]').length }; }""")
        seen.update(r["s"]); max_dom = max(max_dom, r["dom"]); max_img = max(max_img, r["img"])
        if not shot_mid and y > total_h * 0.4:
            settle(pg); shot(pg, "2-gallery-mid-scroll.png"); shot_mid = True
        if y >= total_h - pg.evaluate("innerHeight"):
            break
        y += step
    settle(pg)
    check(len(seen) == n_all, f"cuộn qua phải thấy đủ {n_all} ảnh, mới thấy {len(seen)}")
    check(max_dom < 400, f"số ô trong DOM phải bị chặn, đang {max_dom}")
    print(f"cuộn hết: thấy {len(seen)}/{n_all} ảnh, DOM tối đa {max_dom} ô, ô đang giữ ảnh tối đa {max_img}")
    shot(pg, "3-gallery-bottom.png")
    check(pg.is_visible("#pill") and pg.is_visible("#pill-find"), "nút nổi phải hiện khi đã cuộn qua đầu trang")
    pg.evaluate("scrollTo(0, 0)")
    pg.wait_for_function("document.getElementById('pill').hidden")
    pg.evaluate("scrollTo(0, document.documentElement.scrollHeight * 0.3)")
    settle(pg)

    # 3. lightbox từ thư viện (chế độ cả album)
    t = vis_tile(pg, "#vg-all")
    check(t, "cần một ô nhìn thấy để mở")
    idx = t[0]
    originals_before = len([u for m, u in reqs if "/originals/" in u])
    pg.touchscreen.tap(t[1], t[2])
    pg.wait_for_selector("#lb:not([hidden])")
    check(pg.text_content("#lb-count").replace(".", "").startswith(f"{idx + 1} /"), "đếm ảnh trong lightbox: " + pg.text_content("#lb-count"))
    check(pg.text_content("#lb-count").endswith(pg.inner_text("#count").split()[0]), "lightbox đi qua cả album: " + pg.text_content("#lb-count"))
    pg.wait_for_function("document.getElementById('lb-img').naturalWidth > 1000", timeout=30000)
    check("-2000.webp" in pg.evaluate("document.getElementById('lb-img').currentSrc"), "lightbox dùng bản WebP 2000 px")
    pg.wait_for_timeout(400 if not a.reduced_motion else 0)
    shot(pg, "4-lightbox-from-gallery.png")
    zoom = lambda: float(pg.get_attribute("#lb", "data-zoom"))
    rect = lambda: pg.evaluate("(() => { const i = document.getElementById('lb-img'), b = i.getBoundingClientRect(); const k = Math.min(b.width / i.naturalWidth, b.height / i.naturalHeight); const w = i.naturalWidth * k, h = i.naturalHeight * k; return { l: b.left + (b.width - w) / 2, t: b.top + (b.height - h) / 2, w, h, bl: b.left, bt: b.top, bw: b.width, bh: b.height }; })()")
    r = rect()
    cx, cy = r["l"] + r["w"] / 2, r["t"] + r["h"] / 2
    pg.touchscreen.tap(cx, cy); pg.wait_for_timeout(60); pg.touchscreen.tap(cx, cy)
    pg.wait_for_function("parseFloat(document.getElementById('lb').dataset.zoom) > 2.4")
    check(abs(zoom() - 2.5) < 0.05, "chạm đúp phải zoom ~2.5×")
    pg.touchscreen.tap(cx, cy); pg.wait_for_timeout(60); pg.touchscreen.tap(cx, cy)
    pg.wait_for_function("parseFloat(document.getElementById('lb').dataset.zoom) < 1.01")
    if not webkit:
        cdp = ctx.new_cdp_session(pg)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx - 45, "y": cy, "id": 1}, {"x": cx + 45, "y": cy, "id": 2}]})
        for k in range(1, 6):
            d = 45 + 10 * k
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": cx - d, "y": cy, "id": 1}, {"x": cx + d, "y": cy, "id": 2}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pg.wait_for_timeout(100)
        check(zoom() > 1.8, "chụm hai ngón phải zoom, đang là %s" % zoom())
    pg.keyboard.press("ArrowRight")
    pg.wait_for_function(f"document.getElementById('lb-count').textContent.replace(/\\./g, '').startsWith('{idx + 2} /')")
    check(abs(zoom() - 1) < 0.01, "đổi ảnh phải đặt lại zoom")
    # chế độ cả album: KHÔNG tải sẵn ảnh gốc cho nút Chia sẻ (1.900 ảnh gốc ~3 MB), chỉ tải khi bấm
    pg.wait_for_function("document.getElementById('lb-share').offsetParent !== null")
    pg.wait_for_timeout(2300)
    check(len([u for m, u in reqs if "/originals/" in u]) == originals_before, "cả album: không được tải sẵn ảnh gốc: %s" % [u for m, u in reqs if "/originals/" in u])
    pg.click("#lb-share")
    pg.wait_for_function("document.getElementById('lb-share').textContent.trim() === 'Chia sẻ' || document.getElementById('lb-share').hasAttribute('aria-busy')")
    pg.wait_for_function("window.__shares.length === 0 && document.getElementById('lb-share').textContent.trim() === 'Chia sẻ'", timeout=30000)
    check(len([u for m, u in reqs if "/originals/" in u]) == originals_before + 1, "bấm Chia sẻ ở cả album: tải ảnh gốc lúc bấm")
    pg.click("#lb-share")  # lần hai: file đã sẵn → chia sẻ đồng bộ trong cú bấm
    sh = pg.evaluate("window.__shares")
    check(len(sh) == 1 and sh[0]["sync"] and sh[0]["size"] > 10000, "bấm lần hai phải gọi share đồng bộ: %s" % sh)
    print("lightbox (cả album): đếm đúng, bản 2000 WebP, zoom, không tải sẵn ảnh gốc, chia sẻ khi bấm: OK")
    # đóng: focus quay về đúng ô (dù đã lướt qua ảnh khác); Tab bị giữ trong lightbox
    if not webkit:  # Safari mặc định không cho Tab dừng ở nút/liên kết: kiểm Tab chỉ ở Chromium
        pg.keyboard.press("Tab"); pg.keyboard.press("Tab")
        check(pg.evaluate("document.getElementById('lb').contains(document.activeElement)"), "Tab phải ở trong lightbox")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#lb", state="hidden")
    check(webkit or pg.evaluate("document.activeElement && document.activeElement.classList.contains('tile') ? +document.activeElement.dataset.i : -1") == idx + 1, "đóng lightbox: focus về ô của ảnh đang xem")
    y_before = pg.evaluate("scrollY")

    # 4. tìm bằng selfie → thu hẹp
    pg.set_input_files("#in-file", a.selfie)
    pg.wait_for_selector("#results:not([hidden])", timeout=60000)
    pg.wait_for_selector("#vg-main .tile img.on", timeout=30000)
    pg.wait_for_timeout(500)
    check(pg.is_hidden("#all"), "tìm selfie: thư viện đầy đủ phải ẩn (thu hẹp)")
    nm = int(pg.text_content("#g-main-n").split()[0])
    check(nm > 1, "phải có ảnh trong 'Ảnh của bạn'")
    check(pg.get_attribute("#maybe-toggle", "aria-expanded") == "false" if pg.is_visible("#g-maybe") else True, "'Có thể cũng là bạn' mặc định thu gọn")
    check("điểm" not in pg.inner_text("#results").lower() and not pg.locator(".score").count(), "khách không được thấy điểm")
    print("selfie:", pg.inner_text("#s-title"), "|", pg.inner_text("#g-main-n"), "|", pg.inner_text("#maybe-n") if pg.is_visible("#maybe-n") else "(không có nhóm 'có thể')")
    pg.wait_for_timeout(900)
    shot(pg, "5-selfie-results.png")
    if pg.is_visible("#g-maybe"):
        pg.click("#maybe-toggle")
        check(pg.get_attribute("#maybe-toggle", "aria-expanded") == "true" and pg.locator("#vg-maybe .tile").count() > 0, "mở nhóm 'Có thể cũng là bạn'")
        pg.locator("#g-maybe").scroll_into_view_if_needed()
        pg.wait_for_selector("#vg-maybe .tile img.on", timeout=20000)
        shot(pg, "5b-selfie-maybe-open.png")
        pg.click("#maybe-toggle")
    # ảnh kết quả đi đúng URL v/w/h/c như thư viện
    pg.evaluate("document.getElementById('results').scrollIntoView()")
    pg.wait_for_timeout(300)
    settle(pg, "#vg-main")
    src = pg.evaluate("[...document.querySelectorAll('#vg-main .tile img[src]')].map(i => i.currentSrc)[0] || ''")
    check("/i/" in src and ("-480.webp" in src or "-240.webp" in src), "kết quả dùng URL /i/ có v: " + src)
    t = vis_tile(pg, "#vg-main")
    pg.touchscreen.tap(t[1], t[2])
    pg.wait_for_selector("#lb:not([hidden])")
    check(pg.text_content("#lb-count").endswith(f"/ {nm}"), "lightbox của kết quả đi qua đúng tập kết quả: " + pg.text_content("#lb-count"))
    n0 = len([u for m, u in reqs if "/originals/" in u])
    pg.wait_for_timeout(2400)  # SHARE_PREFETCH_MS = 1500
    check(len([u for m, u in reqs if "/originals/" in u]) == n0 + 1, "ảnh của bạn: tải sẵn đúng 1 ảnh gốc (ảnh đang xem)")
    pg.wait_for_function("document.getElementById('lb-share').textContent.trim() === 'Chia sẻ'", timeout=30000)
    pg.evaluate("window.__deny = 0")
    pg.click("#lb-share")
    sh = pg.evaluate("window.__shares")
    check(len(sh) == 2 and sh[1]["sync"], "ảnh của bạn: share đồng bộ khi file đã tải sẵn: %s" % sh)
    pg.wait_for_timeout(600)
    shot(pg, "6-lightbox-from-results.png")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#lb", state="hidden")
    log_posts = [u for m, u in reqs if m == "POST"]
    check(all(urlparse(u).netloc == APP.netloc and urlparse(u).path == "/api/search" for u in log_posts), "mọi POST của khách chỉ tới /api/search cùng nguồn gốc: %s" % log_posts)
    check(selfie_posts and all(u == APP_ORIGIN + "/api/search" for u in selfie_posts), "selfie phải được gửi tới /api/search cùng nguồn gốc: %s" % selfie_posts)
    if a.mock:
        lg = mock_log()
        check(all(m == "GET" and path.startswith("/i/") for m, path in lg["cdn"]), "'CDN' chỉ được thấy GET /i/*: %s" % [x for x in lg["cdn"] if not (x[0] == "GET" and x[1].startswith("/i/"))][:5])
        check(all(k != "cdn" for k, path, n in lg["post"]), "không POST nào tới CDN")
        sp = [x for x in lg["post"] if x[1] == "/api/search"]
        check(sp and all(x[0] == "app" and x[2] > 100 for x in sp), "selfie chỉ tới cổng ứng dụng: %s" % sp)
        print(f"selfie: {len(sp)} POST /api/search tới cổng ứng dụng, 0 POST và {len(lg['cdn'])} GET /i/ tại 'CDN'")

    # 5. quay lại tất cả
    pg.evaluate("scrollTo(0, 0)")
    pg.wait_for_function("!document.getElementById('pill').hidden || true")
    pg.click("#b-all")
    pg.wait_for_selector("#all:not([hidden])")
    check(pg.is_hidden("#results") and pg.is_hidden("#finder"), "Xem tất cả ảnh: kết quả phải ẩn")
    settle(pg)
    check(abs(pg.evaluate("scrollY") - y_before) < 3, "quay lại tất cả: về đúng chỗ đang xem (%s vs %s)" % (pg.evaluate("scrollY"), y_before))
    check(pg.evaluate("document.querySelectorAll('#vg-all .tile').length") > 5 and pg.evaluate("document.querySelectorAll('#vg-main .tile').length") == 0, "dọn kết quả khi về tất cả")
    shot(pg, "7-back-to-all.png")
    print("Xem tất cả ảnh: về đúng vị trí cuộn, kết quả đã dọn")

    # 6. Lưu ảnh (tải ảnh gốc cùng nguồn gốc)
    t = vis_tile(pg, "#vg-all")
    pg.touchscreen.tap(t[1], t[2])
    pg.wait_for_selector("#lb:not([hidden])")
    with pg.expect_download(timeout=30000) as dl:
        pg.click("#lb-save")
    d = dl.value
    size = os.path.getsize(d.path())
    check(size > 10000, "file tải về phải là ảnh gốc")
    print("Lưu ảnh:", d.suggested_filename, size, "byte")
    pg.keyboard.press("Escape")
    check(not errors, "lỗi JS/console: %s" % errors)
    ctx.close()

    # ---------- đường lùi ảnh: CDN hỏng → /i/ cùng nguồn gốc → /thumbs ----------
    if cdn:
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        got = []
        ctx.on("response", lambda r: got.append((r.url, r.status)))
        ctx.route(cdn + "/**", lambda r: r.abort())
        pg = ctx.new_page(); pg.goto(a.url + "/")
        pg.wait_for_selector("#vg-all .tile img.on", timeout=30000); settle(pg)
        srcs = pg.evaluate("[...document.querySelectorAll('#vg-all .tile img[src]')].filter(i => i.complete && i.naturalWidth > 0).map(i => i.currentSrc)")
        check(srcs and all(s.startswith(APP_ORIGIN + "/i/") or "/thumbs/" in s for s in srcs), "CDN hỏng: ảnh phải lùi về /i/ cùng nguồn gốc: %s" % srcs[:3])
        print(f"CDN hỏng hoàn toàn: {len(srcs)} ảnh nhìn thấy đều lùi về {APP_ORIGIN}/i/ (hoặc /thumbs)")
        ctx.close()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    ctx.route("**/i/**", lambda r: r.abort())
    pg = ctx.new_page(); pg.goto(a.url + "/")
    pg.wait_for_selector("#vg-all .tile img.on", timeout=30000); settle(pg)
    srcs = pg.evaluate("[...document.querySelectorAll('#vg-all .tile img[src]')].filter(i => i.complete && i.naturalWidth > 0).map(i => i.currentSrc)")
    check(srcs and all("/thumbs/" in s for s in srcs), "/i/ hỏng hẳn: phải lùi về /thumbs: %s" % srcs[:3])
    pg.touchscreen.tap(*vis_tile(pg, "#vg-all")[1:])
    pg.wait_for_function("document.getElementById('lb-img').currentSrc.includes('/previews/') && document.getElementById('lb-img').naturalWidth > 500", timeout=20000)
    print(f"/i/ hỏng hẳn: ảnh nhỏ → /thumbs ({len(srcs)}), lightbox → /previews")
    ctx.close()

    # ---------- album trống ----------
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm)
    ctx.route("**/api/gallery", lambda r: r.fulfill(status=200, content_type="application/json", body='{"mv":"0","cdn":"","dv":2,"photos":[]}'))
    pg = ctx.new_page(); pg.goto(a.url + "/")
    pg.wait_for_function("document.getElementById('empty-t').textContent.length > 0")
    check(pg.inner_text("#empty-t") == "Album đang được cập nhật" and pg.is_hidden("#cta-row") and pg.locator(".tile").count() == 0, "album trống: lời nhắn ấm áp, không nút selfie")
    pg.wait_for_timeout(900 if not a.reduced_motion else 0)
    shot(pg, "8-empty-gallery.png")
    print("album trống:", pg.inner_text("#empty-t"))
    ctx.close()

    # ---------- desktop ----------
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm)
    watch(ctx)
    pg = ctx.new_page(); pg.goto(a.url + "/")
    pg.wait_for_selector("#vg-all .tile img.on", timeout=30000); settle(pg)
    pg.wait_for_timeout(1000 if not a.reduced_motion else 0)
    shot(pg, "d1-gallery-first-load.png")
    pg.evaluate("scrollTo(0, document.documentElement.scrollHeight * 0.45)"); settle(pg)
    shot(pg, "d2-gallery-mid-scroll.png")
    # bàn phím: Tab tới nút Tìm ảnh rồi vào ô ảnh đầu; Enter mở lightbox; Escape trả focus
    pg.evaluate("scrollTo(0, 0)")
    if not webkit:
        pg.wait_for_selector('#vg-all .tile[data-i="0"]')
        pg.focus("#in-file")
        pg.keyboard.press("Tab")
        check(pg.evaluate("document.activeElement.classList.contains('tile') && document.activeElement.dataset.i") == "0", "Tab từ nút Tìm ảnh phải vào ô ảnh đầu tiên")
        for _ in range(5):
            pg.keyboard.press("Tab")
        check(pg.evaluate("document.activeElement.dataset.i") == "5", "Tab đi tuần tự qua các ô")
    else:
        pg.locator('#vg-all .tile[data-i="5"]').focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#lb:not([hidden])")
    check(pg.is_visible("#lb-prev") and pg.is_visible("#lb-next"), "desktop phải có mũi tên")
    pg.wait_for_function("document.getElementById('lb-img').naturalWidth > 1000", timeout=30000)
    shot(pg, "d3-lightbox.png")
    for _ in range(3):
        pg.keyboard.press("ArrowRight")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#lb", state="hidden")
    check(pg.evaluate("document.activeElement.dataset.i") == "8", "đóng: focus về ô của ảnh đang xem")
    pg.set_input_files("#in-file", a.selfie)
    pg.wait_for_selector("#vg-main .tile img.on", timeout=60000)
    pg.wait_for_timeout(1000 if not a.reduced_motion else 0)
    shot(pg, "d4-selfie-results.png")
    pg.click("#b-all"); pg.wait_for_selector("#all:not([hidden])")
    check(pg.locator("#vg-all .tile").count() > 5, "desktop: về tất cả")
    settle(pg)
    shot(pg, "d5-back-to-all.png")
    ctx.close()
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm)
    ctx.route("**/api/gallery", lambda r: r.fulfill(status=200, content_type="application/json", body='{"mv":"0","cdn":"","dv":2,"photos":[]}'))
    pg = ctx.new_page(); pg.goto(a.url + "/")
    pg.wait_for_function("document.getElementById('empty-t').textContent.length > 0")
    pg.wait_for_timeout(1300 if not a.reduced_motion else 0)
    shot(pg, "d8-empty-gallery.png")
    ctx.close()

    # ---------- /manage: khoá chủ nhà (hợp đồng §8) ----------
    if a.manage_token:
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm)
        pg = ctx.new_page()
        resp = pg.goto(a.url + "/manage")
        check(resp.status == 401 and "no-store" in (resp.headers.get("cache-control") or ""), "GET /manage chưa có cookie: 401 + no-store, đang là %s" % resp.status)
        pg.wait_for_selector("#login-form")
        check(pg.text_content("h1").strip() == "Trang chủ nhà" and pg.evaluate("document.querySelector('#token-in').type") == "password", "trang đăng nhập: bộ nhận diện + ô mật khẩu")
        pg.wait_for_timeout(500)
        shot(pg, "9-manage-login.png")
        r = pg.evaluate("fetch('/api/upload?name=x.jpg', { method: 'POST', body: 'x' }).then(r => r.status)")
        check(r == 401, "POST /api/upload chưa đăng nhập phải 401, đang là %s" % r)
        pg.fill("#token-in", "ma-sai"); pg.click("#login-btn")
        pg.wait_for_function("!document.getElementById('login-msg').hidden")
        check("không đúng" in pg.text_content("#login-msg"), "mã sai: báo 'Mã không đúng'")
        shot(pg, "9b-manage-login-wrong.png")
        pg.fill("#token-in", a.manage_token); pg.click("#login-btn")
        pg.wait_for_function("document.getElementById('disk') && document.getElementById('disk').textContent.includes('GB')", timeout=15000)
        ck = [c for c in ctx.cookies() if c["name"] == "album_token"]
        check(ck and ck[0]["httpOnly"] and ck[0]["sameSite"] == "Strict" and ck[0]["path"] == "/", "cookie album_token: HttpOnly, SameSite=Strict, Path=/: %s" % ck)
        check("album_token" not in pg.evaluate("document.cookie") and pg.evaluate("localStorage.length") == 0, "mã không được lộ cho JS / lưu ở localStorage")
        check("hạn mức" not in pg.inner_text("#disk"), "max_total_bytes null: không hiện hạn mức: " + pg.inner_text("#disk"))
        print("/manage (có khoá): 401 + trang đăng nhập → mã sai báo lỗi → mã đúng vào trang; cookie HttpOnly; không hạn mức:", pg.inner_text("#disk")[:90])
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_function("document.getElementById('upchips').textContent.includes('xong')", timeout=15000)
        pg.wait_for_timeout(300)
        shot(pg, "9c-manage-after-login.png")
        ctx.clear_cookies()  # cookie hết hạn giữa chừng: tải lên bị 401 → quay lại trang đăng nhập
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_selector("#login-form", timeout=15000)
        print("cookie hết hạn: tải lên 401 → quay về trang đăng nhập")
        ctx.close()
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm)
        pg = ctx.new_page(); pg.goto(a.url + "/manage"); pg.wait_for_selector("#login-form"); pg.wait_for_timeout(500)
        shot(pg, "d9-manage-login.png")
        for _ in range(10):  # 10 lần sai/phút: lần kế tiếp bị chặn (429)
            pg.fill("#token-in", "sai"); pg.click("#login-btn")
            pg.wait_for_function("!document.getElementById('login-btn').disabled")
        pg.fill("#token-in", "sai"); pg.click("#login-btn")
        pg.wait_for_function("document.getElementById('login-msg').textContent.includes('đợi')", timeout=10000)
        print("đăng nhập sai quá nhiều lần: báo chờ (429)")
        ctx.close()
        if a.mock:
            urllib.request.urlopen(urllib.request.Request(a.url + "/__mock/reset", method="POST", data=b""))
    if a.open_url:  # máy chủ không đặt token: /manage mở thẳng
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm)
        pg = ctx.new_page()
        resp = pg.goto(a.open_url + "/manage")
        check(resp.status == 200, "token không đặt: /manage phải mở thẳng, đang là %s" % resp.status)
        pg.wait_for_function("document.getElementById('disk').textContent.includes('GB')")
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_function("document.getElementById('upchips').textContent.includes('xong')", timeout=15000)
        pg.wait_for_timeout(300)
        shot(pg, "9d-manage-open.png")
        print("/manage (không khoá): mở thẳng, tải lên chạy không cần mã")
        ctx.close()
    external = [u for u in external if urlparse(u).netloc not in allowed]
    print("request ngoài ứng dụng/CDN:", external or "không có")
    check(not external, "có request ra ngoài: %s" % external[:5])
    b.close()
print("E2E OK")
