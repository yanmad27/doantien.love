"""Test ẩn/hiện ảnh (workstream F): bảng photo_hidden + cài đặt tìm kiếm, API chủ nhà, token ảnh, rò rỉ công khai.
Engine giả (không cần model): mỗi ảnh mang một "thẻ người" trong khối 32x32 ở góc trên trái → embedding one-hot theo thẻ, nên tìm kiếm
trả đúng tập ảnh cùng thẻ. Ảnh tổng hợp, không dùng ảnh thật.   .venv/bin/python -W ignore -m unittest discover -s dev -p 'test_*.py'"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ALBUM = os.path.dirname(HERE)
sys.path.insert(0, ALBUM)
sys.path.insert(0, HERE)
import index_photos  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
from _asgi import call  # noqa: E402
from faces import Face  # noqa: E402

TOKEN = "tok-f"
# (tên, giây, phần thập phân, độ sáng): a/b/c là chuỗi chụp liên tiếp; s1,s2 đứng một mình; n1 không EXIF
FRAMES = [("a1", 10, "000", 100), ("a2", 10, "200", 101), ("a3", 10, "400", 102), ("b1", 20, "000", 150), ("b2", 20, "200", 151),
          ("c1", 30, "000", 60), ("c2", 30, "200", 61), ("c3", 30, "400", 62), ("s1", 40, "000", 200), ("s2", 50, "000", 30)]
NAMES = [f[0] for f in FRAMES] + ["n1"]
PID = {n: store.photo_id(n + ".jpg") for n in NAMES}
NAME_OF = {v: k for k, v in PID.items()}


class TagEngine:
    """detect: một "mặt" cố định; embed: one-hot theo thẻ người đọc ở điểm (16,16) của ảnh. hold: Event chặn Xử lý (chỉ luồng 'process')."""
    hold = None

    def __init__(self, *a, **k):
        pass

    def detect(self, rgb, size):
        if TagEngine.hold is not None and threading.current_thread().name == "process":
            TagEngine.hold.wait(30)
        return [Face(bbox=np.array([40.0, 40.0, 240.0, 240.0]), score=0.99, kps=np.zeros((5, 2)))]

    def embed(self, rgb, faces):
        k = int(round((int(rgb[16, 16, 0]) - 10) / 20))
        for f in faces:
            f.emb = np.zeros(512, np.float32)
            f.emb[k] = 1.0


def jpeg(path, shade=128, tag=1, exif=None):
    size = (400, 300)
    im = Image.new("RGB", size, (shade,) * 3)
    for x in range(0, size[0], 40):
        im.paste((shade // 2 + x % 90,) * 3, (x, 0, x + 20, size[1] // 2))
    im.paste((20 * tag + 10, 128, 128), (0, 0, 32, 32))
    kw = {"exif": exif} if exif is not None else {}
    im.save(path, "JPEG", quality=95, **kw)


def build_originals(d, skip=(), tags=None):
    o = os.path.join(d, "originals")
    os.makedirs(o, exist_ok=True)
    tags = tags or {}
    for name, sec, sub, shade in FRAMES:
        if name in skip:
            continue
        ex = Image.Exif()
        ex[0x010F], ex[0x0110] = "Cam", "M1"
        ifd = ex.get_ifd(0x8769)
        ifd[0x9003] = "2026:05:01 12:00:%02d" % sec
        ifd[0x9291] = sub
        jpeg(os.path.join(o, name + ".jpg"), shade, tags.get(name, 1), ex)
    if "n1" not in skip:
        jpeg(os.path.join(o, "n1.jpg"), 90, tags.get("n1", 1))
    return o


def make_app(d, **kw):
    kw.setdefault("admin_token", TOKEN)
    with mock.patch.object(server, "FaceEngine", TagEngine):
        return server.create_app(d, "unused", ["CPUExecutionProvider"], **kw)


def process(app):
    st, _, body, _ = call(app, "POST", "/api/process", headers=cookie_hdr(app))
    assert st == 200, (st, body)
    app.state.job.thread.join(120)


def cookie_hdr(app):
    if not hasattr(app.state, "ck"):
        st, h, _, _ = call(app, "POST", "/api/admin-login", headers={"X-Forwarded-For": "7.7.7.7"}, body=json.dumps({"token": TOKEN}).encode())
        assert st == 204
        app.state.ck = {"Cookie": h["set-cookie"].split(";")[0]}
    return dict(app.state.ck)


def post(app, path, obj, headers=None, raw=None, ctype="application/json"):
    h = cookie_hdr(app) if headers is None else headers
    h = dict(h, **({"Content-Type": ctype} if ctype else {}))
    return call(app, "POST", path, headers=h, body=raw if raw is not None else json.dumps(obj).encode())


def hide(app, names, hidden=True):
    st, _, body, _ = post(app, "/api/admin/visibility", {"ids": [PID[n] for n in names], "hidden": hidden})
    assert st == 200, (st, body)
    return json.loads(body)


def gallery(app, headers=None):
    st, h, body, _ = call(app, "GET", "/api/gallery", headers=headers)
    return st, h, body


def gal_rows(app):
    return json.loads(gallery(app)[2])["photos"]


QUERY_BODY = None


def query_bytes(d, tag=1):
    p = os.path.join(d, "q.jpg")
    jpeg(p, 128, tag)
    return open(p, "rb").read()


def search(app, q):
    st, _, body, _ = call(app, "POST", "/api/search", body=q)
    assert st == 200, (st, body)
    return json.loads(body)


def strip_search(r):
    return {k: v for k, v in r.items() if k != "timing_ms"} | {"results": [{k: v for k, v in x.items() if k != "t"} for x in r["results"]]}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.orig = build_originals(self.d)
        self.app = make_app(self.d)
        process(self.app)
        self.q = query_bytes(self.d)


class HiddenTests(Base):
    def test_gallery_hide_unhide_etag_roundtrip(self):
        st, h0, b0 = gallery(self.app)
        self.assertEqual((st, h0["cache-control"]), (200, "no-cache"))
        self.assertEqual(len(json.loads(b0)["photos"]), 11)
        out = hide(self.app, ["a1", "s2"])
        self.assertEqual((out["changed"], out["unknown"], out["counts"]), (2, 0, dict(all=11, shown=9, hidden=2)))
        st, h1, b1 = gallery(self.app)
        self.assertNotEqual(h1["etag"], h0["etag"])
        for n in ("a1", "s2"):
            self.assertNotIn(PID[n].encode(), b1)
        self.assertEqual(len(json.loads(b1)["photos"]), 9)
        self.assertEqual(gallery(self.app, {"If-None-Match": h0["etag"]})[0], 200)  # ETag cũ không còn khớp
        self.assertEqual(gallery(self.app, {"If-None-Match": h1["etag"]})[0], 304)
        out = hide(self.app, ["a1", "s2"], hidden=False)
        self.assertEqual(out["changed"], 2)
        st, h2, b2 = gallery(self.app)
        self.assertEqual((h2["etag"], b2), (h0["etag"], b0))  # hiện lại = ETag và body y hệt ban đầu
        self.assertEqual(gallery(self.app, {"If-None-Match": h0["etag"]})[0], 304)

    def test_hide_is_idempotent_and_counts_join_photos(self):
        self.assertEqual(hide(self.app, ["a1", "a2"])["changed"], 2)
        out = hide(self.app, ["a1", "a2", "a3"])
        self.assertEqual((out["changed"], out["counts"]["hidden"]), (1, 3))
        ghost = "f" * 16
        st, _, body, _ = post(self.app, "/api/admin/visibility", {"ids": [ghost, PID["a1"], PID["a1"]], "hidden": True})
        out = json.loads(body)
        self.assertEqual((st, out["changed"], out["unknown"], out["counts"]["hidden"]), (200, 0, 1, 3))  # trùng id được gộp; id lạ không ghi
        db = sqlite3.connect(store.db_path(self.d))
        self.assertEqual(db.execute("SELECT count(*) FROM photo_hidden WHERE id=?", (ghost,)).fetchone()[0], 0)
        db.execute("INSERT INTO photo_hidden VALUES (?, 1)", (ghost,))  # dòng mồ côi không được tính vào counts
        db.commit()
        db.close()
        out = hide(self.app, ["s1"])
        self.assertEqual(out["counts"], dict(all=11, shown=7, hidden=4))
        self.assertEqual(hide(self.app, ["a1", "a2", "a3", "s1"], hidden=False)["counts"], dict(all=11, shown=11, hidden=0))
        self.assertEqual(hide(self.app, ["a1"], hidden=False)["changed"], 0)

    def test_burst_remap_cases(self):
        by = lambda: {p[6]: p for p in gal_rows(self.app)}
        base = by()
        self.assertEqual({base[n + ".jpg"][7] for n in ("a1", "a2", "a3")}, {PID["a1"]})
        hide(self.app, ["a1"])  # khung đầu bị ẩn → nhóm công khai lấy khung hiện đầu tiên
        g = by()
        self.assertNotIn("a1.jpg", g)
        self.assertEqual((g["a2.jpg"][7], g["a3.jpg"][7]), (PID["a2"], PID["a2"]))
        hide(self.app, ["c1", "c2"])  # nhóm 3 khung chỉ còn 1 hiện → b null
        g = by()
        self.assertIsNone(g["c3.jpg"][7])
        hide(self.app, ["b1", "b2"])  # ẩn hết → không còn dòng nào của nhóm
        g = by()
        self.assertFalse({"b1.jpg", "b2.jpg", "c1.jpg", "c2.jpg"} & set(g))
        body = gallery(self.app)[2]
        self.assertTrue(all(PID[n].encode() not in body for n in ("a1", "b1", "b2", "c1", "c2")))  # không id ẩn nào, kể cả làm b
        shown = {p[0] for p in json.loads(body)["photos"]}
        for p in json.loads(body)["photos"]:
            self.assertTrue(p[7] is None or p[7] in shown)

    def test_search_setting_on_and_off_with_counts(self):
        r = search(self.app, self.q)
        self.assertNotIn("indexed_photos", r)
        self.assertNotIn("indexed_faces", r)
        self.assertEqual((r["total_matches"], len(r["results"]), r["truncated"]), (11, 11, False))
        hide(self.app, ["a1", "b1", "s2"])
        r = search(self.app, self.q)  # mặc định BẬT: ảnh ẩn vẫn trúng, không cờ
        self.assertEqual((r["total_matches"], len(r["results"])), (11, 11))
        self.assertTrue(all(x["t"] for x in r["results"]))  # visible và hidden đều mang t
        self.assertTrue(all(not any(k in x for k in ("hidden", "h_", "is_hidden")) for x in r["results"]))
        st, _, body, _ = post(self.app, "/api/admin/settings", {"search_includes_hidden": False})
        self.assertEqual((st, json.loads(body)), (200, {"settings": {"search_includes_hidden": False}}))
        r = search(self.app, self.q)
        ids = [x["id"] for x in r["results"]]
        self.assertEqual((r["total_matches"], len(ids), len(set(ids))), (8, 8, 8))
        self.assertFalse({PID["a1"], PID["b1"], PID["s2"]} & set(ids))
        with mock.patch.object(server, "MAX_RESULTS", 5):  # lọc ẩn TRƯỚC cắt: tổng/cờ cắt theo ảnh hiện
            r = search(self.app, self.q)
            self.assertEqual((r["total_matches"], len(r["results"]), r["truncated"]), (8, 5, True))
            self.assertFalse({PID["a1"], PID["b1"], PID["s2"]} & {x["id"] for x in r["results"]})
        st, _, body, _ = post(self.app, "/api/admin/settings", {"search_includes_hidden": True})
        self.assertEqual(search(self.app, self.q)["total_matches"], 11)
        # bền qua khởi động lại
        post(self.app, "/api/admin/settings", {"search_includes_hidden": False})
        app2 = make_app(self.d)
        self.assertEqual(search(app2, self.q)["total_matches"], 8)
        g = json.loads(call(app2, "GET", "/api/admin/photos", headers=cookie_hdr(app2))[2])
        self.assertEqual(g["settings"], {"search_includes_hidden": False})

    def test_search_on_burst_rules(self):
        # a1,a2 ẩn; a3 hiện: hai khung ẩn cùng trúng → b = khung đầu trong các trúng; khung hiện duy nhất → b null
        hide(self.app, ["a1", "a2", "c1"])
        r = search(self.app, self.q)
        b = {NAME_OF[x["id"]]: x["b"] for x in r["results"]}
        self.assertEqual((b["a1"], b["a2"]), (PID["a1"], PID["a1"]))
        self.assertIsNone(b["a3"])
        self.assertIsNone(b["c1"])  # một khung ẩn lẻ loi
        self.assertEqual((b["c2"], b["c3"]), (PID["c2"], PID["c2"]))  # nhóm công khai = hai khung hiện
        self.assertEqual((b["b1"], b["b2"]), (PID["b1"], PID["b1"]))
        ids = {x["id"] for x in r["results"]}
        for x in r["results"]:
            self.assertTrue(x["b"] is None or x["b"] in ids)  # không có id nhóm của khung ngoài phản hồi
        # chỉ một số khung trúng (thẻ khác): H ẩn + V1 hiện cùng nhóm → ô không tách, không lộ id lạ
        d2 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d2, True)
        build_originals(d2, tags={"a2": 2, "a3": 2})  # chỉ a1 trúng thẻ 1 trong nhóm a; b,c... trúng
        app = make_app(d2)
        process(app)
        hide(app, ["a1"])  # a1 (trúng) ẩn; a2,a3 không trúng
        r = search(app, query_bytes(d2))
        got = {NAME_OF[x["id"]]: x["b"] for x in r["results"]}
        self.assertIsNone(got["a1"])  # một khung ẩn trúng, không có bạn cùng nhóm trong phản hồi
        hide(app, ["a1"], hidden=False)
        hide(app, ["a2"])
        d3 = query_bytes(d2, tag=2)  # trúng a2 (ẩn) + a3 (hiện)
        got = {NAME_OF[x["id"]]: x["b"] for x in search(app, d3)["results"]}
        self.assertEqual(set(got), {"a2", "a3"})
        # a2 ẩn lẻ loi → null. a3 hiện: nhóm công khai {a1,a3} (a2 ẩn không tính) → b = a1, một ảnh HIỆN (không có trong phản hồi: theo hợp đồng
        # F, khung hiện lấy đúng b của manifest công khai; không bao giờ là id ẩn)
        self.assertEqual((got["a2"], got["a3"]), (None, PID["a1"]))
        self.assertNotIn(PID["a2"], got.values())
        # H ẩn (a1) + V1 hiện (a2) cùng nhóm cùng trúng, a3 không trúng: ô của V1 không tách, H không lộ/nhận id nhóm lạ
        d4 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d4, True)
        build_originals(d4, tags={"a3": 2})
        app4 = make_app(d4)
        process(app4)
        hide(app4, ["a1"])
        got = {NAME_OF[x["id"]]: x["b"] for x in search(app4, query_bytes(d4))["results"] if NAME_OF[x["id"]] in "a1 a2 a3"}
        self.assertEqual(got, {"a1": None, "a2": PID["a2"]})
        self.assertEqual({p[6]: p[7] for p in gal_rows(app4)}["a2.jpg"], PID["a2"])  # đúng b của manifest công khai

    def test_admin_photos_shape(self):
        hide(self.app, ["a2"])
        st, h, body, _ = call(self.app, "GET", "/api/admin/photos", headers=cookie_hdr(self.app))
        self.assertEqual((st, h["cache-control"]), (200, "no-store"))
        d = json.loads(body)
        self.assertEqual(set(d), {"photos", "counts", "settings", "by_event", "events"})
        self.assertEqual(d["counts"], dict(all=11, shown=10, hidden=1))
        self.assertEqual(d["settings"], {"search_includes_hidden": True})
        ids = [p[0] for p in d["photos"]]
        self.assertEqual((len(ids), len(set(ids))), (11, 11))  # duy nhất
        self.assertTrue(all(len(p) == 10 for p in d["photos"]))
        by = {p[6]: p for p in d["photos"]}
        self.assertEqual([by[n + ".jpg"][8] for n in ("a1", "a2", "a3")], [0, 1, 0])
        self.assertEqual({by[n + ".jpg"][7] for n in ("a1", "a2", "a3")}, {PID["a1"]})  # b = burst GỐC đầy đủ
        self.assertIsNone(by["s1.jpg"][7])
        self.assertEqual([p[6] for p in d["photos"]][-1], "n1.jpg")
        for p in d["photos"]:
            self.assertEqual(sorted(type(x).__name__ for x in p[1:3]), ["int", "int"])
        # public gallery và search cũng duy nhất
        g = [p[0] for p in gal_rows(self.app)]
        self.assertEqual(len(g), len(set(g)))
        s = [x["id"] for x in search(self.app, self.q)["results"]]
        self.assertEqual(len(s), len(set(s)))

    def test_hide_during_process_is_immediate(self):
        self.assertEqual(len(gal_rows(self.app)), 11)  # manifest vừa dựng: trong 3 s nữa Xử lý chạy sẽ trả bản nhớ
        jpeg(os.path.join(self.orig, "new.jpg"), 77, 1)
        TagEngine.hold = threading.Event()
        self.addCleanup(setattr, TagEngine, "hold", None)
        try:
            st, _, body, _ = call(self.app, "POST", "/api/process", headers=cookie_hdr(self.app))
            self.assertEqual(st, 200)
            self.assertTrue(self.app.state.job.running)
            hide(self.app, ["a1"])
            rows = gal_rows(self.app)
            self.assertNotIn(PID["a1"], [p[0] for p in rows])  # ngay lập tức, dù Xử lý đang chạy
            self.assertEqual(len(rows), 10)
            hide(self.app, ["a1"], hidden=False)
            self.assertIn(PID["a1"], [p[0] for p in gal_rows(self.app)])
            post(self.app, "/api/admin/settings", {"search_includes_hidden": False})
            hide(self.app, ["a2"])
            self.assertNotIn(PID["a2"], [x["id"] for x in search(self.app, self.q)["results"]])
        finally:
            TagEngine.hold.set()
            self.app.state.job.thread.join(60)
        self.assertEqual(len(gal_rows(self.app)), 11)  # ảnh mới xuất hiện sau Xử lý (a2 vẫn ẩn)  = 12 - 1
        db = sqlite3.connect(store.db_path(self.d))
        self.assertEqual([r[0] for r in db.execute("SELECT id FROM photo_hidden")], [PID["a2"]])  # Xử lý không đụng photo_hidden
        db.close()

    def test_image_tokens_and_routes(self):
        hide(self.app, ["a1"])
        hid, vis = PID["a1"], PID["s1"]
        r = search(self.app, self.q)
        tok = {x["id"]: x["t"] for x in r["results"]}
        self.assertIn(hid, tok)
        routes = lambda pid: [f"/originals/{pid}", f"/thumbs/{pid}.jpg", f"/previews/{pid}.jpg"]
        ghost = "e" * 16
        for pid_route, ghost_route in zip(routes(hid), routes(ghost)):
            for method in ("GET",) if "originals" in pid_route else ("GET", "HEAD"):  # /originals chỉ GET (như trước)
                a = call(self.app, method, pid_route)
                b = call(self.app, method, ghost_route)
                self.assertEqual((a[0], a[1], a[3]), (404, b[1], b[3]))
                self.assertEqual(a[2], b[2])  # y hệt id không tồn tại: body + header
                st, h, _, _ = call(self.app, method, pid_route + "?t=" + tok[hid])
                self.assertEqual((st, h["cache-control"]), (200, "private, no-store"))
                self.assertEqual(call(self.app, method, pid_route + "?t=" + tok[vis])[0], 404)  # token của ảnh khác
        for url in routes(hid):
            tt = tok[hid]
            exp, mac = tt.split(".")
            for bad in (exp + "." + ("0" if mac[0] != "0" else "1") + mac[1:], str(int(exp) + 5) + "." + mac, "x", "", mac, exp + "."):
                self.assertEqual(call(self.app, "GET", url + "?t=" + bad)[:3][0], 404, bad)
            with mock.patch.object(server.time, "time", return_value=int(exp) + 1):  # quá hạn 24 h
                self.assertEqual(call(self.app, "GET", url + "?t=" + tt)[0], 404)
            with mock.patch.object(server.time, "time", return_value=int(exp) - 1):
                self.assertEqual(call(self.app, "GET", url + "?t=" + tt)[0], 200)
        # ảnh hiện: không cần token, header như cũ
        for url in routes(vis):
            st, h, _, _ = call(self.app, "GET", url)
            self.assertEqual(st, 200)
            self.assertNotIn("no-store", h["cache-control"])
        # ảnh ẩn: /i/ vẫn phục vụ với v đúng (v không đoán được), sai v → 404
        v = next(p[4] for p in json.loads(call(self.app, "GET", "/api/admin/photos", headers=cookie_hdr(self.app))[2])["photos"] if p[0] == hid)
        self.assertEqual(call(self.app, "GET", f"/i/{hid}-{v}-240.webp")[0], 200)
        self.assertEqual(call(self.app, "GET", f"/i/{hid}-{'0' * 8}-240.webp")[0], 404)
        # bí mật ảnh: 0600, luôn có
        p = os.path.join(self.d, ".image_secret")
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
        self.assertEqual(len(open(p, "rb").read()), 32)
        # đổi bí mật (xoá file + khởi động lại) → token cũ vô hiệu
        os.remove(p)
        app2 = make_app(self.d)
        self.assertEqual(call(app2, "GET", f"/originals/{hid}?t={tok[hid]}")[0], 404)

    def test_image_secret_without_admin_token(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        make_app(d, admin_token="")
        self.assertTrue(os.path.exists(os.path.join(d, ".image_secret")))


class TokenInvalidationTests(Base):
    def tokens(self, q=None):
        return {x["id"]: x["t"] for x in search(self.app, q or self.q)["results"]}

    def code(self, pid, t, route="thumbs/{}.jpg"):
        return call(self.app, "GET", f"/{route.format(pid)}?t={t}")[0]

    def test_token_issued_while_visible_dies_when_hidden_and_survives_other_hides(self):
        tok = self.tokens()
        a1, s1 = PID["a1"], PID["s1"]
        self.assertEqual(self.code(a1, tok[a1]), 200)  # ảnh hiện: token hợp lệ (và không cần)
        hide(self.app, ["s2"])  # ẩn ảnh KHÁC: token của ảnh hiện vẫn sống
        self.assertEqual((self.code(a1, tok[a1]), self.code(s1, tok[s1])), (200, 200))
        hide(self.app, ["a1"])
        self.assertEqual(call(self.app, "GET", f"/thumbs/{a1}.jpg")[0], 404)
        self.assertEqual(self.code(a1, tok[a1]), 404)  # cấp lúc còn hiện → ẩn → hết hiệu lực
        self.assertEqual(self.code(PID["a2"], self.tokens()[PID["a2"]]), 200)

    def test_unhide_then_rehide_invalidates(self):
        hide(self.app, ["a1"])
        a1 = PID["a1"]
        t1 = self.tokens()[a1]
        self.assertEqual(self.code(a1, t1), 200)
        hide(self.app, ["a1"], hidden=False)
        hide(self.app, ["a1"])
        self.assertEqual(self.code(a1, t1), 404)
        self.assertEqual(self.code(a1, self.tokens()[a1]), 200)

    def test_setting_change_invalidates_hidden_tokens(self):
        hide(self.app, ["a1"])
        a1, s1 = PID["a1"], PID["s1"]
        tok = self.tokens()  # search ON: ảnh ẩn trúng, mang t
        self.assertEqual(self.code(a1, tok[a1]), 200)
        post(self.app, "/api/admin/settings", {"search_includes_hidden": True})  # không đổi giá trị → epoch giữ nguyên
        self.assertEqual(self.code(a1, tok[a1]), 200)
        post(self.app, "/api/admin/settings", {"search_includes_hidden": False})
        self.assertEqual(self.code(a1, tok[a1]), 404)
        self.assertEqual(self.code(s1, tok[s1]), 200)  # ảnh hiện không bị ảnh hưởng
        post(self.app, "/api/admin/settings", {"search_includes_hidden": True})
        self.assertEqual(self.code(a1, tok[a1]), 404)  # bật lại cũng không hồi sinh token cũ
        self.assertEqual(self.code(a1, self.tokens()[a1]), 200)
        app2 = make_app(self.d)  # epoch bền qua khởi động lại
        self.assertEqual(call(app2, "GET", f"/thumbs/{a1}.jpg?t={tok[a1]}")[0], 404)

    def test_legacy_cache_is_five_minutes(self):
        h = call(self.app, "GET", f"/thumbs/{PID['s1']}.jpg")[1]
        self.assertEqual(h["cache-control"], "public, max-age=300")
        self.assertEqual(call(self.app, "GET", f"/previews/{PID['s1']}.jpg")[1]["cache-control"], "public, max-age=300")


class AdminTests(Base):
    def test_fail_closed_without_admin_token(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        build_originals(d)
        app = make_app(d, admin_token="")
        process_open = call(app, "POST", "/api/process")
        self.assertEqual(process_open[0], 200)  # upload/process mở như trước khi chưa đặt mã
        app.state.job.thread.join(60)
        before = gallery(app)[2]
        for h in (None, {"Origin": "http://localhost"}, {"X-Album-Token": TOKEN}):
            self.assertEqual(call(app, "GET", "/api/admin/photos", headers=h)[0], 403)
            for path, obj in (("/api/admin/visibility", {"ids": [PID["a1"]], "hidden": True}), ("/api/admin/settings", {"search_includes_hidden": False})):
                self.assertEqual(post(app, path, obj, headers=h or {})[0], 403, (path, h))
        self.assertEqual(gallery(app)[2], before)  # không có gì đổi
        db = sqlite3.connect(store.db_path(d))
        self.assertEqual(db.execute("SELECT count(*) FROM photo_hidden").fetchone()[0], 0)
        db.close()
        for path in ("/api/stats", "/api/process"):
            self.assertEqual(call(app, "GET", path)[0], 200)  # vẫn mở như trước

    def test_ids_reject_trailing_newline(self):
        self.assertEqual(post(self.app, "/api/admin/visibility", {"ids": [PID["a1"] + "\n"], "hidden": True})[0], 400)
        self.assertEqual(len(gal_rows(self.app)), 11)

    def test_admin_only_401(self):
        ip = {"X-Forwarded-For": "5.5.5.5"}
        for hdr in (ip, dict(ip, **{"X-Album-Token": "wrong"}), dict(ip, **{"Cookie": "album_session=bad"})):
            for method, path in (("GET", "/api/admin/photos"), ("GET", "/api/stats"), ("GET", "/api/process")):
                st, h, _, _ = call(self.app, method, path, headers=hdr)
                self.assertEqual((st, h["cache-control"]), (401, "no-store"), (path, hdr))
            for path, obj in (("/api/admin/visibility", {"ids": [PID["a1"]], "hidden": True}), ("/api/admin/settings", {"search_includes_hidden": False})):
                st, h, _, nread = post(self.app, path, obj, headers=hdr)
                self.assertEqual(st, 401)
                self.assertEqual(nread, 0)  # require_admin chạy trước khi đọc body
        self.assertEqual(len(gal_rows(self.app)), 11)  # không có gì bị ẩn
        for path in ("/api/admin/photos", "/api/stats", "/api/process"):
            self.assertEqual(call(self.app, "GET", path, headers=cookie_hdr(self.app))[0], 200)
        self.assertEqual(call(self.app, "GET", "/api/stats", headers={"X-Album-Token": TOKEN})[0], 200)
        self.assertEqual(call(self.app, "GET", "/healthz")[0], 200)
        self.assertEqual(call(self.app, "GET", "/api/gallery")[0], 200)  # công khai
        self.assertEqual(call(self.app, "POST", "/api/search", body=self.q)[0], 200)

    def test_csrf_origin_and_validation(self):
        ok = {"ids": [PID["a1"]], "hidden": True}
        ck = cookie_hdr(self.app)
        for extra in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://localhost.evil.test"},
                      {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "none"},
                      {"Origin": "http://localhost", "Sec-Fetch-Site": "cross-site"}):
            for path, obj in (("/api/admin/visibility", ok), ("/api/admin/settings", {"search_includes_hidden": False})):
                self.assertEqual(post(self.app, path, obj, headers=dict(ck, **extra))[0], 403, extra)
        self.assertEqual(len(gal_rows(self.app)), 11)
        self.assertEqual(post(self.app, "/api/admin/visibility", ok, headers=dict(ck, Origin="http://localhost", **{"Sec-Fetch-Site": "same-origin"}))[0], 200)
        self.assertEqual(post(self.app, "/api/admin/visibility", ok, headers=dict(ck, Origin="http://LOCALHOST"))[0], 200)
        bad = lambda **kw: post(self.app, "/api/admin/visibility", kw.pop("obj", None), **kw)
        a1 = PID["a1"]
        cases = [dict(raw=b"{not json"), dict(raw=b"[]"), dict(raw=b"null"), dict(obj={"ids": [a1]}), dict(obj={"hidden": True}),
                 dict(obj={"ids": [a1], "hidden": "true"}), dict(obj={"ids": [a1], "hidden": 1}), dict(obj={"ids": [a1], "hidden": None}),
                 dict(obj={"ids": [], "hidden": True}), dict(obj={"ids": a1, "hidden": True}), dict(obj={"ids": [a1[:-1]], "hidden": True}),
                 dict(obj={"ids": [a1.upper()], "hidden": True}), dict(obj={"ids": [5], "hidden": True}),
                 dict(obj={"ids": [a1], "hidden": True, "extra": 1}),
                 dict(obj={"ids": ["%016x" % i for i in range(5001)], "hidden": True}),
                 dict(obj={"ids": [a1], "hidden": True}, ctype="text/plain"), dict(obj={"ids": [a1], "hidden": True}, ctype=None)]
        for c in cases:
            self.assertEqual(bad(**c)[0], 400, c)
        self.assertEqual(post(self.app, "/api/admin/visibility", {"ids": ["%016x" % i for i in range(5000)], "hidden": True})[0], 200)  # đúng 5000: được
        self.assertEqual(post(self.app, "/api/admin/visibility", {"ids": [a1], "hidden": False, "x": 1})[0], 400)
        # settings: kiểu sai
        for obj in ({"search_includes_hidden": "no"}, {"search_includes_hidden": 0}, {}, {"search_includes_hidden": True, "z": 1}):
            self.assertEqual(post(self.app, "/api/admin/settings", obj)[0], 400, obj)
        self.assertEqual(post(self.app, "/api/admin/settings", None, raw=b"xx")[0], 400)
        # body quá cỡ: 413, cả khi khai báo lẫn khi không
        big = b'{"ids":["' + b"a" * (300 * 1024) + b'"],"hidden":true}'
        st, _, _, _ = post(self.app, "/api/admin/visibility", None, raw=big)
        self.assertEqual(st, 413)

    def test_body_cap_streams_without_content_length(self):
        import asyncio
        chunks = [b'{"ids":["' + b"a" * 100000] + [b"a" * 100000] * 4
        app = self.app
        scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method="POST", scheme="http", path="/api/admin/visibility",
                     raw_path=b"/api/admin/visibility", query_string=b"", client=("1.2.3.4", 1), server=("x", 80),
                     headers=[(b"host", b"localhost"), (b"content-type", b"application/json"), (b"cookie", cookie_hdr(app)["Cookie"].encode())])
        got, served = {}, [0]

        async def receive():
            if served[0] < len(chunks):
                served[0] += 1
                return {"type": "http.request", "body": chunks[served[0] - 1], "more_body": served[0] < len(chunks)}
            await asyncio.sleep(3600)

        async def send(m):
            if m["type"] == "http.response.start":
                got["status"] = m["status"]

        asyncio.run(asyncio.wait_for(app(scope, receive, send), 30))
        self.assertEqual(got["status"], 413)
        self.assertLessEqual(served[0], 4)  # dừng đọc ngay khi vượt ~256 KB


class OwnerStateTests(Base):
    def run_cli(self, *args):
        with mock.patch.object(index_photos, "FaceEngine", TagEngine), mock.patch.object(sys, "argv", ["index_photos.py", "--photos", self.orig,
                                                                                                       "--data-dir", self.d, *args]):
            index_photos.main()

    def hidden_rows(self):
        db = sqlite3.connect(store.db_path(self.d))
        try:
            return sorted(db.execute("SELECT id FROM photo_hidden").fetchall()), db.execute("SELECT key, value FROM settings").fetchall()
        finally:
            db.close()

    def test_rebuild_prune_process_keep_photo_hidden(self):
        hide(self.app, ["a1", "s1"])
        post(self.app, "/api/admin/settings", {"search_includes_hidden": False})
        want = self.hidden_rows()
        self.assertEqual(len(want[0]), 2)
        process(self.app)  # Xử lý lại: không đụng
        self.assertEqual(self.hidden_rows(), want)
        os.remove(os.path.join(self.orig, "s1.jpg"))  # --prune: ảnh bị xoá khỏi chỉ mục nhưng dòng ẩn giữ nguyên (không khoá ngoại)
        self.run_cli("--prune")
        self.assertEqual(self.hidden_rows(), want)
        self.run_cli("--rebuild")
        self.assertEqual(self.hidden_rows(), want)
        db = sqlite3.connect(store.db_path(self.d))
        self.assertEqual(db.execute("SELECT count(*) FROM photos").fetchone()[0], 10)
        db.close()
        app = make_app(self.d)
        ids = [p[0] for p in gal_rows(app)]
        self.assertNotIn(PID["a1"], ids)
        self.assertEqual(len(ids), 9)  # 10 ảnh − a1 (s1 đã xoá, dòng ẩn của nó là mồ côi)
        c = json.loads(call(app, "GET", "/api/admin/photos", headers=cookie_hdr(app))[2])
        self.assertEqual(c["counts"], dict(all=10, shown=9, hidden=1))  # mồ côi không tính
        self.assertEqual(c["settings"], {"search_includes_hidden": False})
        self.assertNotIn(PID["a1"], [x["id"] for x in search(app, self.q)["results"]])

    def test_rebuild_backs_up_and_aborts_on_corrupt_db(self):
        hide(self.app, ["a1"])
        want = self.hidden_rows()
        dbf = store.db_path(self.d)
        baks = lambda: sorted(f for f in os.listdir(self.d) if f.startswith("index.db.bak-") and not f.endswith(("-wal", "-shm")))
        self.run_cli("--rebuild")
        self.assertEqual(self.hidden_rows(), want)
        self.assertEqual(len(baks()), 1)  # bản sao DB cũ còn trên đĩa
        bak = sqlite3.connect(os.path.join(self.d, baks()[0]))
        self.assertEqual(bak.execute("SELECT id FROM photo_hidden").fetchall(), want[0])
        bak.close()
        # DB hỏng: dừng, không xoá, trỏ tới bản sao
        open(dbf, "wb").write(b"this is not a sqlite database" * 100)
        garbage = open(dbf, "rb").read()
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("--rebuild")
        self.assertIn("index.db.bak-", str(cm.exception))
        self.assertIn("--discard-owner-state", str(cm.exception))
        self.assertEqual(open(dbf, "rb").read(), garbage)  # DB cũ chưa bị đụng
        self.assertTrue(any(open(os.path.join(self.d, f), "rb").read() == garbage for f in baks()))
        self.run_cli("--rebuild", "--discard-owner-state")  # chủ nhà chấp nhận bỏ trạng thái
        self.assertEqual(self.hidden_rows(), ([], []))
        db = sqlite3.connect(dbf)
        self.assertEqual(db.execute("SELECT count(*) FROM photos").fetchone()[0], 11)
        db.close()

    def test_new_upload_visible_and_table_created_on_old_db(self):
        db = sqlite3.connect(store.db_path(self.d))
        db.execute("DROP TABLE photo_hidden")
        db.execute("DROP TABLE settings")
        db.commit()
        db.close()
        app = make_app(self.d)  # khởi động tạo lại bảng (DB từ bản trước)
        self.assertEqual(len(gal_rows(app)), 11)
        self.assertEqual(self.hidden_rows(), ([], []))


class DifferentialLeakTest(unittest.TestCase):
    def test_hidden_equals_never_indexed(self):
        hidden = ["a1", "a3", "b1", "b2", "c2", "s2"]
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d1, True)
        self.addCleanup(shutil.rmtree, d2, True)
        build_originals(d1)
        build_originals(d2, skip=hidden)
        a1, a2 = make_app(d1), make_app(d2)
        process(a1)
        process(a2)
        hide(a1, hidden)
        post(a1, "/api/admin/settings", {"search_includes_hidden": False})
        # 1) /api/gallery: thân + ETag + header y hệt (mv suy ra từ nội dung manifest nên cũng trùng)
        for hd in (None, {"Accept-Encoding": "gzip"}):
            s1, h1, b1 = gallery(a1, hd)
            s2, h2, b2 = gallery(a2, hd)
            self.assertEqual((s1, h1, b1), (s2, h2, b2))
            self.assertEqual(len(json.loads(gzip.decompress(b1) if hd else b1)["photos"]), 5)
        # 2) tìm kiếm với cài đặt TẮT, cùng selfie (mọi thẻ), trừ token t
        for tag in (1, 2):
            q = query_bytes(d1, tag)
            r1, r2 = search(a1, q), search(a2, q)
            self.assertEqual(strip_search(r1), strip_search(r2))
        q = query_bytes(d1)
        self.assertEqual(search(a1, q)["total_matches"], 5)
        # 3) 404: ảnh ẩn ở DB-1 = id chưa từng lập chỉ mục ở DB-2, y từng byte
        for n in hidden:
            for route in (f"/originals/{PID[n]}", f"/thumbs/{PID[n]}.jpg", f"/previews/{PID[n]}.jpg"):
                x, y = call(a1, "GET", route), call(a2, "GET", route)
                self.assertEqual((x[0], x[1], x[2]), (404, y[1], y[2]), route)
        # có token hợp lệ (cài đặt BẬT, mọi kết quả mang t) → 200
        post(a1, "/api/admin/settings", {"search_includes_hidden": True})
        res = {x["id"]: x["t"] for x in search(a1, q)["results"]}
        self.assertEqual(len(res), 11)
        for n in hidden:
            for route in (f"/originals/{PID[n]}", f"/thumbs/{PID[n]}.jpg", f"/previews/{PID[n]}.jpg"):
                self.assertEqual(call(a1, "GET", route + "?t=" + res[PID[n]])[0], 200, route)
        # 4) các điểm công khai khác không phân biệt được: healthz không có số ảnh, stats/process cần cookie
        self.assertEqual(json.loads(call(a1, "GET", "/healthz")[2]).keys(), json.loads(call(a2, "GET", "/healthz")[2]).keys())


if __name__ == "__main__":
    unittest.main()
