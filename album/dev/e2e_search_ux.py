"""Chỉ dùng khi phát triển: thử trình duyệt thật (Playwright) cho trải nghiệm tìm selfie không chọn ngày. Tự dựng ảnh tổng hợp + máy chủ giả
(dev/mock_gallery.py --events) và THAY /api/search bằng bản giả xác định (có thể giữ phản hồi), giữ/nhả phản hồi ảnh để thấy lưới dựng khi ảnh còn chờ.

  .venv/bin/python -I dev/e2e_search_ux.py [--port 8838] [--browser chromium|webkit]

Kiểm: không có nút chọn phạm vi; mỗi lần tìm đúng một /api/search không kèm scope, kết quả cả hai ngày cùng một danh sách; "Đang tìm" hiện từ lúc chọn
ảnh tới lúc có phản hồi; lưới dựng ngay khi phản hồi về dù ảnh còn chờ; ô điểm cao nhất ưu tiên cao + tối đa 6 ảnh cùng lúc theo thứ tự điểm; đổi selfie giữa chừng,
thoát, thử lại; không ghi gì vào localStorage/sessionStorage/IndexedDB/Cache API. Thoát mã ≠ 0 nếu một bước hỏng."""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8838)
ap.add_argument("--browser", default="chromium", choices=["chromium", "webkit"])
a = ap.parse_args()
E4, E17 = "2026-10-04", "2026-10-17"
HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(cond, msg):
    print(("ok    " if cond else "HỎNG  ") + msg)
    if not cond:
        fails.append(msg)


tmp = tempfile.mkdtemp(prefix="album-sc-")
photos = os.path.join(tmp, "photos"); os.makedirs(photos)
rnd = random.Random(3)
for i in range(6):
    Image.effect_noise((900, 600), 40 + i * 10).convert("RGB").save(os.path.join(photos, f"p{i}.jpg"), quality=80)
selfies = []
for k, n in enumerate((300, 520)):
    p = os.path.join(tmp, f"selfie{k}.jpg"); Image.effect_noise((n, n), 60).convert("RGB").save(p, quality=90); selfies.append(p)
S0, S1 = (os.path.getsize(x) for x in selfies)  # cỡ ảnh = hạt giống của phản hồi giả

srv = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mock_gallery.py"), "--port", str(a.port), "--no-cdn", "--count", "90", "--events",
                        "--photos", photos, "--data", os.path.join(tmp, "data")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
URL = f"http://127.0.0.1:{a.port}"


def stop():
    srv.terminate()
    try:
        srv.wait(10)
    except Exception:
        srv.kill()


for _ in range(300):
    try:
        urllib.request.urlopen(URL + "/api/gallery", timeout=1).read(); break
    except Exception:
        time.sleep(0.1)
else:
    stop(); sys.exit("máy chủ giả không lên")
rows = json.load(urllib.request.urlopen(URL + "/api/gallery"))["photos"]
HID = [("f%015x" % i, ev) for i, ev in enumerate((E4, E17, E17), 1)]  # ảnh ẩn: không có trong manifest, vẫn mang ev


def base_results(seed):
    """48 kết quả xác định theo selfie (seed = cỡ ảnh): điểm trùng từng cặp, vắt qua 0.40 / 0.28, thêm ảnh ẩn."""
    r = random.Random(seed)
    pick = r.sample(rows, 45)
    res = [dict(id=p[0], b=p[7], ev=p[8], name=p[0] + ".jpg", w=p[1] if isinstance(p[1], int) else 400, h=300, v=None, c="#888888", n_faces=1,
                bbox=[10, 10, 90, 90], t="x") for p in pick]
    for hid, ev in HID:
        res.insert(r.randrange(len(res)), dict(id=hid, b=None, ev=ev, name=hid + ".jpg", w=400, h=300, v=None, c="#888888", n_faces=1, bbox=[10, 10, 90, 90], t="tok" + hid))
    for j, x in enumerate(res):
        x["score"] = round(0.9 - 0.03 * (j // 2), 4)
    return res


def answer(seed):
    res = base_results(seed)
    return dict(query=dict(n_faces=1, n_ignored=0, bbox=[5, 5, 95, 95], width=100, height=100, det_score=.9), results=res, total_matches=len(res),
                truncated=False, timing_ms=dict(decode=1, detect=1, embed=1, compare=1, total=4))


GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


class Stub:
    """Thay /api/search: ghi lại mọi request; hold=True giữ phản hồi tới khi release(); fail = số lần trả 503 trước."""
    def __init__(self, pg, hold=False, fail=0):
        self.hold, self.held, self.reqs, self.fail = hold, [], [], fail
        pg.route("**/api/search*", self.handle)

    def handle(self, route):
        rq = route.request
        item = (route, len(rq.post_data_buffer or b""))
        self.reqs.append((rq.url, item[1]))
        if self.hold:
            self.held.append(item)
        else:
            self.send(item)

    def send(self, item):
        route, seed = item
        if self.fail > 0:
            self.fail -= 1
            return route.fulfill(status=503, content_type="application/json", body='{"detail":"busy"}')
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer(seed)))

    def release(self, i):
        self.send(self.held[i])


class Imgs:
    """Chặn mọi ảnh (/thumbs /previews /i): hold=True thì giữ lại tới khi nhả; ghi thứ tự yêu cầu."""
    def __init__(self, pg, hold=False):
        self.hold, self.held, self.seen = hold, [], []
        pg.route("**/{thumbs,previews,i}/**", self.handle)

    def handle(self, route):
        self.seen.append(route.request.url)
        if self.hold:
            self.held.append(route)
        else:
            route.fulfill(status=200, content_type="image/gif", body=GIF)

    def release(self, n=1):
        for _ in range(n):
            if self.held:
                self.held.pop(0).fulfill(status=200, content_type="image/gif", body=GIF)


def pid(url):
    m = re.search(r"/(?:thumbs|previews)/([^./]+)\.jpg|/i/([^-/]+)-", url)
    return m and (m.group(1) or m.group(2))


def snap(pg):
    return pg.evaluate("""() => ({ main: gMain.flat.map(x => x.id), maybe: gMaybe.flat.map(x => x.id), mi: gMain.items.map(x => x.id), yi: gMaybe.items.map(x => x.id),
        resultsHidden: document.getElementById('results').hidden, title: document.getElementById('s-title').textContent,
        n: data ? data.results.length : -1, evs: data ? [...new Set(data.results.map(x => x.ev))].sort() : [] })""")


def wait_done(pg):
    pg.wait_for_function("document.getElementById('s-loading').hidden && !document.getElementById('s-msg').hidden", timeout=20000)


def wait_for(pg, fn, n=100):
    for _ in range(n):
        if fn():
            return True
        pg.wait_for_timeout(50)
    return False


def fresh(br, hold=False, fail=0, hold_img=False, url="/"):
    ctx = br.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    im = Imgs(pg, hold=False)
    st = Stub(pg, hold=hold, fail=fail)
    pg.goto(URL + url)
    pg.wait_for_selector("#ev-tabs:not([hidden]) button"); pg.wait_for_selector("#vg-all .tile")
    pg.wait_for_timeout(300)
    im.hold = hold_img  # thư viện đã nạp xong bằng ảnh giả; từ đây mới giữ ảnh
    im.seen.clear()
    pg.evaluate("() => { window.__prep = 0; const p = prepare; prepare = async x => { window.__prep++; return p(x); }; }")
    return ctx, pg, st, im, errs


def storage_empty(pg):
    return pg.evaluate("""async () => ({ ls: localStorage.length, ss: sessionStorage.length,
        idb: ('databases' in indexedDB) ? (await indexedDB.databases()).length : 0, cache: 'caches' in self ? (await caches.keys()).length : 0 })""")


try:
    with sync_playwright() as p:
        br = getattr(p, a.browser).launch()

        # 1) không còn chip; tab ngày vẫn còn
        ctx, pg, st, im, errs = fresh(br)
        check(pg.locator(".scope, #scope-hero, #scope-finder, [data-scope]").count() == 0, "không có nút chọn phạm vi trong DOM")
        check(pg.locator("#ev-tabs button").count() == 2, "tab ngày duyệt ảnh vẫn còn")
        check(pg.evaluate("typeof setScope === 'undefined' && typeof scope === 'undefined' && typeof cache === 'undefined' && typeof viewFor === 'undefined'"), "mã chết (scope/cache/viewFor) đã bỏ")

        # 2) một lần tìm = một request không scope; hai ngày chung một danh sách
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        check(len(st.reqs) == 1 and "scope" not in st.reqs[0][0] and st.reqs[0][0].endswith("/api/search"), f"một /api/search không kèm scope: {st.reqs}")
        s = snap(pg)
        check(s["evs"] == [E4, E17] and s["n"] == 48, f"kết quả có cả hai ngày ({s['evs']}, {s['n']})")
        want = answer(S0)["results"]
        ids = [x["id"] for x in want]
        shown = set(s["main"]) | set(s["maybe"])
        exp = {x["id"]: x for x in want if x["score"] >= 0.28}
        check(set(exp) <= shown, f"mọi kết quả ≥ 0.28 đều có mặt trong lưới ({len(set(exp) - shown)} thiếu)")
        both = {exp[i]["ev"] for i in exp}
        check(both == {E4, E17}, f"lưới trộn cả hai ngày ({both})")
        sc = pg.evaluate("gMain.items.map(x => x.score)")
        check(sc and sc == sorted(sc, reverse=True) and all(v >= 0.40 for v in sc), "nhóm chắc chắn xếp điểm giảm dần, đều ≥ 0.40")
        check(pg.evaluate("document.getElementById('status').getAttribute('role')") == "status", "vùng trạng thái có role=status")
        # đổi tab ngày: không gửi request tìm
        pg.evaluate("setEvent('%s', false)" % E17)
        check(len(st.reqs) == 1 and pg.evaluate("curEv") == E17, "đổi ngày duyệt ảnh không gửi /api/search")
        check(not errs, f"không lỗi JS {errs}")
        ctx.close()

        # 3) "Đang tìm" hiện ngay khi chọn ảnh, qua prepare() và suốt vòng máy chủ
        ctx, pg, st, im, errs = fresh(br, hold=True)
        pg.set_input_files("#in-file", selfies[0])
        vis = pg.evaluate("() => { const l = document.getElementById('s-loading'); return !l.hidden && /Đang tìm/.test(l.textContent) && !document.getElementById('finder').hidden; }")
        check(vis, "'Đang tìm' hiện ngay sau khi chọn ảnh")
        check(wait_for(pg, lambda: len(st.held) == 1), "request tới máy chủ đã gửi (đang giữ)")
        check(pg.evaluate("!document.getElementById('s-loading').hidden") and pg.locator("#s-loading").is_visible(), "'Đang tìm' vẫn hiện khi chờ máy chủ")
        check(pg.evaluate("document.getElementById('status').matches('[aria-live=polite], [role=status]')"), "trạng thái có aria-live/role")
        check(pg.locator("#results").is_hidden(), "chưa có lưới trước khi có phản hồi")
        st.release(0); wait_done(pg)
        check(pg.locator("#s-loading").is_hidden(), "'Đang tìm' ẩn khi có kết quả")
        ctx.close()

        # 4) lưới dựng ngay khi phản hồi về dù ảnh còn chờ; ưu tiên + hàng đợi 6
        ctx, pg, st, im, errs = fresh(br, hold_img=True)
        pg.set_input_files("#in-file", selfies[0])
        wait_done(pg)
        tiles = pg.evaluate("""() => [...document.querySelectorAll('#vg-main .tile')].map(b => { const r = b.getBoundingClientRect(), i = b.querySelector('img');
            return { id: b.dataset.id, k: +b.dataset.i, ar: gMain.items[+b.dataset.i].w / gMain.items[+b.dataset.i].h, w: r.width, h: r.height, bg: b.style.background, loading: i.loading, pri: i.fetchPriority, src: !!i.getAttribute('src'), on: i.classList.contains('on') }; })""")
        check(len(tiles) >= 6, f"lưới đã dựng cùng nhịp với phản hồi ({len(tiles)} ô) khi ảnh còn chờ")
        check(not any(t["on"] for t in tiles), "chưa ô nào tải xong ảnh (ảnh đang bị giữ) — lưới vẫn có ô giữ chỗ")
        check(all(t["w"] > 0 and t["h"] > 0 and t["bg"] for t in tiles), "ô giữ chỗ có kích thước và màu nền")
        bad = [t["id"] for t in tiles if abs(t["w"] / t["h"] - t["ar"]) > 0.08]
        check(not bad, f"ô giữ chỗ đúng tỉ lệ w/h ({bad[:3]})")
        top = [t for t in tiles if t["k"] < 12]
        check(top and all(t["pri"] == "high" and t["loading"] == "eager" for t in top), "12 ô điểm cao nhất: fetchpriority=high + loading=eager")
        rest = [t for t in tiles if t["k"] >= 12]
        check(all(t["pri"] != "high" and t["loading"] != "eager" for t in rest), f"các ô còn lại không high, không eager ({len(rest)} ô)")
        check(all(t["pri"] == "low" and t["loading"] == "lazy" for t in rest if t["src"]), "ô ngoài top đã nạp thì low + lazy")
        wait_for(pg, lambda: len(im.held) >= 6)
        pg.wait_for_timeout(400)
        order = pg.evaluate("gMain.items.map(x => x.id)")
        topids = order[:12]
        seen_top = lambda: [pid(u) for u in im.seen if pid(u) in topids]
        check(seen_top() == order[:6], f"đúng 6 ảnh đầu (điểm cao nhất, theo thứ tự) được yêu cầu: {seen_top()} vs {order[:6]}")
        check(all(pid(u) in topids or order.index(pid(u)) >= 12 for u in im.seen if pid(u) in order), "ảnh ngoài top chỉ là ô hạng ≥ 12 (nạp lười)")
        im.release(1)
        check(wait_for(pg, lambda: len(seen_top()) == 7) and seen_top()[6] == order[6], "nhả một ảnh → ảnh điểm kế tiếp được nạp")
        for _ in range(40):  # nhả dần để hàng đợi chạy hết các ô top
            if len(seen_top()) >= 12 and not im.held: break
            if im.held: im.release(1)
            pg.wait_for_timeout(40)
        check(seen_top() == order[:12], f"12 ô đầu được nạp đúng thứ tự điểm ({len(seen_top())})")
        check(not errs, f"không lỗi JS {errs}")
        # cuộn: các hàng sau vẫn nạp lười
        im.hold = False; n0 = len(im.seen)
        pg.evaluate("window.scrollBy(0, 2500)"); pg.wait_for_timeout(600)
        check(len(im.seen) > n0, "cuộn xuống vẫn nạp thêm ảnh (lazy hàng sau)")
        ctx.close()

        # 5) đổi selfie giữa chừng
        ctx, pg, st, im, errs = fresh(br, hold=True)
        pg.set_input_files("#in-file", selfies[0]); wait_for(pg, lambda: len(st.held) == 1)
        pg.set_input_files("#in-file", selfies[1]); wait_for(pg, lambda: len(st.held) == 2)
        check([r[1] for r in st.reqs] == [S0, S1], f"hai selfie → hai request đúng thân: {[r[1] for r in st.reqs]}")
        st.release(0); pg.wait_for_timeout(400)
        check(pg.locator("#s-loading").is_visible() and snap(pg)["n"] == -1, "phản hồi của selfie cũ bị bỏ, vẫn 'Đang tìm'")
        st.release(1); wait_done(pg)
        s = snap(pg)
        check(s["n"] == 48 and s["mi"][:3] == [x for x in pg.evaluate("gMain.items.map(x => x.id)")][:3], "kết quả là của selfie mới")
        ctx.close()

        # 6) thoát trong lúc chờ; kết quả về muộn bị bỏ; selfie không còn trong RAM
        ctx, pg, st, im, errs = fresh(br, hold=True)
        pg.set_input_files("#in-file", selfies[0]); wait_for(pg, lambda: len(st.held) == 1)
        pg.click("#b-all") if pg.locator("#b-all").is_visible() else pg.evaluate("leaveMine()")
        pg.wait_for_timeout(200)
        st.release(0); pg.wait_for_timeout(400)
        check(pg.locator("#results").is_hidden() and pg.locator("#finder").is_hidden() and snap(pg)["n"] == -1, "thoát rồi phản hồi về muộn → không hiện kết quả")
        check(pg.evaluate("prepared === null && file === null"), "thoát: thân selfie đã chuẩn bị bị bỏ")
        ctx.close()

        # 7) lỗi → Thử lại chạy lại tìm (dùng lại thân đã chuẩn bị); chọn ảnh mới bỏ thân cũ; ảnh không hợp lệ
        ctx, pg, st, im, errs = fresh(br, fail=1)
        pg.set_input_files("#in-file", selfies[0])
        pg.wait_for_selector("#b-retry:not([hidden])", timeout=20000)
        check(len(st.reqs) == 1 and pg.evaluate("window.__prep") == 1, "lỗi 503: hiện nút Thử lại sau một request")
        pg.click("#b-retry"); wait_done(pg)
        check(len(st.reqs) == 2 and all("scope" not in r[0] for r in st.reqs) and pg.evaluate("window.__prep") == 1, f"Thử lại: một request nữa, không chuẩn bị lại ảnh: {st.reqs}")
        check(snap(pg)["n"] == 48, "Thử lại ra kết quả")
        pg.set_input_files("#in-file", selfies[1]); wait_done(pg)
        check(pg.evaluate("window.__prep") == 2 and pg.evaluate("prepared.file === file"), "chọn ảnh mới: chuẩn bị lại, thân cũ bị thay")
        bad = os.path.join(tmp, "x.txt"); open(bad, "w").write("hi")
        pg.set_input_files("#in-file", {"name": "x.txt", "mimeType": "text/plain", "buffer": b"hi"})
        pg.wait_for_timeout(300)
        check(pg.evaluate("prepared === null") and "không phải ảnh" in pg.inner_text("#s-title"), "ảnh không hợp lệ: thân đã chuẩn bị bị bỏ")
        # 8) không ghi gì vào bộ nhớ trình duyệt
        s = storage_empty(pg)
        check(s == dict(ls=0, ss=0, idb=0, cache=0), f"không có localStorage/sessionStorage/IndexedDB/Cache API: {s}")
        check(not errs, f"không lỗi JS {errs}")
        ctx.close()

        # 9) ?tune=1 vẫn dựng lưới
        ctx, pg, st, im, errs = fresh(br, url="/?tune=1")
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        check(len(st.reqs) == 1 and pg.locator("#vg-main .tile").count() > 0, "tune: một request, có lưới")
        ctx.close()
        br.close()
finally:
    stop()
    shutil.rmtree(tmp, True)

print("\n%d bước hỏng" % len(fails) if fails else "\ntất cả đạt")
sys.exit(1 if fails else 0)
