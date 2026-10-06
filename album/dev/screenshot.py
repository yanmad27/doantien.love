"""Chỉ dùng khi phát triển: mở trang, tải selfie lên qua ô chọn file thật, chụp màn hình.
Cần requirements-dev.txt + `playwright install chromium`.

  python dev/screenshot.py --selfie a.jpg --out shot.png [--threshold 0.35] [--url http://127.0.0.1:8000]
"""
import argparse

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--selfie", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--threshold", type=float)
a = ap.parse_args()

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1100, "height": 900})
    pg.goto(a.url)
    pg.set_input_files("#file", a.selfie)
    pg.click("#go")
    pg.wait_for_selector("#controls:not([hidden])")
    if a.threshold is not None:
        pg.eval_on_selector("#th", "(el, v) => { el.value = v; el.dispatchEvent(new Event('input')); }", str(a.threshold))
    pg.wait_for_selector(".card img")
    pg.wait_for_load_state("networkidle")
    print("hiển thị:", pg.inner_text("#count"))
    pg.screenshot(path=a.out, full_page=True)
    b.close()
