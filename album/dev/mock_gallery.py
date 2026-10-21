"""Chỉ dùng khi phát triển: máy chủ giả cho giao diện album (không cần model/ảnh thật).

Phục vụ album/static như server.py (thay __V__, __CDN_PRECONNECT__), cùng /api/gallery theo hợp đồng,
/i/* /thumbs/* /previews/* /originals/* sinh từ ảnh thử công khai (không bao giờ dùng ảnh cưới thật),
/api/search giả, và một "CDN" ở cổng thứ hai (chỉ GET /i/*) để thử đường ảnh khác nguồn gốc.

  python dev/mock_gallery.py [--port 8820] [--cdn-port 8821] [--count 1902] [--photos /tmp/album-test/photos]
                             [--data /tmp/album-e/data-b] [--legacy-frac 0.03] [--cdn-fail 0]
                             [--kbps 0 --rtt-ms 0 --connect-rtts 2]   # giả lập mạng chậm ở phía máy chủ (chung cho cả 2 cổng)
                             [--html-preconnect] [--admin-token T] [--no-cdn]

/__mock/log (cổng ứng dụng): các request đã thấy ở "CDN" và POST ở cả hai cổng; POST /__mock/reset xoá log.
"""
import argparse
import colorsys
import glob
import gzip
import hashlib
import hmac
import io
import json
import os
import random
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageEnhance, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "static")
ASPECTS = [3 / 2, 2 / 3, 4 / 3, 3 / 4, 1.0, 16 / 9, 5 / 4]
SIZES = (240, 480, 2000)
TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2", ".webp": "image/webp",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8"}

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8820)
ap.add_argument("--cdn-port", type=int, default=8821)
ap.add_argument("--no-cdn", action="store_true", help="manifest cdn = '' (ảnh cùng nguồn gốc)")
ap.add_argument("--count", type=int, default=1902)
ap.add_argument("--photos", default="/tmp/album-test/photos")
ap.add_argument("--data", default="/tmp/album-e/data-b")
ap.add_argument("--legacy-frac", type=float, default=0.03, help="tỉ lệ ảnh v=null (chưa có WebP, đi đường /thumbs /previews)")
ap.add_argument("--cdn-fail", type=float, default=0.0, help="tỉ lệ ảnh mà CDN trả 404 (thử đường lùi về cùng nguồn gốc)")
ap.add_argument("--kbps", type=float, default=0, help="băng thông chung (kbit/s), 0 = không giới hạn")
ap.add_argument("--rtt-ms", type=float, default=0)
ap.add_argument("--connect-rtts", type=float, default=2, help="mỗi kết nối TCP mới tốn ngần này RTT trước khi đọc request (TCP+TLS)")
ap.add_argument("--html-preconnect", action="store_true", help="thay <!--__CDN_PRECONNECT__--> như backend thật sẽ làm")
ap.add_argument("--admin-token", default="", help="bật chặn POST /api/upload|process (X-Album-Token / cookie album_token)")
ap.add_argument("--quota-gb", type=float, default=0, help="0 = không có hạn mức tổng (max_total_bytes: null)")
ap.add_argument("--dup", action="store_true", help="cố ý đưa dòng trùng vào manifest và vào kết quả /api/search (client phải chỉ hiện mỗi ảnh một lần)")
ap.add_argument("--search-n", type=int, default=70, help="số ảnh /api/search trả về (tới cả album; hợp đồng §10: trần >= 3000)")
ap.add_argument("--admin-delay", type=float, default=0, help="giây trễ thêm cho POST /api/admin/visibility (thử cập nhật lạc quan)")
ap.add_argument("--events", action="store_true", help="đưa ~1/3 ảnh sang sự kiện 17/10 (mặc định: tất cả 4/10 như production → bộ chọn ngày không hiện)")
ap.add_argument("--process-adds", type=int, default=0, help="POST /api/process thêm ngần này ảnh (ngày 17/10) sau ~1.5 s 'running' rồi 'done' — thử lưới /manage tự làm mới sau Xử lý (0 = như cũ: done ngay)")
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()
os.makedirs(a.data, exist_ok=True)
CDN = "" if (a.no_cdn or not a.cdn_port) else f"http://localhost:{a.cdn_port}"
DV = 2


# ---------- ảnh: vài chục biến thể (cắt tỉ lệ khác nhau + đổi màu) từ ảnh thử công khai ----------
def build_variants():
    vdir = os.path.join(a.data, "variants")
    meta_p = os.path.join(vdir, "meta.json")
    if os.path.exists(meta_p):
        with open(meta_p) as fh:
            return json.load(fh)
    os.makedirs(vdir, exist_ok=True)
    srcs = [p for p in sorted(glob.glob(os.path.join(a.photos, "*.jpg"))) if os.path.basename(p) != "broken.jpg"]
    if not srcs:
        sys.exit("không có ảnh thử trong " + a.photos)
    rnd = random.Random(a.seed)
    metas = []
    for si, sp in enumerate(srcs):
        base = ImageOps.exif_transpose(Image.open(sp)).convert("RGB")
        for ai, ar in enumerate(ASPECTS):
            W, H = base.size
            cw, ch = (W, int(W / ar)) if W / H < ar else (int(H * ar), H)
            cw, ch = min(cw, W), min(ch, H)
            x, y = rnd.randint(0, W - cw), rnd.randint(0, H - ch)
            im = base.crop((x, y, x + cw, y + ch))
            if max(im.size) > 3000:
                im.thumbnail((3000, 3000), Image.LANCZOS)
            hue = rnd.uniform(-.06, .06)  # đổi màu nhẹ để các biến thể khác nhau
            r, g, b = im.resize((1, 1), Image.BOX).getpixel((0, 0))
            im = ImageEnhance.Color(im).enhance(rnd.uniform(.85, 1.2))
            im = Image.blend(im, Image.new("RGB", im.size, tuple(int(v * 255) for v in colorsys.hsv_to_rgb((hue % 1), .5, .85))), .06)
            k = len(metas)
            im.save(os.path.join(vdir, f"{k}.orig.jpg"), quality=86)
            w, h = im.size
            for s in SIZES:
                if s == 2000:
                    t = im.copy(); t.thumbnail((2000, 2000), Image.LANCZOS)  # cạnh dài 2000
                else:
                    t = im.resize((s, max(1, round(h * s / w))), Image.LANCZOS)  # 240/480 = chiều RỘNG của WebP
                t.save(os.path.join(vdir, f"{k}.{s}.webp"), "WEBP", quality=72 if s != 2000 else 78, method=4)
            t = im.copy(); t.thumbnail((480, 480), Image.LANCZOS); t.save(os.path.join(vdir, f"{k}.thumb.jpg"), quality=80)
            t = im.copy(); t.thumbnail((1600, 1600), Image.LANCZOS); t.save(os.path.join(vdir, f"{k}.preview.jpg"), quality=82)
            c = Image.open(os.path.join(vdir, f"{k}.240.webp")).convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
            metas.append(dict(k=k, w=w, h=h, c="#%02x%02x%02x" % c))
    with open(meta_p, "w") as fh:
        json.dump(metas, fh)
    return metas


VARS = build_variants()
VDIR = os.path.join(a.data, "variants")


def make_manifest():
    rnd = random.Random(a.seed + 1)
    rows, t = [], 1790000000.0 + 9 * 3600
    for i in range(a.count):
        pid = hashlib.sha1(f"photo-{i}".encode()).hexdigest()[:16]
        m = VARS[(i * 7 + i // 9) % len(VARS)]
        legacy = rnd.random() < a.legacy_frac
        v = None if legacy else hashlib.sha256(f"{pid}-{DV}".encode()).hexdigest()[:8]
        taken = None if i < max(0, int(a.count * .02)) else round(t, 3)
        if taken:
            t += rnd.uniform(4, 40)
        rows.append([pid, m["w"], m["h"], taken, v, None if legacy and rnd.random() < .5 else m["c"], f"IMG_{i + 1:04d}.JPG", m["k"], None])
    rows.sort(key=lambda r: (r[3] is not None, r[3] or 0, r[6]))
    # ảnh chụp liên thanh (burst): 2–4 khung liền nhau cùng kích thước/hình, chung `b` = id khung đầu (hợp đồng §10); phần lớn ảnh b = null
    r2, i = random.Random(a.seed + 2), 0
    while i < len(rows) - 4:
        if r2.random() < .1:
            n = r2.choice([2, 2, 2, 3, 3, 4])  # phần lớn là cặp và bộ ba
            for k in range(n):
                rows[i + k][8] = rows[i][0]
                if k:
                    for f in (1, 2, 5, 7):
                        rows[i + k][f] = rows[i][f]
                    rows[i + k][3] = round(rows[i][3] + 0.17 * k, 3) if rows[i][3] else None  # cách nhau 0.17 s như chụp liên thanh thật
            i += n
        else:
            i += 1
    return rows


ROWS = make_manifest()
BY_ID = {r[0]: r for r in ROWS}
UNIQ = list(BY_ID.values())
DUPS = {"manifest": [], "search": []}
SCEN = {}
BURSTS = []
_cur = {}
for _r in ROWS:
    if _r[8]:
        _cur.setdefault(_r[8], []).append(_r)
BURSTS = [g for g in _cur.values() if len(g) >= 2]
if a.dup and len(ROWS) > 800:  # dòng trùng: kề nhau, ở xa, và một bản trùng id nhưng khác v
    c1, c2, c3 = list(ROWS[11]), list(ROWS[300]), list(ROWS[5])
    c3[4] = "deadbeef"
    ROWS.insert(700, c3); ROWS.append(c2); ROWS.insert(12, c1)
    DUPS["manifest"] = [c1[0], c2[0], c3[0]]
    ROWS[200][1] = ROWS[200][2] = 0           # w/h = 0: client phải chặn tỉ lệ khung hình
    ROWS[201][1], ROWS[201][2] = 9000, 100    # panorama cực đoan
FAIL = {r[0] for r in ROWS if int(hashlib.sha1(r[0].encode()).hexdigest()[:6], 16) / 0xFFFFFF < a.cdn_fail}
EV_IDS = ("2026-10-04", "2026-10-17")
EVENT = {r[0]: ("2026-10-17" if a.events and int(r[0][:4], 16) % 3 == 0 else "2026-10-04") for r in ROWS}   # vắng = 4/10 (như bảng photo_event)
STATE_LOCK = threading.RLock()
JOB = {"t0": None, "applied": 0, "n": 0}   # job Xử lý giả (--process-adds)
HIDDEN = set()                      # id ảnh đang ẩn (hợp đồng F.1; mặc định không ẩn gì)
SETTINGS = {"search_includes_hidden": True}
SECRET = os.urandom(32)
PUB = {}                            # manifest công khai (chỉ ảnh đang hiện) + b đã dồn lại (hợp đồng F.3)


def rebuild_public():
    """Chỉ ảnh đang hiện; b = khung hiện đầu tiên của chuỗi gốc, chuỗi <2 khung hiện thì null. Một hàm dùng chung cho gallery và search."""
    with STATE_LOCK:
        vis = [r for r in ROWS if r[0] not in HIDDEN]
        groups = {}
        for r in vis:
            if r[8]:
                groups.setdefault((r[8], EVENT[r[0]]), []).append(r[0])   # chuỗi không vắt qua sự kiện
        pub_b = {}
        for g in groups.values():
            for i in g:
                pub_b[i] = g[0] if len(g) >= 2 else None
        rows = [r[:7] + [pub_b.get(r[0]), EVENT[r[0]]] for r in vis]
        n_ev = {e: sum(1 for r in rows if r[8] == e) for e in EV_IDS}
        events = [dict(id=e, label={"2026-10-04": "4/10", "2026-10-17": "17/10"}[e], n=n_ev[e]) for e in EV_IDS]
        body = json.dumps(dict(mv=hashlib.sha1(json.dumps(rows).encode()).hexdigest()[:10], cdn=CDN, dv=DV, events=events, photos=rows), separators=(",", ":")).encode()
        PUB.update(pub_b=pub_b, body=body, gz=gzip.compress(body, 6), etag='"' + hashlib.sha1(body).hexdigest()[:16] + '"')


rebuild_public()


def counts():
    with STATE_LOCK:
        seen = {r[0] for r in ROWS}
        h = len(HIDDEN & seen)
        return {"all": len(seen), "shown": len(seen) - h, "hidden": h}


def make_token(pid, ttl=86400):
    exp = int(time.time()) + ttl
    return f"{exp}.{hmac.new(SECRET, f'{pid}.{exp}'.encode(), hashlib.sha256).hexdigest()[:24]}"


def token_ok(pid, tok):
    try:
        exp, sig = tok.split(".")
        return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(SECRET, f"{pid}.{exp}".encode(), hashlib.sha256).hexdigest()[:24])
    except Exception:
        return False


def static_version():
    h = hashlib.sha1()
    for d, _, fs in sorted(os.walk(STATIC)):
        for f in sorted(fs):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, STATIC).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:10]


def page(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        html = fh.read().replace("__V__", static_version())
    if a.html_preconnect and CDN:
        html = html.replace("<!--__CDN_PRECONNECT__-->", f'<link rel="preconnect" href="{CDN}">')
    return html.encode()


# ---------- mạng chậm ở phía máy chủ ----------
shape_lock, shape_free = threading.Lock(), [0.0]


def shaped_write(wfile, data):
    if not a.kbps:
        wfile.write(data)
        return
    bps = a.kbps * 1000 / 8
    for o in range(0, len(data), 4096):
        part = data[o:o + 4096]
        with shape_lock:
            now = time.monotonic()
            start = max(now, shape_free[0])
            shape_free[0] = start + len(part) / bps
        wait = start + len(part) / bps - now
        if wait > 0:
            time.sleep(wait)
        wfile.write(part)


SESSION = hashlib.sha256(("mock-session:" + a.admin_token).encode()).hexdigest()  # như backend thật: cookie phiên không chứa mã
LOG = {"cdn": [], "post": []}
FAILS = []
log_lock = threading.Lock()


def fresh(rid):
    return ROWS and rid in BY_ID


def search_payload(body, scope='all'):
    rnd = random.Random(hashlib.sha1(body).hexdigest())
    if len(body) < 100:
        return 422, {"error": "no_face"}
    try:
        w, h = Image.open(io.BytesIO(body)).size
    except Exception:
        w, h = 640, 480
    with STATE_LOCK:
        hidden, incl, pub_b = set(HIDDEN), SETTINGS["search_includes_hidden"], dict(PUB["pub_b"])
    elig = [r for r in UNIQ if (incl or r[0] not in hidden) and (scope == 'all' or EVENT[r[0]] == scope)]  # tắt: bỏ ảnh ẩn TRƯỚC khi xếp hạng/cắt
    pool = rnd.sample(elig, min(len(elig), a.search_n)) if elig else []
    res = []
    N, MAIN = len(pool), round(len(pool) * 40 / 70)
    for j, r in enumerate(pool):
        score = 0.78 - j * (0.38 / max(MAIN, 1)) if j < MAIN else 0.395 - (j - MAIN) * (0.11 / max(N - MAIN, 1))
        res.append(dict(id=r[0], b=r[8], name=r[6], score=round(score, 4), thumb=f"thumbs/{r[0]}.jpg", preview=f"previews/{r[0]}.jpg",
                        width=r[1], height=r[2], v=r[4], w=r[1], h=r[2], c=r[5], n_faces=1, bbox=[r[1] * .3, r[2] * .2, r[1] * .55, r[2] * .5]))
    # các chuỗi liên thanh có chủ đích (hợp đồng §11): khung điểm cao + khung điểm thấp (một ô, ở nhóm cao), tất cả khung khớp, chỉ 1 khung khớp, chỉ khung điểm thấp
    groups = [g for g in BURSTS if all(r[0] in BY_ID and (incl or r[0] not in hidden) and (scope == 'all' or EVENT[r[0]] == scope) for r in g)]
    plan = [(0, [.62, .33]), (1, [.71, .52]), (2, [.45]), (3, [.31]), (4, [.55, .5])]
    SCEN.clear()
    for gi, scores in plan:
        if gi < len(groups) and len(groups[gi]) >= len(scores) + (1 if gi == 2 else 0) and not (gi == 4 and not a.dup):
            g = groups[gi]
            ids = {r[0] for r in g}
            res = [x for x in res if x["id"] not in ids]
            for ki, (r, s) in enumerate(zip(g, scores)):
                # gi == 4 ("stale"): kết quả tìm kiếm lệch với manifest — khung đầu b:null dù manifest có b (ảnh phải vẫn chỉ hiện một lần)
                res.append(dict(id=r[0], _stale=(gi == 4 and ki == 0), b=(None if (gi == 4 and ki == 0) else r[8]), name=r[6], score=s, thumb=f"thumbs/{r[0]}.jpg", preview=f"previews/{r[0]}.jpg", width=r[1], height=r[2],
                                v=r[4], w=r[1], h=r[2], c=r[5], n_faces=1, bbox=[r[1] * .3, r[2] * .2, r[1] * .55, r[2] * .5]))
            SCEN[["split", "all_main", "partial", "maybe_only", "stale"][gi]] = dict(b=g[0][8], frames=[r[0] for r in g], matched=[r[0] for r in g[:len(scores)]])
    # chuỗi mà CHỈ một khung không phải khung đầu khớp: b (đã dồn) = id khung đầu, khung đầu không nằm trong kết quả (guest.js phải gom theo giá trị b)
    ng = [g for g in groups[5:] if len(g) >= 3 and not any(r[0] in hidden for r in g)]
    if ng:
        g = ng[0]; r = g[1]
        res = [x for x in res if x["id"] not in {q[0] for q in g}]
        res.append(dict(id=r[0], b=r[8], name=r[6], score=0.6, thumb=f"thumbs/{r[0]}.jpg", preview=f"previews/{r[0]}.jpg", width=r[1], height=r[2],
                        v=r[4], w=r[1], h=r[2], c=r[5], n_faces=1, bbox=[r[1] * .3, r[2] * .2, r[1] * .55, r[2] * .5]))
        SCEN["nonhead"] = dict(b=g[0][8], frames=[q[0] for q in g], matched=[r[0]])
    if a.dup and len(res) > 46:  # cùng một ảnh hai lần: bản điểm thấp của ảnh đầu bảng, và bản điểm cao của ảnh đang ở nhóm "có thể"
        res.append(dict(res[0], score=0.33)); res.append(dict(res[45], score=0.5))
        DUPS["search"] = [res[0]["id"], res[45]["id"]]
    # hợp đồng F.3: b công khai cho ảnh hiện; ảnh ẩn b = null, trừ khi >=2 khung ẩn cùng chuỗi gốc đều trúng → khung đầu trong số trúng; mọi hit có t
    orig = {r[0]: r[8] for r in UNIQ}
    hh = {}
    for x in res:
        if x["id"] in hidden and orig.get(x["id"]):
            hh.setdefault(orig[x["id"]], []).append(x["id"])
    order = {r[0]: i for i, r in enumerate(ROWS)}
    for x in res:
        x["ev"] = EVENT.get(x["id"], "2026-10-04")
        stale = x.pop("_stale", False)
        if x["id"] in hidden:
            g = sorted(hh.get(orig.get(x["id"]), []), key=order.get)
            x["b"] = g[0] if len(g) >= 2 else None
        elif not stale:
            x["b"] = pub_b.get(x["id"])
        x["t"] = make_token(x["id"])
    return 200, dict(query=dict(n_faces=1, n_ignored=0, bbox=[w * .3, h * .2, w * .7, h * .65], width=w, height=h, det_score=.9),
                     results=res, total_matches=len(res), truncated=False,
                     timing_ms=dict(decode=5, detect=30, embed=10, compare=1, total=46))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    kind = "app"

    def setup(self):
        super().setup()
        if a.rtt_ms and a.connect_rtts:
            time.sleep(a.rtt_ms * a.connect_rtts / 1000)  # TCP + TLS của kết nối mới (preconnect làm phần này trước)

    def log_message(self, *x):
        pass

    def send(self, code, body=b"", ctype="application/octet-stream", extra=None, head=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            shaped_write(self.wfile, body)

    def authed(self, header=False):
        ck = self.headers.get("Cookie") or ""
        return ("album_session=" + SESSION) in ck.split("; ") or (header and self.headers.get("X-Album-Token") == a.admin_token)

    def html(self, body, head, code=200):
        hdr = {"Cache-Control": "no-store"}
        if "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body, hdr["Content-Encoding"] = gzip.compress(body, 6), "gzip"
        self.send(code, body, "text/html; charset=utf-8", hdr, head)

    def unauth(self):
        self.send(401, b'{"detail":"C\xe1\xba\xa7n m\xc3\xa3 ch\xe1\xbb\xa7 nh\xc3\xa0."}', "application/json", {"Cache-Control": "no-store"})

    def token_for(self, pid, query):
        """ảnh ẩn: 404 như id không tồn tại, trừ khi ?t= đúng (hợp đồng F.3)"""
        with STATE_LOCK:
            if pid not in HIDDEN:
                return True, {}
        t = dict(x.split("=", 1) for x in query.split("&") if "=" in x).get("t", "")
        return token_ok(pid, t), {"Cache-Control": "private, no-store"}

    def nf(self):
        self.send(404, b'{"detail":"not found"}', "application/json", {"Cache-Control": "no-store"})

    def do_HEAD(self):
        self.do_GET(True)

    def do_OPTIONS(self):
        self.send(204, b"", "text/plain", {"Allow": "GET, HEAD, OPTIONS"})

    def do_GET(self, head=False):
        u = urlparse(self.path)
        path = u.path
        if path == "/__mock/shape" and self.kind == "app":  # đổi mạng giả lập khi đang chạy: /__mock/shape?kbps=0&rtt=0
            q = dict(x.split("=") for x in u.query.split("&") if "=" in x)
            a.kbps = float(q.get("kbps", a.kbps)); a.rtt_ms = float(q.get("rtt", a.rtt_ms))
            return self.send(200, b"{}", "application/json", {"Cache-Control": "no-store"})
        if a.rtt_ms:
            time.sleep(a.rtt_ms / 1000)
        if self.kind == "cdn":
            with log_lock:
                LOG["cdn"].append(["GET", path])
            if not path.startswith("/i/"):
                return self.nf()
            return self.image(path, head, cdn=True)
        if path in ("/", "/index.html"):
            return self.html(page("index.html"), head)
        if path == "/manage":
            if a.admin_token and not self.authed():  # như backend thật (hợp đồng §8): 401 + trang đăng nhập
                return self.html(page("manage-login.html"), head, 401)
            return self.html(page("manage.html"), head)
        if path.startswith("/static/"):
            p = os.path.normpath(os.path.join(STATIC, path[8:]))
            if not p.startswith(STATIC) or not os.path.isfile(p):
                return self.nf()
            with open(p, "rb") as fh:
                body = fh.read()
            ext, hdr = os.path.splitext(p)[1], {"Cache-Control": "public, max-age=2592000"}
            if ext in (".css", ".js", ".svg") and "gzip" in (self.headers.get("Accept-Encoding") or ""):  # như một reverse proxy thật
                body, hdr["Content-Encoding"] = gzip.compress(body, 6), "gzip"
            return self.send(200, body, TYPES.get(ext, "application/octet-stream"), hdr, head)
        if path == "/api/gallery":
            with STATE_LOCK:
                pub = dict(PUB)
            hdr = {"Cache-Control": "no-cache", "ETag": pub["etag"], "Vary": "Accept-Encoding"}
            if self.headers.get("If-None-Match") == pub["etag"]:
                return self.send(304, b"", "application/json", hdr)
            gz = "gzip" in (self.headers.get("Accept-Encoding") or "")
            if gz:
                hdr["Content-Encoding"] = "gzip"
            return self.send(200, pub["gz"] if gz else pub["body"], "application/json", hdr, head)
        if path in ("/api/stats", "/api/process", "/api/admin/photos") and a.admin_token and not self.authed():
            return self.unauth()
        if path == "/api/admin/photos":
            with STATE_LOCK:
                body = dict(photos=[r[:7] + [r[8], 1 if r[0] in HIDDEN else 0, EVENT[r[0]]] for r in ROWS], counts=counts(), settings=dict(SETTINGS))
            return self.send(200, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        if path == "/api/stats":
            s = dict(originals=len(ROWS), photos=len(ROWS), pending=0, faces=len(ROWS) * 2, disk_free=50 * 2 ** 30, disk_total=100 * 2 ** 30,
                     stored_bytes=len(ROWS) * 3_000_000, max_total_bytes=int(a.quota_gb * 2 ** 30) if a.quota_gb else None, max_upload_mb=60)
            return self.send(200, json.dumps(s).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        if path == "/api/process":
            s = dict(state="idle", done=0, total=0, faces=0, failed=0, rejected=[], errors=[], elapsed=0, current="")
            with STATE_LOCK:
                if JOB["t0"] is not None:
                    el = time.time() - JOB["t0"]
                    if el < 1.5:
                        s.update(state="running", total=a.process_adds, done=int(el / 1.5 * a.process_adds), elapsed=el, current="x.jpg")
                    else:
                        if not JOB["applied"]:  # ảnh mới chỉ hiện trong /api/admin/photos + manifest SAU khi job xong
                            for j in range(a.process_adds):
                                pid = hashlib.sha1(f"added-{JOB['n']}-{j}".encode()).hexdigest()[:16]
                                m = VARS[j % len(VARS)]
                                row = [pid, m["w"], m["h"], 1790900000.0 + j, hashlib.sha256(f"{pid}-{DV}".encode()).hexdigest()[:8], m["c"], f"NEW_{JOB['n']}_{j}.JPG", m["k"], None]
                                ROWS.append(row); BY_ID[pid] = row; EVENT[pid] = "2026-10-17"
                            rebuild_public()
                            JOB["applied"] = 1
                        s.update(state="done", total=a.process_adds, done=a.process_adds, faces=a.process_adds, elapsed=1.5)
            return self.send(200, json.dumps(s).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        if path == "/__mock/dups":
            return self.send(200, json.dumps(dict(DUPS, bursts=SCEN)).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        if path == "/__mock/log":
            with log_lock:
                return self.send(200, json.dumps(LOG).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        m = re.match(r"^/(thumbs|previews)/([0-9a-f]{16})\.jpg$", path)
        if m and m.group(2) in BY_ID:
            ok, extra = self.token_for(m.group(2), u.query)
            if not ok:
                return self.nf()
            k = BY_ID[m.group(2)][7]
            with open(os.path.join(VDIR, f"{k}.{'thumb' if m.group(1) == 'thumbs' else 'preview'}.jpg"), "rb") as fh:
                return self.send(200, fh.read(), "image/jpeg", dict({"Cache-Control": "public, max-age=3600"}, **extra), head)
        m = re.match(r"^/originals/([0-9a-f]{16})$", path)
        if m and m.group(1) in BY_ID:
            ok, extra = self.token_for(m.group(1), u.query)
            if not ok:
                return self.nf()
            r = BY_ID[m.group(1)]
            with open(os.path.join(VDIR, f"{r[7]}.orig.jpg"), "rb") as fh:
                return self.send(200, fh.read(), "image/jpeg", dict({"Content-Disposition": f'attachment; filename="{r[6]}"', "Cache-Control": "private, max-age=3600"}, **extra), head)
        if path.startswith("/i/"):
            return self.image(path, head)
        self.nf()

    def image(self, path, head, cdn=False):
        m = re.match(r"^/i/([0-9a-f]{16})-([0-9a-f]{8})-(240|480|2000)\.webp$", path)
        r = BY_ID.get(m.group(1)) if m else None
        if not r or r[4] != m.group(2) or (cdn and r[0] in FAIL):
            return self.nf()
        with open(os.path.join(VDIR, f"{r[7]}.{m.group(3)}.webp"), "rb") as fh:
            self.send(200, fh.read(), "image/webp", {"Cache-Control": "public, max-age=31536000, immutable", "Access-Control-Allow-Origin": "*"}, head)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        path = urlparse(self.path).path
        with log_lock:
            LOG["post"].append([self.kind, path, n])
            if self.kind == "cdn":
                LOG["cdn"].append(["POST", path])
        if self.kind == "cdn":
            return self.nf()
        if path == "/__mock/unhide":  # đưa mock về trạng thái ban đầu (không ẩn gì, cài đặt mặc định)
            with STATE_LOCK:
                for k in EVENT: EVENT[k] = '2026-10-17' if a.events and int(k[:4], 16) % 3 == 0 else '2026-10-04'
                HIDDEN.clear(); SETTINGS["search_includes_hidden"] = True; rebuild_public()
            return self.send(200, b"{}", "application/json")
        if path == "/__mock/reset":
            with log_lock:
                LOG["cdn"].clear(); LOG["post"].clear(); FAILS.clear()
            return self.send(200, b"{}", "application/json")
        if path == "/api/admin-login" and a.admin_token:
            now = time.time()
            with log_lock:
                FAILS[:] = [x for x in FAILS if now - x < 60]
                if len(FAILS) >= 10:
                    return self.send(429, b'{"detail":"Th\xe1\xbb\xad sai qu\xc3\xa1 nhi\xe1\xbb\x81u l\xe1\xba\xa7n."}', "application/json", {"Cache-Control": "no-store"})
            try:
                tok = json.loads(body or b"{}").get("token", "")
            except Exception:
                tok = ""
            if tok == a.admin_token:
                self.send_response(204)
                self.send_header("Set-Cookie", f"album_session={SESSION}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with log_lock:
                FAILS.append(now)
            return self.send(401, b'{"detail":"M\xc3\xa3 kh\xc3\xb4ng \xc4\x91\xc3\xbang."}', "application/json", {"Cache-Control": "no-store"})
        if path in ("/api/admin/visibility", "/api/admin/settings", "/api/admin/event"):
            if a.admin_token and not self.authed():
                return self.unauth()
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
                return self.send(403, b'{"detail":"forbidden"}', "application/json", {"Cache-Control": "no-store"})
            return self.admin_post(path, body)
        gated = path in ("/api/upload", "/api/process")
        if gated and a.admin_token:
            if not self.authed(header=True):
                return self.send(401, b'{"detail":"C\xe1\xba\xa7n m\xc3\xa3 ch\xe1\xbb\xa7 nh\xc3\xa0."}', "application/json", {"Cache-Control": "no-store"})
        if path == "/api/search":
            scope = (parse_qs(urlparse(self.path).query).get("scope") or ["all"])[0]
            if scope != "all" and scope not in EV_IDS:
                return self.send(400, b'{"detail":"scope"}', "application/json", {"Cache-Control": "no-store"})
            code, obj = search_payload(body, scope)
            return self.send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})
        if path == "/api/upload":
            q = parse_qs(urlparse(self.path).query)
            ev = (q.get("event") or ["2026-10-04"])[0]
            if ev not in EV_IDS:
                return self.send(400, b'{"detail":"event"}', "application/json")
            nm = (q.get("name") or ["x.jpg"])[0]
            if nm.startswith("dup"):  # ảnh trùng: giữ sự kiện của ảnh cũ (ở đây luôn 17/10 để thử lời báo)
                return self.send(200, json.dumps(dict(status="duplicate", name="old-" + nm, event="2026-10-17")).encode(), "application/json")
            return self.send(200, json.dumps(dict(status="ok", name=nm, renamed=False, event=ev)).encode(), "application/json")
        if path == "/api/process":
            if a.process_adds:
                with STATE_LOCK:
                    JOB.update(t0=time.time(), applied=0, n=JOB["n"] + 1)
                return self.send(200, json.dumps(dict(state="running", done=0, total=a.process_adds, faces=0, failed=0, rejected=[], errors=[], elapsed=0, current="")).encode(), "application/json")
            return self.send(200, json.dumps(dict(state="done", done=0, total=0, faces=0, failed=0, rejected=[], errors=[], elapsed=0, current="")).encode(), "application/json")
        self.nf()


def _admin_post(self, path, body):
    def reply(code, obj):
        self.send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})
    try:
        d = json.loads(body or b"")
        assert isinstance(d, dict)
        if path == "/api/admin/settings":
            v = d["search_includes_hidden"]
            assert isinstance(v, bool)
            with STATE_LOCK:
                SETTINGS["search_includes_hidden"] = v
                return reply(200, {"settings": dict(SETTINGS)})
        if path == "/api/admin/event":
            ids, ev = d["ids"], d["event"]
            assert ev in EV_IDS and isinstance(ids, list) and 1 <= len(ids) <= 5000 and set(d) == {"ids", "event"}
            assert all(isinstance(i, str) and re.fullmatch(r"[0-9a-f]{16}", i) for i in ids)
            ids = list(dict.fromkeys(ids))
            with STATE_LOCK:
                known = [i for i in ids if i in BY_ID]
                changed = sum(1 for i in known if EVENT[i] != ev)
                for i in known:
                    EVENT[i] = ev
                rebuild_public()
                return reply(200, {"changed": changed, "unknown": len(ids) - len(known), "event": ev})
        ids, hid = d["ids"], d["hidden"]
        assert isinstance(hid, bool) and isinstance(ids, list) and 1 <= len(ids) <= 5000
        assert all(isinstance(i, str) and re.fullmatch(r"[0-9a-f]{16}", i) for i in ids)
    except Exception:
        return reply(400, {"detail": "yêu cầu không hợp lệ"})
    ids = list(dict.fromkeys(ids))
    with STATE_LOCK:
        known = [i for i in ids if i in BY_ID]
        before = len(HIDDEN)
        if hid:
            HIDDEN.update(known)
        else:
            HIDDEN.difference_update(known)
        changed = abs(len(HIDDEN) - before)
        rebuild_public()
        if a.admin_delay:
            time.sleep(a.admin_delay)
        reply(200, {"changed": changed, "unknown": len(ids) - len(known), "counts": counts()})


H.admin_post = _admin_post


class CdnH(H):
    kind = "cdn"


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


if __name__ == "__main__":
    servers = [Srv(("127.0.0.1", a.port), H)]
    if CDN:
        servers.append(Srv(("127.0.0.1", a.cdn_port), CdnH))
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"mock: app http://localhost:{a.port}  cdn {CDN or '(không)'}  {len(ROWS)} ảnh  {len(PUB['gz']) // 1024} KB gzip manifest", flush=True)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
