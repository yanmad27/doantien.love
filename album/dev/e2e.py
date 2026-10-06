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
ap.add_argument("--photos", nargs="*", help="tải lên + Xử lý trước (backend thật, thư mục dữ liệu trống); nếu máy chủ có khoá thì đăng nhập bằng --manage-token")
ap.add_argument("--setup-only", action="store_true", help="chỉ tải lên + Xử lý (--photos) rồi thoát")
ap.add_argument("--test-throttle", action="store_true", help="thử chặn đăng nhập sai quá nhiều lần (429) — chỉ nên chạy một lần: máy chủ thật sẽ khoá đăng nhập một lúc")
ap.add_argument("--mock", action="store_true", help="đang chạy dev/mock_gallery.py: kiểm thêm log của 'CDN', đường lùi và /manage có mã")
ap.add_argument("--manage-token", default="", help="máy chủ (--url) chạy với token này: thử trang đăng nhập /manage (dùng mã giả, vd. test-token)")
ap.add_argument("--big-url", default="", help="máy chủ giả chạy với --search-n lớn (~1900): thử 'ảnh của bạn' có hàng nghìn kết quả")
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


def capture_search(pg):
    """thu thân JSON của /api/search ngay khi về. Chromium đẩy thân response ra khỏi bộ nhớ nếu đọc muộn (đọc trong route);
    WebKit lại treo khi route.fetch() (đọc ở sự kiện response). Trả (danh sách kết quả, hàm gỡ)"""
    got = []
    if webkit:
        def on_resp(r):
            if r.url.endswith("/api/search"):
                got.append(r.json())
        pg.on("response", on_resp)
        return got, lambda: pg.remove_listener("response", on_resp)
    def on_route(route):
        r = route.fetch(); got.append(r.json()); route.fulfill(response=r)
    pg.route("**/api/search", on_route)
    return got, lambda: pg.unroute("**/api/search", on_route)


def load_rows(url):
    """dòng manifest đã bỏ id trùng (giữ dòng đầu), như client"""
    with urllib.request.urlopen(url + "/api/gallery") as f:
        rows = json.load(f)["photos"]
    seen, out = set(), []
    for p in rows:
        if p[0] not in seen:
            seen.add(p[0]); out.append(p)
    return rows, out


def group_tiles(rows):
    """gộp theo b (hợp đồng §11): ô nằm ở khung đầu tiên của chuỗi, mỗi ô = danh sách id các khung theo thứ tự manifest"""
    by_b, tiles = {}, []
    for p in rows:
        b = p[7] if len(p) > 7 else None
        if b and b in by_b:
            by_b[b].append(p[0])
        else:
            tiles.append([p[0]])
            if b:
                by_b[b] = tiles[-1]
    return tiles, by_b


def expect_results(resp, rows):
    """kết quả mong đợi: mỗi chuỗi một ô, điểm = khung khớp nhất, nhóm theo điểm đó; lightbox gồm MỌI khung của chuỗi"""
    _, by_b = group_tiles(rows)
    bman = {p[0]: (p[7] if len(p) > 7 else None) for p in rows}  # `b` theo manifest: client dùng nó (kết quả tìm kiếm có thể lệch/cũ)
    best, bof = {}, {}
    for r in resp["results"]:
        best[r["id"]] = max(best.get(r["id"], -1), r["score"]); bof[r["id"]] = bman.get(r["id"], r.get("b"))
    seen, tiles = {}, []
    for pid, s in sorted(best.items(), key=lambda kv: -kv[1]):
        b = bof[pid]
        key = b or pid
        if key in seen:
            continue
        frames = by_b.get(b, [pid]) if b else [pid]
        seen[key] = 1
        tiles.append(dict(key=key, score=s, frames=frames, head=frames[0]))
    main = [x for x in tiles if x["score"] >= 0.40]
    maybe = [x for x in tiles if 0.28 <= x["score"] < 0.40]
    return main, maybe


def edge_check(pg, label):
    """cuộn hết thư viện: mọi ô nằm trọn trong khung chứa (không tràn phải), kích thước hữu hạn > 0; trả về độ tràn lớn nhất (px)"""
    vh = pg.evaluate("innerHeight")
    total = pg.evaluate("document.documentElement.scrollHeight")
    worst, y, seen_tiles = -999.0, 0, 0
    while True:
        pg.evaluate("y => scrollTo(0, y)", y)
        pg.wait_for_timeout(30)
        r = pg.evaluate("""() => { const c = document.getElementById('vg-all').getBoundingClientRect(); let worst = -999, n = 0, bad = 0;
          for (const b of document.querySelectorAll('#vg-all .tile')) { const q = b.getBoundingClientRect(); n++;
            worst = Math.max(worst, q.right - c.right, c.left - q.left);
            if (!(q.width > 4 && q.height > 4 && isFinite(q.width) && isFinite(q.height))) bad++; }
          return { worst, n, bad, rows: [...document.querySelectorAll('#vg-all .vrow')].map(r => { const ts = [...r.children], a = ts[0].getBoundingClientRect(), z = ts[ts.length - 1].getBoundingClientRect(); return z.right - a.left; }), w: c.width }; }""")
        worst = max(worst, r["worst"]); seen_tiles += r["n"]
        check(r["bad"] == 0, f"{label}: có ô rộng/cao không hợp lệ (0, NaN, quá nhỏ)")
        if y >= total - vh:
            break
        y += vh * 3.5
    check(worst <= 0.5, f"{label}: ô tràn ra ngoài khung chứa {worst:.1f}px")
    return worst


def collect_ids(pg, root, label, reverse=False, stats=None):
    """cuộn từ đầu tới cuối vùng `root`, gom (chỉ số, id) của mọi ô từng được dựng. Mỗi lần chụp DOM: không id nào xuất hiện hai lần, id khớp chỉ số"""
    top, h = pg.evaluate("r => { const b = document.querySelector(r).getBoundingClientRect(); return [b.top + scrollY, b.height]; }", root)
    vh = pg.evaluate("innerHeight")
    seen, y = {}, (max(0, top + h - vh) if reverse else max(0, top - 100))
    while True:
        pg.evaluate("y => scrollTo(0, y)", y)
        pg.wait_for_timeout(40)
        snap3 = pg.evaluate("r => [...document.querySelectorAll(r + ' .tile')].map(b => [+b.dataset.i, b.dataset.id, +((b.querySelector('.stack') || {}).textContent || '+0').slice(1)])", root)
        snap = [x[:2] for x in snap3]
        ids = [x[1] for x in snap]
        check([x[0] for x in snap] == sorted(x[0] for x in snap), f"{label}: thứ tự DOM của các ô phải đúng thứ tự hiển thị (Tab/trình đọc màn hình)")
        if stats is not None:
            stats["dom"] = max(stats.get("dom", 0), len(snap))
            for i, _pid, nstack in snap3:
                stats.setdefault("frames", {})[i] = 1 + nstack
        check(len(ids) == len(set(ids)), f"{label}: có ảnh xuất hiện 2 lần trong DOM: {[i for i in set(ids) if ids.count(i) > 1][:3]}")
        for i, pid in snap:
            check(seen.setdefault(i, pid) == pid, f"{label}: ô số {i} đổi id giữa các lần chụp")
        if reverse:
            if y <= max(0, top - 100):
                break
            y = max(max(0, top - 100), y - vh * 0.8)
        else:
            if y >= top + h - vh:
                break
            y += vh * 0.8
    check(sorted(seen) == list(range(len(seen))), f"{label}: chỉ số ô có khoảng trống")
    return [seen[i] for i in sorted(seen)]


WALK = """n => { const cur = () => document.getElementById('lb-save').getAttribute('href').split('/').pop();
  const key = k => window.dispatchEvent(new KeyboardEvent('keydown', { key: k }));
  const fwd = [cur()]; for (let i = 0; i < n - 1; i++) { key('ArrowRight'); fwd.push(cur()); }
  const countAtEnd = document.getElementById('lb-count').textContent; key('ArrowRight'); const afterEnd = cur();
  const back = [cur()]; for (let i = 0; i < n - 1; i++) { key('ArrowLeft'); back.push(cur()); }
  key('ArrowLeft'); const afterStart = cur();
  return { fwd, back, afterEnd, afterStart, countAtEnd }; }"""


def walk_check(pg, expected, label):
    """từ ảnh đầu: n−1 lần Tiếp đi qua n id khác nhau đúng thứ tự, không vòng lại; Trước đi ngược đúng lối cũ"""
    for pat in ("**/*-2000.webp", "**/previews/*"):  # chặn tải ảnh 2000 px trong lúc bấm liên tục (abort: không để lại bản giả trong cache trình duyệt)
        pg.route(pat, lambda r, _b=None: r.abort())
    n = len(expected)
    check(pg.evaluate("document.getElementById('lb-save').getAttribute('href').split('/').pop()") == expected[0], f"{label}: phải bắt đầu ở ảnh đầu")
    w = pg.evaluate(WALK, n)
    pg.unroute("**/*-2000.webp"); pg.unroute("**/previews/*")
    check(len(w["fwd"]) == n and len(set(w["fwd"])) == n, f"{label}: {n - 1} lần Tiếp phải qua {n} id khác nhau, được {len(set(w['fwd']))}")
    check(w["fwd"] == expected, f"{label}: thứ tự Tiếp khác thứ tự hiển thị")
    check(w["afterEnd"] == expected[-1], f"{label}: Tiếp ở ảnh cuối không được vòng lại")
    check(w["back"] == expected[::-1] and w["afterStart"] == expected[0], f"{label}: Trước phải đi ngược đúng lối cũ")
    check(w["countAtEnd"].replace(".", "") == f"{n} / {n}", f"{label}: đếm ở ảnh cuối: {w['countAtEnd']}")
    print(f"{label}: Tiếp ×{n - 1} → {len(set(w['fwd']))}/{n} id khác nhau đúng thứ tự, không vòng; Trước ×{n - 1} đi ngược đúng lối cũ")


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
        if pg.locator("#login-form").count():  # máy chủ có khoá: đăng nhập như chủ nhà
            check(a.manage_token, "máy chủ đòi mã chủ nhà: cần --manage-token")
            pg.fill("#token-in", a.manage_token); pg.click("#login-btn")
        pg.wait_for_function("document.getElementById('disk') && document.getElementById('disk').textContent.includes('GB')", timeout=30000)
        pg.set_input_files("#pick", a.photos)
        pg.wait_for_function("document.getElementById('upsum').textContent.includes('Hoàn tất')", timeout=300000)
        pg.click("#proc")
        pg.wait_for_function("document.getElementById('pmsg').textContent.includes('Hoàn tất')", timeout=900000)
        print("đã tải lên + xử lý:", pg.inner_text("#ptiles").replace("\n", " "), "|", pg.inner_text("#upchips").replace("\n", ", "))
        ctx.close()
        if a.setup_only:
            b.close(); sys.exit(0)

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
    raw_rows, rows = load_rows(a.url)
    raw_ids = [p[0] for p in raw_rows]
    uniq_ids = [p[0] for p in rows]
    tiles, by_b = group_tiles(rows)
    heads_ids = [x[0] for x in tiles]
    flat = [i for x in tiles for i in x]
    at, k = [], 0
    for x in tiles:
        at.append(k); k += len(x)
    tile_of = {pid: ti for ti, x in enumerate(tiles) for pid in x}
    T = len(tiles)
    check(sorted(flat) == sorted(uniq_ids), "các khung của mọi ô phải đúng bằng manifest, mỗi id một lần")
    check(n_all == len(uniq_ids), f"số ảnh hiển thị ({n_all}) phải bằng số id khác nhau trong manifest ({len(uniq_ids)})")
    if a.mock:
        with urllib.request.urlopen(a.url + "/__mock/dups") as f:
            dups = json.load(f)
        if dups["manifest"]:
            check(len(raw_ids) > len(uniq_ids), "mock --dup: manifest phải có dòng trùng")
            print(f"manifest có {len(raw_ids)} dòng nhưng {len(uniq_ids)} id khác nhau (trùng: {len(raw_ids) - len(uniq_ids)}); header hiện {n_all}; {T} ô sau khi gộp chuỗi liên thanh")
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
    seen, max_dom, max_img, seen_ids, tstats = set(), 0, 0, {}, {}
    total_h = pg.evaluate("document.documentElement.scrollHeight")
    y, step, shot_mid = 0, pg.evaluate("innerHeight * 0.8"), False
    while True:
        pg.evaluate("y => scrollTo(0, y)", y)
        pg.wait_for_timeout(35)
        r = pg.evaluate("""() => { const vh = innerHeight, s = []; for (const b of document.querySelectorAll('#vg-all .tile')) { const q = b.getBoundingClientRect(); if (q.bottom > 0 && q.top < vh) s.push(+b.dataset.i); }
          return { s, dom: document.querySelectorAll('#vg-all .tile').length, img: document.querySelectorAll('#vg-all .tile img[src]').length,
                   ids: [...document.querySelectorAll('#vg-all .tile')].map(b => [+b.dataset.i, b.dataset.id, +((b.querySelector('.stack') || {}).textContent || '+0').slice(1)]) }; }""")
        ids = [x[1] for x in r["ids"]]
        check([x[0] for x in r["ids"]] == sorted(x[0] for x in r["ids"]), "cuộn: thứ tự DOM của các ô phải đúng thứ tự hiển thị")
        for i, pid, ns in r["ids"]:
            tstats[i] = 1 + ns
        check(len(ids) == len(set(ids)), "cuộn: có ảnh xuất hiện 2 lần trong DOM")
        for i, pid, _ns in r["ids"]:
            check(seen_ids.setdefault(i, pid) == pid and heads_ids[i] == pid, f"cuộn: ô {i} sai id so với manifest")
        seen.update(r["s"]); max_dom = max(max_dom, r["dom"]); max_img = max(max_img, r["img"])
        if not shot_mid and y > total_h * 0.4:
            settle(pg); shot(pg, "2-gallery-mid-scroll.png"); shot_mid = True
        if y >= total_h - pg.evaluate("innerHeight"):
            break
        y += step
    settle(pg)
    check(len(seen) == T, f"cuộn qua phải thấy đủ {T} ô, mới thấy {len(seen)}")
    check(max_dom < 400, f"số ô trong DOM phải bị chặn, đang {max_dom}")
    check(len(seen_ids) == len(set(seen_ids.values())) == T and sorted(seen_ids) == list(range(T)),
          f"mọi ô đúng 1 lần, không khoảng trống: dựng {len(seen_ids)}, id khác nhau {len(set(seen_ids.values()))}, ô mong đợi {T}")
    check(all(tstats[i] == len(tiles[i]) for i in range(T)) and sum(tstats.values()) == n_all,
          f"số khung theo huy hiệu +N phải khớp từng chuỗi và cộng đủ {n_all} ảnh: {sum(tstats.values())}")
    print(f"không trùng ảnh: {len(seen_ids)} ô = {len(set(seen_ids.values()))} id khác nhau, chỉ số 0..{T - 1} liền mạch; huy hiệu +N cộng lại {sum(tstats.values())} khung = {n_all} ảnh trong manifest ({sum(1 for x in tiles if len(x) > 1)} chuỗi liên thanh)")
    # cuộn ngược (dựng/gỡ lại các hàng), rồi đổi cỡ màn hình (tính lại bố cục): vẫn không trùng
    back_ids = collect_ids(pg, "#vg-all", "cuộn ngược", reverse=True)
    check(back_ids == heads_ids, "cuộn ngược: danh sách ô phải đúng")
    for vw, vhh in ((360, 640), (844, 390), (390, 844)):
        pg.set_viewport_size({"width": vw, "height": vhh})
        pg.wait_for_timeout(300)
        ids_r = collect_ids(pg, "#vg-all", f"đổi cỡ {vw}×{vhh}")
        check(ids_r == heads_ids, f"đổi cỡ {vw}×{vhh}: danh sách ô phải đúng ({len(ids_r)} vs {T})")
    print("cuộn ngược + đổi cỡ (360×640, 844×390, 390×844): mỗi lần chụp DOM không id nào lặp, và đủ", T, "ô")
    res_edge = []
    for vw in (390, 520, 760, 920, 932, 1280):  # khoảng cách giữa ô do JS đặt: không ô nào tràn ra khung ở bất kỳ bề ngang nào
        pg.set_viewport_size({"width": vw, "height": 844})
        pg.wait_for_timeout(300)
        res_edge.append((vw, round(edge_check(pg, f"bề ngang {vw}"), 1)))
    print("mép phải ô cuối mỗi hàng ≤ khung chứa (độ tràn lớn nhất, px; âm/0 = khít):", res_edge)
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(300)
    pg.evaluate("scrollTo(0, 0)")
    pg.wait_for_selector('#vg-all .tile[data-i="0"]')
    pg.evaluate("document.querySelector('#vg-all .tile[data-i=\"0\"]').click()")
    pg.wait_for_selector("#lb:not([hidden])")
    walk_check(pg, flat, "lightbox cả album (mọi khung của mọi ô)")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#lb", state="hidden")
    print(f"cuộn hết: thấy {len(seen)}/{T} ô, DOM tối đa {max_dom} ô, ô đang giữ ảnh tối đa {max_img}")
    pg.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
    settle(pg)
    shot(pg, "3-gallery-bottom.png")
    check(not pg.is_visible("#pill"), "chân trang hiện ra / đang cuộn xuống: nút nổi phải ẩn (không che chân trang)")
    pg.evaluate("scrollBy(0, -500)"); pg.wait_for_timeout(300)  # cuộn lên: hiện lại
    check(pg.is_visible("#pill") and pg.is_visible("#pill-find"), "cuộn lên: nút nổi 'Tìm ảnh của bạn' phải hiện")
    pg.wait_for_timeout(450); shot(pg, "3b-pill-scroll-up.png")
    pg.evaluate("scrollBy(0, 400)"); pg.wait_for_timeout(300)
    check(not pg.is_visible("#pill"), "cuộn xuống: nút nổi ẩn")
    print("nút nổi: ẩn khi cuộn xuống và khi chân trang hiện; hiện lại khi cuộn lên")
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
    check(pg.text_content("#lb-count").replace(".", "").startswith(f"{at[idx] + 1} /"), "đếm ảnh trong lightbox: " + pg.text_content("#lb-count"))
    check(pg.text_content("#lb-count").endswith(pg.inner_text("#count").split()[0]), "lightbox đi qua cả album: " + pg.text_content("#lb-count"))
    pg.wait_for_function("(() => { const i = document.getElementById('lb-img'); return i.complete && i.naturalWidth > 0 && /-2000\\.webp|\\/previews\\//.test(i.currentSrc); })()", timeout=30000)
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
    pg.wait_for_function(f"document.getElementById('lb-count').textContent.replace(/\\./g, '').startsWith('{at[idx] + 2} /')")
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
    got_i = pg.evaluate("document.activeElement && document.activeElement.classList.contains('tile') ? +document.activeElement.dataset.i : -1")
    check(webkit or got_i == tile_of[flat[at[idx] + 1]], f"đóng lightbox: focus về ô chứa ảnh đang xem (được {got_i}, cần {tile_of[flat[at[idx] + 1]]}, idx {idx})")
    y_before = pg.evaluate("scrollY")

    # 4. tìm bằng selfie → thu hẹp
    got, stop_cap = capture_search(pg)
    pg.set_input_files("#in-file", a.selfie)
    pg.wait_for_selector("#results:not([hidden])", timeout=60000)
    stop_cap()
    resp = got[0]
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
    # mỗi chuỗi liên thanh một ô; mỗi ảnh một khung; hai nhóm không giao nhau; ô ở nhóm cao hơn theo khung khớp nhất
    exp_main, exp_maybe = expect_results(resp, rows)
    stats, stats_y = {}, {}
    main_ids = collect_ids(pg, "#vg-main", "Ảnh của bạn", stats=stats)
    maybe_ids = []
    if exp_maybe:
        pg.click("#maybe-toggle")
        maybe_ids = collect_ids(pg, "#vg-maybe", "Có thể cũng là bạn", stats=stats_y)
        check(all(stats_y["frames"][i] == len(x["frames"]) for i, x in enumerate(exp_maybe)), "huy hiệu +N của ô 'Có thể cũng là bạn' khớp số khung của chuỗi")
        pg.click("#maybe-toggle")
    check(main_ids == [x["head"] for x in exp_main] and maybe_ids == [x["head"] for x in exp_maybe],
          f"ô kết quả phải đúng theo khung khớp nhất của chuỗi: {len(main_ids)}/{len(exp_main)}, {len(maybe_ids)}/{len(exp_maybe)}")
    check(all(stats["frames"][i] == len(x["frames"]) for i, x in enumerate(exp_main)), "huy hiệu +N của ô 'Ảnh của bạn' khớp số khung của chuỗi")
    flat_main = [f for x in exp_main for f in x["frames"]]
    flat_maybe = [f for x in exp_maybe for f in x["frames"]]
    check(not set(main_ids) & set(maybe_ids) and not set(flat_main) & set(flat_maybe), "hai nhóm không được có chung ô hay chung khung")
    check(len(flat_main + flat_maybe) == len(set(flat_main + flat_maybe)), "mỗi khung đúng một lần trong toàn bộ kết quả")
    check(pg.text_content("#g-main-n").split()[0] == str(len(main_ids)), "số đếm nhóm chính phải bằng số ô")
    n_mine = len(flat_main) + len(flat_maybe)
    print(f"kết quả: máy chủ trả {len(resp['results'])} dòng → {len(exp_main)} + {len(exp_maybe)} ô ({len(flat_main)} + {len(flat_maybe)} khung); đếm hiển thị: {pg.text_content('#g-main-n').strip()}")
    if a.mock:
        with urllib.request.urlopen(a.url + "/__mock/dups") as f:
            md = json.load(f)
        sd, sc = md["search"], md["bursts"]
        if sd:
            check(len(resp["results"]) > len({r["id"] for r in resp["results"]}), "mock --dup: kết quả phải có dòng trùng")
            check(sd[0] in flat_main and sd[0] not in flat_maybe and sd[1] in flat_main and sd[1] not in flat_maybe, "ảnh trùng điểm cao/thấp phải nằm một lần ở nhóm cao")
            check(flat_main.count(sd[0]) == 1 and flat_main.count(sd[1]) == 1, "ảnh trùng chỉ một khung")
            print(f"dòng trùng trong kết quả: mỗi ảnh một khung, ở nhóm 'Ảnh của bạn'")
        if sc:
            sp = sc["split"]  # chuỗi có khung 0.62 (nhóm chính) và 0.33 (nhóm có thể): một ô, ở 'Ảnh của bạn'
            check(sp["frames"][0] in main_ids and not set(sp["frames"]) & set(flat_maybe) and set(sp["frames"]) <= set(flat_main), "chuỗi khung 0.62 + 0.33: một ô, ở 'Ảnh của bạn', mọi khung theo ô")
            check(sum(1 for i in main_ids + maybe_ids if i == sp["frames"][0]) == 1, "chuỗi tách ngưỡng chỉ hiện đúng một lần")
            am = sc["all_main"]; check(am["frames"][0] in main_ids and set(am["frames"]) <= set(flat_main), "chuỗi mọi khung đều khớp: một ô ở nhóm chính")
            pa = sc["partial"]; check(pa["frames"][0] in main_ids and set(pa["frames"]) <= set(flat_main) and len(pa["matched"]) < len(pa["frames"]),
                                     "chuỗi chỉ 1 khung khớp: lightbox vẫn gồm mọi khung của chuỗi (kể cả khung không khớp)")
            sl = sc.get("stale")
            if sl:
                allf = flat_main + flat_maybe
                check(all(allf.count(f) == 1 for f in sl["frames"]), "kết quả lệch manifest (b:null ở khung đầu): mọi khung chỉ hiện đúng một lần")
                check(sl["frames"][0] in main_ids, "chuỗi lệch b: đúng một ô ở nhóm chính")
                print(f"b lệch giữa manifest và kết quả tìm kiếm ({len(sl['frames'])} khung): mỗi khung đúng một lần, một ô")
            mo = sc["maybe_only"]; check(mo["frames"][0] in maybe_ids and not set(mo["frames"]) & set(flat_main), "chuỗi chỉ khung điểm thấp: ô ở 'Có thể cũng là bạn'")
            print(f"chuỗi liên thanh trong kết quả: tách ngưỡng 0.62/0.33 → 1 ô ở 'Ảnh của bạn' ({len(sp['frames'])} khung); tất cả khung khớp; chỉ 1 khung khớp → đủ {len(pa['frames'])} khung; chỉ điểm thấp → 'có thể'")
    # ảnh kết quả đi đúng URL v/w/h/c như thư viện
    pg.evaluate("document.getElementById('results').scrollIntoView()")
    pg.wait_for_timeout(300)
    settle(pg, "#vg-main")
    src = pg.evaluate("[...document.querySelectorAll('#vg-main .tile img[src]')].map(i => i.currentSrc)[0] || ''")
    check("/i/" in src and ("-480.webp" in src or "-240.webp" in src), "kết quả dùng URL /i/ có v: " + src)
    t = vis_tile(pg, "#vg-main")
    pg.touchscreen.tap(t[1], t[2])
    pg.wait_for_selector("#lb:not([hidden])")
    check(pg.text_content("#lb-count").replace(".", "").endswith(f"/ {n_mine}"), "lightbox của kết quả đi qua cả ảnh của bạn + có thể (%d): %s" % (n_mine, pg.text_content("#lb-count")))
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
    pg.evaluate("document.querySelector('#vg-main .tile[data-i=\"0\"]').click()")
    pg.wait_for_selector("#lb:not([hidden])")
    walk_check(pg, flat_main + flat_maybe, "lightbox ảnh của bạn (mọi khung: chính + có thể)")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#lb", state="hidden")
    pg.evaluate("document.querySelector('#vg-main .tile[data-i=\"0\"]').click()")
    pg.wait_for_selector("#lb:not([hidden])")
    pg.go_back(); pg.wait_for_selector("#lb", state="hidden")
    check(pg.is_visible("#results") and pg.is_hidden("#all"), "Back khi đang xem ảnh: chỉ đóng lightbox, vẫn ở kết quả")
    log_posts = [u for m, u in reqs if m == "POST"]
    check(all(urlparse(u).netloc == APP.netloc and urlparse(u).path == "/api/search" for u in log_posts), "mọi POST của khách chỉ tới /api/search cùng nguồn gốc: %s" % log_posts)
    check(selfie_posts and all(u == APP_ORIGIN + "/api/search" for u in selfie_posts), "selfie phải được gửi tới /api/search cùng nguồn gốc: %s" % selfie_posts)
    print(f"selfie: {len(selfie_posts)} POST /api/search, tất cả tới {sorted(set(selfie_posts))}; mọi request có thân đều tới {APP_ORIGIN}")
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
    errors = [x for x in errors if not x.startswith("Failed to load resource")]  # request bị chặn có chủ ý (đi qua walk_check) cũng ghi vào console
    check(not errors, "lỗi JS/console: %s" % errors[:5])
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

    # ---------- chuỗi liên thanh (hợp đồng §11): gộp thành một ô theo mặc định ----------
    def burst_checks(pg, pre):
        si = next(i for i, x in enumerate(tiles) if len(x) > 1)
        for _ in range(60):  # cuộn tới khi ô gộp đầu tiên được dựng
            if pg.locator('#vg-all .tile.stacked').count():
                break
            pg.evaluate("scrollBy(0, 400)"); pg.wait_for_timeout(40)
        st = pg.evaluate("(() => { const b = document.querySelector('#vg-all .tile.stacked'); return [b.dataset.id, +b.dataset.i, b.querySelector('.stack').textContent, b.getAttribute('aria-label')]; })()")
        ti = st[1]
        check(st[0] == tiles[ti][0] and st[2] == f"+{len(tiles[ti]) - 1}" and "ảnh chụp liên tiếp" in st[3], f"ô gộp: khung đầu + huy hiệu +N + nhãn: {st}")
        pg.evaluate("document.querySelector('#vg-all .tile.stacked').scrollIntoView({ block: 'center' })")
        settle(pg); shot(pg, pre + "10-burst-tile.png")
        pg.evaluate("document.querySelector('#vg-all .tile.stacked').click()")
        pg.wait_for_selector("#lb:not([hidden])")
        cid = lambda: pg.evaluate("document.getElementById('lb-save').getAttribute('href').split('/').pop()")
        check(cid() == tiles[ti][0], "chạm ô gộp: lightbox mở ở khung đầu")
        check(pg.text_content("#lb-count").replace(".", "").startswith(f"{at[ti] + 1} /"), "đếm theo khung: " + pg.text_content("#lb-count"))
        for k in range(1, len(tiles[ti])):  # lướt qua từng khung của chuỗi
            pg.keyboard.press("ArrowRight")
            check(cid() == tiles[ti][k], f"Tiếp: khung {k + 1} của chuỗi")
            if k == 1:
                pg.wait_for_function("(() => { const i = document.getElementById('lb-img'); return i.complete && i.naturalWidth > 0 && /-2000\\.webp|\\/previews\\//.test(i.currentSrc); })()", timeout=30000)
                check(f"2/{len(tiles[ti])}" in pg.text_content("#lb-burst"), "nhãn 'Chụp liên tiếp 2/N': " + pg.text_content("#lb-burst"))
                pg.wait_for_timeout(450); shot(pg, pre + "10b-lightbox-in-burst.png")
        pg.keyboard.press("ArrowRight")
        check(cid() == tiles[ti + 1][0], "hết chuỗi thì sang khung đầu của ô kế tiếp")
        pg.keyboard.press("ArrowLeft"); pg.keyboard.press("ArrowLeft")
        check(cid() == tiles[ti][-2 if len(tiles[ti]) > 1 else 0], "Trước đi ngược đúng lối cũ")
        pg.keyboard.press("Escape"); pg.wait_for_selector("#lb", state="hidden")
        return len(tiles[ti])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm)
    pg = ctx.new_page(); pg.goto(a.url + "/")
    pg.wait_for_selector("#vg-all .tile img.on", timeout=30000)
    nb = burst_checks(pg, "")
    print(f"chuỗi liên thanh (điện thoại): ô gộp có khung đầu + '+N', chạm mở khung đầu, lướt hết {nb} khung rồi sang ô kế, Trước đi ngược")
    ctx.close()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page(); pg.goto(a.url + "/?bursts=0")
    pg.wait_for_selector("#vg-all .tile img.on", timeout=30000)
    ids0 = collect_ids(pg, "#vg-all", "?bursts=0")
    check(ids0 == uniq_ids and pg.locator(".tile.stacked").count() == 0, "?bursts=0: mỗi khung một ô, không gộp")
    print(f"?bursts=0 (gỡ gộp): {len(ids0)} ô = {n_all} ảnh, không huy hiệu")
    ctx.close()

    # ---------- "ảnh của bạn" có hàng nghìn kết quả: dựng theo cửa sổ như thư viện ----------
    if a.big_url:
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm)
        pg = ctx.new_page(); pg.goto(a.big_url + "/")
        pg.wait_for_selector("#vg-all .tile img.on", timeout=30000)
        got, stop_cap = capture_search(pg)
        pg.set_input_files("#in-file", a.selfie)
        pg.wait_for_selector("#results:not([hidden])", timeout=120000)
        stop_cap()
        rs = got[0]["results"]
        _, rows_big = load_rows(a.big_url)
        em, ey = expect_results(got[0], rows_big)
        check(len(em) > 500 and len(ey) > 300, f"cần hàng nghìn kết quả để thử: {len(em)} + {len(ey)} ô")
        stats = {}
        mi = collect_ids(pg, "#vg-main", "ảnh của bạn (lớn)", stats=stats)
        pg.click("#maybe-toggle")
        stats_y = {}
        yi = collect_ids(pg, "#vg-maybe", "có thể (lớn)", stats=stats_y)
        check(mi == [x["head"] for x in em] and yi == [x["head"] for x in ey] and not set(mi) & set(yi), f"kết quả lớn: đúng thứ tự, một ô một chuỗi, hai nhóm rời nhau ({len(mi)} + {len(yi)})")
        check(max(stats["dom"], stats_y["dom"]) < 400, f"kết quả lớn: DOM phải bị chặn, đang {max(stats['dom'], stats_y['dom'])} ô")
        check(all(stats["frames"][i] == len(x["frames"]) for i, x in enumerate(em)) and all(stats_y["frames"][i] == len(x["frames"]) for i, x in enumerate(ey)), "kết quả lớn: huy hiệu +N khớp số khung")
        fm = [f for x in em for f in x["frames"]]; fy = [f for x in ey for f in x["frames"]]
        check(len(fm + fy) == len(set(fm + fy)) and not set(fm) & set(fy), "kết quả lớn: mỗi khung một lần, hai nhóm rời nhau")
        pg.evaluate("document.getElementById('results').scrollIntoView()")
        pg.wait_for_selector('#vg-main .tile[data-i="0"]')
        shot(pg, "11-selfie-results-thousands.png")
        pg.evaluate("document.querySelector('#vg-main .tile[data-i=\"0\"]').click()")
        pg.wait_for_selector("#lb:not([hidden])")
        walk_check(pg, fm + fy, "lightbox ảnh của bạn (hàng nghìn, mọi khung)")
        pg.keyboard.press("Escape")
        print(f"ảnh của bạn lớn: máy chủ trả {len(rs)} dòng → {len(mi)} + {len(yi)} ô ({len(fm)} + {len(fy)} khung), DOM tối đa {max(stats['dom'], stats_y['dom'])} ô khi cuộn hết")
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
    pg.wait_for_function("(() => { const i = document.getElementById('lb-img'); return i.complete && i.naturalWidth > 0 && /-2000\\.webp|\\/previews\\//.test(i.currentSrc); })()", timeout=30000)
    shot(pg, "d3-lightbox.png")
    for _ in range(3):
        pg.keyboard.press("ArrowRight")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#lb", state="hidden")
    check(pg.evaluate("document.activeElement.dataset.i") == "8", "đóng: focus về ô của ảnh đang xem")
    pg.set_input_files("#in-file", a.selfie)
    pg.wait_for_selector("#vg-main .tile img.on", timeout=60000)
    pg.wait_for_timeout(1000 if not a.reduced_motion else 0)
    shot(pg, "d4-selfie-results.png")
    pg.go_back(); pg.wait_for_selector("#all:not([hidden])")  # nút Back: từ kết quả về thư viện (không rời trang)
    check(pg.is_hidden("#results") and pg.is_hidden("#finder") and pg.locator("#vg-all .tile").count() > 5, "Back từ kết quả: về thư viện")
    pg.go_back(); pg.wait_for_function("location.href === 'about:blank'", timeout=10000)  # Back lần nữa mới rời trang
    print("Back: kết quả → thư viện → rời trang (about:blank)")
    pg.goto(a.url + "/"); pg.wait_for_selector("#vg-all .tile img.on", timeout=30000)
    settle(pg)
    shot(pg, "d5-back-to-all.png")
    pg.evaluate("scrollTo(0, 0)"); pg.wait_for_selector('#vg-all .tile[data-i="0"]')
    burst_checks(pg, "d")
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
        ck = [c for c in ctx.cookies() if c["name"] in ("album_token", "album_session")]  # backend thật dùng album_session (HMAC, không chứa mã)
        check(ck and all(c["httpOnly"] and c["sameSite"] == "Strict" and c["path"] == "/" for c in ck), "cookie đăng nhập: HttpOnly, SameSite=Strict, Path=/: %s" % [(c["name"], c["httpOnly"]) for c in ck])
        check(not any(c["name"] in pg.evaluate("document.cookie") for c in ck) and pg.evaluate("localStorage.length") == 0, "cookie/mã không được lộ cho JS, không lưu ở localStorage")
        check(a.manage_token not in (ck[0]["value"] or ""), "cookie không chứa mã chủ nhà")
        check("hạn mức" not in pg.inner_text("#disk"), "max_total_bytes null: không hiện hạn mức: " + pg.inner_text("#disk"))
        print("/manage (có khoá): 401 + trang đăng nhập → mã sai báo lỗi → mã đúng vào trang; cookie HttpOnly; không hạn mức:", pg.inner_text("#disk")[:90])
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_function("/xong|trùng/.test(document.getElementById('upchips').textContent)", timeout=15000)
        pg.wait_for_timeout(300)
        shot(pg, "9c-manage-after-login.png")
        # tải lên: 429 được thử lại như 503; 200 mà không phải JSON là lỗi (không phải "trùng với undefined")
        cnt = [0]
        def flaky(route):
            cnt[0] += 1
            if cnt[0] == 1:
                route.fulfill(status=429, content_type="application/json", body="{}")
            else:
                route.continue_()
        pg.route("**/api/upload*", flaky)
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_function("(() => { const s = document.querySelector('#files li .f-st'); return s && /✓|trùng/.test(s.textContent); })()", timeout=20000)
        check(cnt[0] >= 2, "429 phải được thử lại")
        pg.unroute("**/api/upload*", flaky)
        pg.route("**/api/upload*", lambda r, _q=None: r.fulfill(status=200, content_type="text/plain", body="ok"))
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_function("(() => { const s = document.querySelector('#files li .f-st'); return s && s.textContent.includes('✗'); })()", timeout=10000)
        check("undefined" not in pg.inner_text("#files"), "200 không phải JSON: báo lỗi, không in 'undefined'")
        pg.unroute("**/api/upload*")
        print("tải lên: 429 thử lại rồi xong; 200 không phải JSON → lỗi rõ ràng")
        ctx.clear_cookies()  # cookie hết hạn giữa chừng: tải lên bị 401 → quay lại trang đăng nhập
        pg.set_input_files("#pick", a.selfie)
        pg.wait_for_selector("#login-form", timeout=15000)
        print("cookie hết hạn: tải lên 401 → quay về trang đăng nhập")
        ctx.close()
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion=rm)
        pg = ctx.new_page(); pg.goto(a.url + "/manage"); pg.wait_for_selector("#login-form"); pg.wait_for_timeout(500)
        shot(pg, "d9-manage-login.png")
        for _ in range(10 if (a.mock or a.test_throttle) else 0):  # 10 lần sai/phút: lần kế tiếp bị chặn (429)
            pg.fill("#token-in", "sai"); pg.click("#login-btn")
            pg.wait_for_function("!document.getElementById('login-btn').disabled")
        if a.mock or a.test_throttle:
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
        pg.wait_for_function("/xong|trùng/.test(document.getElementById('upchips').textContent)", timeout=15000)
        pg.wait_for_timeout(300)
        shot(pg, "9d-manage-open.png")
        print("/manage (không khoá): mở thẳng, tải lên chạy không cần mã")
        ctx.close()
    external = [u for u in external if urlparse(u).netloc not in allowed]
    print("request ngoài ứng dụng/CDN:", external or "không có")
    check(not external, "có request ra ngoài: %s" % external[:5])
    b.close()
print("E2E OK")
