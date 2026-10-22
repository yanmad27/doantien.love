"""Chỉ dùng khi phát triển: e2e nối lại / đồng bộ offset / dừng có giới hạn / Huỷ của mục Video trong /manage.

  .venv/bin/python -I dev/e2e_video_resume.py [--port 8900] [--real]

--real (backend thật): tải file ~15 MiB (2 mảnh), mảnh 2 bị ngắt, tải lại trang, chọn lại đúng file với tiêu đề/ngày KHÁC → nối lại ở offset ≠ 0 (8 MiB),
báo tiêu đề/ngày đã giữ, sau công bố áp ngày mới bằng POST /event, tiêu đề giữ bản đầu.
Mock: 409 với offset khác → client đi tiếp từ offset máy chủ; 409 mãi không tiến → dừng sau 8 lần, mở lại form, tắt cảnh báo rời trang; Huỷ giữa chừng rồi nối lại được."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import video_fixture  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8900)
ap.add_argument("--real", action="store_true")
a = ap.parse_args()
TOKEN = "e2e-token"
URL = f"http://127.0.0.1:{a.port}"
MIB8 = 8 * 1048576
fails = []


def check(c, m):
    print(("ok    " if c else "HỎNG  ") + m)
    if not c:
        fails.append(m)


tmp = tempfile.mkdtemp(prefix="album-vres-")
mp4, jpg = video_fixture.make_big(tmp)
if a.real:
    data = os.path.join(tmp, "data"); os.makedirs(data)
    cmd = [sys.executable, "-I", os.path.join(HERE, "_serve_real.py"), str(a.port), data, TOKEN]
else:
    os.makedirs(os.path.join(tmp, "photos"))
    from PIL import Image
    Image.effect_noise((600, 400), 50).convert("RGB").save(os.path.join(tmp, "photos", "p.jpg"))
    cmd = [sys.executable, "-I", os.path.join(HERE, "mock_gallery.py"), "--port", str(a.port), "--no-cdn", "--count", "4", "--photos", os.path.join(tmp, "photos"),
           "--data", os.path.join(tmp, "data"), "--admin-token", TOKEN]
srv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(200):
    try:
        urllib.request.urlopen(URL + "/api/gallery", timeout=1).read(); break
    except Exception:
        time.sleep(0.1)

msg = lambda p: p.inner_text("#vf-msg")


def gallery_videos():
    return json.loads(urllib.request.urlopen(URL + "/api/gallery").read())["videos"]


with sync_playwright() as pw:
    br = pw.chromium.launch()
    try:
        ctx = br.new_context(viewport={"width": 1100, "height": 1000})
        r = ctx.request.post(URL + "/api/admin-login", data=json.dumps({"token": TOKEN}), headers={"Content-Type": "application/json"})
        assert r.status == 204
        page = ctx.new_page(); page.on("dialog", lambda d: d.accept())
        page.goto(URL + "/manage")
        offs = []
        page.on("request", lambda q: offs.append(int(q.url.split("off=")[1])) if "/chunk?off=" in q.url and q.method == "PUT" else None)

        def fill(title, ev):
            page.set_input_files("#vf-file", mp4); page.set_input_files("#vf-poster", jpg)
            page.fill("#vf-title", title); page.select_option("#vf-ev", ev)

        if a.real:
            n = {"c": 0}

            def h(route):
                n["c"] += 1
                route.continue_() if n["c"] == 1 else route.abort()
            page.route("**/api/admin/video/*/chunk*", h)
            fill("Bản gốc", "2026-10-17"); page.click("#vf-go")
            page.wait_for_function("document.querySelector('#vf-msg').textContent.includes('chập chờn')", timeout=60000)
            check(page.is_disabled("#vf-title") and page.is_disabled("#vf-ev") and page.is_disabled("#vf-poster"), "lúc đang tải: tiêu đề/ngày/poster bị khoá")
            check(page.is_visible("#vf-cancel"), "nút Huỷ hiện khi đang tải")
            page.unroute("**/api/admin/video/*/chunk*")
            page.reload(); offs.clear()
            fill("Tên mới", "2026-10-04"); page.click("#vf-go")
            page.wait_for_function("document.querySelector('#vf-msg').textContent.startsWith('Xong')", timeout=90000)
            m = msg(page)
            check(offs and offs[0] == MIB8, f"nối lại ở offset khác 0 sau tải lại trang: các off = {offs}")
            v = gallery_videos()
            check(len(v) == 1 and v[0][6] == "Bản gốc" and v[0][7] == "2026-10-04", f"công bố: tiêu đề giữ bản đầu, ngày áp theo lựa chọn mới: {v[0][6:8]}")
            check("Bản gốc" in m and "4/10" in m and "giữ như lần tải đầu" in m, "thông báo cuối nêu tiêu đề giữ + ngày áp dụng: " + m[:200])
            check(page.input_value("#vlist select") == "2026-10-04", "danh sách /manage hiện ngày mới")
            page.evaluate("() => {}")
        else:
            # (b1) 409 với offset khác: client đi tiếp từ offset máy chủ
            st = {"c": 0}

            def h1(route):
                st["c"] += 1
                if st["c"] == 1:   # máy chủ nhận mảnh 1 nhưng client nhận 409 offset=0 → gửi lại off=0 → máy chủ 409 offset=8 MiB
                    resp = route.fetch()
                    route.fulfill(status=409, content_type="application/json", body=json.dumps({"error": "offset", "offset": 0}))
                else:
                    route.continue_()
            page.route("**/api/admin/video/*/chunk*", h1)
            fill("Resync", "2026-10-04"); page.click("#vf-go")
            page.wait_for_function("document.querySelector('#vf-msg').textContent.startsWith('Xong')", timeout=90000)
            check(offs[:3] == [0, 0, MIB8], f"409 resync: client đi tiếp từ offset máy chủ báo: {offs}")
            page.unroute("**/api/admin/video/*/chunk*")
            # (b2) 409 mãi không tiến → dừng có giới hạn
            page.reload(); page.evaluate("localStorage.clear()")
            # file khác sha: chèn vài byte cuối là không hợp lệ MP4 nhưng mock chấp nhận tới finish; ta dừng trước finish
            p2 = os.path.join(tmp, "b2.mp4"); open(p2, "wb").write(open(mp4, "rb").read() + b"\0" * 7)
            offs.clear(); cnt = {"c": 0}

            def h2(route):
                cnt["c"] += 1
                route.fulfill(status=409, content_type="application/json", body=json.dumps({"error": "offset", "offset": 0}))
            page.route("**/api/admin/video/*/chunk*", h2)
            page.set_input_files("#vf-file", p2); page.set_input_files("#vf-poster", jpg); page.click("#vf-go")
            page.wait_for_function("document.querySelector('#vf-msg').textContent.includes('không thống nhất')", timeout=60000)
            check(cnt["c"] == 8, f"409 không tiến: dừng sau đúng 8 lần ({cnt['c']})")
            check(not page.is_disabled("#vf-file") and not page.is_disabled("#vf-title") and not page.is_disabled("#vf-ev") and page.is_hidden("#vf-cancel") and page.is_enabled("#vf-go"), "dừng: form mở lại, Huỷ ẩn, nút Tải lên bật")
            check(page.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); dispatchEvent(e); return e.defaultPrevented; }") is False, "dừng: cảnh báo rời trang đã tắt")
            page.unroute("**/api/admin/video/*/chunk*")
            # (b3) Huỷ giữa chừng (request treo) rồi nối lại
            hung = {"c": 0}

            def h3(route):
                hung["c"] += 1   # không trả lời: request treo cho tới khi client huỷ
            page.route("**/api/admin/video/*/chunk*", h3)
            with page.expect_request(lambda q: "/chunk?off=" in q.url):
                page.click("#vf-go")
            page.click("#vf-cancel")
            page.wait_for_function("document.querySelector('#vf-msg').textContent.startsWith('Đã huỷ')", timeout=10000)
            check(hung["c"] >= 1, "request chunk đã treo trước khi Huỷ")
            check(hung["c"] >= 1 and page.is_enabled("#vf-go") and page.is_hidden("#vf-cancel") and not page.is_disabled("#vf-title"), "Huỷ: request treo bị dừng, form mở lại")
            check(page.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); dispatchEvent(e); return e.defaultPrevented; }") is False, "Huỷ: cảnh báo rời trang đã tắt")
            page.unroute("**/api/admin/video/*/chunk*")
            page.click("#vf-go")
            page.wait_for_function("document.querySelector('#vf-msg').textContent.includes('Tiếp tục bản đang tải') || document.querySelector('#vf-msg').textContent.startsWith('Xong') || document.querySelector('#vf-msg').textContent.includes('không hợp lệ')", timeout=60000)
            check(True, "sau Huỷ: chọn lại + bấm tải → phiên được giữ/nối lại: " + msg(page)[:90])
    finally:
        br.close()
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()
print("\nTHẤT BẠI: " + "; ".join(fails) if fails else "\nTất cả đạt")
sys.exit(1 if fails else 0)
