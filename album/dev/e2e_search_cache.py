"""Chỉ dùng khi phát triển: thử trình duyệt thật (Playwright) cho bộ nhớ kết quả tìm selfie theo ngày. Tự dựng ảnh tổng hợp + máy chủ giả
(dev/mock_gallery.py --events) và THAY /api/search bằng bản giả xác định, có thể giữ phản hồi để dựng thứ tự về muộn.

  .venv/bin/python -I dev/e2e_search_cache.py [--port 8838] [--browser chromium|webkit]

Kiểm: đổi ngày không gửi lại /api/search; kết quả lọc == kết quả máy chủ tính theo ?scope= (đường lùi); bấm ngày liên tục khi đang tải lên;
đổi selfie giữa chừng; phản hồi lùi về muộn; Back/thoát; ?tune=1. Thoát mã ≠ 0 nếu một bước hỏng."""
import argparse
import json
import os
import random
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


def answer(seed, sc, cap=None):
    res = [x for x in base_results(seed) if sc == "all" or x["ev"] == sc]
    top = res if cap is None else res[:cap]
    return dict(query=dict(n_faces=1, n_ignored=0, bbox=[5, 5, 95, 95], width=100, height=100, det_score=.9), results=top, total_matches=len(res),
                truncated=cap is not None and len(res) > cap, timing_ms=dict(decode=1, detect=1, embed=1, compare=1, total=4))


class Stub:
    def __init__(self, pg, cap=None, hold=False):
        self.cap, self.hold, self.held, self.reqs, self.fail_scoped = cap, hold, [], [], 0
        pg.route("**/api/search*", self.handle)

    def handle(self, route):
        rq = route.request
        sc = (rq.url.split("scope=") + ["all"])[1] if "scope=" in rq.url else "all"
        seed = len(rq.post_data_buffer or b"")
        self.reqs.append((sc, seed))
        item = (route, seed, sc)
        if self.hold:
            self.held.append(item)
        else:
            self.send(item)

    def send(self, item):
        route, seed, sc = item
        if sc != "all" and self.fail_scoped > 0:
            self.fail_scoped -= 1
            return route.fulfill(status=503, content_type="application/json", body='{"detail":"busy"}')
        body = answer(seed, sc, self.cap if sc == "all" else None)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def release(self, i):
        self.send(self.held[i])


GATE = """() => { window.__gates = []; window.__prep = 0; const f = window.fetch, p = prepare; prepare = async x => { window.__prep++; return p(x); };
  window.fetch = async (u, o) => { const r = await f(u, o); if (!String(u).includes('/api/search')) return r; let rel; const g = new Promise(res => rel = res); window.__gates.push(rel);
    return { status: r.status, ok: r.ok, json: async () => { await g; return r.json(); } }; }; }"""
def gates(pg): return pg.evaluate("window.__gates.length")
def open_gate(pg, i): pg.evaluate(f"window.__gates[{i}]()")
def wait_gates(pg, n):
    for _ in range(100):
        if gates(pg) >= n: return
        pg.wait_for_timeout(50)


def snap(pg):
    return pg.evaluate("""() => ({ main: gMain.flat.map(x => x.id), maybe: gMaybe.flat.map(x => x.id), mi: gMain.items.map(x => x.id), yi: gMaybe.items.map(x => x.id),
        resultsHidden: document.getElementById('results').hidden, title: document.getElementById('s-title').textContent, text: document.getElementById('s-text').textContent,
        trunc: document.getElementById('trunc').hidden, n: data ? data.results.length : -1, total: data ? data.total_matches : -1, tr: data ? data.truncated : null })""")


def chip(pg, sc):
    pg.click('#scope-finder button[data-scope="%s"]' % sc)


def wait_done(pg):
    pg.wait_for_function("document.getElementById('s-loading').hidden && !document.getElementById('s-msg').hidden", timeout=20000)


def fresh(br, **kw):
    ctx = br.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("**/{thumbs,previews,i}/**", lambda r: r.fulfill(status=200, content_type="image/gif", body=bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")))
    st = Stub(pg, **kw)
    pg.goto(URL + "/")
    pg.wait_for_selector("#ev-tabs:not([hidden]) button"); pg.wait_for_selector("#vg-all .tile")
    return ctx, pg, st, errs


try:
    with sync_playwright() as p:
        br = getattr(p, a.browser).launch()

        # 1) bộ nhớ hợp lệ: đổi ngày = 0 request, kết quả == đường lùi (máy chủ tính theo ?scope=)
        views = {}
        for mode, cap in (("cache", None), ("fallback", 20)):  # cap=20 < 48: phản hồi "tất cả" bị cắt → phải hỏi máy chủ từng ngày
            ctx, pg, st, errs = fresh(br, cap=cap)
            pg.click('#scope-hero button[data-scope="%s"]' % E17)  # chọn trước ngày: lần tìm đầu vẫn là "tất cả"
            pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
            check(st.reqs[:1] == [("all", S0)], f"{mode}: lần tìm đầu không kèm scope dù đã chọn 17/10: {st.reqs}")
            for sc in (E17, E4, "all", E17, "all", E4):
                n0 = len(st.reqs)
                chip(pg, sc); wait_done(pg)
                s = snap(pg)
                if cap is None:
                    check(len(st.reqs) == n0, f"{mode}: đổi sang {sc} không gửi /api/search")
                views.setdefault((mode, sc), s)
                check(s == views[(mode, sc)], f"{mode}: {sc} lần nào cũng cùng kết quả")
            if mode == "fallback":
                check(len(st.reqs) == 3 and {r[0] for r in st.reqs[1:]} == {E4, E17}, f"fallback: mỗi ngày chỉ hỏi máy chủ một lần, về 'tất cả' không hỏi: {st.reqs}")
            check(not errs, f"{mode}: không lỗi JS {errs}")
            ctx.close()
        for sc in (E4, E17):
            c, f = views[("cache", sc)], views[("fallback", sc)]
            check(c["mi"] == f["mi"] and c["yi"] == f["yi"] and c["main"] == f["main"] and c["maybe"] == f["maybe"], f"{sc}: lọc phía khách == kết quả máy chủ (ô, khung, nhóm chắc chắn/có thể)")
            check(c["total"] == f["total"] == c["n"] and not c["tr"] and not f["tr"], f"{sc}: total_matches = số kết quả, truncated=false ({c['total']},{f['total']})")
            exp = [x for x in base_results(S0) if x["ev"] == sc]
            check(c["n"] == len(exp), f"{sc}: số kết quả khớp phía giả {c['n']} vs {len(exp)}")
        check(views[("cache", "all")]["n"] == 48 and views[("fallback", "all")]["n"] == 20 and views[("fallback", "all")]["tr"], "'tất cả': đủ khi hợp lệ, bị cắt giữ nguyên siêu dữ liệu khi cắt")
        check(views[("cache", E4)]["mi"] and views[("cache", E17)]["mi"] and views[("cache", E4)]["n"] + views[("cache", E17)]["n"] == 48, "hai ngày cộng lại đủ 48 (kể cả ảnh ẩn ngoài manifest)")

        # 1b) bộ nhớ không dùng lại khi thiếu/ev hỏng, hoặc thiếu truncated
        for label, tweak in (("ev thiếu", lambda b: b["results"][5].pop("ev")), ("ev lạ", lambda b: b["results"][5].update(ev="1999-01-01")), ("thiếu truncated", lambda b: b.pop("truncated"))):
            ctx, pg, st, errs = fresh(br)
            st_send = st.send
            def send(item, st=st, tweak=tweak):
                route, seed, sc = item
                b = answer(seed, sc, None)
                if sc == "all":
                    tweak(b)
                route.fulfill(status=200, content_type="application/json", body=json.dumps(b))
            st.send = send
            pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
            chip(pg, E17); wait_done(pg)
            check([r[0] for r in st.reqs] == ["all", E17], f"{label}: không dùng lại, hỏi máy chủ theo ?scope=: {st.reqs}")
            chip(pg, "all"); wait_done(pg)
            check(len(st.reqs) == 2 and snap(pg)["n"] in (48,), f"{label}: về 'tất cả' hiện đúng phản hồi gốc không hỏi lại ({st.reqs})")
            ctx.close()

        # 1c) ?tune=1
        ctx, pg, st, errs = fresh(br)
        pg.goto(URL + "/?tune=1"); pg.wait_for_selector("#vg-all .tile")
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        chip(pg, E17); wait_done(pg)
        check(len(st.reqs) == 1 and "ô /" in pg.inner_text("#tcount"), f"tune: đổi ngày không gửi lại; {pg.inner_text('#tcount')}")
        check(f"/ {snap(pg)['n']} ảnh" in pg.inner_text("#tcount"), "tune: đếm theo kết quả đã lọc")
        ctx.close()

        # 2) bấm ngày liên tục khi đang tải lên / xử lý
        ctx, pg, st, errs = fresh(br, hold=True)
        pg.set_input_files("#in-file", selfies[0])
        for _ in range(100):
            if st.held: break
            pg.wait_for_timeout(50)
        for sc in (E17, E4, "all", E17, E4, E17):
            chip(pg, sc)
        check(len(st.reqs) == 1, f"bấm ngày liên tục khi chờ: vẫn một request ({st.reqs})")
        st.release(0); wait_done(pg)
        s = snap(pg)
        check(len(st.reqs) == 1 and s["n"] == len([x for x in base_results(S0) if x["ev"] == E17]), f"phản hồi về → hiện đúng ngày cuối (17/10) {s['n']}")
        check(pg.get_attribute('#scope-finder button[data-scope="%s"]' % E17, "aria-pressed") == "true", "chip 17/10 đang bật")
        ctx.close()

        # 3) đổi selfie giữa chừng: phản hồi cũ về muộn bị bỏ
        ctx, pg, st, errs = fresh(br, hold=True)
        pg.set_input_files("#in-file", selfies[0])
        for _ in range(100):
            if len(st.held) == 1: break
            pg.wait_for_timeout(50)
        pg.set_input_files("#in-file", selfies[1])
        for _ in range(100):
            if len(st.held) == 2: break
            pg.wait_for_timeout(50)
        st.release(0); pg.wait_for_timeout(400)  # phản hồi của selfie cũ về trước
        check(pg.evaluate("data") is None and not pg.is_hidden("#s-loading"), "selfie cũ về muộn: không hiện, vẫn đang tải")
        st.release(1); wait_done(pg)
        check(snap(pg)["n"] == 48 and pg.evaluate("data.results[0].id") == base_results(S1)[0]["id"], "chỉ kết quả của selfie mới được hiện")
        n0 = len(st.reqs); chip(pg, E4); wait_done(pg)
        check(len(st.reqs) == n0 and pg.evaluate("data.results.every(x => x.ev === '%s')" % E4), "sau đó đổi ngày dùng bộ nhớ của selfie mới")
        ctx.close()

        # 4) phản hồi theo ngày (đường lùi) về muộn
        ctx, pg, st, errs = fresh(br, cap=20)
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        st.hold = True
        chip(pg, E17)
        for _ in range(100):
            if len(st.held) == 1: break
            pg.wait_for_timeout(50)
        chip(pg, "all"); pg.wait_for_timeout(100)
        check(len(st.reqs) == 2 and snap(pg)["n"] == 20 and pg.is_hidden("#s-loading"), "đường lùi: về 'tất cả' hiện ngay từ bộ nhớ, không hỏi thêm")
        st.release(0); pg.wait_for_timeout(500)
        check(snap(pg)["n"] == 20 and pg.get_attribute('#scope-finder button[data-scope="all"]', "aria-pressed") == "true", "phản hồi 17/10 về muộn không đè lên 'tất cả'")
        chip(pg, E17)
        for _ in range(100):
            if len(st.held) == 2: break
            pg.wait_for_timeout(50)
        st.release(1); wait_done(pg)
        e17 = len([x for x in base_results(S0) if x["ev"] == E17])
        check(len(st.reqs) == 3 and snap(pg)["n"] == e17, "17/10 hỏi lại sau khi yêu cầu cũ bị thay → hiện đúng")
        chip(pg, "all"); chip(pg, E17)
        check(len(st.reqs) == 3 and snap(pg)["n"] == e17, "17/10 đã nhận → các lần sau dùng bộ nhớ, không hỏi")
        chip(pg, E4)
        for _ in range(100):
            if len(st.held) == 3: break
            pg.wait_for_timeout(50)
        chip(pg, E17)  # đang chờ 4/10 mà đổi sang ngày đã có: hiện ngay, yêu cầu 4/10 bị bỏ
        check(len(st.reqs) == 4 and snap(pg)["n"] == e17 and pg.is_hidden("#s-loading"), "đang chờ theo ngày mà chọn ngày đã có → hiện ngay")
        chip(pg, E4)
        for _ in range(100):
            if len(st.held) == 4: break
            pg.wait_for_timeout(50)
        st.release(2); pg.wait_for_timeout(300)
        check(not pg.is_hidden("#s-loading"), "phản hồi 4/10 cũ (đã bị thay) về muộn: không hiện")
        st.release(3); wait_done(pg)
        check(snap(pg)["n"] == len([x for x in base_results(S0) if x["ev"] == E4]), "đường lùi: yêu cầu 4/10 mới nhất về → hiện 4/10")
        ctx.close()

        # 5) Back / thoát khi đang chờ và sau khi có kết quả
        ctx, pg, st, errs = fresh(br, hold=True)
        pg.set_input_files("#in-file", selfies[0])
        for _ in range(100):
            if st.held: break
            pg.wait_for_timeout(50)
        pg.go_back(); pg.wait_for_selector("#vg-all .tile")
        st.release(0); pg.wait_for_timeout(500)
        check(pg.evaluate("data") is None and pg.is_hidden("#results") and not pg.is_hidden("#all"), "Back khi đang tìm: phản hồi về muộn không hiện kết quả")
        st.hold = False
        pg.set_input_files("#in-file", selfies[1]); wait_done(pg)
        check(len(st.reqs) == 2 and st.reqs[1] == ("all", S1), f"sau Back: chọn selfie mới → tìm mới ({st.reqs})")
        pg.go_back(); pg.wait_for_selector("#vg-all .tile")
        check(pg.evaluate("cache") is None and pg.evaluate("data") is None, "thoát: bộ nhớ kết quả bị huỷ")
        # chọn lại selfie vẫn sạch: không dùng lại kết quả cũ
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        check(len(st.reqs) == 3 and snap(pg)["n"] == 48 and pg.evaluate("data.results[0].id") == base_results(S0)[0]["id"], "vào lại → tìm mới, kết quả của selfie mới")
        # chọn file không phải ảnh → huỷ bộ nhớ
        pg.set_input_files("#in-file", files=[{"name": "a.txt", "mimeType": "text/plain", "buffer": b"hi"}])
        pg.wait_for_timeout(300)
        check(pg.evaluate("cache") is None and pg.evaluate("data") is None, "chọn file không phải ảnh → huỷ bộ nhớ")
        check(not errs, f"không lỗi JS {errs}")
        ctx.close()

        # 6) ngày không có kết quả: lời nhắn như cũ
        ctx, pg, st, errs = fresh(br)
        orig_answer = answer
        def only4(route, st=st):
            rq = route.request; seed = len(rq.post_data_buffer or b"")
            b = answer(seed, "all"); b["results"] = [x for x in b["results"] if x["ev"] == E4]; b["total_matches"] = len(b["results"])
            route.fulfill(status=200, content_type="application/json", body=json.dumps(b))
        pg.unroute("**/api/search*"); pg.route("**/api/search*", only4)
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        chip(pg, E17); wait_done(pg)
        check("Chưa có ảnh nào khớp trong ngày 17/10" in pg.inner_text("#s-text") and pg.is_hidden("#results"), f"ngày rỗng: lời nhắn như cũ: {pg.inner_text('#s-text')}")
        chip(pg, "all"); wait_done(pg)
        check(not pg.is_hidden("#results") and "Đã tìm xong" in pg.inner_text("#s-title"), "ngày rỗng rồi về 'tất cả': kết quả hiện lại")
        ctx.close()

        # 7) thân phản hồi về chậm (đã có header, r.json() chờ): đổi selfie / bấm ngày trong lúc đó
        ctx, pg, st, errs = fresh(br)
        pg.evaluate(GATE)
        pg.set_input_files("#in-file", selfies[0]); wait_gates(pg, 1)
        pg.set_input_files("#in-file", selfies[1]); wait_gates(pg, 2)
        open_gate(pg, 0); pg.wait_for_timeout(400)  # thân selfie cũ về sau khi đã đổi selfie
        check(pg.evaluate("data") is None and not pg.is_hidden("#s-loading"), "thân chậm của selfie cũ về sau khi đổi selfie: bỏ")
        for sc in (E17, E4, E17):
            chip(pg, sc)
        open_gate(pg, 1); wait_done(pg)
        check(len(st.reqs) == 2 and snap(pg)["n"] == len([x for x in base_results(S1) if x["ev"] == E17]) and pg.evaluate("data.results.every(x => x.ev === '%s')" % E17),
              "thân chậm của selfie mới + bấm ngày khi chờ: hiện ngày cuối, không yêu cầu thêm")
        ctx.close()
        ctx, pg, st, errs = fresh(br, cap=20)  # thân theo ngày về chậm, trong lúc đó bấm 'tất cả' / ngày khác
        pg.evaluate(GATE)
        pg.set_input_files("#in-file", selfies[0]); wait_gates(pg, 1); open_gate(pg, 0); wait_done(pg)
        chip(pg, E17); wait_gates(pg, 2)
        chip(pg, "all")
        check(snap(pg)["n"] == 20 and pg.is_hidden("#s-loading"), "thân theo ngày chưa về: bấm 'tất cả' hiện ngay")
        open_gate(pg, 1); pg.wait_for_timeout(400)
        check(snap(pg)["n"] == 20 and pg.get_attribute('#scope-finder button[data-scope="all"]', "aria-pressed") == "true", "thân 17/10 về sau khi đã đổi sang 'tất cả': không đè")
        chip(pg, E4); wait_gates(pg, 3); chip(pg, E17); wait_gates(pg, 4); open_gate(pg, 3); wait_done(pg)
        check(snap(pg)["n"] == len([x for x in base_results(S0) if x["ev"] == E17]) and pg.evaluate("__prep") == 1, f"nhiều yêu cầu theo ngày chồng nhau: hiện 17/10; selfie chỉ chuẩn bị 1 lần ({pg.evaluate('__prep')})")
        open_gate(pg, 2); pg.wait_for_timeout(300)
        check(snap(pg)["n"] == len([x for x in base_results(S0) if x["ev"] == E17]), "thân 4/10 về muộn không đè 17/10")
        ctx.close()

        # 8) thử lại sau khi yêu cầu theo ngày hỏng
        ctx, pg, st, errs = fresh(br, cap=20)
        pg.evaluate(GATE)
        pg.set_input_files("#in-file", selfies[0]); wait_gates(pg, 1); open_gate(pg, 0); wait_done(pg)
        st.fail_scoped = 1
        chip(pg, E17); wait_gates(pg, 2); open_gate(pg, 1)
        pg.wait_for_selector("#b-retry:not([hidden])", timeout=10000)
        check("Đang đông khách" in pg.inner_text("#s-title"), "yêu cầu theo ngày 503 → báo bận có nút thử lại")
        pg.click("#b-retry"); wait_gates(pg, 3); open_gate(pg, 2); wait_done(pg)
        check([r[0] for r in st.reqs] == ["all", E17, E17] and all(r[1] == S0 for r in st.reqs) and snap(pg)["n"] == len([x for x in base_results(S0) if x["ev"] == E17]),
              f"thử lại → chỉ hỏi lại theo ngày (cùng thân selfie), hiện đúng: {st.reqs}")
        check(pg.evaluate("__prep") == 1, "thử lại không chuẩn bị lại selfie")
        ctx.close()

        # 9) bộ nhớ hết hạn sau 10 phút (đồng hồ tiêm)
        ctx, pg, st, errs = fresh(br)
        pg.set_input_files("#in-file", selfies[0]); wait_done(pg)
        pg.evaluate("window.__o = clock; clock = () => __o() + 9 * 60 * 1000")
        chip(pg, E17); wait_done(pg)
        check(len(st.reqs) == 1 and snap(pg)["n"] == len([x for x in base_results(S0) if x["ev"] == E17]), "9 phút: vẫn dùng bộ nhớ, 0 request")
        pg.evaluate("clock = () => __o() + 10 * 60 * 1000 + 5")
        chip(pg, E4); wait_done(pg)
        check([r[0] for r in st.reqs] == ["all", "all"] and snap(pg)["n"] == len([x for x in base_results(S0) if x["ev"] == E4]), f"quá 10 phút: đổi ngày → tìm lại không scope rồi lọc: {st.reqs}")
        chip(pg, E17); chip(pg, "all"); wait_done(pg)
        check(len(st.reqs) == 2 and snap(pg)["n"] == 48, "sau khi làm mới: lại dùng bộ nhớ")
        pg.evaluate("clock = () => __o() + 25 * 60 * 1000")
        chip(pg, E4); wait_done(pg)
        check(len(st.reqs) == 3, "hết hạn lần nữa → tìm lại")
        check(not errs, f"không lỗi JS {errs}")
        ctx.close()
        br.close()
finally:
    stop()
    import shutil
    shutil.rmtree(tmp, True)
print("\nHỎNG %d bước" % len(fails) if fails else "\nTất cả đạt")
sys.exit(1 if fails else 0)
