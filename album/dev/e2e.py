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

    # ---- khách (điện thoại): selfie → lưới → lightbox → Lưu ảnh ----
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                        reduced_motion=rm, accept_downloads=True)
    watch(ctx)
    pg = ctx.new_page()
    pg.goto(a.url + "/")
    pg.wait_for_selector("#st-idle")
    check(pg.locator("label:has(#in-cam)").is_visible(), "nút Chụp ảnh selfie phải hiện trên điện thoại")
    check(pg.get_attribute("#in-cam", "capture") == "user", "input selfie phải có capture=user")
    shot(pg, "5-guest-idle.png")
    pg.set_input_files("#in-lib-touch", a.selfie)
    pg.wait_for_selector("#results:not([hidden])", timeout=60000)
    pg.wait_for_selector("#grid-main .tile img.on, #grid-maybe .tile img.on", timeout=30000)
    pg.wait_for_load_state("networkidle")
    n = pg.locator(".tile").count()
    check(n > 0, "phải có ảnh kết quả")
    check("điểm" not in pg.inner_text("#results").lower() and not pg.locator(".score").count(), "khách không được thấy điểm")
    print("kết quả:", pg.inner_text("#s-title"), "|", pg.inner_text("#g-main-n"), "|", n, "ô ảnh")
    shot(pg, "6-guest-results.png")
    pg.locator("#grid-main .tile, #grid-maybe .tile").first.click()
    pg.wait_for_function("document.getElementById('lb-img').naturalWidth > 1000", timeout=30000)
    print("lightbox:", pg.inner_text("#lb-count"), pg.eval_on_selector("#lb-img", "i => i.naturalWidth + 'x' + i.naturalHeight"))
    pg.screenshot(path=os.path.join(a.out, "7-guest-lightbox.png"))
    if n > 1:
        pg.keyboard.press("ArrowRight")
        pg.wait_for_function("document.getElementById('lb-count').textContent.startsWith('2 /')")
    with pg.expect_download(timeout=30000) as dl:
        pg.click("#lb-save")
    d = dl.value
    path = d.path()
    size = os.path.getsize(path)
    check(size > 10000, "file tải về phải là ảnh gốc")
    print("tải về:", d.suggested_filename, size, "byte")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#lb", state="hidden")
    ctx.close()

    # ---- không request nào ra ngoài ----
    print("request ngoài localhost:", external or "không có")
    check(not external, "có request ra ngoài: %s" % external)
    b.close()
print("E2E OK")
