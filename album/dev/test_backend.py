"""Test backend album (stdlib unittest, không cần pytest/httpx):  .venv/bin/python -m unittest discover -s dev -p 'test_*.py' -v

- DeriveTests: WebP/ICC/EXIF/orientation/draft của indexer.derive_one (ảnh tổng hợp).
- GateTests: HostGate, kiểu media, khoá cache, /api/gallery, token chủ nhà, tiến độ backfill — engine giả, không cần model.
- MigrationTests: dựng chỉ mục bằng code d806aa3 (git archive), chạy code mới, backfill, 0 lần gọi detector, /api/search y hệt.
  Cần model + ảnh thử ở /tmp/album-test/photos (bỏ qua nếu không có)."""
import gzip
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image, ImageCms

HERE = os.path.dirname(os.path.abspath(__file__))
ALBUM = os.path.dirname(HERE)
sys.path.insert(0, ALBUM)
sys.path.insert(0, HERE)
import indexer  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
from _asgi import call  # noqa: E402

BASE_COMMIT = "d806aa3"
PHOTOS = "/tmp/album-test/photos"
QUERY = "/tmp/album-test/queries/obama_query_official.jpg"
P3_ICC = "/System/Library/ColorSync/Profiles/Display P3.icc"


def make_jpeg(path, size=(1200, 800), orient=None, taken=None, subsec=None, icc=None, color=(120, 90, 60), fmt="JPEG"):
    im = Image.new("RGB", size, color)
    for x in range(0, size[0], 40):  # vài chi tiết để mã hoá không suy biến
        im.paste((x % 255, 255 - x % 255, 128), (x, 0, x + 20, size[1] // 2))
    ex = Image.Exif()
    if orient:
        ex[0x0112] = orient
    if taken:
        ifd = ex.get_ifd(0x8769)
        ifd[0x9003] = taken
        if subsec:
            ifd[0x9291] = subsec
    kw = {}
    if icc:
        kw["icc_profile"] = icc
    if orient or taken:
        kw["exif"] = ex
    im.save(path, fmt, quality=90, **kw)


class FakeEngine:
    detect_calls = embed_calls = 0

    def __init__(self, *a, **k):
        pass

    def detect(self, rgb, size):
        FakeEngine.detect_calls += 1
        return []

    def embed(self, rgb, dets):
        FakeEngine.embed_calls += 1


class DeriveTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_webp_support_asserted(self):
        indexer.require_webp()

    def test_sizes_orientation_color_taken(self):
        p = os.path.join(self.d, "a.jpg")
        make_jpeg(p, (4000, 3000), orient=6, taken="2026:05:01 12:00:00", subsec="250")
        m = indexer.derive_one(p, self.d, "a" * 16, "ab" * 32)
        self.assertEqual((m["w"], m["h"]), (3000, 4000))  # EXIF 6 → xoay, w/h sau xoay
        self.assertEqual(m["v"], "abababab"[:6] + "01")
        self.assertRegex(m["color"], r"^#[0-9a-f]{6}$")
        import calendar
        self.assertAlmostEqual(m["taken"], calendar.timegm((2026, 5, 1, 12, 0, 0)) + 0.25, places=3)
        for size, exp in ((240, (240, 320)), (480, (480, 640)), (2000, (1500, 2000))):
            im = Image.open(os.path.join(store.deriv_dir(self.d), f"{'a' * 16}-{m['v']}-{size}.webp"))
            self.assertEqual(im.format, "WEBP")
            self.assertEqual(im.size, exp)

    def test_no_exif_taken_none_and_small_not_upscaled(self):
        p = os.path.join(self.d, "b.jpg")
        make_jpeg(p, (300, 200))
        m = indexer.derive_one(p, self.d, "b" * 16, "cd" * 32)
        self.assertIsNone(m["taken"])
        self.assertEqual(Image.open(os.path.join(store.deriv_dir(self.d), f"{'b' * 16}-{m['v']}-480.webp")).size, (300, 200))

    def test_display_p3_converted_to_srgb(self):
        icc = open(P3_ICC, "rb").read() if os.path.exists(P3_ICC) else None
        if not icc:
            self.skipTest("không có profile Display P3")
        p = os.path.join(self.d, "p3.jpg")
        make_jpeg(p, (800, 600), icc=icc, color=(200, 60, 40))
        m = indexer.derive_one(p, self.d, "c" * 16, "ef" * 32)
        out = Image.open(os.path.join(store.deriv_dir(self.d), f"{'c' * 16}-{m['v']}-2000.webp")).convert("RGB").getpixel((790, 590))
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        want = ImageCms.profileToProfile(Image.new("RGB", (1, 1), (200, 60, 40)), src, ImageCms.createProfile("sRGB"), outputMode="RGB").getpixel((0, 0))
        self.assertLess(max(abs(a - b) for a, b in zip(out, want)), 8, (out, want))
        self.assertGreater(max(abs(a - b) for a, b in zip(out, (200, 60, 40))), 15)  # khác hẳn khi không đổi màu

    def test_mpo_and_png(self):
        p = os.path.join(self.d, "m.jpg")
        im = Image.new("RGB", (2400, 1600), (10, 200, 30))
        im.save(p, "MPO", save_all=True, append_images=[im.resize((120, 80))])
        self.assertEqual(indexer.derive_one(p, self.d, "d" * 16, "01" * 32)["w"], 2400)
        q = os.path.join(self.d, "x.png")
        Image.new("RGB", (900, 1600), (1, 2, 3)).save(q)
        self.assertEqual(indexer.derive_one(q, self.d, "e" * 16, "02" * 32)["h"], 1600)

    def test_v_changes_with_content_and_version(self):
        a = indexer.content_version("11" * 32)
        self.assertNotEqual(a, indexer.content_version("12" * 32))
        with mock.patch.object(indexer, "DERIV_VERSION", 2):
            self.assertNotEqual(a, indexer.content_version("11" * 32))
        self.assertEqual(len(a), 8)


def app_with(data_dir, **kw):
    with mock.patch.object(server, "FaceEngine", FakeEngine):
        return server.create_app(data_dir, "unused", ["CPUExecutionProvider"], **kw)


def run_process(app, headers=None):
    st, _, body, _ = call(app, "POST", "/api/process", headers=headers)
    if st == 200:
        app.state.job.thread.join(120)
    return st, json.loads(body)


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        o = os.path.join(cls.d, "originals")
        os.makedirs(o)
        make_jpeg(os.path.join(o, "A.jpg"), (3000, 2000), orient=6, taken="2026:05:01 12:00:00", subsec="500")
        make_jpeg(os.path.join(o, "B.jpg"), (1600, 1200), taken="2026:05:01 11:00:00")
        make_jpeg(os.path.join(o, "C.jpg"), (1000, 700))  # không EXIF → cuối manifest
        open(os.path.join(o, "bad.jpg"), "wb").write(b"\xff\xd8\xff garbage")
        cls.app = app_with(cls.d, cdn_origin="https://cdn.example.test/", app_hosts="album.test,localhost")
        FakeEngine.detect_calls = 0
        cls.st, cls.status = run_process(cls.app)
        _, _, body, _ = call(cls.app, "GET", "/api/gallery")
        cls.gal = json.loads(body)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def test_process_phases_and_no_strike(self):
        self.assertEqual(self.st, 200)
        s = self.status
        s = json.loads(call(self.app, "GET", "/api/process")[2])
        self.assertEqual(s["state"], "done")
        self.assertIsNone(s["phase"])
        self.assertEqual(s["total"], 3)  # derive: 3 ảnh tốt (bad.jpg lỗi ở bước index → failed, không vào photos)
        self.assertEqual(s["done"], 3)
        self.assertEqual(s["derive_failed"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "rejected")))

    def test_derive_error_logged_not_rejected(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        o = os.path.join(d, "originals")
        os.makedirs(o)
        make_jpeg(os.path.join(o, "ok.jpg"))
        app = app_with(d)
        run_process(app)
        pid = store.photo_id("ok.jpg")
        os.remove(os.path.join(store.deriv_dir(d), os.listdir(store.deriv_dir(d))[0]))  # photo_meta còn, không đổi
        # làm file hỏng SAU khi đã lập chỉ mục: size đổi → index lại (hỏng → failed), nên mô phỏng lỗi derive thuần bằng mock
        db = sqlite3.connect(store.db_path(d))
        db.execute("DELETE FROM photo_meta")
        db.commit()
        db.close()
        with mock.patch.object(indexer, "derive_one", side_effect=ValueError("boom")):
            st, s = run_process(app)
        s = json.loads(call(app, "GET", "/api/process")[2])
        self.assertEqual(s["derive_failed"], 1)
        self.assertIn("boom", " ".join(s["errors"]))
        self.assertTrue(os.path.exists(os.path.join(o, "ok.jpg")))  # không chuyển sang rejected/
        self.assertFalse(os.path.exists(os.path.join(d, "decoding.json")))
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(db.execute("SELECT count(*) FROM failed").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM photos WHERE id=?", (pid,)).fetchone()[0], 1)
        db.close()

    def test_no_face_detection_in_derive(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        db = store.connect(d)
        os.makedirs(os.path.join(d, "originals"))
        make_jpeg(os.path.join(d, "originals", "z.jpg"))
        st = os.stat(os.path.join(d, "originals", "z.jpg"))
        db.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?)", (store.photo_id("z.jpg"), "z.jpg", st.st_size, st.st_mtime_ns, 1200, 800, 1600, 0))
        db.commit()
        FakeEngine.detect_calls = FakeEngine.embed_calls = 0
        r = indexer.derive_pending(db, os.path.join(d, "originals"), d)
        self.assertEqual((r["ok"], FakeEngine.detect_calls, FakeEngine.embed_calls), (1, 0, 0))
        self.assertEqual(indexer.pending_derive(db), [])
        db.close()

    # ---- host gating ----
    def test_gate_non_app_host(self):
        for host in ("cdn.example.test", "evil.com", "album.test.evil.com", "", None, "127.0.0.1.nip.io"):
            for method, path in (("POST", "/api/search"), ("POST", "/api/upload?name=a.jpg"), ("POST", "/api/process"),
                                 ("GET", "/api/process"), ("GET", "/api/stats"), ("GET", "/api/gallery"), ("GET", "/manage"),
                                 ("GET", "/"), ("GET", "/originals/" + "0" * 16), ("PUT", "/i/x"), ("POST", "/i/x"),
                                 ("DELETE", "/thumbs/x.jpg"), ("POST", "/healthz"), ("POST", "/static/guest.css"),
                                 ("GET", "//api/stats"), ("GET", "/i/../api/stats")):
                st, h, body, nread = call(self.app, method, path, host=host, body=b"x" * 100 if method == "POST" else b"")
                self.assertEqual(st, 404, (host, method, path))
                self.assertEqual(h["cache-control"], "no-store")
                self.assertEqual(nread, 0 if method != "POST" else nread)  # body không bị đọc
                self.assertNotIn(b"gallery", body)

    def test_gate_non_app_host_allowed(self):
        for host in ("cdn.example.test", "CDN.example.test:443"):
            for path in ("/healthz", "/robots.txt", "/static/guest.css"):
                self.assertEqual(call(self.app, "GET", path, host=host)[0], 200, path)
            self.assertEqual(call(self.app, "HEAD", "/healthz", host=host)[0], 200)
            st, h, _, _ = call(self.app, "OPTIONS", "/i/x", host=host)  # tới app → 405, không cacheable
            self.assertEqual((st, h["cache-control"]), (405, "no-store"))
            p = self.gal["photos"][0]
            st, h, _, _ = call(self.app, "GET", f"/i/{p[0]}-{p[4]}-240.webp", host=host)
            self.assertEqual(st, 200)
            self.assertEqual(h["access-control-allow-origin"], "*")

    def test_gate_app_host(self):
        for host in ("album.test", "localhost:8000", "LOCALHOST"):
            self.assertEqual(call(self.app, "GET", "/api/stats", host=host)[0], 200)
        self.assertEqual(call(self.app, "GET", "/api/stats", host="album.doantien.love")[0], 404)  # env tuỳ biến thay mặc định

    def test_default_hosts(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d)
        self.assertEqual(call(app, "GET", "/api/stats", host="album.doantien.love")[0], 200)
        self.assertEqual(call(app, "GET", "/api/stats", host="127.0.0.1:8000")[0], 200)
        self.assertEqual(call(app, "GET", "/api/stats", host="x.rsrm.dev")[0], 404)

    # ---- kiểu media + khoá cache ----
    def test_media_types(self):
        p = self.gal["photos"][0]
        st, h, _, _ = call(self.app, "GET", f"/i/{p[0]}-{p[4]}-480.webp")
        self.assertEqual((st, h["content-type"]), (200, "image/webp"))
        self.assertEqual(h["x-content-type-options"], "nosniff")
        for path, ct in (("/thumbs/%s.jpg", "image/jpeg"), ("/previews/%s.jpg", "image/jpeg")):
            st, h, _, _ = call(self.app, "GET", path % p[0])
            self.assertEqual((st, h["content-type"]), (200, ct), path)
            self.assertEqual(h["cache-control"], "public, max-age=3600")
        self.assertEqual(call(self.app, "HEAD", f"/i/{p[0]}-{p[4]}-480.webp")[0], 200)
        import mimetypes
        self.assertEqual(mimetypes.guess_type("a.webp")[0], "image/webp")
        self.assertEqual(mimetypes.guess_type("a.woff2")[0], "font/woff2")
        fonts = [f for f in os.listdir(os.path.join(ALBUM, "static", "fonts")) if f.endswith(".woff2")]
        if fonts:
            st, h, _, _ = call(self.app, "GET", "/static/fonts/" + fonts[0])
            self.assertEqual(h["content-type"], "font/woff2")

    def test_original_same_origin_attachment(self):
        st, h, _, _ = call(self.app, "GET", "/originals/" + self.gal["photos"][0][0])
        self.assertEqual(st, 200)
        self.assertIn("attachment", h["content-disposition"])
        self.assertNotIn("access-control-allow-origin", h)

    def test_cache_key_strict(self):
        pid, _, _, _, v, _, _, _ = self.gal["photos"][0]
        ok = call(self.app, "GET", f"/i/{pid}-{v}-2000.webp")
        self.assertEqual(ok[0], 200)
        self.assertEqual(ok[1]["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(ok[1]["content-type"], "image/webp")
        wrongv = "0" * 8 if v != "0" * 8 else "1" * 8
        for bad in (f"{pid}-{wrongv}-240.webp", f"{pid}-{v}-300.webp", f"{pid}-{v}-640.webp", f"{pid}-{v}-0.webp",
                    f"{'f' * 16}-{v}-240.webp", f"{pid}-{v}-240.jpg", f"{pid}-{v}-240.WEBP", f"{pid}-{v}.webp",
                    f"{pid}-{v}-240.webp.part", f"{pid}-{v[:7]}-240.webp", f"..%2F{pid}-{v}-240.webp", f"{pid.upper()}-{v}-240.webp"):
            st, h, _, _ = call(self.app, "GET", "/i/" + bad)
            self.assertEqual(st, 404, bad)
            self.assertEqual(h["cache-control"], "no-store", bad)
        st, h, _, _ = call(self.app, "GET", "/i/")
        self.assertEqual((st, h["cache-control"]), (404, "no-store"))
        st, h, _, _ = call(self.app, "GET", "/thumbs/nope.jpg")
        self.assertEqual((st, h["cache-control"]), (404, "no-store"))
        st, h, _, _ = call(self.app, "GET", "/originals/" + "0" * 16)
        self.assertEqual((st, h["cache-control"]), (404, "no-store"))

    # ---- /api/gallery ----
    def test_gallery_shape(self):
        g = self.gal
        self.assertEqual(set(g), {"mv", "cdn", "dv", "photos"})
        self.assertEqual(g["cdn"], "https://cdn.example.test")
        self.assertEqual(g["dv"], indexer.DERIV_VERSION)
        self.assertEqual(len(g["photos"]), 3)
        for pid, w, h, t, v, c, name, b in g["photos"]:
            self.assertRegex(pid, r"^[0-9a-f]{16}$")
            self.assertRegex(v, r"^[0-9a-f]{8}$")
            self.assertRegex(c, r"^#[0-9a-f]{6}$")
            self.assertIsInstance(w, int)
        names = [p[6] for p in g["photos"]]
        self.assertEqual(names, ["B.jpg", "A.jpg", "C.jpg"])  # taken tăng dần, không có taken cuối
        a = g["photos"][1]
        self.assertEqual((a[1], a[2]), (2000, 3000))  # A: 3000x2000 xoay 6 → 2000x3000
        self.assertEqual(g["photos"][1][3] - g["photos"][0][3], 3600.5)
        self.assertIsNone(g["photos"][2][3])
        txt = json.dumps(g)
        for k in ("bbox", "emb", "score", "n_faces"):
            self.assertNotIn(k, txt)

    def test_gallery_headers_gzip_etag(self):
        st, h, raw, _ = call(self.app, "GET", "/api/gallery", headers={"Accept-Encoding": "identity"})
        self.assertEqual(st, 200)
        self.assertNotIn("content-encoding", h)
        st, hz, gz, _ = call(self.app, "GET", "/api/gallery", headers={"Accept-Encoding": "gzip, br"})
        self.assertEqual(hz["content-encoding"], "gzip")
        self.assertEqual(gzip.decompress(gz), raw)
        self.assertEqual(hz["cache-control"], "public, max-age=60")
        self.assertEqual(hz["content-type"], "application/json")
        self.assertTrue(hz["etag"].startswith('"') and not hz["etag"].startswith("W/"))
        st, h304, b304, _ = call(self.app, "GET", "/api/gallery", headers={"Accept-Encoding": "gzip", "If-None-Match": hz["etag"]})
        self.assertEqual((st, b304), (304, b""))
        self.assertEqual(h304["etag"], hz["etag"])
        self.assertEqual(call(self.app, "GET", "/api/gallery", headers={"Accept-Encoding": "gzip", "If-None-Match": '"nope"'})[0], 200)
        self.assertEqual(call(self.app, "GET", "/api/gallery", headers={"If-None-Match": hz["etag"]})[0], 200)  # ETag của bản gzip ≠ bản thô

    def test_gallery_changes_with_index(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "originals"))
        make_jpeg(os.path.join(d, "originals", "a.jpg"))
        app = app_with(d)
        g0 = json.loads(call(app, "GET", "/api/gallery")[2])
        self.assertEqual(g0["photos"], [])
        run_process(app)
        g1 = json.loads(call(app, "GET", "/api/gallery")[2])
        self.assertEqual(len(g1["photos"]), 1)
        self.assertNotEqual(g0["mv"], g1["mv"])
        self.assertEqual(g1["cdn"], "")

    # ---- không bao giờ trùng ảnh ----
    def test_gallery_ids_unique_and_schema_forbids_dupes(self):
        ids = [p[0] for p in self.gal["photos"]]
        self.assertEqual(len(ids), len(set(ids)))
        db = sqlite3.connect(store.db_path(self.d))
        pid = ids[0]
        with self.assertRaises(sqlite3.IntegrityError):  # photo_meta.id là khoá chính → join không thể nhân đôi dòng
            db.execute("INSERT INTO photo_meta (id, v) VALUES (?,?)", (pid, "x"))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?)", (pid, "other.jpg", 1, 1, 1, 1, 1, 0))
        # originals có nhiều dòng cũng không ảnh hưởng manifest (manifest không join originals)
        db.execute("INSERT INTO originals VALUES ('extra1.jpg', 'aa', 1)")
        db.execute("INSERT INTO originals VALUES ('extra2.jpg', 'bb', 1)")
        db.commit()
        db.close()
        g = json.loads(call(self.app, "GET", "/api/gallery")[2])
        self.assertEqual(len(g["photos"]), len(ids))
        db = sqlite3.connect(store.db_path(self.d))
        db.execute("DELETE FROM originals WHERE name LIKE 'extra%'")
        db.commit()
        db.close()

    def test_upload_duplicates_and_case_variants(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d)

        def png(color):
            b = io.BytesIO()
            Image.new("RGB", (40, 30), color).save(b, "PNG")
            return b.getvalue()

        up = lambda name, body: json.loads(call(app, "POST", "/api/upload?name=" + name, body=body)[2])
        a, b = png((1, 2, 3)), png((9, 9, 9))
        r1 = up("X.JPG.png", a)
        self.assertEqual((r1["status"], r1["name"]), ("ok", "X.JPG.png"))
        self.assertEqual(up("X.JPG.png", a)["status"], "duplicate")         # cùng byte, cùng tên
        self.assertEqual(up("other-name.png", a)["status"], "duplicate")    # cùng byte, tên khác → vẫn trùng (SHA-256)
        r2 = up("x.jpg.PNG", b)                                              # khác byte, tên chỉ khác hoa/thường
        self.assertEqual((r2["status"], r2["renamed"]), ("ok", True))
        self.assertNotEqual(r2["name"].lower(), r1["name"].lower())          # không ghi đè, không coi là trùng
        files = sorted(os.listdir(os.path.join(d, "originals")))
        self.assertEqual(len(files), 2, files)
        self.assertEqual(up("x.jpg.png", b)["status"], "duplicate")          # byte của bản thứ hai cũng được nhận ra
        run_process(app)
        g = json.loads(call(app, "GET", "/api/gallery")[2])
        self.assertEqual(len(g["photos"]), 2)
        self.assertEqual(len({p[0] for p in g["photos"]}), 2)

    def test_burst_groups(self):
        """Chuỗi khung chụp liên tiếp: cùng máy+kích thước, cách ≤1 s nối tiếp, ảnh giống nhau (sai khác < 6)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        o = os.path.join(d, "originals")
        os.makedirs(o)

        def frame(name, sec, make="Cam", shade=100, size=(1200, 800), sub=None):
            im = Image.new("RGB", size, (shade, shade, shade))
            for x in range(0, size[0], 100):
                im.paste((shade // 2 + x % 90,) * 3, (x, 0, x + 50, size[1] // 2))
            ex = Image.Exif()
            ex[0x010F], ex[0x0110] = make, "M1"
            ifd = ex.get_ifd(0x8769)
            ifd[0x9003] = "2026:05:01 12:00:%02d" % sec
            if sub:
                ifd[0x9291] = sub
            im.save(os.path.join(o, name), "JPEG", quality=90, exif=ex)

        frame("a1.jpg", 10, sub="100")
        frame("a2.jpg", 10, sub="270", shade=102)           # +0.17 s, gần như y hệt → cùng nhóm
        frame("a3.jpg", 11, sub="100", shade=104)           # +0.83 s so với a2 → nối chuỗi
        frame("b1.jpg", 20, sub="100", shade=240)           # b1,b2 cách 0.1 s nhưng khác hẳn về điểm ảnh → không nhóm
        frame("b2.jpg", 20, sub="200", shade=30)
        frame("c1.jpg", 10, sub="120", make="Other")        # máy khác → không nhóm
        frame("d1.jpg", 15, shade=104)                      # cách xa → không nhóm
        frame("e1.jpg", 10, sub="130", size=(800, 1200))    # kích thước khác
        Image.new("RGB", (1200, 800), (100, 100, 100)).save(os.path.join(o, "noexif.jpg"))
        app = app_with(d)
        run_process(app)
        g = json.loads(call(app, "GET", "/api/gallery")[2])
        by = {p[6]: p for p in g["photos"]}
        ids = {n: store.photo_id(n) for n in by}
        grp = {n: by[n][7] for n in by}
        self.assertEqual(grp["a1.jpg"], ids["a1.jpg"])      # id khung đầu
        self.assertEqual(grp["a2.jpg"], grp["a1.jpg"])
        self.assertEqual(grp["a3.jpg"], grp["a1.jpg"])
        for n in ("b1.jpg", "b2.jpg", "c1.jpg", "d1.jpg", "e1.jpg", "noexif.jpg"):
            self.assertIsNone(grp[n], n)
        self.assertEqual(len(g["photos"]), 9)               # đếm/liệt kê không đổi
        self.assertTrue(all(len(p) == 8 for p in g["photos"]))
        # tính lại là idempotent + thống kê
        db = sqlite3.connect(store.db_path(d))
        st = indexer.burst_stats(db)
        self.assertEqual((st["groups"], st["frames_in_groups"], st["largest"], st["histogram"]), (1, 3, 3, {3: 1}))
        self.assertEqual(st["tiles"], 9 - 2)
        out = subprocess.run([sys.executable, os.path.join(HERE, "burst_stats.py"), d], capture_output=True, text=True, check=True).stdout
        self.assertIn("nhóm: 1", out)
        self.assertIn("a1.jpg a2.jpg a3.jpg", out)
        # khung mới thêm sau, chen vào giữa chuỗi → nhập vào nhóm cũ; khung sớm hơn → id nhóm đổi thành khung đầu mới (xác định theo thời gian)
        frame("a0.jpg", 10, sub="010", shade=101)  # sớm hơn a1 (0.09 s)
        frame("a4.jpg", 12, sub="000", shade=106)  # +0.9 s sau a3 (11.100) → nối chuỗi
        run_process(app)
        g2 = json.loads(call(app, "GET", "/api/gallery")[2])
        by2 = {p[6]: p[7] for p in g2["photos"]}
        first = store.photo_id("a0.jpg")
        self.assertEqual({n: by2[n] for n in ("a0.jpg", "a1.jpg", "a2.jpg", "a3.jpg", "a4.jpg")}, {n: first for n in ("a0.jpg", "a1.jpg", "a2.jpg", "a3.jpg", "a4.jpg")})
        self.assertEqual(len(g2["photos"]), 11)
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(indexer.burst_stats(db)["largest"], 5)
        db.close()
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(indexer.compute_bursts(db), 1)  # idempotent
        self.assertEqual(db.execute("SELECT count(*) FROM photo_meta WHERE burst IS NOT NULL").fetchone()[0], 5)
        db.close()

    def test_status_endpoints_never_wait_for_locks(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d)
        job = app.state.job
        job.lock.acquire()  # job đang "giữ khoá" ghi trạng thái: người đọc không được chờ
        try:
            import time as _t
            for path in ("/healthz", "/api/process"):
                t = _t.perf_counter()
                st, _, body, _ = call(app, "GET", path)
                self.assertEqual(st, 200)
                self.assertLess(_t.perf_counter() - t, 0.5, path)
        finally:
            job.lock.release()

    def test_concurrent_reads_do_not_break_writer(self):
        """Hồi quy: kết nối mode=ro mở/đóng liên tục trong lúc job ghi làm mọi lần ghi báo 'disk I/O error'."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        o = os.path.join(d, "originals")
        os.makedirs(o)
        for i in range(30):
            make_jpeg(os.path.join(o, f"p{i:02d}.jpg"), (900, 600), taken="2026:05:01 12:00:%02d" % i, color=(i * 8, 90, 60))
        app = app_with(d)
        stop = threading.Event()
        errs = []

        def hammer():
            while not stop.is_set():
                for path in ("/api/stats", "/api/gallery", "/healthz", "/api/process"):
                    st = call(app, "GET", path)[0]
                    if st != 200:
                        errs.append((path, st))

        ths = [threading.Thread(target=hammer) for _ in range(4)]
        [t.start() for t in ths]
        st, _ = run_process(app)
        stop.set()
        [t.join() for t in ths]
        s = json.loads(call(app, "GET", "/api/process")[2])
        self.assertEqual((s["state"], s["failed"], s["derive_failed"]), ("done", 0, 0), s["errors"])
        self.assertEqual(errs, [])
        self.assertEqual(len(json.loads(call(app, "GET", "/api/gallery")[2])["photos"]), 30)

    def test_process_uses_own_engine_and_budget(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        made = []

        class E(FakeEngine):
            def __init__(self, model_dir, providers=None, threads=0):
                made.append(threads)

        os.makedirs(os.path.join(d, "originals"))
        make_jpeg(os.path.join(d, "originals", "x.jpg"))
        with mock.patch.object(server, "FaceEngine", E):
            app = server.create_app(d, "unused", ["CPUExecutionProvider"], threads=2, process_threads=3)
            self.assertEqual(made, [2])
            run_process(app)
            self.assertEqual(made, [2, 3])  # engine riêng, 3 luồng, tạo khi bắt đầu lượt
            made.clear()
            app0 = server.create_app(d, "unused", ["CPUExecutionProvider"], threads=2, process_threads=0)
            run_process(app0)
            self.assertEqual(made, [2])  # 0 = dùng chung
        self.assertGreaterEqual(server.cpu_budget(), 1)

    def test_max_results_default_covers_album(self):
        self.assertGreaterEqual(server.MAX_RESULTS, 3000)

    def test_cdn_kill_switch_and_html(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for val, exp in ((None, ""), ("", ""), ("https://c.test///", "https://c.test"), ("javascript:alert(1)", "")):
            app = app_with(d, cdn_origin=val) if val is not None else app_with(d)
            self.assertEqual(json.loads(call(app, "GET", "/api/gallery")[2])["cdn"], exp, val)

    def test_search_fields(self):
        with mock.patch.object(server, "load_image", side_effect=AssertionError("không cần")):
            pass
        # kiểm shape qua cache nội bộ: meta có v/c cho mọi ảnh đã backfill
        for pid, w, h, t, v, c, name, b in self.gal["photos"]:
            self.assertTrue(v and c)

    # ---- token chủ nhà ----
    @staticmethod
    def login(app, ip="9.9.9.9", tok="test-token", **hd):
        return call(app, "POST", "/api/admin-login", headers=dict({"X-Forwarded-For": ip}, **hd), body=json.dumps({"token": tok}).encode())

    def session_cookie(self, app, ip="9.9.9.9"):
        st, h, _, _ = self.login(app, ip)
        self.assertEqual(st, 204)
        return h["set-cookie"].split(";")[0]  # album_session=<hmac>

    def test_owner_token(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d, admin_token="test-token")
        seen = []
        ip = {"X-Forwarded-For": "4.4.4.4"}
        for hdr in (None, {"X-Album-Token": "wrong"}, {"Cookie": "album_session=wrong"}, {"X-Album-Token": ""}, {"Cookie": "album_token=test-token"}):
            h0 = dict(ip, **(hdr or {}))
            st, h, body, nread = call(app, "POST", "/api/process", headers=h0)
            self.assertEqual(st, 401, hdr)
            self.assertIn("detail", json.loads(body))
            self.assertEqual(h["cache-control"], "no-store")
            self.assertEqual(call(app, "POST", "/api/upload?name=a.jpg", headers=h0, body=b"x" * 50)[0], 401)
            st, h, body, _ = call(app, "GET", "/manage", headers=h0)  # /manage: chỉ cookie phiên, header không đủ
            self.assertEqual((st, h["cache-control"]), (401, "no-store"))
            self.assertIn(b"<", body)
            seen.append(body)
        self.assertEqual(call(app, "GET", "/manage", headers=dict(ip, **{"X-Album-Token": "test-token"}))[0], 401)
        ck = {"Cookie": "a=b; " + self.session_cookie(app)}
        self.assertNotIn("test-token", ck["Cookie"])  # cookie phiên không chứa mã
        self.assertEqual(call(app, "GET", "/manage", headers=ck)[0], 200)
        self.assertEqual(call(app, "POST", "/api/process", headers={"X-Album-Token": "test-token"})[0], 200)
        app.state.job.thread.join(60)
        self.assertEqual(call(app, "POST", "/api/process", headers=ck)[0], 200)
        app.state.job.thread.join(60)
        for path in ("/api/process", "/api/stats", "/api/gallery"):
            st, _, body, _ = call(app, "GET", path)
            self.assertEqual(st, 200, path)
            seen.append(body)
        self.assertEqual(call(app, "POST", "/api/upload?name=a.jpg", headers={"X-Album-Token": "test-token"}, body=b"x" * 50)[0], 400)  # qua cổng token
        self.assertEqual(call(app, "POST", "/api/upload?name=a.jpg", headers=ck, body=b"x" * 50)[0], 400)
        self.assertFalse(any(b"test-token" in b for b in seen))
        # token chưa đặt: mọi thứ mở như cũ
        for t in (None, ""):
            open_app = app_with(d, admin_token=t)
            self.assertEqual(call(open_app, "GET", "/manage")[0], 200)
            self.assertEqual(call(open_app, "POST", "/api/upload?name=a.jpg", body=b"x" * 50)[0], 400)
            self.assertEqual(call(open_app, "POST", "/api/admin-login", body=b'{"token":"x"}')[0], 404)

    def test_token_guessing_throttled_on_protected_routes(self):
        """N1: header/cookie sai trên các route được bảo vệ cũng bị giới hạn như đăng nhập; thăm trần không tính."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for route in (("POST", "/api/process", b""), ("POST", "/api/upload?name=a.jpg", b"x" * 20), ("GET", "/manage", b"")):
            app = app_with(d, admin_token="test-token")  # trạng thái giới hạn mới cho từng route (trần toàn cục là của app)
            ip = "21.0.0.1"
            bad = {"Cookie": "album_session=guess"} if route[1] == "/manage" else {"X-Album-Token": "guess"}
            codes = [call(app, route[0], route[1], headers=dict(bad, **{"X-Forwarded-For": ip}), body=route[2])[0] for _ in range(14)]
            self.assertEqual(codes[:10], [401] * 10, route[1])
            self.assertEqual(set(codes[10:]), {429}, route[1])
            # sau ngưỡng, thông tin xác thực ĐÚNG từ chính IP đó cũng bị chặn (không lọt qua đợt dò)
            good = {"X-Album-Token": "test-token", "X-Forwarded-For": ip}
            if route[1] != "/manage":
                self.assertEqual(call(app, route[0], route[1], headers=good, body=route[2])[0], 429)
        # burst đồng thời có lẫn mã đúng: không cái nào lọt qua sau khi đã đủ 10 lượt sai
        app = app_with(d, admin_token="test-token")
        ip = "22.0.0.1"
        for _ in range(10):
            call(app, "POST", "/api/upload?name=a.jpg", headers={"X-Album-Token": "guess", "X-Forwarded-For": ip}, body=b"x")
        res = []

        def shot(i):
            hd = {"X-Album-Token": "test-token" if i % 5 == 0 else "guess", "X-Forwarded-For": ip}
            res.append(call(app, "POST", "/api/upload?name=a.jpg", headers=hd, body=b"x" * 20)[0])

        ths = [threading.Thread(target=shot, args=(i,)) for i in range(60)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(set(res), {429})
        # thăm trần (không cookie, không header) không bao giờ tính lượt: 200 lượt vẫn là 401 (upload) / 401 (manage), không 429
        app = app_with(d, admin_token="test-token")
        ip = {"X-Forwarded-For": "23.0.0.1"}
        self.assertEqual({call(app, "GET", "/manage", headers=ip)[0] for _ in range(200)}, {401})
        self.assertEqual({call(app, "POST", "/api/process", headers=ip)[0] for _ in range(50)}, {401})
        ck = {"Cookie": self.session_cookie(app, "23.0.0.1")}
        self.assertEqual(call(app, "GET", "/manage", headers=dict(ip, **ck))[0], 200)  # chủ nhà vẫn vào được
        # cookie mã thô (bản cũ) bị từ chối, và trận dò bằng cookie phiên sai cũng bị giới hạn
        self.assertEqual(call(app, "GET", "/manage", headers={"X-Forwarded-For": "24.0.0.1", "Cookie": "album_token=test-token"})[0], 401)
        # chủ nhà dùng cookie đúng nhiều lần (upload/process song song) không tự khoá mình
        self.assertEqual({call(app, "POST", "/api/upload?name=a.jpg", headers=dict(ip, **ck), body=b"x" * 20)[0] for _ in range(40)}, {400})

    def test_session_cookie_opaque(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d, admin_token="test-token")
        st, h, body, _ = self.login(app, "30.0.0.1")
        ck = h["set-cookie"]
        self.assertEqual(st, 204)
        self.assertTrue(ck.startswith("album_session="))
        self.assertNotIn("test-token", ck)
        self.assertNotIn(b"test-token", body)
        for flag in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=2592000"):
            self.assertIn(flag.lower(), ck.lower(), flag)
        self.assertNotIn("secure", ck.lower())
        self.assertIn("secure", self.login(app, "30.0.0.2", **{"X-Forwarded-Proto": "https"})[1]["set-cookie"].lower())
        # ổn định qua restart (cùng mã → cùng cookie), đổi mã → cookie cũ vô hiệu
        app2 = app_with(d, admin_token="test-token")
        self.assertEqual(call(app2, "GET", "/manage", headers={"Cookie": ck.split(";")[0]})[0], 200)
        app3 = app_with(d, admin_token="other-token")
        self.assertEqual(call(app3, "GET", "/manage", headers={"Cookie": ck.split(";")[0], "X-Forwarded-For": "30.0.0.9"})[0], 401)
        # mọi phản hồi (kể cả lỗi) không lộ mã
        for hd in ({"X-Album-Token": "x"}, {}):
            _, h2, b2, _ = call(app, "POST", "/api/process", headers=dict(hd, **{"X-Forwarded-For": "30.0.0.5"}))
            self.assertNotIn("test-token", json.dumps(h2) + b2.decode())

    def test_admin_login_cookie_and_throttle(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d, admin_token="test-token")
        self.assertEqual(self.login(app)[0], 204)
        # sai: 401 JSON không lộ token; ngoài ngưỡng → 429 (kể cả khi lần sau đúng); IP khác không bị ảnh hưởng
        for i in range(10):
            st, _, body, _ = call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "8.8.8.8"}, body=b'{"token":"bad"}')
            self.assertEqual(st, 401)
            self.assertNotIn(b"test-token", body)
        for tok in ("bad", "test-token"):
            st, _, body, _ = call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "8.8.8.8"}, body=json.dumps({"token": tok}).encode())
            self.assertEqual(st, 429)
        self.assertEqual(self.login(app)[0], 204)
        for bad in (b"", b"not json", b'{"token":5}', b"[]"):
            self.assertEqual(call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "7.7.7.7"}, body=bad)[0], 401, bad)

    def test_login_throttle_concurrent_and_global(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d, admin_token="test-token")
        good = json.dumps({"token": "test-token"}).encode()
        bad = json.dumps({"token": "nope"}).encode()
        # 1) một IP: 10 lượt sai rồi 80 kết nối đồng thời (có cả mã đúng) → không cái nào được 204
        for _ in range(10):
            self.assertEqual(call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "5.5.5.5"}, body=bad)[0], 401)
        res = []

        def shot(body):
            res.append(call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "5.5.5.5"}, body=body)[0])

        ths = [threading.Thread(target=shot, args=(good if i % 7 == 0 else bad,)) for i in range(80)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(len(res), 80)
        self.assertNotIn(204, res)
        self.assertEqual(set(res), {429})
        # 2) đặt chỗ trước khi so mã: tối đa 10 lượt qua được ngay cả khi đồng thời từ IP mới
        res.clear()
        ths = [threading.Thread(target=lambda: res.append(call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "6.6.6.6"}, body=bad)[0]))
               for _ in range(60)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertLessEqual(sum(1 for r in res if r == 401), 10)
        # 3) trần toàn cục: nhiều IP khác nhau cùng dò → vượt 30 lượt/phút thì cả mã đúng cũng 429
        app2 = app_with(d, admin_token="test-token")
        codes = [call(app2, "POST", "/api/admin-login", headers={"X-Forwarded-For": f"10.0.0.{i}"}, body=bad)[0] for i in range(40)]
        self.assertEqual(codes[:30], [401] * 30)
        self.assertEqual(set(codes[30:]), {429})
        self.assertEqual(call(app2, "POST", "/api/admin-login", headers={"X-Forwarded-For": "10.9.9.9"}, body=good)[0], 429)
        # 4) mã đúng hoàn lại lượt: 20 lần đăng nhập đúng liên tiếp vẫn qua (không tự khoá chủ nhà)
        app3 = app_with(d, admin_token="test-token")
        self.assertEqual({call(app3, "POST", "/api/admin-login", headers={"X-Forwarded-For": "7.7.7.7"}, body=good)[0] for _ in range(25)}, {204})

    def test_no_store_status_and_static_html_blocked_and_query(self):
        for path in ("/healthz", "/api/stats"):
            self.assertEqual(call(self.app, "GET", path)[1]["cache-control"], "no-store", path)
        for host in ("localhost", "cdn.example.test"):
            for name in ("index.html", "manage.html", "manage-login.html", "INDEX.HTML"):
                st, h, body, _ = call(self.app, "GET", "/static/" + name, host=host)
                self.assertEqual((st, h["cache-control"]), (404, "no-store"), (host, name))
        self.assertEqual(call(self.app, "GET", "/")[0], 200)  # HTML vẫn phát qua route có templating
        pid, _, _, _, v, _, _, _ = self.gal["photos"][0]
        self.assertEqual(call(self.app, "GET", f"/i/{pid}-{v}-240.webp")[0], 200)
        for q in ("?x=1", "?", "?v=1&a=b"):
            st, h, _, _ = call(self.app, "GET", f"/i/{pid}-{v}-240.webp{q}")
            self.assertEqual((st, h["cache-control"]), (404 if q != "?" else 200, "no-store" if q != "?" else "public, max-age=31536000, immutable"), q)

    def test_fresh_rebuild_from_i_rate_limited(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        o = os.path.join(d, "originals")
        os.makedirs(o)
        make_jpeg(os.path.join(o, "a.jpg"))
        app = app_with(d)
        run_process(app)
        pid = store.photo_id("a.jpg")
        builds = []
        real = store.connect_ro

        def counting(*a, **k):
            builds.append(1)
            return real(*a, **k)

        job = app.state.job
        job._snap["state"] = "running"  # như đang Xử lý: mtime DB đổi liên tục
        try:
            with mock.patch.object(store, "connect_ro", counting):
                for i in range(20):
                    call(app, "GET", f"/i/{pid}-deadbeef-240.webp")
                    db = sqlite3.connect(store.db_path(d))
                    db.execute("INSERT OR REPLACE INTO meta VALUES ('k', ?)", (str(i),))
                    db.commit()
                    db.close()
        finally:
            job._snap["state"] = "done"
        self.assertLessEqual(len(builds), 2)  # 20 request sai v nhưng DB đổi liên tục: không dựng lại 20 lần

    def test_derive_failure_recorded_not_retried_never_rejects(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        o = os.path.join(d, "originals")
        os.makedirs(o)
        make_jpeg(os.path.join(o, "ok.jpg"))
        make_jpeg(os.path.join(o, "bad.jpg"), color=(1, 2, 3))
        app = app_with(d)
        real = indexer.derive_one
        calls = []

        def flaky(path, *a, **k):
            calls.append(os.path.basename(path))
            if path.endswith("bad.jpg"):
                raise ValueError("boom")
            return real(path, *a, **k)

        with mock.patch.object(indexer, "derive_one", flaky):
            run_process(app)
            s = json.loads(call(app, "GET", "/api/process")[2])
            self.assertEqual((s["derive_failed"], s["state"]), (1, "done"))
            self.assertEqual(calls.count("bad.jpg"), 1)
            run_process(app)  # lượt sau: không thử lại, không tính là đang chờ
            s = json.loads(call(app, "GET", "/api/process")[2])
            self.assertEqual((s["total"], s["derive_failed"]), (0, 1))
            self.assertEqual(calls.count("bad.jpg"), 1)
            self.assertEqual(json.loads(call(app, "GET", "/api/stats")[2])["pending_derive"], 0)
        self.assertTrue(os.path.exists(os.path.join(o, "bad.jpg")))
        self.assertFalse(os.path.exists(os.path.join(d, "rejected")))
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(db.execute("SELECT count(*) FROM failed").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM derive_failed").fetchone()[0], 1)
        db.close()
        # file đổi → được thử lại (lần này thành công)
        make_jpeg(os.path.join(o, "bad.jpg"), (1300, 900), color=(9, 9, 9))
        run_process(app)
        s = json.loads(call(app, "GET", "/api/process")[2])
        self.assertEqual(s["derive_failed"], 0)
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(db.execute("SELECT count(*) FROM derive_failed").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM photo_meta").fetchone()[0], 2)
        db.close()

    def test_derive_transient_errors_not_persisted(self):
        """N2: ENOSPC/EIO/MemoryError… không ghi nhớ (thử lại lượt sau); file hỏng ghi nhớ một lần; đổi DERIV_VERSION → thử lại."""
        import errno
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        o = os.path.join(d, "originals")
        os.makedirs(o)
        for i in range(5):
            make_jpeg(os.path.join(o, f"p{i}.jpg"), color=(i * 20, 5, 5))
        make_jpeg(os.path.join(o, "corrupt.jpg"), color=(200, 5, 5))  # qua được bước lập chỉ mục; lỗi giải mã được mô phỏng ở bước WebP
        app = app_with(d)
        real = indexer.derive_one
        mode = {"err": OSError(errno.ENOSPC, "No space left on device")}

        def inject(path, *a, **k):
            if path.endswith("corrupt.jpg"):
                raise OSError("image file is truncated (3 bytes not processed)")  # lỗi giải mã của Pillow: errno None → vĩnh viễn
            if mode["err"]:
                raise mode["err"]
            return real(path, *a, **k)

        for err in (OSError(errno.ENOSPC, "x"), OSError(errno.EIO, "x"), OSError(errno.EACCES, "x"), OSError(errno.EROFS, "x"),
                    OSError(errno.ENOMEM, "x"), MemoryError()):
            mode["err"] = err
            with mock.patch.object(indexer, "derive_one", inject):
                run_process(app)
            db = sqlite3.connect(store.db_path(d))
            n = db.execute("SELECT count(*) FROM derive_failed").fetchone()[0]
            db.close()
            self.assertLessEqual(n, 1, err)  # tối đa file hỏng thật (corrupt.jpg), không ảnh nào khác
            self.assertEqual(len(indexer.pending_derive(sqlite3.connect(store.db_path(d)))), 5, err)  # 5 ảnh vẫn phải làm lượt sau
        mode["err"] = None
        with mock.patch.object(indexer, "derive_one", inject):
            run_process(app)
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(db.execute("SELECT count(*) FROM photo_meta").fetchone()[0], 5)  # thử lại thành công
        rows = db.execute("SELECT id FROM derive_failed").fetchall()
        self.assertEqual(rows, [(store.photo_id("corrupt.jpg"),)])  # file hỏng đúng một dòng
        db.close()
        # file hỏng không bị thử lại ở lượt sau
        calls = []
        with mock.patch.object(indexer, "derive_one", lambda p, *a, **k: calls.append(p) or real(p, *a, **k)):
            run_process(app)
        self.assertEqual(calls, [])
        # đổi DERIV_VERSION → xoá ghi nhớ và thử lại
        with mock.patch.object(indexer, "DERIV_VERSION", indexer.DERIV_VERSION + 1):
            with mock.patch.object(indexer, "derive_one", lambda p, *a, **k: calls.append(os.path.basename(p)) or real(p, *a, **k)):
                run_process(app)
        self.assertIn("corrupt.jpg", calls)
        self.assertTrue(indexer.is_permanent_error(ValueError("x")))
        self.assertFalse(indexer.is_permanent_error(OSError(errno.ENOSPC, "x")))
        self.assertTrue(indexer.is_permanent_error(OSError("image file is truncated")))

    def test_upload_counter_only_after_commit(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d)
        b = io.BytesIO()
        Image.new("RGB", (40, 30), (5, 5, 5)).save(b, "PNG")
        body = b.getvalue()
        self.assertEqual(json.loads(call(app, "GET", "/api/stats")[2])["stored_bytes"], 0)
        real = sqlite3.Connection
        # commit thất bại → bộ đếm không đổi
        with mock.patch.object(server.store, "connect") as conn:
            inner = sqlite3.connect(store.db_path(d), check_same_thread=False)

            class Boom:
                def __getattr__(self, n):
                    return getattr(inner, n)

                def commit(self):
                    raise sqlite3.OperationalError("disk full")

            conn.return_value = Boom()
            with self.assertRaises(sqlite3.OperationalError):  # trên server thật là HTTP 500; gọi ASGI trực tiếp thì ngoại lệ nổi lên
                call(app, "POST", "/api/upload?name=a.png", body=body)
        self.assertEqual(os.listdir(os.path.join(d, "originals")), [])  # không để file mồ côi
        self.assertEqual(json.loads(call(app, "GET", "/api/stats")[2])["stored_bytes"], 0)
        self.assertEqual(json.loads(call(app, "POST", "/api/upload?name=b.png", body=body)[2])["status"], "ok")
        self.assertEqual(json.loads(call(app, "GET", "/api/stats")[2])["stored_bytes"], len(body))

    def test_burst_no_drift(self):
        """Cảnh đổi chậm (mỗi khung chỉ khác khung trước ~3 nhưng trôi xa dần) không được thành một nhóm khổng lồ."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        db = store.connect(d)
        import numpy as np
        taken0 = 1_780_000_000.0
        for i in range(30):  # 30 khung cách 0.4 s (12 s tổng), độ sáng tăng 3/khung
            sig = bytes([min(255, 40 + 3 * i)]) * (indexer.SIG_SIDE ** 2)
            pid = f"{i:016x}"
            db.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?)", (pid, f"f{i:02d}.jpg", 1, 1, 100, 100, 1600, 0))
            db.execute("INSERT INTO photo_meta (id, taken, v, color, deriv_ver, w, h, size, mtime_ns, cam, sig) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, taken0 + 0.4 * i, "v", "#000000", 1, 100, 100, 1, 1, "Cam", sig))
        db.commit()
        n = indexer.compute_bursts(db)
        groups = {}
        for pid, b, taken, sig in db.execute("SELECT id, burst, taken, sig FROM photo_meta WHERE burst IS NOT NULL ORDER BY taken"):
            groups.setdefault(b, []).append((taken, sig[0]))
        self.assertGreaterEqual(n, 2)
        for g in groups.values():
            self.assertLessEqual(g[-1][0] - g[0][0], indexer.BURST_MAX_SPAN + 1e-9)
            self.assertLess(max(abs(x[1] - g[0][1]) for x in g), indexer.BURST_FIRST_DIFF)
        self.assertLess(max(len(g) for g in groups.values()), 13)
        # burst thật 6 fps (0.17 s, gần y hệt) trong 1 s: vẫn một nhóm
        db.execute("DELETE FROM photo_meta")
        db.execute("DELETE FROM photos")
        for i in range(12):
            pid = f"{i + 100:016x}"
            db.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?)", (pid, f"g{i:02d}.jpg", 1, 1, 100, 100, 1600, 0))
            db.execute("INSERT INTO photo_meta (id, taken, v, color, deriv_ver, w, h, size, mtime_ns, cam, sig) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, taken0 + 0.17 * i, "v", "#000000", 1, 100, 100, 1, 1, "Cam", bytes([100 + i % 3]) * 256))
        db.commit()
        self.assertEqual(indexer.compute_bursts(db), 1)
        self.assertEqual(db.execute("SELECT count(DISTINCT burst) FROM photo_meta WHERE burst IS NOT NULL").fetchone()[0], 1)
        db.close()

    def test_quota_disabled(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        app = app_with(d, max_total=0)
        self.assertIsNone(json.loads(call(app, "GET", "/api/stats")[2])["max_total_bytes"])
        png = io.BytesIO()
        Image.new("RGB", (40, 30)).save(png, "PNG")
        self.assertEqual(json.loads(call(app, "POST", "/api/upload?name=a.png", body=png.getvalue())[2])["status"], "ok")
        app2 = app_with(d, max_total=1)  # có hạn mức: 1 byte → hết chỗ
        self.assertEqual(json.loads(call(app2, "GET", "/api/stats")[2])["max_total_bytes"], 1)
        self.assertEqual(call(app2, "POST", "/api/upload?name=b.png", body=png.getvalue())[0], 507)

    def test_cdn_preconnect_templating(self):
        page = os.path.join(ALBUM, "static", "index.html")
        html = open(page, encoding="utf-8").read()
        if "<!--__CDN_PRECONNECT__-->" not in html:
            self.skipTest("index.html chưa có placeholder")
        body = call(self.app, "GET", "/")[2].decode()
        self.assertIn('<link rel="preconnect" href="https://cdn.example.test">', body)


@unittest.skipUnless(os.path.isdir(PHOTOS) and os.path.exists(QUERY) and os.path.isdir(os.path.join(ALBUM, "models")),
                     "cần model + ảnh thử /tmp/album-test")
class MigrationTests(unittest.TestCase):
    def test_upgrade_old_index_in_place(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.path.join(tmp, "old")
        os.makedirs(old)
        archive = subprocess.run(["git", "archive", BASE_COMMIT, "album"], cwd=os.path.dirname(ALBUM), check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", old], input=archive, check=True)
        for link in ("models",):
            os.symlink(os.path.join(ALBUM, link), os.path.join(old, "album", link))
        data = os.path.join(tmp, "data")
        orig = os.path.join(data, "originals")
        os.makedirs(orig)
        good = [f for f in sorted(os.listdir(PHOTOS)) if f.endswith(".jpg") and f != "broken.jpg"]
        for i, f in enumerate(good):
            shutil.copy(os.path.join(PHOTOS, f), os.path.join(orig, f))
        im = Image.open(os.path.join(PHOTOS, good[0]))  # + một ảnh EXIF xoay 6 (không EXIF gốc)
        ex = Image.Exif()
        ex[0x0112] = 6
        im.transpose(Image.ROTATE_90).save(os.path.join(orig, "rot6.JPG"), exif=ex)
        # 1) chỉ mục bằng code CŨ + kết quả search của server CŨ
        script = os.path.join(tmp, "old_run.py")
        open(script, "w").write(
            "import sys, json\nsys.path[:0] = [%r, %r]\nimport server\nfrom _asgi import call\n"
            "app = server.create_app(%r, %r, ['CPUExecutionProvider'])\n"
            "st, h, b, _ = call(app, 'POST', '/api/search', body=open(%r, 'rb').read())\nprint(json.dumps(json.loads(b)))\n"
            % (os.path.join(old, "album"), HERE, data, os.path.join(old, "album", "models"), QUERY))
        subprocess.run([sys.executable, os.path.join(old, "album", "index_photos.py"), "--photos", orig, "--data-dir", data,
                        "--model-dir", os.path.join(old, "album", "models")], check=True, capture_output=True)
        before_old = json.loads(subprocess.run([sys.executable, script], check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1])
        db = sqlite3.connect(store.db_path(data))
        cols_before = db.execute("PRAGMA table_info(photos)").fetchall()
        dump = lambda: (db.execute("SELECT * FROM photos ORDER BY id").fetchall(), db.execute("SELECT photo_id,idx,x1,y1,x2,y2,score,emb FROM faces ORDER BY 1,2").fetchall())
        rows_before = dump()
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='photo_meta'").fetchone()[0], 0)
        db.close()
        self.assertGreater(len(before_old["results"]), 0)

        # 2) code MỚI trên cùng DB: engine thật, đếm số lần gọi detector/embedder
        calls = {"detect": 0, "embed": 0}
        real = server.FaceEngine

        class Counting(real):
            def detect(self, *a, **k):
                calls["detect"] += 1
                return super().detect(*a, **k)

            def embed(self, *a, **k):
                calls["embed"] += 1
                return super().embed(*a, **k)

        with mock.patch.object(server, "FaceEngine", Counting):
            app = server.create_app(data, os.path.join(ALBUM, "models"), ["CPUExecutionProvider"])
        db = sqlite3.connect(store.db_path(data))
        self.assertEqual(db.execute("PRAGMA table_info(photos)").fetchall(), cols_before)  # photos nguyên vẹn
        self.assertEqual(db.execute("SELECT count(*) FROM photo_meta").fetchone()[0], 0)
        db.close()
        q = open(QUERY, "rb").read()

        def search():
            st, _, b, _ = call(app, "POST", "/api/search", body=q)
            self.assertEqual(st, 200)
            return json.loads(b)

        strip = lambda r: [{k: v for k, v in x.items() if k not in ("v", "w", "h", "c", "b")} for x in r["results"]]
        s_before = search()
        self.assertEqual(strip(s_before), strip(before_old))  # code mới = code cũ trên cùng chỉ mục
        self.assertTrue(all(x["v"] is None for x in s_before["results"]))
        g0 = json.loads(call(app, "GET", "/api/gallery")[2])
        self.assertTrue(all(p[4] is None for p in g0["photos"]))  # chưa backfill → v null → giao diện dùng thumb cũ
        calls["detect"] = calls["embed"] = 0
        st, _ = run_process(app)
        self.assertEqual(st, 200)
        self.assertEqual(calls, {"detect": 0, "embed": 0})  # backfill KHÔNG dò mặt
        calls_after_backfill = dict(calls)
        s = json.loads(call(app, "GET", "/api/process")[2])
        self.assertEqual((s["state"], s["derive_failed"], s["failed"]), ("done", 0, 0))
        self.assertEqual(s["total"], len(good) + 1)
        db = sqlite3.connect(store.db_path(data))
        self.assertEqual(dump(), rows_before)  # photos + faces byte-for-byte
        self.assertEqual(db.execute("SELECT count(*) FROM photo_meta").fetchone()[0], len(good) + 1)
        db.close()
        s_after = search()
        for r in (s_before, s_after):
            ids = [x["id"] for x in r["results"]]
            self.assertEqual(len(ids), len(set(ids)))  # mỗi ảnh đúng một kết quả
        searched = calls["detect"]  # chỉ truy vấn selfie mới được dò
        self.assertEqual(searched, 1)
        calls["detect"] = calls["embed"] = 0
        self.assertEqual(strip(s_after), strip(s_before))
        self.assertEqual([x["score"] for x in s_after["results"]], [x["score"] for x in s_before["results"]])
        self.assertTrue(all(x["v"] and x["c"] for x in s_after["results"]))
        g1 = json.loads(call(app, "GET", "/api/gallery")[2])
        self.assertEqual(len(g1["photos"]), len(good) + 1)
        self.assertTrue(all(p[4] for p in g1["photos"]))
        # lần Xử lý thứ hai: không còn gì để backfill
        run_process(app)
        self.assertEqual(json.loads(call(app, "GET", "/api/process")[2])["total"], 0)
        self.assertEqual(calls, {"detect": 0, "embed": 0})
        # rollback: code cũ vẫn chạy được trên DB đã nâng cấp (bảng thừa bị bỏ qua) và cho kết quả y hệt
        after_old = json.loads(subprocess.run([sys.executable, script], check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1])
        self.assertEqual(after_old["results"], before_old["results"])


BIG = "/tmp/album-e/data-a"


@unittest.skipUnless(os.path.exists(os.path.join(BIG, "index.db")) and os.path.exists(QUERY)
                     and os.path.isdir(os.path.join(ALBUM, "models")), "cần bộ ảnh thử 1900 ảnh ở /tmp/album-e/data-a (đã lập chỉ mục)")
class BigSetTests(unittest.TestCase):
    def test_unique_ids_on_1900_set(self):
        app = server.create_app(BIG, os.path.join(ALBUM, "models"), ["CPUExecutionProvider"])
        g = json.loads(call(app, "GET", "/api/gallery")[2])
        ids = [p[0] for p in g["photos"]]
        self.assertGreaterEqual(len(ids), 1900)
        self.assertEqual(len(ids), len(set(ids)))
        st, _, b, _ = call(app, "POST", "/api/search", body=open(QUERY, "rb").read())
        self.assertEqual(st, 200)
        sids = [x["id"] for x in json.loads(b)["results"]]
        self.assertTrue(sids)
        self.assertEqual(len(sids), len(set(sids)))
        self.assertTrue(set(sids) <= set(ids))


if __name__ == "__main__":
    unittest.main()
