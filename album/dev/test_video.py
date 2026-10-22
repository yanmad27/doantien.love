"""Test video phía server: tải lên theo mảnh có thể nối lại, kiểm MP4, công bố, phục vụ có Range, /api/gallery "videos", --rebuild.
MP4 là fixture tổng hợp dựng tay bằng box (không video thật).   .venv/bin/python -W ignore -m unittest discover -s dev -p 'test_*.py'"""
import asyncio
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import index_photos  # noqa: E402
import mp4box  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
from _asgi import call  # noqa: E402
from test_hidden import TOKEN, Base, cookie_hdr, gal_rows, gallery, hide, make_app, post  # noqa: E402

E4, E17 = store.EVENT_IDS


def box(typ, payload=b"", large=False, zero=False):
    n = 8 + len(payload)
    if large:
        return struct.pack(">I4sQ", 1, typ, n + 8) + payload
    return struct.pack(">I4s", 0 if zero else n, typ) + payload


def tkhd(w, h, rot=0):
    mat = {0: (0x10000, 0, 0, 0, 0x10000, 0), 90: (0, 0x10000, 0, -0x10000, 0, 0), 180: (-0x10000, 0, 0, 0, -0x10000, 0),
           270: (0, -0x10000, 0, 0x10000, 0, 0)}[rot]
    return box(b"tkhd", struct.pack(">I5I8s4H", 3, 0, 0, 1, 0, 1000, b"", 0, 0, 0, 0) + struct.pack(">9i", *mat[:6], 0, 0, 0x40000000)
               + struct.pack(">II", w << 16, h << 16))


def trak(kind, codec, w=0, h=0, rot=0):
    mdhd = box(b"mdhd", struct.pack(">I4I2H", 0, 0, 0, 1000, 1000, 0, 0))
    hdlr = box(b"hdlr", struct.pack(">I4s4s12s", 0, b"", kind, b"") + b"\0")
    stsd = box(b"stsd", struct.pack(">II", 0, 1) + box(codec, bytes(78)))
    return box(b"trak", tkhd(w, h, rot) + box(b"mdia", mdhd + hdlr + box(b"minf", box(b"stbl", stsd))))


def make_mp4(w=320, h=240, rot=0, vcodec=b"avc1", audio=b"mp4a", faststart=True, seed=b"x", mdat="normal", extra=b"", two_video=False, pad=b""):
    mvhd = box(b"mvhd", struct.pack(">I4I", 0, 0, 0, 1000, 2500) + bytes(80))
    traks = trak(b"vide", vcodec, w, h, rot) + (trak(b"vide", vcodec, w, h) if two_video else b"") + (trak(b"soun", audio) if audio else b"")
    moov = box(b"moov", mvhd + traks)
    payload = hashlib.sha256(seed).digest() * 40 + pad
    md = box(b"mdat", payload, large=mdat == "large", zero=mdat == "zero")
    ftyp = box(b"ftyp", b"isom\0\0\2\0isomavc1")
    return ftyp + (moov + md if faststart else md + moov) + extra


def jpeg_bytes(w=64, h=48, color=(10, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def abort_call(app, method, url, headers, declared, part):
    """Thân yêu cầu ngắt giữa chừng: gửi `part` rồi http.disconnect, Content-Length khai báo `declared`."""
    hs = [(b"host", b"localhost"), (b"content-length", str(declared).encode())] + [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    path, _, qs = url.partition("?")
    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method=method, scheme="http", path=path, raw_path=path.encode(),
                 query_string=qs.encode(), headers=hs, client=("1.2.3.4", 1), server=("x", 80))
    msgs = [{"type": "http.request", "body": part, "more_body": True}, {"type": "http.disconnect"}]
    out = {"status": None}

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]

    async def run():
        await asyncio.wait_for(app(scope, receive, send), 30)
    try:
        asyncio.run(run())
    except Exception:
        pass
    return out["status"]


class VBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.app = make_app(self.d)

    def hdr(self, **extra):
        return dict(cookie_hdr(self.app), **extra)

    def init(self, data, size=None, **over):
        body = dict(name="a.mp4", size=len(data) if size is None else size, sha256=sha(data), ev=E4, title="Cô dâu")
        body.update(over)
        for k in [k for k, v in body.items() if v is ...]:
            del body[k]
        st, _, out, _ = post(self.app, "/api/admin/video/init", body)
        return st, json.loads(out)

    def chunk(self, uid, off, data, headers=None):
        st, _, out, _ = call(self.app, "PUT", f"/api/admin/video/{uid}/chunk?off={off}", headers=headers or self.hdr(), body=data)
        return st, json.loads(out)

    def poster(self, uid, data, headers=None):
        st, _, out, _ = call(self.app, "PUT", f"/api/admin/video/{uid}/poster", headers=headers or self.hdr(), body=data)
        return st, json.loads(out)

    def finish(self, uid, headers=None, app=None):
        st, _, out, _ = call(app or self.app, "POST", f"/api/admin/video/{uid}/finish", headers=headers or self.hdr())
        return st, json.loads(out)

    def status(self, uid, app=None):
        st, _, out, _ = call(app or self.app, "GET", f"/api/admin/video/{uid}", headers=self.hdr())
        return st, json.loads(out)

    def session(self, data, step=1 << 20, **over):
        st, out = self.init(data, **over)
        self.assertEqual(st, 200, out)
        uid = out["uid"]
        for off in range(0, len(data), step):
            self.assertEqual(self.chunk(uid, off, data[off:off + step])[0], 200)
        return uid

    def publish(self, data=None, poster=None, **over):
        data = data or make_mp4()
        uid = self.session(data, **over)
        self.assertEqual(self.poster(uid, poster or jpeg_bytes())[0], 200)
        st, out = self.finish(uid)
        self.assertEqual(st, 200, out)
        return uid, out

    def get(self, path, headers=None, host="localhost", method="GET"):
        return call(self.app, method, path, host=host, headers=headers)

    def vrows(self):
        return json.loads(gallery(self.app)[2])["videos"]


class InitTests(VBase):
    def test_init_ok_shape(self):
        data = make_mp4()
        st, out = self.init(data)
        self.assertEqual(st, 200)
        self.assertEqual((out["offset"], out["size"], out["chunk_max"]), (0, len(data), 16 * 1024 * 1024))
        self.assertRegex(out["uid"], r"^[0-9a-f]{32}$")
        self.assertGreater(out["expires"], 0)
        self.assertTrue(os.path.isdir(os.path.join(self.d, "video-staging")))

    def test_auth_origin_and_body_rules(self):
        data = make_mp4()
        ok = dict(name="a.mp4", size=len(data), sha256=sha(data), ev=E4, title="t")
        body = json.dumps(ok).encode()
        jh = {"Content-Type": "application/json"}
        self.assertEqual(call(self.app, "POST", "/api/admin/video/init", headers=jh, body=body)[0], 401)
        self.assertEqual(call(self.app, "POST", "/api/admin/video/init", headers=dict(jh, **{"X-Album-Token": "bad"}), body=body)[0], 401)
        self.assertEqual(call(self.app, "POST", "/api/admin/video/init", headers=dict(jh, **{"X-Album-Token": TOKEN}), body=body)[0], 200)
        self.assertEqual(post(self.app, "/api/admin/video/init", ok, headers=self.hdr(Origin="http://evil.example"))[0], 403)
        self.assertEqual(post(self.app, "/api/admin/video/init", ok, headers=self.hdr(**{"Sec-Fetch-Site": "cross-site"}))[0], 403)
        self.assertEqual(post(self.app, "/api/admin/video/init", ok, headers=self.hdr(Origin="http://localhost"))[0], 200)
        self.assertEqual(post(self.app, "/api/admin/video/init", ok, ctype="text/plain")[0], 400)
        big = dict(ok, title="x" * 5000)
        self.assertEqual(post(self.app, "/api/admin/video/init", big)[0], 413)
        self.assertEqual(post(self.app, "/api/admin/video/init", None, raw=b"[1]")[0], 400)
        off = make_app(tempfile.mkdtemp(), admin_token="")  # chưa đặt ALBUM_ADMIN_TOKEN → 403
        self.addCleanup(shutil.rmtree, off.state.__dict__.get("d", ""), True)
        self.assertEqual(post(off, "/api/admin/video/init", ok, headers={})[0], 403)

    def test_field_validation(self):
        data = make_mp4()
        for over in (dict(name="a.mov"), dict(name="a.jpg"), dict(name=5), dict(name=...), dict(size=0), dict(size=-1), dict(size="9"),
                     dict(size=2 * 1024 ** 3 + 1), dict(size=True), dict(sha256="ab"), dict(sha256="z" * 64), dict(sha256=5),
                     dict(ev="2026-01-01"), dict(ev=3), dict(ev=["x"]), dict(title="x" * 121), dict(title=5), dict(title="a\nb")):
            with self.subTest(over=over):
                self.assertEqual(self.init(data, **over)[0], 400)
        self.assertEqual(self.init(data, size=2 * 1024 ** 3)[0], 200)
        self.assertEqual(self.init(make_mp4(seed=b"t"), title="x" * 120)[0], 200)

    def test_ev_optional(self):
        for i, over in enumerate((dict(ev=None), dict(ev=...))):
            data = make_mp4(seed=bytes([i]))
            st, out = self.init(data, **over)
            self.assertEqual(st, 200, out)
            uid = out["uid"]
            self.chunk(uid, 0, data)
            self.poster(uid, jpeg_bytes())
            st, res = self.finish(uid)
            self.assertEqual((st, res["ev"]), (200, None))
        self.assertEqual([r[7] for r in self.vrows()], [None, None])

    def test_idempotent_init_resumes_same_session(self):
        data = make_mp4()
        _, a = self.init(data)
        self.chunk(a["uid"], 0, data[:50])
        st, b = self.init(data)
        self.assertEqual((st, b["uid"], b["offset"]), (200, a["uid"], 50))
        self.assertEqual(self.init(data, size=len(data) + 1)[0], 409)

    def test_too_many_sessions_429(self):
        self.assertEqual(self.init(make_mp4(seed=b"1"))[0], 200)
        self.assertEqual(self.init(make_mp4(seed=b"2"))[0], 200)
        st, out = self.init(make_mp4(seed=b"3"))
        self.assertEqual((st, out["error"]), (429, "too_many_sessions"))
        self.assertEqual(self.init(make_mp4(seed=b"1"))[0], 200)  # nối lại phiên có sẵn vẫn được

    def test_disk_floor_507(self):
        data = make_mp4()
        free = types.SimpleNamespace(free=5120 * 1024 ** 2 + len(data) - 1, total=1, used=0)
        with mock.patch.object(server.shutil, "disk_usage", return_value=free):
            self.assertEqual(self.init(data)[0], 507)
        free.free += 1
        with mock.patch.object(server.shutil, "disk_usage", return_value=free):
            self.assertEqual(self.init(data)[0], 200)
            self.assertEqual(self.init(make_mp4(seed=b"o"))[0], 507)  # phần chưa nhận của phiên trước cũng được giữ chỗ

    def test_duplicate_published_409(self):
        data = make_mp4()
        _, res = self.publish(data)
        st, out = self.init(data)
        self.assertEqual((st, out), (409, dict(error="duplicate", id=res["id"])))


class ChunkTests(VBase):
    def setUp(self):
        super().setUp()
        self.data = make_mp4()
        self.uid = self.init(self.data)[1]["uid"]
        self.part = os.path.join(self.d, "video-staging", self.uid + ".part")

    def test_exact_offsets_and_status(self):
        n = len(self.data)
        self.assertEqual(self.chunk(self.uid, 0, self.data[:100]), (200, dict(offset=100)))
        self.assertEqual(self.status(self.uid)[1]["offset"], 100)
        self.assertEqual(self.chunk(self.uid, 100, self.data[100:]), (200, dict(offset=n)))
        self.assertEqual(open(self.part, "rb").read(), self.data)
        st, s = self.status(self.uid)
        self.assertEqual((st, s["state"], s["poster"], s["size"]), (200, "uploading", False, n))
        self.poster(self.uid, jpeg_bytes())
        self.assertEqual(self.status(self.uid)[1]["state"], "ready")

    def test_out_of_order_duplicate_and_overrun(self):
        self.assertEqual(self.chunk(self.uid, 10, b"abc"), (409, dict(error="offset", offset=0)))
        self.assertEqual(self.chunk(self.uid, 0, self.data[:50])[0], 200)
        self.assertEqual(self.chunk(self.uid, 0, self.data[:50]), (409, dict(error="offset", offset=50)))
        self.assertEqual(self.chunk(self.uid, 60, b"abc")[0], 409)
        self.assertEqual(self.chunk(self.uid, 50, bytes(len(self.data)))[0], 409)  # vượt size
        self.assertEqual(os.path.getsize(self.part), 50)
        self.assertEqual(call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=x", headers=self.hdr(), body=b"a")[0], 400)
        self.assertEqual(call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=50", headers=self.hdr(), body=b"")[0], 400)

    def test_oversize_chunk_413(self):
        st, _, _, _ = call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=0", headers=self.hdr(), body=bytes(server.VIDEO_CHUNK_MAX + 1))
        self.assertEqual(st, 413)
        self.assertEqual(os.path.getsize(self.part), 0)

    def test_auth_unknown_uid(self):
        self.assertEqual(call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=0", body=b"a")[0], 401)
        self.assertEqual(call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=0", headers=self.hdr(Origin="http://evil"), body=b"a")[0], 403)
        self.assertEqual(call(self.app, "PUT", f"/api/admin/video/{self.uid}/poster", body=b"a")[0], 401)
        self.assertEqual(call(self.app, "POST", f"/api/admin/video/{self.uid}/finish")[0], 401)
        self.assertEqual(call(self.app, "GET", f"/api/admin/video/{self.uid}")[0], 401)
        self.assertEqual(self.chunk("0" * 32, 0, b"a")[0], 404)
        self.assertEqual(self.chunk("../x", 0, b"a")[0], 404)
        self.assertEqual(self.status("f" * 32)[0], 404)

    def test_concurrent_chunks_one_wins(self):
        res, gate = [], threading.Barrier(2)

        def go(b):
            gate.wait()
            res.append(self.chunk(self.uid, 0, b)[0])
        ts = [threading.Thread(target=go, args=(self.data[:40],)), threading.Thread(target=go, args=(bytes(40),))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(res), [200, 409])
        self.assertEqual(os.path.getsize(self.part), 40)

    def test_busy_lock_returns_409(self):
        lk = server_lock(self.app, self.uid)
        with lk:
            self.assertEqual(self.chunk(self.uid, 0, self.data[:10]), (409, dict(error="busy", offset=0)))

    def test_aborted_body_leaves_no_bytes(self):
        st = abort_call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=0", self.hdr(), 1000, b"x" * 300)
        self.assertNotEqual(st, 200)
        self.assertEqual((self.status(self.uid)[1]["offset"], os.path.getsize(self.part)), (0, 0))
        short = call(self.app, "PUT", f"/api/admin/video/{self.uid}/chunk?off=0", headers=self.hdr(**{"Content-Length": "999"}), body=b"")
        self.assertIn(short[0], (400, 409))

    def test_write_failure_truncates_back_to_committed(self):
        self.assertEqual(self.chunk(self.uid, 0, self.data[:30])[0], 200)
        with mock.patch.object(server.os, "fsync", side_effect=OSError("disk gone")):
            st, out = self.chunk(self.uid, 30, self.data[30:90])
        self.assertEqual((st, out["offset"]), (500, 30))
        self.assertEqual(os.path.getsize(self.part), 30)
        self.assertEqual(self.chunk(self.uid, 30, self.data[30:90])[0], 200)  # thử lại được

    def test_disk_floor_during_chunk(self):
        with mock.patch.object(server.shutil, "disk_usage", return_value=types.SimpleNamespace(free=1, total=2, used=1)):
            self.assertEqual(self.chunk(self.uid, 0, b"abc")[0], 507)
        self.assertEqual(os.path.getsize(self.part), 0)


def server_lock(app, uid):
    return app.state.video_locks[uid]


class PersistenceTests(VBase):
    def test_resume_after_restart(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        self.chunk(uid, 0, data[:77])
        self.poster(uid, jpeg_bytes())
        with open(os.path.join(self.d, "video-staging", uid + ".part"), "ab") as fh:
            fh.write(b"JUNK")  # chunk dở dang lúc chết, chưa được ghi nhận
        app2 = make_app(self.d)
        st, s = self.status(uid, app=app2)
        self.assertEqual((st, s["offset"], s["poster"]), (200, 77, True))
        self.assertEqual(os.path.getsize(os.path.join(self.d, "video-staging", uid + ".part")), 77)
        self.app = app2
        self.assertEqual(self.chunk(uid, 77, data[77:])[0], 200)
        self.assertEqual(self.finish(uid)[0], 200)

    def test_expired_sessions_cleaned_at_startup(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        stage = os.path.join(self.d, "video-staging")
        meta = os.path.join(stage, uid + ".json")
        s = json.load(open(meta))
        s["expires"] = 1
        json.dump(s, open(meta, "w"))
        open(os.path.join(stage, "f" * 32 + ".part"), "wb").close()  # file mồ côi
        app2 = make_app(self.d)
        self.assertEqual(self.status(uid, app=app2)[0], 404)
        self.assertEqual(os.listdir(stage), [])

    def test_expired_session_cleaned_while_running(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        with mock.patch.object(server.time, "time", return_value=server.time.time() + 25 * 3600):
            self.assertEqual(self.status(uid)[0], 404)
            self.assertEqual(self.init(make_mp4(seed=b"n"))[0], 200)  # init dọn phiên hết hạn và không bị 429 vì nó
        self.assertFalse(os.path.exists(os.path.join(self.d, "video-staging", uid + ".part")))

    def test_old_db_without_videos_table_opens(self):
        db = sqlite3.connect(store.db_path(self.d))
        db.execute("DROP TABLE videos")
        db.commit()
        db.close()
        self.assertEqual(store.keep_videos(store.db_path(self.d)), [])
        app2 = make_app(self.d)
        self.assertEqual(json.loads(gallery(app2)[2])["videos"], [])


class PosterTests(VBase):
    def setUp(self):
        super().setUp()
        self.data = make_mp4()
        self.uid = self.session(self.data)

    def test_non_jpeg_rejected(self):
        b = io.BytesIO()
        Image.new("RGB", (8, 8)).save(b, "PNG")
        self.assertEqual(self.poster(self.uid, b.getvalue())[0], 400)
        self.assertEqual(self.poster(self.uid, b"not an image")[0], 400)
        self.assertEqual(self.poster(self.uid, b"")[0], 400)
        self.assertFalse(self.status(self.uid)[1]["poster"])

    def test_too_big_and_too_large_dimensions(self):
        self.assertEqual(self.poster(self.uid, bytes(server.POSTER_MAX_BYTES + 1))[0], 413)
        self.assertEqual(self.poster(self.uid, jpeg_bytes(4097, 8))[0], 400)
        self.assertEqual(self.poster(self.uid, jpeg_bytes(4096, 8))[0], 200)

    def test_decompression_bomb_header_rejected_before_decode(self):
        j = bytearray(jpeg_bytes())
        i = j.find(b"\xff\xc0")
        struct.pack_into(">HH", j, i + 5, 60000, 60000)  # khai báo 3.6 Gpx
        with mock.patch.object(Image.Image, "load", side_effect=AssertionError("không được giải mã")):
            self.assertEqual(self.poster(self.uid, bytes(j))[0], 400)

    def test_truncated_jpeg_rejected(self):
        self.assertEqual(self.poster(self.uid, jpeg_bytes(800, 600)[:-200] + b"")[0] in (200, 400), True)
        j = jpeg_bytes(300, 300)
        self.assertEqual(self.poster(self.uid, j[:len(j) // 2])[0], 400)

    def test_replace_poster_changes_pv(self):
        p1, p2 = jpeg_bytes(color=(1, 2, 3)), jpeg_bytes(color=(200, 100, 50))
        self.poster(self.uid, p1)
        self.poster(self.uid, p2)
        st, res = self.finish(self.uid)
        self.assertEqual((st, res["pv"]), (200, sha(p2)[:10]))
        self.assertEqual(open(os.path.join(self.d, "videos", "posters", f"{res['id']}-{res['pv']}.jpg"), "rb").read(), p2)
        self.assertEqual(self.poster(self.uid, p1)[0], 409)  # đã công bố


class Mp4BoxTests(unittest.TestCase):
    def probe(self, data):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "a.mp4")
        open(p, "wb").write(data)
        return mp4box.probe(p)

    def bad(self, data, text=""):
        with self.assertRaises(mp4box.Mp4Error) as cm:
            self.probe(data)
        self.assertIn(text, str(cm.exception))

    def test_ok_with_and_without_audio(self):
        r = self.probe(make_mp4())
        self.assertEqual((r["w"], r["h"], r["duration"], r["codec"], r["audio"]), (320, 240, 2.5, "avc1", True))
        self.assertFalse(self.probe(make_mp4(audio=None))["audio"])
        self.assertEqual(self.probe(make_mp4(vcodec=b"avc3"))["codec"], "avc3")

    def test_rotation_swaps_display_size(self):
        for rot, want in ((0, (320, 240)), (90, (240, 320)), (180, (320, 240)), (270, (240, 320))):
            r = self.probe(make_mp4(rot=rot))
            self.assertEqual((r["w"], r["h"]), want, rot)

    def test_largesize_and_size_zero_mdat(self):
        self.assertEqual(self.probe(make_mp4(mdat="large"))["w"], 320)
        self.assertEqual(self.probe(make_mp4(mdat="zero"))["w"], 320)

    def test_not_faststart(self):
        self.bad(make_mp4(faststart=False), "moov")

    def test_hevc_and_other_codecs(self):
        self.bad(make_mp4(vcodec=b"hvc1"), "H.264")
        self.bad(make_mp4(vcodec=b"hev1"), "H.264")
        self.bad(make_mp4(audio=b"ac-3"), "mp4a")

    def test_track_layout(self):
        self.bad(make_mp4(two_video=True), "một track video")

    def test_truncated(self):
        full = make_mp4()
        for cut in (1, 5, 40, len(full) - full.find(b"mdat") + 3):
            with self.subTest(cut=cut):
                self.bad(full[:-cut])
        self.bad(full[:60])  # cắt giữa moov
        self.bad(b"")
        self.bad(full + b"\0\0\0")  # đuôi thừa nhỏ hơn một header

    def test_malformed_boxes(self):
        self.bad(struct.pack(">I4s", 4, b"free") + make_mp4(), "kích thước")  # size < header
        self.bad(struct.pack(">I4sQ", 1, b"free", 10) + make_mp4(), "kích thước")  # largesize < 16
        self.bad(struct.pack(">I4s", 10 ** 9, b"free") + make_mp4(), "vượt")
        self.bad(box(b"free") + make_mp4(), "ftyp")  # không có ftyp đầu
        f = make_mp4()
        self.bad(f.replace(b"moov", b"free"), "moov")
        self.bad(box(b"ftyp", b"isom") + box(b"mdat", b"abc"), "moov")
        self.bad(box(b"ftyp", b"isom") + box(b"moov", b"\0\0\0\x20trak" + b"\0" * 8), "")  # box con vượt moov

    def test_size_zero_non_final_box_swallows_rest_and_fails(self):
        bad = box(b"ftyp", b"isom") + box(b"free", b"", zero=True) + make_mp4()[len(box(b"ftyp", b"isom\0\0\2\0isomavc1")):]
        self.bad(bad, "moov")  # size 0 nuốt hết phần còn lại → không thấy moov

    def test_mdat_payload_never_read(self):
        seen = []
        real = open

        def spy(p, *a, **k):
            fh = real(p, *a, **k)
            orig = fh.read
            fh.read = lambda n=-1: (seen.append(n), orig(n))[1]
            return fh
        data = make_mp4() + b""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "a.mp4")
        real(p, "wb").write(data)
        with mock.patch("builtins.open", spy):
            mp4box.probe(p)
        self.assertTrue(seen and max(seen) <= 64 * 1024 * 1024)
        self.assertNotIn(-1, seen)


class FinishTests(VBase):
    def test_sha_mismatch_422_and_session_freed(self):
        data = make_mp4()
        uid = self.session(data, sha256=sha(b"other"))
        self.poster(uid, jpeg_bytes())
        st, out = self.finish(uid)
        self.assertEqual((st, out["error"]), (422, "invalid"))
        self.assertIn("sha256", out["reason"])
        self.assertEqual(self.status(uid)[0], 404)
        self.assertEqual(self.vrows(), [])

    def test_invalid_mp4_422(self):
        for data, text in ((make_mp4(faststart=False), "moov"), (make_mp4(vcodec=b"hvc1"), "H.264"), (make_mp4()[:-100], "")):
            with self.subTest(text=text):
                uid = self.session(data)
                self.poster(uid, jpeg_bytes())
                st, out = self.finish(uid)
                self.assertEqual(st, 422, out)
                self.assertIn(text, out["reason"])
        self.assertEqual(os.listdir(os.path.join(self.d, "videos")), ["posters"])
        self.assertEqual(os.listdir(os.path.join(self.d, "videos", "posters")), [])

    def test_incomplete_and_no_poster_409(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        self.chunk(uid, 0, data[:10])
        self.poster(uid, jpeg_bytes())
        self.assertEqual(self.finish(uid), (409, dict(error="incomplete", offset=10)))
        self.chunk(uid, 10, data[10:])
        uid2 = self.session(make_mp4(seed=b"np"))
        self.assertEqual(self.finish(uid2), (409, dict(error="no_poster")))

    def test_publish_result_files_and_idempotent(self):
        data, post_ = make_mp4(rot=90), jpeg_bytes()
        uid, res = self.publish(data, post_, title="Lễ cưới", ev=E17)
        self.assertEqual(res, dict(id=hashlib.sha1(sha(data).encode()).hexdigest()[:16], v=sha(data)[:10], pv=sha(post_)[:10], w=240, h=320,
                                   duration=2.5, title="Lễ cưới", ev=E17))
        self.assertEqual(open(os.path.join(self.d, "videos", f"{res['id']}-{res['v']}.mp4"), "rb").read(), data)
        self.assertEqual(os.listdir(os.path.join(self.d, "video-staging")), [uid + ".json"])
        self.assertEqual(self.finish(uid), (200, res))
        st, s = self.status(uid)
        self.assertEqual((st, s["state"], s["id"]), (200, "published", res["id"]))
        self.assertEqual(self.chunk(uid, 0, b"a")[0], 409)
        db = sqlite3.connect(store.db_path(self.d))
        row = db.execute("SELECT id, name, sha256, size, v, pv, w, h, duration, codec, title, ev, sort FROM videos").fetchall()
        self.assertEqual(row, [(res["id"], "a.mp4", sha(data), len(data), res["v"], res["pv"], 240, 320, 2.5, "avc1", "Lễ cưới", E17, 1)])
        # sau restart: finish của phiên đã công bố vẫn trả cùng kết quả
        self.assertEqual(self.finish(uid, app=make_app(self.d)), (200, res))

    def test_db_failure_leaves_no_published_row_and_allows_retry(self):
        data = make_mp4()
        uid = self.session(data)
        self.poster(uid, jpeg_bytes())
        real = server.store.connect

        def boom(d, init=True):
            if init is False:
                raise sqlite3.OperationalError("boom")
            return real(d, init)
        with mock.patch.object(server.store, "connect", boom), self.assertRaises(Exception):
            self.finish(uid)
        self.assertEqual(os.listdir(os.path.join(self.d, "videos")), ["posters"])
        self.assertEqual(os.listdir(os.path.join(self.d, "videos", "posters")), [])
        self.assertEqual(self.vrows(), [])
        self.assertEqual(self.status(uid)[1]["state"], "ready")
        self.assertEqual(self.finish(uid)[0], 200)
        self.assertEqual(len(self.vrows()), 1)

    def test_sort_increments_per_event(self):
        self.publish(make_mp4(seed=b"1"), ev=E4)
        self.publish(make_mp4(seed=b"2"), ev=E4)
        self.publish(make_mp4(seed=b"3"), ev=E17)
        self.assertEqual(sorted((r[7], r[8]) for r in self.vrows()), [(E4, 1), (E4, 2), (E17, 1)])


class ServeTests(VBase):
    def setUp(self):
        super().setUp()
        self.data = make_mp4(seed=b"serve", pad=bytes(range(256)) * 8)
        self.poster_b = jpeg_bytes(200, 100)
        _, self.res = self.publish(self.data, self.poster_b)
        self.url = f"/v/{self.res['id']}-{self.res['v']}.mp4"
        self.purl = f"/v/{self.res['id']}-{self.res['pv']}.jpg"
        self.n = len(self.data)

    def test_full_get_and_head(self):
        st, h, body, _ = self.get(self.url)
        self.assertEqual((st, body), (200, self.data))
        self.assertEqual((h["content-type"], h["accept-ranges"], h["content-length"], h["cache-control"]),
                         ("video/mp4", "bytes", str(self.n), server.IMMUTABLE))
        st, h, body, _ = self.get(self.url, method="HEAD")
        self.assertEqual((st, body, h["content-length"], h["content-type"], h["cache-control"]), (200, b"", str(self.n), "video/mp4", server.IMMUTABLE))
        st, h, body, _ = self.get(self.purl)
        self.assertEqual((st, body, h["content-type"], h["cache-control"]), (200, self.poster_b, "image/jpeg", server.IMMUTABLE))
        self.assertEqual(self.get(self.purl, method="HEAD")[0], 200)

    def test_ranges(self):
        cases = {"bytes=0-9": (0, 10), "bytes=5-": (5, self.n), "bytes=-7": (self.n - 7, self.n), "bytes=3-3": (3, 4),
                 f"bytes=10-{self.n + 500}": (10, self.n)}
        for rng, (a, b) in cases.items():
            with self.subTest(rng=rng):
                st, h, body, _ = self.get(self.url, headers={"Range": rng})
                self.assertEqual((st, body), (206, self.data[a:b]))
                self.assertEqual((h["content-range"], h["content-length"], h["accept-ranges"], h["cache-control"]),
                                 (f"bytes {a}-{b - 1}/{self.n}", str(b - a), "bytes", server.IMMUTABLE))
        st, h, _, _ = self.get(self.url, headers={"Range": "bytes=0-4"}, method="HEAD")
        self.assertEqual((st, h["content-length"], h["content-range"]), (206, "5", f"bytes 0-4/{self.n}"))

    def test_unsatisfiable_416(self):
        st, h, _, _ = self.get(self.url, headers={"Range": f"bytes={self.n}-"})
        self.assertEqual((st, h["content-range"], h["cache-control"]), (416, f"*/{self.n}", "no-store"))
        self.assertEqual(self.get(self.url, headers={"Range": f"bytes={self.n + 9}-{self.n + 20}"})[0], 416)

    def test_if_range(self):
        _, h, _, _ = self.get(self.url)
        st, _, body, _ = self.get(self.url, headers={"Range": "bytes=0-3", "If-Range": h["etag"]})
        self.assertEqual((st, body), (206, self.data[:4]))
        st, _, body, _ = self.get(self.url, headers={"Range": "bytes=0-3", "If-Range": '"stale"'})
        self.assertEqual((st, body), (200, self.data))

    def test_no_content_encoding(self):
        for u in (self.url, self.purl):
            st, h, _, _ = self.get(u, headers={"Accept-Encoding": "gzip, br"})
            self.assertEqual(st, 200)
            self.assertNotIn("content-encoding", h)

    def test_404_cases_no_store(self):
        vid, v, pv = self.res["id"], self.res["v"], self.res["pv"]
        for u in (self.url + "?x=1", f"/v/{vid}-{'0' * 10}.mp4", f"/v/{vid}-{pv}.mp4", f"/v/{vid}-{v}.jpg", f"/v/{'0' * 16}-{v}.mp4",
                  f"/v/{vid}-{v[:8]}.mp4", f"/v/{vid}-{v}.MP4", "/v/x", f"/v/..%2f{vid}-{v}.mp4"):
            with self.subTest(u=u):
                st, h, _, _ = self.get(u)
                self.assertEqual((st, h["cache-control"]), (404, "no-store"))

    def test_cdn_host_cannot_reach_video(self):
        for host in ("cdn.example.com", "localhost.evil.com"):
            self.assertEqual(self.get(self.url, host=host)[0], 404)
            self.assertEqual(self.get(self.purl, host=host, method="HEAD")[0], 404)
        self.assertNotIn("/v/", server.PUBLIC_PREFIXES)

    def test_missing_file_404(self):
        os.remove(os.path.join(self.d, "videos", f"{self.res['id']}-{self.res['v']}.mp4"))
        self.assertEqual(self.get(self.url)[0], 404)
        self.assertEqual(self.get(self.purl)[0], 200)


class GalleryTests(Base):
    def vpublish(self, seed, **over):
        data = make_mp4(seed=seed)
        body = dict(name="a.mp4", size=len(data), sha256=sha(data), ev=E4, title="t")
        body.update(over)
        for k in [k for k, v in body.items() if v is ...]:
            del body[k]
        st, _, out, _ = post(self.app, "/api/admin/video/init", body)
        uid = json.loads(out)["uid"]
        h = cookie_hdr(self.app)
        call(self.app, "PUT", f"/api/admin/video/{uid}/chunk?off=0", headers=h, body=data)
        call(self.app, "PUT", f"/api/admin/video/{uid}/poster", headers=h, body=jpeg_bytes())
        st, _, out, _ = call(self.app, "POST", f"/api/admin/video/{uid}/finish", headers=h)
        self.assertEqual(st, 200, out)
        return json.loads(out)

    def test_videos_key_shape_ordering_and_nine_element_photos(self):
        g = json.loads(gallery(self.app)[2])
        self.assertEqual(g["videos"], [])
        self.assertEqual(len(g["photos"]), 11)
        a = self.vpublish(b"a", ev=None, title="trước cưới")
        b = self.vpublish(b"b", ev=E17, title="17")
        c = self.vpublish(b"c", ev=E4, title="4")
        d = self.vpublish(b"d", ev=E4, title="4b")
        g = json.loads(gallery(self.app)[2])
        self.assertTrue(all(len(r) == 9 for r in g["photos"]))
        self.assertEqual([r[0] for r in g["videos"]], [c["id"], d["id"], b["id"], a["id"]])
        self.assertEqual(g["videos"][0], [c["id"], c["v"], c["pv"], 320, 240, 2.5, "4", E4, 1])
        self.assertEqual(g["videos"][3][7:], [None, 1])
        self.assertTrue(all(len(r) == 9 for r in g["videos"]))
        self.assertEqual(gal_rows(self.app), g["photos"])
        self.assertEqual(list(g)[:4], ["mv", "cdn", "dv", "events"])

    def test_etag_changes_on_publish_and_old_validator_gets_200(self):
        st, h0, b0 = gallery(self.app)
        gz = {"Accept-Encoding": "gzip"}
        _, hz0, _ = gallery(self.app, gz)
        self.assertEqual(gallery(self.app, {"If-None-Match": h0["etag"]})[0], 304)
        self.assertEqual(gallery(self.app, dict(gz, **{"If-None-Match": hz0["etag"]}))[0], 304)
        self.vpublish(b"e")
        st, h1, b1 = gallery(self.app, {"If-None-Match": h0["etag"]})
        self.assertEqual(st, 200)
        self.assertNotEqual(h1["etag"], h0["etag"])
        self.assertNotEqual(json.loads(b1)["mv"], json.loads(b0)["mv"])
        st, hz1, _ = gallery(self.app, dict(gz, **{"If-None-Match": hz0["etag"]}))
        self.assertEqual((st, hz1["content-encoding"]), (200, "gzip"))
        self.assertEqual(gallery(self.app, {"If-None-Match": h1["etag"]})[0], 304)

    def test_mv_unchanged_when_no_videos(self):
        g = json.loads(gallery(self.app)[2])
        import hashlib as hl
        photos = json.dumps(g["photos"], ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(g["mv"], hl.sha1(f"{server.DERIV_VERSION}|{''}|{photos}".encode()).hexdigest()[:12])  # y hệt công thức trước khi có video

    def test_hiding_photos_does_not_touch_videos(self):
        self.vpublish(b"h")
        before = json.loads(gallery(self.app)[2])["videos"]
        hide(self.app, ["a1"])
        self.assertEqual(json.loads(gallery(self.app)[2])["videos"], before)


class RebuildTests(Base):
    def test_rebuild_cli_keeps_video_rows_files_and_owner_state(self):
        data = make_mp4(seed=b"rb")
        h = cookie_hdr(self.app)
        _, _, out, _ = post(self.app, "/api/admin/video/init", dict(name="a.mp4", size=len(data), sha256=sha(data), ev=None, title="t"))
        uid = json.loads(out)["uid"]
        call(self.app, "PUT", f"/api/admin/video/{uid}/chunk?off=0", headers=h, body=data)
        call(self.app, "PUT", f"/api/admin/video/{uid}/poster", headers=h, body=jpeg_bytes())
        res = json.loads(call(self.app, "POST", f"/api/admin/video/{uid}/finish", headers=h)[2])
        hide(self.app, ["a1"])
        post(self.app, "/api/admin/event", {"ids": [__import__("test_hidden").PID["s1"]], "event": E17})
        db = sqlite3.connect(store.db_path(self.d))
        want = db.execute("SELECT * FROM videos").fetchall()
        db.close()
        from test_hidden import TagEngine
        with mock.patch.object(sys, "argv", ["index_photos.py", "--photos", self.orig, "--data-dir", self.d, "--rebuild"]), \
                mock.patch.object(index_photos, "FaceEngine", TagEngine):
            index_photos.main()
        db = sqlite3.connect(store.db_path(self.d))
        self.assertEqual(db.execute("SELECT * FROM videos").fetchall(), want)
        self.assertEqual(db.execute("SELECT count(*) FROM photo_hidden").fetchone()[0], 1)
        db.close()
        self.assertTrue(os.path.exists(os.path.join(self.d, "videos", f"{res['id']}-{res['v']}.mp4")))
        self.assertTrue(os.path.exists(os.path.join(self.d, "videos", "posters", f"{res['id']}-{res['pv']}.jpg")))
        app2 = make_app(self.d)
        self.assertEqual([r[0] for r in json.loads(gallery(app2)[2])["videos"]], [res["id"]])
        self.assertEqual(call(app2, "GET", f"/v/{res['id']}-{res['v']}.mp4")[2], data)

    def test_keep_and_restore_videos_roundtrip_and_owner_state_signature(self):
        rows = [("i" * 16, "n.mp4", "s" * 64, 5, "v" * 10, "p" * 10, 1, 2, 3.5, "avc1", "t", None, 1, 9.0)]
        db = store.connect(self.d)
        store.restore_videos(db, rows)
        store.restore_videos(db, rows)  # idempotent
        db.close()
        self.assertEqual(store.keep_videos(store.db_path(self.d)), rows)
        self.assertEqual(len(store.keep_owner_state(store.db_path(self.d))), 3)  # chữ ký cũ giữ nguyên
        db = store.connect(self.d)
        store.restore_owner_state(db, [], [])  # gọi 2 tham số như cũ vẫn chạy
        db.close()


if __name__ == "__main__":
    unittest.main()
