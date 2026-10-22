"""Chỉ dùng khi phát triển: e2e tab Video của khách + mục Video của /manage trên mock (dev/mock_gallery.py) hoặc backend THẬT (--real).

  .venv/bin/python -I dev/e2e_video.py [--port 8850] [--real]

Mock: ma trận hiện tab (2 ngày / 1 ngày + video / chỉ video / không video), #ev=video, poster lazy, preload=none (không request media trước khi chạm),
một video phát một lúc, dừng khi rời tab / vào tìm kiếm, play() bị từ chối, /manage tải lên → nối lại sau khi mảnh bị ngắt → công bố, đổi ngày.
--real: cùng luồng /manage nhưng với create_app thật + MP4 H.264 dựng bằng ffmpeg; khách thấy thẻ và video phát (readyState ≥ 2, currentTime tăng)."""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import video_fixture  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8850)
ap.add_argument("--real", action="store_true")
a = ap.parse_args()
TOKEN = "e2e-token"
URL = f"http://127.0.0.1:{a.port}"
PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
SHOTS = os.environ.get("SHOTS", "")
fails = []


def check(cond, msg):
    print(("ok    " if cond else "HỎNG  ") + msg)
    if not cond:
        fails.append(msg)


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name))


tmp = tempfile.mkdtemp(prefix="album-vid-")
mp4, jpg = video_fixture.make(tmp)
photos = os.path.join(tmp, "photos"); os.makedirs(photos)
for i in range(4):
    Image.effect_noise((900, 600), 40 + i * 10).convert("RGB").save(os.path.join(photos, f"p{i}.jpg"), quality=80)
procs = []


def spawn(extra, port):
    p = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mock_gallery.py"), "--port", str(port), "--no-cdn", "--count", "20", "--photos", photos,
                          "--data", os.path.join(tmp, f"data{port}"), "--admin-token", TOKEN] + extra, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    return p


def wait_up(u):
    for _ in range(200):
        try:
            urllib.request.urlopen(u + "/api/gallery", timeout=1).read(); return
        except Exception:
            import time; time.sleep(0.1)
    raise SystemExit("máy chủ không lên")


def login(ctx, base):
    r = ctx.request.post(base + "/api/admin-login", data=json.dumps({"token": TOKEN}), headers={"Content-Type": "application/json"})
    assert r.status == 204, r.status


def sha_of(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def manage_upload(ctx, base, title=None, interrupt=False, ev_value="2026-10-17", shots=None):
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/manage")
    page.set_input_files("#vf-file", mp4)
    check(page.input_value("#vf-title") == "clip", "tiêu đề mặc định = tên file bỏ đuôi")
    if title:
        page.fill("#vf-title", title)
    page.select_option("#vf-ev", ev_value)
    if interrupt:   # ngắt đúng một mảnh: client phải nối lại từ offset máy chủ
        state = {"n": 0}

        def h(route):
            state["n"] += 1
            if state["n"] == 1:
                route.abort()
            else:
                route.continue_()
        page.route("**/api/admin/video/*/chunk*", h)
    page.click("#vf-go")
    page.wait_for_function("document.querySelector('#vf-msg').textContent.startsWith('Xong')", timeout=60000)
    check(True, "tải lên → công bố: " + page.inner_text("#vf-msg")[:60])
    if interrupt:
        check(state["n"] >= 2, f"mảnh bị ngắt rồi gửi lại ({state['n']} lần PUT chunk)")
    page.wait_for_selector("#vlist li")
    return page


def phone_guest(browser, base, hash_="", init=None):
    ctx = browser.new_context(**PHONE)
    pg = ctx.new_page()
    reqs = []
    pg.on("request", lambda r: reqs.append((r.method, r.url, r.headers.get("range", ""))))
    pg.goto(base + "/" + hash_)
    pg.wait_for_selector("#vg-all .tile, #vid-grid .vcard", timeout=15000)
    return ctx, pg, reqs


def tabs(pg):
    return pg.evaluate("[...document.querySelectorAll('#ev-tabs button')].map(b=>b.textContent)") if pg.evaluate("!document.getElementById('ev-tabs').hidden") else None


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    try:
        if not a.real:
            spawn(["--events", "--videos", "3", "--video-file", mp4, "--poster-file", jpg], a.port)
            wait_up(URL)
            # ---- ma trận hiện tab ----
            ctx, pg, reqs = phone_guest(browser, URL)
            check(tabs(pg) == ["Ngày 4/10", "Ngày 17/10", "Video"], f"2 ngày + video: {tabs(pg)}")
            posters = pg.evaluate("[...document.querySelectorAll('#vid-grid img')].map(i=>[i.loading,i.decoding])")
            pg.click("#ev-tabs [data-ev=video]")
            check(pg.evaluate("location.hash") == "#ev=video", "hash #ev=video sau khi bấm tab")
            pg.wait_for_selector("#vid-grid .vcard")
            check(pg.locator("#vid-grid .vcard").count() == 3 and pg.locator("#vg-all").is_hidden(), "3 thẻ video, lưới ảnh ẩn")
            check(all(p == ["lazy", "async"] for p in posters) and len(posters) == 3, "poster loading=lazy decoding=async")
            check(pg.locator("#vid-grid .vcard .vdur").first.inner_text() == "0:02", "thời lượng m:ss")
            check(pg.locator("#vid-grid .vcard .vday").count() == 2, "nhãn ngày chỉ ở video có ngày")
            check(not any(u.endswith(".mp4") for _, u, _ in reqs), "preload=none: chưa có request .mp4 trước khi chạm")
            check(pg.evaluate("document.querySelectorAll('#vid-grid video').length") == 0, "chưa tạo <video> trước khi chạm")
            shot(pg, "guest-video-tab.png")
            # chạm → phát
            pg.locator("#vid-grid .vcard .vplay").nth(0).tap()
            pg.wait_for_function("(()=>{const v=document.querySelector('#vid-grid video');return v&&v.readyState>=2&&v.currentTime>0})()", timeout=15000)
            v0 = pg.evaluate("(()=>{const v=document.querySelector('#vid-grid video');return [v.preload,v.playsInline,v.controls,v.poster.includes('/v/'),v.getAttribute('src')]})()")
            check(v0[0] == "none" and v0[1] and v0[2] and v0[3] and v0[4].startswith("/v/"), f"<video> controls playsinline preload=none poster, src cùng nguồn gốc: {v0}")
            check(any(u.endswith(".mp4") for _, u, _ in reqs), "request .mp4 chỉ sau khi chạm")
            shot(pg, "guest-video-playing.png")
            pg.locator("#vid-grid .vcard .vplay").nth(1).tap()
            pg.wait_for_function("document.querySelectorAll('#vid-grid video').length==2")
            st = pg.evaluate("[...document.querySelectorAll('#vid-grid video')].map(v=>[v.paused,v.hasAttribute('src')])")
            check(st[0] == [True, False] and st[1][1], f"một video phát một lúc, video cũ bị nhả src: {st}")
            # rời tab → nhả
            pg.click("#ev-tabs [data-ev=\"2026-10-04\"]")
            st = pg.evaluate("[...document.querySelectorAll('#vid-grid video')].map(v=>[v.paused,v.hasAttribute('src')])")
            check(all(s == [True, False] for s in st), "rời tab Video: mọi video dừng và nhả src")
            check(pg.evaluate("location.hash") == "#ev=2026-10-04" and pg.locator("#vg-all .tile").count() > 0, "về tab ngày: lưới ảnh dựng lại")
            # vào tìm kiếm khi đang phát
            pg.click("#ev-tabs [data-ev=video]")
            pg.locator("#vid-grid .vcard .vplay").nth(0).tap()
            pg.wait_for_function("(()=>{const v=document.querySelector('#vid-grid video');return v&&!v.paused})()", timeout=15000)
            sel = os.path.join(tmp, "selfie.jpg"); Image.effect_noise((300, 300), 60).convert("RGB").save(sel, quality=90)
            pg.set_input_files("#in-file", sel)
            pg.wait_for_function("document.getElementById('all').hidden")
            st = pg.evaluate("[...document.querySelectorAll('#vid-grid video')].map(v=>[v.paused,v.hasAttribute('src')])")
            check(all(s == [True, False] for s in st), "vào tìm kiếm: video dừng và nhả src")
            # hashchange sang video khi đang ở kết quả → thoát kết quả, hiện tab Video
            pg.evaluate("location.hash='#ev=2026-10-04'"); pg.evaluate("location.hash='#ev=video'")
            pg.wait_for_function("!document.getElementById('all').hidden && !document.getElementById('vid-grid').hidden", timeout=5000)
            check(True, "#ev=video khi đang xem kết quả tìm: thoát kết quả, hiện tab Video")
            ctx.close()
            # deep link
            ctx, pg, _ = phone_guest(browser, URL, "#ev=video")
            check(pg.locator("#vid-grid").is_visible() and pg.locator("#ev-tabs [data-ev=video][aria-selected=true]").count() == 1, "deep link #ev=video mở thẳng tab Video")
            ctx.close()
            # play() bị từ chối: controls vẫn hiện, không ném lỗi
            ctx, pg, _ = phone_guest(browser, URL, "#ev=video")
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.evaluate("() => { HTMLMediaElement.prototype.play = function () { return Promise.reject(new DOMException('blocked', 'NotAllowedError')); }; }")
            pg.locator("#vid-grid .vcard .vplay").nth(0).tap()
            pg.wait_for_selector("#vid-grid video")
            check(pg.evaluate("document.querySelector('#vid-grid video').controls") and not errs, "play() bị từ chối: controls vẫn hiện, không lỗi chưa bắt")
            ctx.close()
            # 1 ngày + video / chỉ video / không video (sửa manifest)
            def fulfil_with(mut):
                c = browser.new_context(**PHONE); p = c.new_page()
                def h(route):
                    r = route.fetch(headers={**route.request.headers, "accept-encoding": "identity"}); m = r.json(); mut(m)
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(m))
                p.route("**/api/gallery", h); p.goto(URL + "/"); p.wait_for_selector("#ev-tabs, #vg-all .tile, #empty:not([hidden])", state="attached"); p.wait_for_timeout(600)
                return c, p
            def one_day(m): m["photos"] = [r for r in m["photos"] if r[8] == "2026-10-04"]; m["events"] = [e for e in m["events"] if e["id"] == "2026-10-04"]
            c, p = fulfil_with(one_day)
            check(tabs(p) == ["Ảnh", "Video"], f"1 ngày + video: {tabs(p)}")
            p.click("#ev-tabs [data-ev=video]"); check(p.locator("#vid-grid .vcard").count() == 3, "1 ngày + video: bấm Video ra 3 thẻ")
            p.click("#ev-tabs [data-ev=photos]"); check(p.locator("#vg-all .tile").count() > 0 and p.locator("#vid-grid").is_hidden(), "1 ngày + video: tab Ảnh quay lại lưới")
            c.close()
            def vid_only(m): m["photos"] = []
            c, p = fulfil_with(vid_only)
            check(tabs(p) == ["Video"] and p.locator("#vid-grid .vcard").count() == 3 and p.locator("#empty").is_hidden(), f"chỉ video: tab Video, thẻ hiện, không có thẻ rỗng: {tabs(p)}")
            c.close()
            def no_vid(m): m["videos"] = []
            c, p = fulfil_with(no_vid)
            check(tabs(p) == ["Ngày 4/10", "Ngày 17/10"], f"không video: chỉ tab ngày: {tabs(p)}")
            c.close()
            def no_vid_one(m): m["videos"] = []; one_day(m)
            c, p = fulfil_with(no_vid_one)
            check(tabs(p) is None, "1 ngày, không video: không có thanh tab")
            c.close()
            def nothing(m): m["videos"] = []; m["photos"] = []
            c, p = fulfil_with(nothing)
            check(tabs(p) is None and p.locator("#empty").is_visible(), "không gì cả: thẻ 'Album đang được cập nhật'")
            c.close()
            def old(m): m.pop("videos", None)
            c, p = fulfil_with(old)
            check(tabs(p) == ["Ngày 4/10", "Ngày 17/10"], "manifest cũ không có 'videos': không lỗi")
            c.close()

        # ---- /manage (mock hoặc thật) ----
        if a.real:
            data = os.path.join(tmp, "real-data"); os.makedirs(data)
            procs.append(subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "_serve_real.py"), str(a.port), data, TOKEN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_up(URL)
            base = URL
        else:
            spawn([], a.port + 1)
            wait_up(f"http://127.0.0.1:{a.port + 1}")
            base = f"http://127.0.0.1:{a.port + 1}"
        mctx = browser.new_context(viewport={"width": 1100, "height": 1000})
        login(mctx, base)
        mp = manage_upload(mctx, base, title="Lễ cưới", interrupt=True, ev_value="2026-10-17")
        mp.fill("#vf-title", "") ; shot(mp, "manage-video-list.png")
        check(mp.locator("#vlist li").count() == 1 and "Lễ cưới" in mp.inner_text("#vlist"), "danh sách /manage có video vừa đăng")
        # đăng lại cùng file → trùng
        mp.set_input_files("#vf-file", mp4); mp.set_input_files("#vf-poster", jpg); mp.click("#vf-go")
        mp.wait_for_function("document.querySelector('#vf-msg').textContent.includes('đã được đăng')", timeout=30000)
        check(True, "đăng lại cùng file: báo đã đăng (409 duplicate)")
        # đổi ngày
        mp.select_option("#vlist select", "")
        mp.wait_for_function("document.querySelector('#vf-msg').textContent.includes('không gán ngày')", timeout=10000)
        mp.reload(); mp.wait_for_selector("#vlist li")
        check(mp.input_value("#vlist select") == "", "đổi ngày → không gán ngày, giữ sau khi tải lại")
        mp.select_option("#vlist select", "2026-10-04")
        mp.wait_for_function("document.querySelector('#vf-msg').textContent.includes('Ngày 4/10') || document.querySelector('#vf-msg').textContent.includes('ngày 4/10')", timeout=10000)
        # khách thấy thẻ và video phát
        ctx, pg, reqs = phone_guest(browser, base, "#ev=video")
        pg.wait_for_selector("#vid-grid .vcard")
        check(pg.locator("#vid-grid .vcard").count() == 1 and "Lễ cưới" in pg.inner_text("#vid-grid"), "khách: thẻ video vừa đăng")
        pg.locator("#vid-grid .vplay").tap()
        pg.wait_for_function("(()=>{const v=document.querySelector('#vid-grid video');return v&&v.readyState>=2&&v.currentTime>0.1})()", timeout=20000)
        t1 = pg.evaluate("document.querySelector('#vid-grid video').currentTime")
        pg.wait_for_timeout(700)
        t2 = pg.evaluate("document.querySelector('#vid-grid video').currentTime")
        check(t2 > t1 or pg.evaluate("document.querySelector('#vid-grid video').ended"), f"khách: video phát (currentTime {t1:.2f} → {t2:.2f})")
        rng = [r for r in reqs if r[1].endswith(".mp4")]
        check(bool(rng) and any(r[2].startswith("bytes=") for r in rng), "trình duyệt tải video bằng Range: " + str([r[2] for r in rng[:3]]))
        shot(pg, "guest-video-real-playing.png" if a.real else "guest-video-uploaded.png")
        ctx.close()
    finally:
        browser.close()
        for p in procs:
            p.terminate()
            try:
                p.wait(10)
            except Exception:
                p.kill()
print("\nTHẤT BẠI: " + "; ".join(fails) if fails else "\nTất cả đạt")
sys.exit(1 if fails else 0)
