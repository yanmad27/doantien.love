"""Chỉ dùng khi phát triển: lái trình duyệt thật qua giao diện album mới (mở thẳng thư viện → cuộn hết → lightbox →
tìm bằng selfie → quay lại tất cả → Lưu ảnh), chụp màn hình từng bước và kiểm các điều bất biến.

  # với máy chủ giả (dev/mock_gallery.py --admin-token test-token; và một bản không token ở cổng 8822 cho --open-url):
  python dev/e2e.py --url http://localhost:8820 --selfie /tmp/album-test/photos/wedding_couple.jpg --out /tmp/album-e/shots --mock --manage-token test-token --open-url http://localhost:8822
  # với backend thật (đã có ảnh được xử lý): bỏ --mock; thêm --photos f1.jpg ... nếu muốn tải lên + Xử lý trước

--visibility: chỉ chạy bộ thử Ẩn / hiện ảnh (hợp đồng F), mỗi trình duyệt một lần, máy tính + điện thoại.
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
ap.add_argument("--visibility", action="store_true", help="chỉ chạy bộ thử Ẩn / hiện ảnh trong /manage (cần --manage-token nếu máy chủ có khoá; mock: --search-n lớn để ảnh ẩn chắc chắn nằm trong kết quả); trả ảnh về trạng thái hiện hết khi xong")
ap.add_argument("--events", action="store_true", help="chỉ chạy bộ thử hai ngày (4/10, 17/10): tab khách, phạm vi tìm selfie, /manage tải lên theo ngày + chuyển ngày; mock: --events --process-adds 4 --admin-token test-token --search-n 70")
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
            check(f"{u.scheme}://{u.netloc}" == APP_ORIGIN and r.url.split("?")[0] == APP_ORIGIN + "/api/search", "POST /api/search phải là đường dẫn tương đối cùng nguồn gốc: " + r.url)
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
            if r.url.split("?")[0].endswith("/api/search"):
                got.append(r.json())
        pg.on("response", on_resp)
        return got, lambda: pg.remove_listener("response", on_resp)
    def on_route(route):
        r = route.fetch(); got.append(r.json()); route.fulfill(response=r)
    pg.route("**/api/search*", on_route)
    return got, lambda: pg.unroute("**/api/search*", on_route)


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


WALK = """n => { const cur = () => document.getElementById('lb-save').getAttribute('href').split('/').pop().split('?')[0];
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
    check(pg.evaluate("document.getElementById('lb-save').getAttribute('href').split('/').pop().split('?')[0]") == expected[0], f"{label}: phải bắt đầu ở ảnh đầu")
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

# ---------- Ẩn / hiện ảnh (hợp đồng F): chạy riêng bằng --visibility, trên Chromium / WebKit, máy tính + điện thoại ----------
def vis_suite():
    selfie_bytes = open(a.selfie, "rb").read()
    num = lambda s: int("".join(ch for ch in s if ch.isdigit()) or 0)
    JS_API = """async ([p, b]) => { const r = await fetch(p, b ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) } : {}); let d = null; try { d = await r.json(); } catch (e) {} return [r.status, d]; }"""

    def api(pg, path, body=None):
        return pg.evaluate(JS_API, [path, body])

    def admin(pg):
        st, d = api(pg, "/api/admin/photos")
        check(st == 200, f"GET /api/admin/photos phải 200, được {st}")
        return d

    def hidden_ids(pg):
        return {p[0] for p in admin(pg)["photos"] if p[8]}

    def login(pg):
        pg.goto(a.url + "/manage")
        if pg.locator("#login-form").count():
            check(a.manage_token, "máy chủ đòi mã chủ nhà: cần --manage-token")
            pg.fill("#token-in", a.manage_token); pg.click("#login-btn")
        pg.wait_for_selector("#vgrid .cell", timeout=30000)

    def reset(pg):
        d = admin(pg)
        ids = [p[0] for p in d["photos"]]
        for i in range(0, len(ids), 5000):
            check(api(pg, "/api/admin/visibility", {"ids": ids[i:i + 5000], "hidden": False})[0] == 200, "reset: hiện tất cả")
        check(api(pg, "/api/admin/settings", {"search_includes_hidden": True})[0] == 200, "reset: bật cài đặt tìm")
        pg.reload(); pg.wait_for_selector("#vgrid .cell", timeout=30000)

    def at(pg, k):
        pg.evaluate("k => document.querySelector('.cell[data-k=\"' + k + '\"]').scrollIntoView({ block: 'center' })", k)

    def tap(pg, k, **kw):
        at(pg, k)
        pg.locator(f'.cell[data-k="{k}"] .tile').click(**kw)

    def sel_n(pg):
        return num(pg.inner_text("#abar-n")) if "Chưa" not in pg.inner_text("#abar-n") else 0

    def selected(pg, bursts, only=None):
        keys = pg.evaluate("[...document.querySelectorAll('.cell')].filter(c => c.dataset.sel === 'all').map(c => c.dataset.k)")
        out = set()
        for k in keys:
            out.update(bursts[k[1:]] if k[0] == "b" and k[1:] in bursts else [k])
        return out & only if only is not None else out  # `only`: bộ lọc đang áp (ô chuỗi chỉ gồm các khung khớp bộ lọc)

    def act(pg, btn, path="/api/admin/visibility"):
        with pg.expect_response(lambda r: r.url.endswith(path) and r.request.method == "POST") as ri:
            pg.click(btn)
        check(ri.value.status == 200, f"{btn}: POST {path} phải 200, được {ri.value.status}")
        pg.wait_for_timeout(120)

    def chips(pg):
        return [num(pg.inner_text(f"#c-{k}")) for k in ("all", "shown", "hidden")]

    def public_rows():
        rows, uniq = load_rows(a.url)
        check(len(rows) == len(uniq), "thư viện công khai: không id nào xuất hiện hai lần")
        return rows

    def guest_walk(bt, rows, label):
        ctx = bt.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)  # không cookie chủ nhà
        pg = ctx.new_page(); pg.goto(a.url + "/")
        pg.wait_for_selector("#vg-all .tile", timeout=30000)
        tiles, _ = group_tiles(rows)
        st = {}
        ids = collect_ids(pg, "#vg-all", label, stats=st)
        check(ids == [t[0] for t in tiles], f"{label}: ô theo đúng manifest công khai ({len(ids)} vs {len(tiles)})")
        check([st["frames"][i] for i in range(len(tiles))] == [len(t) for t in tiles], f"{label}: huy hiệu +N khớp số khung còn hiện")
        pg.evaluate("scrollTo(0, 0)"); pg.wait_for_selector("#vg-all .tile"); pg.locator("#vg-all .tile").first.click(); pg.wait_for_selector("#lb:not([hidden])")
        check("?" not in pg.get_attribute("#lb-save", "href"), f"{label}: ảnh trong thư viện (không phải kết quả tìm) không dùng ?t=")
        ctx.close()
        return len(tiles)

    def run(bt, label, kw):
        ctx = bt.new_context(**kw); pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        login(pg); reset(pg)
        D = admin(pg); R = D["photos"]
        ids_all = [p[0] for p in R]
        rows0 = public_rows()
        check([r[0] for r in rows0] == ids_all, f"{label}: bắt đầu: thư viện công khai = toàn bộ ảnh")
        check(chips(pg) == [len(R), len(R), 0] and D["counts"] == {"all": len(R), "shown": len(R), "hidden": 0}, f"{label}: số đếm bộ lọc {chips(pg)} vs {D['counts']}")
        bursts = {}
        for p in R:
            if p[7]:
                bursts.setdefault(p[7], []).append(p[0])
        big = [b for b, f in bursts.items() if len(f) >= 3]
        B1, B2 = big[0], [b for b, f in bursts.items() if b != big[0] and len(f) >= 2][-1]
        pos = {p[0]: i for i, p in enumerate(R)}
        singles = [p[0] for p in R if not p[7]]
        s0, s1 = singles[3], singles[4]
        later = [s for s in singles if pos[s] >= 600]
        sa = later[0]; sb = next(s for s in later if pos[s] >= pos[sa] + 15)
        sc = next(s for s in later if pos[s] >= pos[sb] + 5); sd = next(s for s in later if pos[s] >= pos[sc] + 12)
        pg.locator("#vis").scroll_into_view_if_needed(); pg.evaluate("document.getElementById('vis').scrollIntoView()")
        pg.wait_for_function("[...document.querySelectorAll('#vgrid .tile img')].slice(0, 6).every(i => i.complete && i.naturalWidth > 0)", timeout=30000)
        shot(pg, f"{label}-1-overview.png")
        print(f"{label}: đăng nhập, mục Ẩn/hiện: {chips(pg)} (tất cả/đang hiện/đang ẩn) = máy chủ {D['counts']}; {len(bursts)} chuỗi")

        # --- chế độ thường: chạm = xem ảnh ---
        at(pg, s0); pg.locator(f'.cell[data-k="{s0}"] .tile').click()
        pg.wait_for_selector("#pv[open]")
        check(R[pos[s0]][6] in pg.inner_text("#pv-cap") and pg.is_hidden("#abar"), f"{label}: chạm ảnh ở chế độ thường mở xem ảnh, không chọn")
        pg.click("#pv-close"); pg.wait_for_selector("#pv", state="hidden")
        # nhấn giữ = vào chế độ chọn + chọn ô đó (và cú nhả không bị tính là chạm)
        at(pg, s0); box = pg.locator(f'.cell[data-k="{s0}"] .tile').bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2); pg.mouse.down(); pg.wait_for_timeout(650); pg.mouse.up(); pg.wait_for_timeout(450)  # cú nhả ngay sau nhấn giữ bị nuốt trong 350 ms
        pg.wait_for_selector("#abar:not([hidden])")
        check(sel_n(pg) == 1 and pg.get_attribute(f'.cell[data-k="{s0}"]', "data-sel") == "all" and not pg.locator("#pv[open]").count(), f"{label}: nhấn giữ → chế độ chọn, chọn 1 ảnh, không mở xem")
        pg.click("#a-clear"); check(sel_n(pg) == 0, f"{label}: Bỏ chọn")

        # --- một ảnh: ẩn (lạc quan) → ngoài thư viện khách → Hoàn tác ---
        tap(pg, s0); check(sel_n(pg) == 1 and "1 ảnh đã chọn" in pg.inner_text("#abar-n"), f"{label}: chọn một ảnh")
        with pg.expect_response(lambda r: r.url.endswith("/api/admin/visibility")) as ri:
            c = pg.evaluate("document.getElementById('a-hide').click(); document.getElementById('c-hidden').textContent")
            check(num(c) == 1, f"{label}: cập nhật lạc quan: số 'Đang ẩn' đổi ngay trước khi máy chủ trả lời ({c})")
        check(ri.value.status == 200, "ẩn một ảnh: 200")
        pg.wait_for_timeout(150)
        check(hidden_ids(pg) == {s0} and "Đã ẩn 1 ảnh" in pg.inner_text("#toast"), f"{label}: ẩn 1 ảnh: máy chủ có đúng 1 ảnh ẩn, toast: {pg.inner_text('#toast')}")
        check(s0 not in {r[0] for r in public_rows()}, f"{label}: ảnh đã ẩn không còn trong thư viện khách")
        shot(pg, f"{label}-5-actionbar-toast.png")
        with pg.expect_response(lambda r: r.url.endswith("/api/admin/visibility")):
            pg.click("#toast-undo")
        pg.wait_for_timeout(150)
        check(hidden_ids(pg) == set() and chips(pg) == [len(R), len(R), 0], f"{label}: Hoàn tác đưa ảnh trở lại")
        print(f"{label}: chọn 1 ảnh → Ẩn (số đếm đổi ngay) → khách không thấy → Hoàn tác → trở lại")

        # --- lỗi mạng: quay lại như cũ + báo ---
        pg.route("**/api/admin/visibility", lambda r: r.fulfill(status=500, content_type="application/json", body="{}"))
        tap(pg, s1); pg.click("#a-hide"); pg.wait_for_function("document.getElementById('toast-t').textContent.includes('Chưa lưu được')", timeout=10000)
        check(chips(pg)[2] == 0 and pg.get_attribute(f'.cell[data-k="{s1}"]', "class").find("is-hidden") < 0, f"{label}: lỗi 500 → ảnh quay lại trạng thái cũ")
        pg.unroute("**/api/admin/visibility")
        pg.click("#a-clear")
        print(f"{label}: lỗi máy chủ → hoàn lại trạng thái cũ + thông báo")

        # --- chuỗi: chọn cả chuỗi, mở chuỗi, chọn một phần ---
        f1 = bursts[B1]
        tap(pg, "b" + B1)
        check(sel_n(pg) == len(f1) and pg.get_attribute(f'.cell[data-k="b{B1}"]', "data-sel") == "all", f"{label}: chạm ô chuỗi chọn đủ {len(f1)} khung")
        pg.click(f'.cell[data-k="b{B1}"] .bopen-btn')
        pg.wait_for_selector(".bopen")
        check(pg.locator(".bopen .cell").count() == len(f1) and sel_n(pg) == len(f1), f"{label}: 'Mở chuỗi' hiện {len(f1)} ô khung, giữ nguyên lựa chọn")
        tap(pg, f1[0]); check(sel_n(pg) == len(f1) - 1, f"{label}: bỏ chọn một khung")
        pg.evaluate("document.getElementById('toast').hidden = true")
        shot(pg, f"{label}-3-expanded-burst.png")
        pg.click(".bopen-x"); pg.wait_for_selector(".bopen", state="detached")
        check(pg.get_attribute(f'.cell[data-k="b{B1}"]', "data-sel") == "part", f"{label}: chuỗi chọn một phần → trạng thái 'part' (dấu –)")
        tap(pg, s0)
        pg.evaluate("document.getElementById('toast').hidden = true")
        shot(pg, f"{label}-2-select-mixed.png")
        pg.click("#a-clear")
        # chỉ ẩn khung thứ hai của B1
        pg.click(f'.cell[data-k="b{B1}"] .bopen-btn'); pg.wait_for_selector(".bopen")
        tap(pg, f1[1]); check(sel_n(pg) == 1, "chọn đúng một khung")
        act(pg, "#a-hide")
        check(hidden_ids(pg) == {f1[1]}, f"{label}: chỉ khung {f1[1]} bị ẩn")
        pg.click(".bopen-x"); pg.wait_for_selector(".bopen", state="detached")
        tags = pg.inner_text(f'.cell[data-k="b{B1}"] .tags')
        check(f"1/{len(f1)} ẩn" in tags, f"{label}: huy hiệu chuỗi hỗn hợp: {tags}")
        # chuỗi B2: ẩn cả chuỗi bằng một chạm
        f2 = bursts[B2]
        tap(pg, "b" + B2); check(sel_n(pg) == len(f2), f"{label}: chọn cả chuỗi B2")
        act(pg, "#a-hide")
        check(hidden_ids(pg) == {f1[1], *f2}, f"{label}: ẩn cả chuỗi B2 ({len(f2)} khung)")
        print(f"{label}: chuỗi — chạm ô = chọn cả {len(f1)} khung; mở chuỗi; chọn một phần = 'part'; ẩn 1 khung → huy hiệu '1/{len(f1)} ẩn'; ẩn cả chuỗi {len(f2)} khung")

        # --- dải: neo + "Chọn đến đây"; Shift-click ---
        tap(pg, sa)
        pg.click("#a-range"); check(pg.get_attribute("#a-range", "aria-pressed") == "true", f"{label}: 'Chọn đến đây' đã bật")
        tap(pg, sb)
        exp = set(ids_all[pos[sa]:pos[sb] + 1])
        got = selected(pg, bursts)
        check(got == exp and sel_n(pg) == len(exp), f"{label}: dải neo→ô cuối: {len(got)} ảnh, cần {len(exp)}")
        act(pg, "#a-hide")
        hid = {f1[1], *f2, *exp}
        check(hidden_ids(pg) == hid, f"{label}: ẩn dải: máy chủ có đúng {len(hid)} ảnh ẩn")
        tap(pg, sc); tap(pg, sd, modifiers=["Shift"])
        exp2 = set(ids_all[pos[sc]:pos[sd] + 1]); got2 = selected(pg, bursts)
        check(got2 == exp2, f"{label}: Shift-click chọn dải: {len(got2)} vs {len(exp2)}")
        act(pg, "#a-hide"); hid |= exp2
        check(hidden_ids(pg) == hid, f"{label}: ẩn dải Shift")
        print(f"{label}: dải bằng 'Chọn đến đây' ({len(exp)} ảnh) + Shift-click ({len(exp2)} ảnh) đúng thứ tự khung; đang ẩn {len(hid)}")

        # --- bộ lọc theo khung ---
        c = chips(pg)
        check(c == [len(R), len(R) - len(hid), len(hid)], f"{label}: số đếm bộ lọc {c}")
        # chọn ở "Tất cả" rồi đổi bộ lọc: lựa chọn chỉ còn các khung nhìn thấy (không "Hiện" nhầm ảnh ngoài màn hình)
        pg.click("#v-all"); check(sel_n(pg) == len(R), f"{label}: chọn tất cả ở 'Tất cả'")
        pg.click('.fchip[data-f="hidden"]'); pg.wait_for_function("document.querySelector('.fchip[data-f=hidden]').getAttribute('aria-pressed') === 'true'")
        check(sel_n(pg) == len(hid), f"{label}: đổi sang 'Đang ẩn': lựa chọn được cắt còn {len(hid)} (được {sel_n(pg)})")
        pg.click("#a-clear")
        pg.click("#v-all")
        check(selected(pg, bursts, hid) == hid and sel_n(pg) == len(hid), f"{label}: lọc 'Đang ẩn' + Chọn tất cả = đúng {len(hid)} khung đang ẩn")
        pg.click("#a-clear")
        check(not pg.locator(".cell:not(.is-hidden)").count(), f"{label}: lọc 'Đang ẩn' chỉ còn ô ẩn")
        pg.evaluate("document.getElementById('vis').scrollIntoView()"); pg.click("#v-mode")  # thoát chế độ chọn cho ảnh chụp
        pg.evaluate("document.querySelector('.vbar').scrollIntoView()"); pg.evaluate("document.getElementById('toast').hidden = true"); shot(pg, f"{label}-4-filter-hidden.png")
        pg.click('.fchip[data-f="shown"]'); pg.click("#v-mode"); pg.click("#v-all")
        check(sel_n(pg) == len(R) - len(hid) and pg.locator(".cell.is-hidden").count() == 0, f"{label}: lọc 'Đang hiện' = {len(R) - len(hid)} khung, không ô ẩn")
        pg.click("#v-mode"); pg.click('.fchip[data-f="all"]')
        print(f"{label}: bộ lọc theo khung: {c}; 'Đang ẩn' + chọn tất cả = {len(hid)}")

        # --- khách: thư viện công khai ---
        rows = public_rows()
        vis_ids = [r[0] for r in rows]
        check(vis_ids == [i for i in ids_all if i not in hid], f"{label}: thư viện khách = toàn bộ trừ ảnh ẩn, giữ thứ tự")
        remain = [i for i in f1 if i != f1[1]]
        bb = {r[0]: r[7] for r in rows}
        check(all(bb[i] == remain[0] for i in remain) if len(remain) >= 2 else all(bb[i] is None for i in remain), f"{label}: chuỗi còn {len(remain)} khung: b = khung đầu còn hiện")
        check(not set(f2) & set(vis_ids), f"{label}: chuỗi ẩn hết không còn ô")
        n_tiles = guest_walk(bt, rows, f"{label}: thư viện khách (đã ẩn)")
        print(f"{label}: khách thấy {len(vis_ids)}/{len(R)} ảnh, {n_tiles} ô; chuỗi {len(f1)} khung ẩn 1 → '+{len(remain) - 1}', chuỗi {len(f2)} khung ẩn hết → không ô; không id trùng")

        # --- tìm selfie: cài đặt BẬT ---
        def search(c2):
            r = c2.request.post(a.url + "/api/search", data=selfie_bytes, headers={"Content-Type": "application/octet-stream"})
            check(r.status == 200, f"/api/search: {r.status}")
            return r.json()
        gctx = bt.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        gctx.add_init_script(SHARE_STUB)
        def ui_search(c2):
            pg.goto("about:blank")  # WebKit: trang chủ nhà đang giữ ~1.900 ảnh nhỏ làm canvas mã hoá lại selfie ra rỗng (no_face) → giải phóng trước
            for attempt in range(3):  # WebKit thỉnh thoảng mã hoá lại selfie thành thân rỗng (mock trả no_face) khi máy bận: thử lại
                gp = c2.new_page(); gp.route("**/*-2000.webp", lambda r: r.abort())
                gp.goto(a.url + "/"); gp.wait_for_selector("#vg-all .tile")
                got, stop = capture_search(gp)
                gp.set_input_files("#in-file", a.selfie)
                try:
                    gp.wait_for_selector("#results:not([hidden])", timeout=20000 if attempt < 2 else 60000)
                except Exception:
                    stop(); gp.close()
                    check(attempt < 2 and webkit, "tìm selfie không ra kết quả")
                    print("  (WebKit: selfie không mã hoá được lần này, thử lại)", got[:1])
                    continue
                gp.wait_for_selector("#vg-main .tile img.on", timeout=30000)
                stop()
                return gp, got[0]

        gp, j0 = ui_search(gctx)
        pg.goto(a.url + "/manage"); pg.wait_for_selector("#vgrid .cell", timeout=30000)  # selfie qua giao diện (trình duyệt mã hoá lại ảnh → kết quả khác đường API thô): lấy các hit đầu bảng của CHÍNH lượt này
        top = [x["id"] for x in sorted(j0["results"], key=lambda x: -x["score"])[:4]]
        gp.close()
        tgt = [i for i in top if i not in hid][:2]
        check(api(pg, "/api/admin/visibility", {"ids": tgt, "hidden": True})[0] == 200, "ẩn 2 ảnh đầu bảng kết quả")
        hid |= set(tgt)
        j1 = search(gctx)
        rid = [x["id"] for x in j1["results"]]
        check(len(rid) == len(set(rid)), f"{label}: tìm (BẬT): không id nào hai lần")
        check(set(tgt) <= set(rid), f"{label}: tìm (BẬT): ảnh ẩn vẫn có trong kết quả")
        check(all(x.get("t") for x in j1["results"]), f"{label}: tìm: mọi hit (cả hiện lẫn ẩn) đều có t")
        check("indexed_photos" not in j1 and "indexed_faces" not in j1, f"{label}: /api/search không còn indexed_*")
        pubb = {r[0]: r[7] for r in public_rows()}
        for x in j1["results"]:
            if x["id"] not in hid:
                check(x["b"] == pubb.get(x["id"]) or (x["b"] is None and pubb.get(x["id"]) is None) or a.mock is False, f"{label}: hit hiện có b khớp thư viện khách: {x['id']}")
            else:
                check(x["b"] is None or x["b"] in set(rid), f"{label}: hit ẩn: b phải null hoặc là id nằm trong kết quả: {x['b']}")
        for i in tgt:
            r = gctx.request.get(a.url + f"/originals/{i}"); check(r.status == 404, f"{label}: ảnh ẩn không có t → 404 ({r.status})")
            r2 = gctx.request.get(a.url + f"/originals/0000000000000000"); check(r.text() == r2.text(), f"{label}: 404 ảnh ẩn giống 404 id lạ")
            t = next(x["t"] for x in j1["results"] if x["id"] == i)
            check(gctx.request.get(a.url + f"/originals/{i}?t={t}").status == 200 and gctx.request.get(a.url + f"/previews/{i}.jpg?t={t}").status == 200, f"{label}: có t → ảnh gốc/xem trước 200")
        # giao diện khách: Lưu / Chia sẻ / đường lùi dùng ?t=
        gp, jU = ui_search(gctx)
        pg.goto(a.url + "/manage"); pg.wait_for_selector("#vgrid .cell", timeout=30000)
        check(set(tgt) <= {x["id"] for x in jU["results"]}, f"{label}: giao diện (BẬT): ảnh ẩn vẫn nằm trong kết quả")
        dom_ids = collect_ids(gp, "#vg-main", f"{label}: kết quả (BẬT)")
        if a.mock:  # chuỗi chỉ có khung KHÔNG phải khung đầu khớp: hit mang b = id khung đầu (không nằm trong kết quả) → một ô, không trùng, lightbox đủ khung từ manifest
            with urllib.request.urlopen(a.url + "/__mock/dups") as f:
                nh = json.load(f)["bursts"].get("nonhead")
            if nh:
                check(nh["b"] in dom_ids and nh["matched"][0] not in dom_ids, f"{label}: nonhead: một ô ở khung đầu {nh['b']}, khung khớp không thành ô riêng")
                gp.evaluate("document.getElementById('vg-main').scrollIntoView()")
                for _ in range(80):
                    if gp.locator(f'#vg-main .tile[data-id="{nh["b"]}"]').count():
                        break
                    gp.evaluate("scrollBy(0, 500)"); gp.wait_for_timeout(40)
                check(gp.locator(f'#vg-main .tile[data-id="{nh["b"]}"]').count() == 1, f"{label}: nonhead: đúng một ô")
                gp.evaluate("id => document.querySelector('#vg-main .tile[data-id=\"' + id + '\"]').click()", nh["b"])
                gp.wait_for_selector("#lb:not([hidden])")
                check(f"/{len(nh['frames'])}" in gp.inner_text("#lb-burst"), f"{label}: nonhead: lightbox đủ {len(nh['frames'])} khung: {gp.inner_text('#lb-burst')}")
                gp.keyboard.press("Escape"); gp.wait_for_selector("#lb", state="hidden")
                print(f"{label}: chuỗi chỉ khớp khung giữa (b = khung đầu không có trong kết quả): một ô, không trùng, lightbox {len(nh['frames'])} khung")
        gp.evaluate("document.getElementById('vg-main').scrollIntoView()")
        gp.wait_for_function("ids => [...document.querySelectorAll('#vg-main .tile')].some(b => ids.includes(b.dataset.id))", arg=tgt, timeout=15000)
        pick = gp.evaluate("ids => [...document.querySelectorAll('#vg-main .tile')].map(b => b.dataset.id).find(i => ids.includes(i))", tgt)
        check(pick in tgt, f"{label}: ảnh ẩn nằm trong kết quả hiển thị")
        with gp.expect_request(lambda r: f"/originals/{pick}?t=" in r.url, timeout=20000):  # Chia sẻ chuẩn bị sẵn ảnh gốc bằng ?t=
            gp.evaluate("id => document.querySelector('#vg-main .tile[data-id=\"' + id + '\"]').click()", pick)
            gp.wait_for_selector("#lb:not([hidden])")
        href = gp.get_attribute("#lb-save", "href")
        check(href.startswith(f"/originals/{pick}?t="), f"{label}: Lưu ảnh dùng ?t=: {href}")
        gp.wait_for_function("id => document.getElementById('lb-img').currentSrc.includes('/previews/' + id + '.jpg?t=')", arg=pick, timeout=20000)
        print(f"{label}: tìm (BẬT): {len(rid)} hit, ảnh ẩn có mặt, mọi hit có t, không id trùng; Lưu ảnh = {href[:40]}…; đường lùi /previews/…?t=; ảnh ẩn không t → 404 như id lạ")
        gp.close()

        # --- cài đặt TẮT ---
        pg.evaluate("document.getElementById('set-note').scrollIntoView({ block: 'center' })")
        pg.click(".switch")
        pg.wait_for_function("document.getElementById('set-note').textContent.includes('Đang tắt')", timeout=10000)
        check(admin(pg)["settings"]["search_includes_hidden"] is False, f"{label}: cài đặt đã lưu ở máy chủ")
        shot(pg, f"{label}-6-setting-toggle.png")
        j2 = search(gctx)
        rid2 = [x["id"] for x in j2["results"]]
        check(not set(rid2) & hid and len(rid2) == len(set(rid2)), f"{label}: tìm (TẮT): không ảnh ẩn nào, không id trùng ({len(set(rid2) & hid)} ảnh ẩn)")
        gp, jO = ui_search(gctx)
        pg.goto(a.url + "/manage"); pg.wait_for_selector("#vgrid .cell", timeout=30000)
        check(not {x["id"] for x in jO["results"]} & hid, f"{label}: giao diện (TẮT): phản hồi không có ảnh ẩn")
        dom2 = collect_ids(gp, "#vg-main", f"{label}: kết quả (TẮT)")
        check(not set(dom2) & hid, f"{label}: giao diện kết quả (TẮT) không có ảnh ẩn")
        gp.close()
        print(f"{label}: tìm (TẮT): {len(rid2)} hit, không ảnh ẩn nào (BẬT có {len(rid)})")
        pg.click(".switch"); pg.wait_for_function("document.getElementById('set-note').textContent.includes('Đang bật')", timeout=10000)
        gctx.close()

        # --- hiện lại tất cả: thư viện khách trở về y nguyên ---
        pg.reload(); pg.wait_for_selector("#vgrid .cell", timeout=30000)  # 2 ảnh ẩn ở trên đi qua API thẳng: tải lại như mở trang mới
        check(chips(pg)[2] == len(hid), f"{label}: tải lại trang: số 'Đang ẩn' = {len(hid)} (được {chips(pg)})")
        pg.click('.fchip[data-f="hidden"]'); pg.click("#v-mode"); pg.click("#v-all")
        check(sel_n(pg) == len(hid), f"{label}: chọn tất cả đang ẩn = {len(hid)}")
        act(pg, "#a-show")
        check(hidden_ids(pg) == set() and chips(pg) == [len(R), len(R), 0], f"{label}: hiện lại tất cả")
        rows2 = public_rows()
        check(rows2 == rows0, f"{label}: thư viện khách trở về y nguyên (cùng tập id, cùng b, cùng thứ tự): {len(rows2)} vs {len(rows0)}")
        pg.click("#v-mode"); pg.click('.fchip[data-f="all"]')
        print(f"{label}: hiện lại tất cả → thư viện khách = bản đầu ({len(rows2)} ảnh, cùng id/b/thứ tự)")

        # --- hàng loạt >200: hỏi xác nhận; Hoàn tác cả loạt ---
        pg.click("#v-mode"); pg.click("#v-all")
        pg.click("#a-hide"); pg.wait_for_selector("#cf[open]")
        check(f"{len(R):,}".replace(",", ".") in pg.inner_text("#cf-t"), f"{label}: hộp xác nhận nêu số ảnh: {pg.inner_text('#cf-t')}")
        pg.click("#cf-no"); pg.wait_for_selector("#cf", state="hidden")
        check(hidden_ids(pg) == set(), f"{label}: Huỷ xác nhận → không ẩn gì")
        with pg.expect_response(lambda r: r.url.endswith("/api/admin/visibility")):
            pg.click("#a-hide"); pg.wait_for_selector("#cf[open]"); pg.click("#cf-yes")
        pg.wait_for_timeout(200)
        check(len(hidden_ids(pg)) == len(R) and public_rows() == [], f"{label}: xác nhận → ẩn cả {len(R)} ảnh, thư viện khách trống")
        with pg.expect_response(lambda r: r.url.endswith("/api/admin/visibility")):
            pg.click("#toast-undo")
        pg.wait_for_timeout(200)
        check(hidden_ids(pg) == set() and public_rows() == rows0, f"{label}: Hoàn tác cả loạt → trở về y nguyên")
        print(f"{label}: {len(R)} ảnh: hỏi xác nhận (>200), Huỷ không đổi gì, Đồng ý ẩn hết, Hoàn tác đưa về y nguyên")

        # --- 401 → trang đăng nhập ---
        ctx.clear_cookies()
        if pg.get_attribute("#v-mode", "aria-pressed") != "true":
            pg.click("#v-mode")
        tap(pg, s0); pg.click("#a-hide")
        pg.wait_for_selector("#login-form", timeout=15000)
        print(f"{label}: cookie hết hạn → bấm Ẩn nhận 401 → quay về trang đăng nhập")
        check(not errs, f"{label}: lỗi JS trên trang: {errs}")
        ctx.close()

    bt = None
    with sync_playwright() as pw:
        bt = getattr(pw, a.browser).launch()
        rm_ = "reduce" if a.reduced_motion else "no-preference"
        run(bt, "desktop", dict(viewport={"width": 1280, "height": 800}, reduced_motion=rm_))
        run(bt, "phone", dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm_))
        bt.close()
    print("E2E (ẩn/hiện) OK")


def events_suite():
    """Hai ngày trong một album (mock --events): tab ngày cho khách, URL giữ ngày, phạm vi tìm selfie, tương thích manifest cũ;
    /manage: chọn ngày khi tải lên, ảnh trùng giữ ngày cũ, huy hiệu ngày, bộ lọc ngày, chuyển ngày hàng loạt + hoàn tác."""
    E4, E17 = "2026-10-04", "2026-10-17"
    num = lambda s: int("".join(ch for ch in s if ch.isdigit()) or 0)

    def run(bt, label, kw):
        ctx = bt.new_context(**kw); pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        urllib.request.urlopen(urllib.request.Request(a.url + "/__mock/unhide", method="POST", data=b""))
        rows, _ = load_rows(a.url)
        by = {E4: [r for r in rows if r[8] == E4], E17: [r for r in rows if r[8] == E17]}
        check(by[E4] and by[E17], f"{label}: mock phải có ảnh của cả hai ngày")
        check(all(not r[7] or r[7] in {x[0] for x in rows if x[8] == r[8]} for r in rows), f"{label}: chuỗi (b) không vắt qua ngày")
        # ---- khách ----
        pg.goto(a.url + "/")
        pg.wait_for_selector("#ev-tabs:not([hidden]) button", timeout=30000); pg.wait_for_selector("#vg-all .tile")
        check(pg.locator("#ev-tabs button").count() == 2, f"{label}: hai tab ngày")
        sel = lambda: pg.get_attribute('#ev-tabs button[aria-selected="true"]', "data-ev")
        check(sel() == E4, f"{label}: tab mặc định 4/10")
        check(num(pg.inner_text("#count")) == len(by[E4]), f"{label}: đếm 4/10 {pg.inner_text('#count')} vs {len(by[E4])}")
        tiles, _ = group_tiles(by[E4])
        check(collect_ids(pg, "#vg-all", label + " 4/10") == [t[0] for t in tiles], f"{label}: thư viện 4/10 đúng manifest")
        pg.evaluate("scrollTo(0, 0)")
        pg.click('#ev-tabs button[data-ev="%s"]' % E17); pg.wait_for_function("location.hash === '#ev=%s'" % E17)
        check(sel() == E17 and num(pg.inner_text("#count")) == len(by[E17]), f"{label}: tab 17/10 + đếm")
        tiles, _ = group_tiles(by[E17])
        check(collect_ids(pg, "#vg-all", label + " 17/10") == [t[0] for t in tiles], f"{label}: thư viện 17/10 đúng manifest")
        shot(pg, "ev-1-guest-tab17.png")
        pg.reload(); pg.wait_for_selector("#ev-tabs:not([hidden]) button"); pg.wait_for_selector("#vg-all .tile")
        check(sel() == E17 and num(pg.inner_text("#count")) == len(by[E17]), f"{label}: tải lại vẫn ở 17/10")
        # ---- phạm vi tìm selfie ----
        check(pg.get_attribute('#scope-hero button[data-scope="all"]', "aria-pressed") == "true", f"{label}: phạm vi mặc định Cả hai")
        pg.evaluate("scrollTo(0, 0)")
        got, stop = capture_search(pg)
        reqs = []
        pg.on("request", lambda r: reqs.append(r.url) if r.method == "POST" and "/api/search" in r.url else None)
        pg.click('#scope-hero button[data-scope="%s"]' % E17)
        pg.set_input_files("#in-file", a.selfie)
        pg.wait_for_selector("#results:not([hidden])", timeout=60000)
        check(len(reqs) == 1 and reqs[-1].endswith("/api/search"), f"{label}: lần tìm đầu luôn không kèm scope (chọn sẵn 17/10 chỉ lọc phía khách): {reqs}")
        check(got[-1]["results"] and {x["ev"] for x in got[-1]["results"]} == {E4, E17}, f"{label}: phản hồi gốc có cả hai ngày")
        ids = lambda: pg.evaluate("gMain.flat.concat(gMaybe.flat).map(x => x.id)")
        evs = {r[0]: r[8] for r in rows}
        check(ids() and all(evs.get(i, E17) == E17 for i in ids()), f"{label}: chỉ hiện ảnh 17/10")
        shot(pg, "ev-2-guest-scope17.png")
        pg.click('#scope-finder button[data-scope="all"]')
        pg.wait_for_function("document.querySelector('#scope-finder button[data-scope=\"all\"]').getAttribute('aria-pressed') === 'true'")
        pg.wait_for_selector("#results:not([hidden])")
        pg.wait_for_timeout(500)
        check(len(reqs) == 1, f"{label}: đổi sang Cả hai không gửi lại /api/search: {reqs}")
        check({evs.get(i, E17) for i in ids()} == {E4, E17}, f"{label}: Cả hai → có ảnh của cả hai ngày")
        stop()
        # ---- manifest cũ (thiếu trường ngày) → không có bộ chọn, mọi ảnh hiện ----
        pg2 = ctx.new_page(); errs2 = []
        pg2.on("pageerror", lambda e: errs2.append(str(e)))
        def old_shape(route):
            r = route.fetch(); m = r.json(); m.pop("events", None)
            m["photos"] = [p[:8] for p in m["photos"]]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(m))
        pg2.route("**/api/gallery", old_shape)
        pg2.goto(a.url + "/#ev=" + E17); pg2.wait_for_selector("#vg-all .tile", timeout=30000)
        check(pg2.locator("#ev-tabs").is_hidden() and pg2.locator("#scope-hero").is_hidden(), f"{label}: manifest cũ: không hiện tab/phạm vi")
        check(num(pg2.inner_text("#count")) == len(rows) and not errs2, f"{label}: manifest cũ: hiện đủ {len(rows)} ảnh {errs2}")
        pg2.close()
        # ---- /manage ----
        pg.goto(a.url + "/manage")
        if pg.locator("#login-form").count():
            pg.fill("#token-in", a.manage_token); pg.click("#login-btn")
        pg.wait_for_selector("#vgrid .cell", timeout=30000)
        check(pg.is_checked('input[name="up-ev"][value="%s"]' % E4), f"{label}: tải lên mặc định 4/10")
        os.makedirs(a.out, exist_ok=True)
        dup = os.path.join(a.out, "dup-selfie.jpg"); open(dup, "wb").write(open(a.selfie, "rb").read())
        with pg.expect_request(lambda r: "/api/upload" in r.url) as ri:
            pg.set_input_files("#pick", a.selfie)
        pg.wait_for_function("document.getElementById('upsum').textContent.length > 0")
        tot = pg.inner_text("#upsum").split(" / ")[-1].strip(" .…").replace("Hoàn tất", "").strip()
        check(tot and not tot.startswith("0 ") and "100%" not in pg.inner_text("#upsum"), f"{label}: tổng dung lượng tải lên phải khác 0: {pg.inner_text('#upsum')}")
        check("event=" + E4 in ri.value.url, f"{label}: tải lên mặc định gửi event=4/10: {ri.value.url}")
        pg.wait_for_function("/xong/.test(document.getElementById('upchips').textContent)", timeout=15000)
        pg.check('input[name="up-ev"][value="%s"]' % E17, force=True)
        with pg.expect_request(lambda r: "/api/upload" in r.url) as ri:
            pg.set_input_files("#pick", a.selfie)
        check("event=" + E17 in ri.value.url, f"{label}: tải lên 17/10 gửi event=2026-10-17: {ri.value.url}")
        pg.wait_for_function("document.querySelector('#files li .f-st').textContent.includes('ngày 17/10')", timeout=15000)
        with pg.expect_request(lambda r: "/api/upload" in r.url):
            pg.set_input_files("#pick", dup)
        pg.wait_for_function("document.querySelector('#files li .f-st').textContent.includes('giữ nguyên ngày 17/10')", timeout=15000)
        print(f"{label}: tải lên theo ngày: mặc định 4/10, chọn 17/10, trùng → '{pg.inner_text('#files li .f-st')}'")
        d = pg.evaluate("fetch('/api/admin/photos').then(r => r.json())")
        P = {p[0]: p for p in d["photos"]}
        check(all(len(p) == 10 for p in P.values()), f"{label}: hàng admin có trường ngày")
        singles = [p[0] for p in d["photos"] if not p[7] and not p[8]][:3]
        check(len(singles) == 3, f"{label}: cần 3 ảnh lẻ")
        # huy hiệu ngày
        tag = lambda k: pg.inner_text('.cell[data-k="%s"] .tag--ev' % k)
        for k in singles:
            pg.evaluate("k => document.querySelector('.cell[data-k=\"' + k + '\"]').scrollIntoView({ block: 'center' })", k)
            check(tag(k) == ("17/10" if P[k][9] == E17 else "4/10"), f"{label}: huy hiệu ngày {k}")
        # bộ lọc ngày
        pg.click('#evf .fchip[data-e="%s"]' % E17)
        n17 = sum(1 for p in d["photos"] if p[9] == E17)
        check(pg.locator("#vgrid .cell").count() > 0 and num(pg.inner_text("#ce-" + E17)) == n17, f"{label}: bộ lọc ngày 17/10 ({pg.inner_text('#ce-' + E17)} vs {n17})")
        check(all(pg.evaluate("[...document.querySelectorAll('#vgrid .tag--ev')].every(t => t.textContent.includes('17/10'))") for _ in [0]), f"{label}: lọc 17/10 chỉ còn ô 17/10")
        pg.click('#evf .fchip[data-e="all"]')
        # chuyển ngày hàng loạt
        tgt = E17 if P[singles[0]][9] != E17 else E4
        pg.click("#v-mode")
        for k in singles[:2]:
            pg.evaluate("k => document.querySelector('.cell[data-k=\"' + k + '\"]').scrollIntoView({ block: 'center' })", k)
            pg.locator('.cell[data-k="%s"] .tile' % k).click()
        check(num(pg.inner_text("#abar-n")) == 2, f"{label}: chọn 2 ảnh")
        want_other = E4 if tgt == E17 else E17
        for _ in [0]:
            with pg.expect_response(lambda r: r.url.endswith("/api/admin/event") and r.request.method == "POST") as rr:
                pg.click("#a-ev-" + tgt)
            check(rr.value.status == 200 and json.loads(rr.value.request.post_data)["event"] == tgt, f"{label}: POST /api/admin/event 200")
        pg.wait_for_timeout(200)
        d2 = pg.evaluate("fetch('/api/admin/photos').then(r => r.json())")
        P2 = {p[0]: p[9] for p in d2["photos"]}
        check(all(P2[k] == tgt for k in singles[:2]) and P2[singles[2]] == P[singles[2]][9], f"{label}: chuyển ngày đúng 2 ảnh")
        pub = {r[0]: r[8] for r in load_rows(a.url)[0]}
        check(all(pub[k] == tgt for k in singles[:2]), f"{label}: manifest công khai thấy ngày mới ngay")
        check(tag(singles[0]) == ("17/10" if tgt == E17 else "4/10"), f"{label}: huy hiệu cập nhật")
        shot(pg, "ev-3-manage-reassign.png")
        pg.click("#toast-undo"); pg.wait_for_timeout(400)
        d3 = pg.evaluate("fetch('/api/admin/photos').then(r => r.json())")
        check({p[0]: p[9] for p in d3["photos"]}[singles[0]] == P[singles[0]][9], f"{label}: hoàn tác trả ngày cũ")
        # ngày không hợp lệ bị máy chủ từ chối
        st = pg.evaluate("fetch('/api/admin/event', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ids: ['%s'], event: '2026-10-05'})}).then(r => r.status)" % singles[0])
        check(st == 400, f"{label}: event lạ phải 400, được {st}")
        # ---- Xử lý xong → lưới + số đếm tự làm mới, không tải lại trang (mock --process-adds 4) ----
        pg.evaluate("window.__noreload = 1"); pg.click('#evf .fchip[data-e="all"]')
        cells0 = pg.locator("#vgrid .cell").count()
        n17_0, all0 = num(pg.inner_text("#ce-" + E17)), num(pg.inner_text("#c-all"))
        gets = []
        pg.on("request", lambda r: gets.append(r.url) if r.method == "GET" and r.url.endswith("/api/admin/photos") else None)
        pg.click("#proc")
        pg.wait_for_function("document.getElementById('pmsg').textContent.includes('Hoàn tất')", timeout=20000)
        pg.wait_for_function("n => document.querySelectorAll('#vgrid .cell').length > n", arg=cells0, timeout=10000)
        check(pg.evaluate("window.__noreload") == 1, f"{label}: lưới phải tự làm mới, không tải lại trang")
        check(len(gets) >= 1, f"{label}: sau Xử lý phải GET /api/admin/photos ({len(gets)})")
        pg.wait_for_function("(a) => Number(document.getElementById('c-all').textContent.replace(/\\D/g, '')) > a", arg=all0, timeout=10000)
        check(num(pg.inner_text("#ce-" + E17)) == n17_0 + 4, f"{label}: đếm ngày 17/10 tăng 4: {n17_0} → {pg.inner_text('#ce-' + E17)}")
        check(pg.locator("#vgrid .cell").count() == cells0 + 4, f"{label}: lưới thêm 4 ô: {cells0} → {pg.locator('#vgrid .cell').count()}")
        check(pg.locator('#vgrid .cell .tag--ev', has_text="17/10").count() >= 4, f"{label}: huy hiệu ngày của ảnh mới")
        check(not errs, f"{label}: lỗi JS trên trang: {errs}")
        print(f"{label}: tab ngày + URL + phạm vi tìm + manifest cũ + /manage (tải lên, huy hiệu, lọc, chuyển ngày, hoàn tác) OK")
        ctx.close()

    with sync_playwright() as pw:
        bt = getattr(pw, a.browser).launch()
        rm_ = "reduce" if a.reduced_motion else "no-preference"
        run(bt, "desktop", dict(viewport={"width": 1280, "height": 800}, reduced_motion=rm_))
        run(bt, "phone", dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion=rm_))
        bt.close()
    urllib.request.urlopen(urllib.request.Request(a.url + "/__mock/unhide", method="POST", data=b""))
    print("E2E (hai ngày) OK")


if a.events:
    events_suite()
    sys.exit(0)


if a.visibility:
    vis_suite()
    sys.exit(0)

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
        cid = lambda: pg.evaluate("document.getElementById('lb-save').getAttribute('href').split('/').pop().split('?')[0]")
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
