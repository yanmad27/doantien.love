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
import io
import json
import os
import random
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

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
        rows.append([pid, m["w"], m["h"], taken, v, None if legacy and rnd.random() < .5 else m["c"], f"IMG_{i + 1:04d}.JPG", m["k"]])
    rows.sort(key=lambda r: (r[3] is not None, r[3] or 0, r[6]))
    return rows


ROWS = make_manifest()
BY_ID = {r[0]: r for r in ROWS}
FAIL = {r[0] for r in ROWS if int(hashlib.sha1(r[0].encode()).hexdigest()[:6], 16) / 0xFFFFFF < a.cdn_fail}
MANIFEST = json.dumps(dict(mv=hashlib.sha1(json.dumps(ROWS).encode()).hexdigest()[:10], cdn=CDN, dv=DV,
                           photos=[r[:7] for r in ROWS]), separators=(",", ":")).encode()
MGZ = gzip.compress(MANIFEST, 6)
META_ETAG = '"' + hashlib.sha1(MANIFEST).hexdigest()[:16] + '"'


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


LOG = {"cdn": [], "post": []}
log_lock = threading.Lock()


def fresh(rid):
    return ROWS and rid in BY_ID


def search_payload(body):
    rnd = random.Random(hashlib.sha1(body).hexdigest())
    if len(body) < 100:
        return 422, {"error": "no_face"}
    try:
        w, h = Image.open(io.BytesIO(body)).size
    except Exception:
        w, h = 640, 480
    pool = rnd.sample(ROWS, min(len(ROWS), 70)) if ROWS else []
    res = []
    for j, r in enumerate(pool):
        score = 0.78 - j * 0.0095 if j < 40 else 0.395 - (j - 40) * 0.0035
        res.append(dict(id=r[0], name=r[6], score=round(score, 4), thumb=f"thumbs/{r[0]}.jpg", preview=f"previews/{r[0]}.jpg",
                        width=r[1], height=r[2], v=r[4], w=r[1], h=r[2], c=r[5], n_faces=1, bbox=[r[1] * .3, r[2] * .2, r[1] * .55, r[2] * .5]))
    return 200, dict(query=dict(n_faces=1, n_ignored=0, bbox=[w * .3, h * .2, w * .7, h * .65], width=w, height=h, det_score=.9),
                     results=res, total_matches=len(res), truncated=False, indexed_photos=len(ROWS), indexed_faces=len(ROWS) * 2,
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

    def nf(self):
        self.send(404, b'{"detail":"not found"}', "application/json", {"Cache-Control": "no-store"})

    def do_HEAD(self):
        self.do_GET(True)

    def do_OPTIONS(self):
        self.send(204, b"", "text/plain", {"Allow": "GET, HEAD, OPTIONS"})

    def do_GET(self, head=False):
        if a.rtt_ms:
            time.sleep(a.rtt_ms / 1000)
        path = urlparse(self.path).path
        if self.kind == "cdn":
            with log_lock:
                LOG["cdn"].append(["GET", path])
            if not path.startswith("/i/"):
                return self.nf()
            return self.image(path, head, cdn=True)
        if path in ("/", "/index.html"):
            return self.send(200, page("index.html"), "text/html; charset=utf-8", {"Cache-Control": "no-store"}, head)
        if path == "/manage":
            return self.send(200, page("manage.html"), "text/html; charset=utf-8", {"Cache-Control": "no-store"}, head)
        if path.startswith("/static/"):
            p = os.path.normpath(os.path.join(STATIC, path[8:]))
            if not p.startswith(STATIC) or not os.path.isfile(p):
                return self.nf()
            with open(p, "rb") as fh:
                body = fh.read()
            return self.send(200, body, TYPES.get(os.path.splitext(p)[1], "application/octet-stream"), {"Cache-Control": "public, max-age=2592000"}, head)
        if path == "/api/gallery":
            hdr = {"Cache-Control": "public, max-age=60", "ETag": META_ETAG, "Vary": "Accept-Encoding"}
            if self.headers.get("If-None-Match") == META_ETAG:
                return self.send(304, b"", "application/json", hdr)
            gz = "gzip" in (self.headers.get("Accept-Encoding") or "")
            if gz:
                hdr["Content-Encoding"] = "gzip"
            return self.send(200, MGZ if gz else MANIFEST, "application/json", hdr, head)
        if path == "/api/stats":
            s = dict(originals=len(ROWS), photos=len(ROWS), pending=0, faces=len(ROWS) * 2, disk_free=50 * 2 ** 30, disk_total=100 * 2 ** 30,
                     stored_bytes=len(ROWS) * 3_000_000, max_total_bytes=60 * 2 ** 30, max_upload_mb=60)
            return self.send(200, json.dumps(s).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        if path == "/api/process":
            s = dict(state="idle", done=0, total=0, faces=0, failed=0, rejected=[], errors=[], elapsed=0, current="")
            return self.send(200, json.dumps(s).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        if path == "/__mock/log":
            with log_lock:
                return self.send(200, json.dumps(LOG).encode(), "application/json", {"Cache-Control": "no-store"}, head)
        m = re.match(r"^/(thumbs|previews)/([0-9a-f]{16})\.jpg$", path)
        if m and m.group(2) in BY_ID:
            k = BY_ID[m.group(2)][7]
            with open(os.path.join(VDIR, f"{k}.{'thumb' if m.group(1) == 'thumbs' else 'preview'}.jpg"), "rb") as fh:
                return self.send(200, fh.read(), "image/jpeg", {"Cache-Control": "public, max-age=3600"}, head)
        m = re.match(r"^/originals/([0-9a-f]{16})$", path)
        if m and m.group(1) in BY_ID:
            r = BY_ID[m.group(1)]
            with open(os.path.join(VDIR, f"{r[7]}.orig.jpg"), "rb") as fh:
                return self.send(200, fh.read(), "image/jpeg", {"Content-Disposition": f'attachment; filename="{r[6]}"', "Cache-Control": "private, max-age=3600"}, head)
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
        if path == "/__mock/reset":
            with log_lock:
                LOG["cdn"].clear(); LOG["post"].clear()
            return self.send(200, b"{}", "application/json")
        gated = path in ("/api/upload", "/api/process")
        if gated and a.admin_token:
            ok = self.headers.get("X-Album-Token") == a.admin_token or ("album_token=" + a.admin_token) in (self.headers.get("Cookie") or "")
            if not ok:
                return self.send(401, b'{"detail":"C\xe1\xba\xa7n m\xc3\xa3 ch\xe1\xbb\xa7 nh\xc3\xa0."}', "application/json", {"Cache-Control": "no-store"})
        if path == "/api/search":
            code, obj = search_payload(body)
            return self.send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})
        if path == "/api/upload":
            return self.send(200, json.dumps(dict(status="ok", name="x.jpg")).encode(), "application/json")
        if path == "/api/process":
            return self.send(200, json.dumps(dict(state="done", done=0, total=0, faces=0, failed=0, rejected=[], errors=[], elapsed=0, current="")).encode(), "application/json")
        self.nf()


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
    print(f"mock: app http://localhost:{a.port}  cdn {CDN or '(không)'}  {len(ROWS)} ảnh  {len(MGZ) // 1024} KB gzip manifest", flush=True)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
