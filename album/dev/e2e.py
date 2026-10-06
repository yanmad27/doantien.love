"""Chỉ dùng khi phát triển: lái trình duyệt thật qua đúng giao diện (tải lên → Xử lý → selfie → mở ảnh),
chụp màn hình từng bước và đo RSS đỉnh của server khi đang xử lý.

  python dev/e2e.py --photos f1.jpg f2.jpg ... --selfie s.jpg --out /tmp/shots [--url http://127.0.0.1:8000] [--pid PID]
"""
import argparse
import os
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--photos", nargs="+", required=True)
ap.add_argument("--selfie", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--pid", type=int, help="PID server để đo RSS đỉnh khi xử lý")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
shot = lambda pg, n: pg.screenshot(path=os.path.join(a.out, n), full_page=True)

peak = [0.0]
stop = threading.Event()


def sample():
    while not stop.is_set():
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(a.pid)], capture_output=True, text=True).stdout.strip()
        if r:
            peak[0] = max(peak[0], int(r) / 1024)
        time.sleep(0.2)


def rss():
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(a.pid)], capture_output=True, text=True).stdout) / 1024


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1000, "height": 900})
    pg.goto(a.url + "/manage")
    shot(pg, "1-manage-empty.png")
    pg.set_input_files("#pick", a.photos)
    pg.wait_for_function("document.getElementById('upsum').textContent.includes('Hoàn tất')", timeout=300000)
    pg.wait_for_function("!document.getElementById('stats').textContent.startsWith('0 ảnh gốc')")
    shot(pg, "2-upload-done.png")
    print("upload:", pg.inner_text("#upsum"))
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
    print("process:", pg.inner_text("#pinfo"))
    if a.pid:
        print(f"RSS đỉnh khi xử lý: {peak[0]:.0f} MB; sau xử lý: {rss():.0f} MB")
    pg.goto(a.url + "/")
    pg.set_input_files("#file", a.selfie)
    pg.click("#go")
    pg.wait_for_selector("#controls:not([hidden])")
    pg.wait_for_selector(".card img")
    pg.wait_for_load_state("networkidle")
    print("tìm:", pg.inner_text("#count"))
    shot(pg, "5-search-results.png")
    if a.pid:
        print(f"RSS sau truy vấn: {rss():.0f} MB")
    pg.click(".card")
    pg.wait_for_function("document.getElementById('lbimg').naturalWidth > 0")
    pg.screenshot(path=os.path.join(a.out, "6-photo-opened.png"))
    print("ảnh mở:", pg.inner_text("#lbname"), pg.eval_on_selector("#lbimg", "i => i.naturalWidth + 'x' + i.naturalHeight"))
    b.close()
