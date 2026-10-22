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
import time
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


class Crash(BaseException):
    """Mô phỏng process chết: không bị `except Exception` của server bắt nên không có rollback nào chạy."""


def crash():
    raise Crash()


def tkhd(w, h, rot=0):
    mat = rot if isinstance(rot, tuple) else {0: (0x10000, 0, 0, 0, 0x10000, 0), 90: (0, 0x10000, 0, -0x10000, 0, 0), 180: (-0x10000, 0, 0, 0, -0x10000, 0),
           270: (0, -0x10000, 0, 0x10000, 0, 0)}[rot]
    return box(b"tkhd", struct.pack(">I5I8s4H", 3, 0, 0, 1, 0, 1000, b"", 0, 0, 0, 0) + struct.pack(">9i", *mat[:6], 0, 0, 0x40000000)
               + struct.pack(">II", w << 16, h << 16))


def trak(kind, codec, w=0, h=0, rot=0, stsd_payload=None):
    mdhd = box(b"mdhd", struct.pack(">I4I2H", 0, 0, 0, 1000, 1000, 0, 0))
    hdlr = box(b"hdlr", struct.pack(">I4s4s12s", 0, b"", kind, b"") + b"\0")
    codecs = codec if isinstance(codec, list) else [codec]
    stsd = box(b"stsd", stsd_payload if stsd_payload is not None else struct.pack(">II", 0, len(codecs)) + b"".join(box(c, bytes(78)) for c in codecs))
    return box(b"trak", tkhd(w, h, rot) + box(b"mdia", mdhd + hdlr + box(b"minf", box(b"stbl", stsd))))


def make_mp4(w=320, h=240, rot=0, vcodec=b"avc1", audio=b"mp4a", faststart=True, seed=b"x", mdat="normal", extra=b"", two_video=False, pad=b"", vtrak=None):
    mvhd = box(b"mvhd", struct.pack(">I4I", 0, 0, 0, 1000, 2500) + bytes(80))
    traks = (vtrak or trak(b"vide", vcodec, w, h, rot)) + (trak(b"vide", vcodec, w, h) if two_video else b"") + (trak(b"soun", audio) if audio else b"")
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


async def acall(app, method, url, headers=None, body=b"", host="localhost", parts=None, gate=None, entered=None):
    """Như _asgi.call nhưng là coroutine: nhiều request chạy CÙNG MỘT event loop. parts + gate: gửi phần đầu, rồi chờ gate mới gửi các phần sau."""
    chunks = list(parts) if parts is not None else [body]
    total = sum(len(c) for c in chunks)
    hs = [(b"host", host.encode())] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if total:
        hs.append((b"content-length", str(total).encode()))
    path, _, qs = url.partition("?")
    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method=method, scheme="http", path=path, raw_path=path.encode(),
                 query_string=qs.encode(), headers=hs, client=("1.2.3.4", 1), server=("x", 80))
    first, out = [True], {"status": None, "body": b""}

    async def receive():
        if chunks:
            if not first[0] and gate is not None:
                if entered is not None:
                    entered.set()
                await gate.wait()
            first[0] = False
            b = chunks.pop(0)
            return {"type": "http.request", "body": b, "more_body": bool(chunks)}
        await asyncio.sleep(3600)

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")
    await asyncio.wait_for(app(scope, receive, send), 30)
    return out["status"], out["body"]


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
        class Spy:
            def __init__(self, fh, reads):
                self.fh, self.reads = fh, reads

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.fh.close()

            def fileno(self):
                return self.fh.fileno()

            def seek(self, o, w=0):
                return self.fh.seek(o, w)

            def read(self, n=-1):
                pos = self.fh.tell()
                b = self.fh.read(n)
                self.reads.append((pos, len(b)))
                return b
        real = open
        for kind in ("normal", "large", "zero"):
            with self.subTest(kind=kind):
                data = make_mp4(mdat=kind, pad=bytes(100_000))
                d = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, d, True)
                p = os.path.join(d, "a.mp4")
                real(p, "wb").write(data)
                start = data.find(b"mdat") - 4 + (16 if kind == "large" else 8)  # đầu phần dữ liệu mdat; đến hết file
                reads = []
                with mock.patch("builtins.open", lambda path, *a, **k: Spy(real(path, *a, **k), reads)):
                    mp4box.probe(p)
                self.assertTrue(reads)
                for pos, n in reads:
                    self.assertFalse(pos < len(data) and pos + n > start and n > 0, f"đọc [{pos},{pos + n}) chạm payload mdat từ {start}")
                self.assertLess(max(pos + n for pos, n in reads), start + 1)

    def test_stsd_entries_validated(self):
        ent = box(b"avc1", bytes(78))
        cases = {
            "entry size 4": struct.pack(">II", 0, 1) + struct.pack(">I4s", 4, b"avc1") + bytes(12),
            "count 2, one present": struct.pack(">II", 0, 2) + ent,
            "count 0": struct.pack(">II", 0, 0) + ent,
            "avc1 + hvc1": struct.pack(">II", 0, 2) + ent + box(b"hvc1", bytes(78)),
            "trailing junk": struct.pack(">II", 0, 1) + ent + b"\0\0",
            "size beyond stsd": struct.pack(">II", 0, 1) + struct.pack(">I4s", 500, b"avc1") + bytes(78),
            "size 0": struct.pack(">II", 0, 1) + struct.pack(">I4s", 0, b"avc1") + bytes(78),
            "huge count": struct.pack(">II", 0, 2 ** 31) + ent,
        }
        for name, payload in cases.items():
            with self.subTest(name):
                self.bad(make_mp4(vtrak=trak(b"vide", b"avc1", 320, 240, stsd_payload=payload)))
        self.assertEqual(self.probe(make_mp4(vtrak=trak(b"vide", [b"avc1", b"avc3"], 320, 240)))["codec"], "avc1")  # nhiều mẫu đều H.264 thì được
        self.bad(make_mp4(vtrak=trak(b"vide", [b"avc1", b"hvc1"], 320, 240)), "H.264")
        self.bad(make_mp4(vtrak=trak(b"vide", [b"hvc1", b"avc1"], 320, 240)), "H.264")

    def test_audio_entries_must_all_be_mp4a(self):
        mvhd = box(b"mvhd", struct.pack(">I4I", 0, 0, 0, 1000, 2500) + bytes(80))
        for audio, ok in (([b"mp4a"], True), ([b"mp4a", b"mp4a"], True), ([b"mp4a", b"ac-3"], False), ([b"ac-3", b"mp4a"], False)):
            with self.subTest(audio=audio):
                moov = box(b"moov", mvhd + trak(b"vide", b"avc1", 320, 240) + trak(b"soun", audio))
                data = box(b"ftyp", b"isom\0\0\2\0isomavc1") + moov + box(b"mdat", bytes(64))
                if ok:
                    self.assertTrue(self.probe(data)["audio"])
                else:
                    self.bad(data, "mp4a")

    def test_tkhd_matrix_only_identity_and_quarter_turns(self):
        one = 0x10000
        for name, mat in (("45deg", (46341, 46341, 0, -46341, 46341, 0)), ("mirror", (-one, 0, 0, one, 0, 0)), ("flipv", (one, 0, 0, -one, 0, 0)),
                          ("scale", (2 * one, 0, 0, one, 0, 0)), ("shear", (one, 0, 0, one // 2, one, 0)), ("zero", (0, 0, 0, 0, 0, 0)),
                          ("perspective", (one, 0, 0, 0, one, 5))):
            with self.subTest(name):
                self.bad(make_mp4(rot=mat), "ma trận")
        for rot in (0, 90, 180, 270):
            self.assertEqual(self.probe(make_mp4(rot=rot))["w"], 320 if rot in (0, 180) else 240)


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
        n = self.n
        cases = {"bytes=0-9": (0, 10), "bytes=5-": (5, n), "bytes=-7": (n - 7, n), "bytes=3-3": (3, 4), f"bytes=10-{n + 500}": (10, n),
                 f"bytes=-{n}": (0, n), f"bytes=-{n + 200}": (0, n), "BYTES=1-2": (1, 3), f"bytes={n - 1}-": (n - 1, n)}
        for rng, (a, b) in cases.items():
            with self.subTest(rng=rng):
                st, h, body, _ = self.get(self.url, headers={"Range": rng})
                self.assertEqual((st, body), (206, self.data[a:b]))
                self.assertEqual((h["content-range"], h["content-length"], h["accept-ranges"], h["cache-control"]),
                                 (f"bytes {a}-{b - 1}/{n}", str(b - a), "bytes", server.IMMUTABLE))
        st, h, _, _ = self.get(self.url, headers={"Range": "bytes=0-4"}, method="HEAD")
        self.assertEqual((st, h["content-length"], h["content-range"]), (206, "5", f"bytes 0-4/{n}"))

    def test_ignored_ranges_serve_full_200(self):
        for rng in ("bytes=9-8", "bytes=0-1,5-6", "bytes=0-1,", "bytes=abc", "bytes=-", "bytes=", "items=0-1", "bytes=1-2-3", "0-5", "bytes= 5 -9"):
            with self.subTest(rng=rng):
                st, h, body, _ = self.get(self.url, headers={"Range": rng})
                self.assertEqual((st, body, h["content-length"], h["content-type"]), (200, self.data, str(self.n), "video/mp4"))
                self.assertNotIn("content-range", h)

    def test_head_mirrors_get_headers(self):
        for rng in (None, "bytes=2-9", "bytes=0-1,5-6", f"bytes={self.n}-"):
            with self.subTest(rng=rng):
                hd = {"Range": rng} if rng else None
                sg, hg, bg, _ = self.get(self.url, headers=hd)
                sh, hh, bh, _ = self.get(self.url, headers=hd, method="HEAD")
                self.assertEqual((sg, bh), (sh, b""))
                for k in ("content-length", "content-range", "accept-ranges", "content-type", "etag", "last-modified", "cache-control"):
                    self.assertEqual(hg.get(k), hh.get(k), k)
                if sg != 416:
                    self.assertEqual(int(hg["content-length"]), len(bg))

    def test_large_file_range_across_chunk_boundaries(self):
        pad = bytes(i % 251 for i in range(700_000))
        data = make_mp4(seed=b"big", pad=pad)
        _, res = self.publish(data)
        url = f"/v/{res['id']}-{res['v']}.mp4"
        for a, b in ((262000, 262300), (0, 600_000), (len(data) - 300_000, len(data) - 1)):
            st, h, body, _ = self.get(url, headers={"Range": f"bytes={a}-{b}"})
            self.assertEqual((st, body, h["content-length"]), (206, data[a:b + 1], str(b - a + 1)))
        self.assertEqual(self.get(url)[2], data)

    def test_unsatisfiable_416(self):
        n = self.n
        for rng in (f"bytes={n}-", f"bytes={n + 9}-{n + 20}", "bytes=-0", f"bytes={n}-{n + 5}", "bytes=99999999999999999999999-"):
            with self.subTest(rng=rng):
                st, h, body, _ = self.get(self.url, headers={"Range": rng})
                self.assertEqual((st, h["content-range"], h["cache-control"]), (416, f"bytes */{n}", "no-store"))
        self.assertEqual(self.get(self.url, headers={"Range": f"bytes={n}-"}, method="HEAD")[0], 416)

    def test_if_range(self):
        _, h, _, _ = self.get(self.url)
        for validator in (h["etag"], h["last-modified"]):
            st, _, body, _ = self.get(self.url, headers={"Range": "bytes=0-3", "If-Range": validator})
            self.assertEqual((st, body), (206, self.data[:4]))
        for bad in ('"stale"', "Mon, 01 Jan 2001 00:00:00 GMT", "W/" + h["etag"]):
            st, hh, body, _ = self.get(self.url, headers={"Range": "bytes=0-3", "If-Range": bad})
            self.assertEqual((st, body, "content-range" in hh), (200, self.data, False))
        st, _, body, _ = self.get(self.url, headers={"Range": f"bytes={self.n}-", "If-Range": '"stale"'})
        self.assertEqual((st, body), (200, self.data))  # validator sai → bỏ Range, kể cả Range không thoả

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
        self.assertEqual(post(self.app, f"/api/admin/video/{res['id']}/event", {"ev": E17})[0], 200)  # ngày đổi sau khi công bố
        db = sqlite3.connect(store.db_path(self.d))
        want = db.execute("SELECT * FROM videos").fetchall()
        db.close()
        self.assertEqual(want[0][11], E17)
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
        self.assertEqual([(r[0], r[7]) for r in json.loads(gallery(app2)[2])["videos"]], [(res["id"], E17)])
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


class RobustnessBase(VBase):
    def ready(self, data=None, poster=None, **over):
        """Phiên đã tải đủ + có poster, chưa finish."""
        data = data or make_mp4()
        poster = poster or jpeg_bytes()
        uid = self.session(data, **over)
        self.assertEqual(self.poster(uid, poster)[0], 200)
        return uid, data, poster

    def crash_finish(self, point, **kw):
        uid, data, pb = self.ready(**kw)
        self.app.state.video_hooks[point] = crash
        with self.assertRaises(Crash):
            self.finish(uid)
        return uid, data, pb

    def restart(self):
        self.app = make_app(self.d)
        return self.app

    def sidecar(self, uid):
        return os.path.join(self.d, "video-staging", uid + ".json")

    def edit(self, sid, **kw):
        s = json.load(open(self.sidecar(sid)))
        s.update(kw)
        json.dump(s, open(self.sidecar(sid), "w"))
        return s

    def assert_published_after_restart(self, uid, data, pb):
        self.restart()
        st, s = self.status(uid)
        self.assertEqual((st, s["state"]), (200, "published"))
        st, res = self.finish(uid)
        self.assertEqual(st, 200, res)
        self.assertEqual(res["pv"], sha(pb)[:10])
        rows = self.vrows()
        self.assertEqual([r[0] for r in rows], [res["id"]])
        self.assertEqual(self.get(f"/v/{res['id']}-{res['v']}.mp4")[2], data)
        self.assertEqual(self.get(f"/v/{res['id']}-{res['pv']}.jpg")[2], pb)
        self.assertEqual(self.finish(uid), (200, res))
        self.restart()  # khởi động lần nữa vẫn y nguyên
        self.assertEqual(self.finish(uid), (200, res))
        self.assertEqual(len(self.vrows()), 1)
        return res


class PublishSerializationTests(RobustnessBase):
    def forge_twin(self, uid):
        stage = os.path.join(self.d, "video-staging")
        twin = "b" * 32
        for ext in (".part", ".poster"):
            shutil.copy(os.path.join(stage, uid + ext), os.path.join(stage, twin + ext))
        meta = json.load(open(os.path.join(stage, uid + ".json")))
        meta["uid"] = twin
        json.dump(meta, open(os.path.join(stage, twin + ".json"), "w"))
        return twin

    def test_p1_1_interleaved_same_sha_first_published_file_survives(self):
        uid, data, pb = self.ready()
        twin = self.forge_twin(uid)  # hai phiên cùng sha256 (dựng tay: init đã không cho tạo)
        app = self.restart()
        out, started = [], []

        def run_twin():
            out.append(self.finish(twin, app=app))

        def start_twin_inside_first_publication():  # B qua hết kiểm tra trước-khoá rồi chờ khoá công bố của A
            t = threading.Thread(target=run_twin)
            app.state.video_hooks.pop("moved")
            t.start()
            started.append(t)
        app.state.video_hooks["moved"] = start_twin_inside_first_publication
        st, res = self.finish(uid, app=app)
        self.assertEqual(st, 200, res)
        started[0].join(60)
        self.assertEqual(out, [(409, dict(error="duplicate", id=res["id"]))])
        self.assertEqual(open(os.path.join(self.d, "videos", f"{res['id']}-{res['v']}.mp4"), "rb").read(), data)
        self.assertEqual(open(os.path.join(self.d, "videos", "posters", f"{res['id']}-{res['pv']}.jpg"), "rb").read(), pb)
        self.assertEqual(self.get(f"/v/{res['id']}-{res['v']}.mp4")[2], data)
        self.assertEqual([r[0] for r in self.vrows()], [res["id"]])
        self.assertEqual(self.status(twin)[0], 404)  # phiên trùng đã được giải phóng

    def test_p1_1_init_duplicate_check_is_serialized_with_publication(self):
        uid, data, pb = self.ready()
        app, holder = self.app, []

        def init_mid_publication():  # init chen vào giữa lúc công bố: phải chờ khoá công bố, rồi thấy 409 (không tạo phiên thứ hai cùng sha)
            app.state.video_hooks.pop("moved")
            t = threading.Thread(target=lambda: holder.append(self.init(data)))
            t.start()
            holder.insert(0, t)
        app.state.video_hooks["moved"] = init_mid_publication
        st, res = self.finish(uid)
        self.assertEqual(st, 200, res)
        holder[0].join(60)
        self.assertEqual(holder[1], (409, dict(error="duplicate", id=res["id"])))
        self.assertEqual(len(app.state.video_sessions), 1)

    def test_p1_1_rollback_never_touches_preexisting_foreign_file(self):
        uid, data, pb = self.ready()
        vid = hashlib.sha1(sha(data).encode()).hexdigest()[:16]
        foreign = os.path.join(self.d, "videos", f"{vid}-{sha(data)[:10]}.mp4")
        open(foreign, "wb").write(b"foreign content")
        with self.assertRaises(Exception):
            self.finish(uid)
        self.assertEqual(open(foreign, "rb").read(), b"foreign content")
        stage = os.path.join(self.d, "video-staging")
        self.assertEqual(open(os.path.join(stage, uid + ".part"), "rb").read(), data)
        self.assertEqual(open(os.path.join(stage, uid + ".poster"), "rb").read(), pb)
        self.assertEqual(self.status(uid)[1]["state"], "ready")
        self.assertEqual(self.vrows(), [])

    def test_p1_1_rollback_after_db_failure_moves_back_only_own_files(self):
        uid, data, pb = self.ready()
        real = server.store.connect

        def boom(d, init=True):
            if init is False:
                raise sqlite3.OperationalError("boom")
            return real(d, init)
        with mock.patch.object(server.store, "connect", boom), self.assertRaises(Exception):
            self.finish(uid)
        stage = os.path.join(self.d, "video-staging")
        self.assertEqual(open(os.path.join(stage, uid + ".part"), "rb").read(), data)
        self.assertEqual(os.listdir(os.path.join(self.d, "videos")), ["posters"])
        self.assertEqual(json.load(open(os.path.join(stage, uid + ".json")))["state"], "uploading")
        self.assertEqual(self.finish(uid)[0], 200)


class CrashSafetyTests(RobustnessBase):
    def test_p1_2_crash_after_publishing_saved_before_any_move(self):
        self.assert_published_after_restart(*self.crash_finish("publishing_saved"))

    def test_p1_2_crash_after_moves_before_db_commit(self):
        uid, data, pb = self.crash_finish("moved")
        self.assertEqual(self.vrows(), [])
        self.assert_published_after_restart(uid, data, pb)

    def test_p1_2_crash_after_commit_before_sidecar_save(self):
        uid, data, pb = self.crash_finish("committed")
        stage = os.path.join(self.d, "video-staging")
        self.assertEqual(json.load(open(os.path.join(stage, uid + ".json")))["state"], "publishing")
        self.assert_published_after_restart(uid, data, pb)

    def test_p1_2_partial_move_rolls_forward(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        res = json.load(open(os.path.join(self.d, "video-staging", uid + ".json")))["result"]
        os.replace(os.path.join(self.d, "video-staging", uid + ".part"), os.path.join(self.d, "videos", f"{res['id']}-{res['v']}.mp4"))  # mp4 đã sang, poster chưa
        self.assert_published_after_restart(uid, data, pb)

    def test_p1_2_old_uploading_sidecar_with_complete_final_files(self):
        uid, data, pb = self.crash_finish("moved")
        meta = os.path.join(self.d, "video-staging", uid + ".json")
        s = json.load(open(meta))
        s["state"], s["result"] = "uploading", None  # sidecar cũ hơn đĩa
        json.dump(s, open(meta, "w"))
        self.assert_published_after_restart(uid, data, pb)

    def test_p1_2_corrupt_final_is_never_discarded_or_published(self):
        uid, data, pb = self.crash_finish("moved")
        res = json.load(open(os.path.join(self.d, "video-staging", uid + ".json")))["result"]
        fmp4 = os.path.join(self.d, "videos", f"{res['id']}-{res['v']}.mp4")
        open(fmp4, "ab").write(b"x")
        self.restart()
        self.assertTrue(os.path.exists(fmp4))
        self.assertEqual(self.vrows(), [])
        self.assertEqual(self.finish(uid)[0], 500)  # không công bố file sai hash, cũng không xoá nó
        self.assertEqual(json.load(open(os.path.join(self.d, "video-staging", uid + ".json")))["state"], "failed")

    def test_p1_2_startup_never_discards_final_files(self):
        _, res = self.publish()
        junk = os.path.join(self.d, "videos", "f" * 16 + "-" + "0" * 10 + ".mp4")
        open(junk, "wb").write(b"orphan")
        self.restart()
        self.assertTrue(os.path.exists(junk))
        self.assertEqual(self.get(f"/v/{res['id']}-{res['v']}.mp4")[0], 200)

    def test_p1_2_finish_after_clean_publish_survives_restart_not_404(self):
        uid, res = self.publish()
        self.restart()
        self.assertEqual(self.finish(uid), (200, res))


class DiskAccountingTests(RobustnessBase):
    FLOOR = 5120 * 1024 ** 2

    def disk(self, free):
        return mock.patch.object(server.shutil, "disk_usage", return_value=types.SimpleNamespace(free=free, total=free * 2, used=0))

    def photo(self):
        return call(self.app, "POST", "/api/upload?name=p.jpg", headers=self.hdr(), body=jpeg_bytes(64, 48, (5, 6, 7)))[0]

    def test_p1_3_photo_upload_subtracts_outstanding_video_reservation(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        body = jpeg_bytes(64, 48, (5, 6, 7))
        with self.disk(self.FLOOR + len(body) + len(data) - 1):
            self.assertEqual(self.photo(), 507)  # phiên video còn nợ len(data) byte
        self.chunk(uid, 0, data)  # nhận đủ → hết nợ
        with self.disk(self.FLOOR + len(body) - 1):
            self.assertEqual(self.photo(), 507)  # ảnh tự đặt chỗ đúng Content-Length của nó
        with self.disk(self.FLOOR + len(body)):
            self.assertEqual(self.photo(), 200)

    def test_p1_3_chunk_admission_checks_chunk_and_other_sessions(self):
        d1, d2 = make_mp4(seed=b"1"), make_mp4(seed=b"2")
        u1, u2 = self.init(d1)[1]["uid"], self.init(d2)[1]["uid"]
        with self.disk(self.FLOOR + len(d1) + len(d2) - 1):
            self.assertEqual(self.chunk(u1, 0, d1)[0], 507)
        with self.disk(self.FLOOR + len(d1) + len(d2)):
            self.assertEqual(self.chunk(u1, 0, d1)[0], 200)
        with self.disk(self.FLOOR + 99):  # u1 đã đủ nên không còn nợ: chỉ cần chừa đúng độ dài chunk
            self.assertEqual(self.chunk(u2, 0, d2[:100])[0], 507)

    def test_p1_3_chunk_alone_needs_its_own_length(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        with self.disk(self.FLOOR + 99):
            self.assertEqual(self.chunk(uid, 0, data[:100])[0], 507)
        with self.disk(self.FLOOR + 100):
            self.assertEqual(self.chunk(uid, 0, data[:100])[0], 200)

    def test_p1_3_poster_put_checks_disk(self):
        uid = self.session(make_mp4())
        pb = jpeg_bytes()
        with self.disk(self.FLOOR + len(pb) - 1):
            self.assertEqual(self.poster(uid, pb)[0], 507)
        self.assertFalse(self.status(uid)[1]["poster"])
        with self.disk(self.FLOOR + len(pb)):
            self.assertEqual(self.poster(uid, pb)[0], 200)

    def test_p1_3_init_counts_other_reservations(self):
        d1, d2 = make_mp4(seed=b"a"), make_mp4(seed=b"b")
        self.init(d1)
        with self.disk(self.FLOOR + len(d1) + len(d2) - 1):
            self.assertEqual(self.init(d2)[0], 507)
        with self.disk(self.FLOOR + len(d1) + len(d2)):
            self.assertEqual(self.init(d2)[0], 200)


class CleanupRaceTests(RobustnessBase):
    def test_p2_6_cleanup_skips_busy_session_then_removes_it(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        part = os.path.join(self.d, "video-staging", uid + ".part")
        with server_lock(self.app, uid):
            with mock.patch.object(server.time, "time", return_value=server.time.time() + 25 * 3600):
                self.app.state.video_cleanup(True)
            self.assertIn(uid, self.app.state.video_sessions)
            self.assertTrue(os.path.exists(part))
        with mock.patch.object(server.time, "time", return_value=server.time.time() + 25 * 3600):
            self.app.state.video_cleanup(True)
        self.assertNotIn(uid, self.app.state.video_sessions)
        self.assertFalse(os.path.exists(part))

    def test_p2_6_publishing_session_never_expires(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        with mock.patch.object(server.time, "time", return_value=server.time.time() + 72 * 3600):
            self.app.state.video_cleanup(True)
        self.assertIn(uid, self.app.state.video_sessions)
        stage = os.path.join(self.d, "video-staging")
        self.assertTrue(all(os.path.exists(os.path.join(stage, uid + e)) for e in (".json", ".part", ".poster")))
        with mock.patch.object(server.time, "time", return_value=server.time.time() + 72 * 3600):
            app2 = make_app(self.d)  # cả khi khởi động sau 3 ngày
        self.assertEqual(len(json.loads(gallery(app2)[2])["videos"]), 1)

    def test_p2_6_chunk_cannot_resurrect_session_deleted_before_lock(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        app = self.app

        def expire_and_clean():
            app.state.video_sessions[uid]["expires"] = 1
            app.state.video_cleanup(True)
        app.state.video_hooks["chunk_before_lock"] = expire_and_clean
        self.assertEqual(self.chunk(uid, 0, data[:50])[0], 404)
        stage = os.path.join(self.d, "video-staging")
        self.assertEqual(os.listdir(stage), [])

    def test_p2_6_finish_and_poster_cannot_resurrect_either(self):
        uid, data, pb = self.ready()
        app = self.app

        def expire_and_clean():
            app.state.video_sessions[uid]["expires"] = 1
            app.state.video_cleanup(True)
        app.state.video_hooks["finish_before_lock"] = expire_and_clean
        self.assertEqual(self.finish(uid)[0], 404)
        self.assertEqual(os.listdir(os.path.join(self.d, "video-staging")), [])
        self.assertEqual(os.listdir(os.path.join(self.d, "videos")), ["posters"])
        self.assertEqual(self.vrows(), [])

    def test_p2_6_cleanup_runs_on_every_admin_video_request(self):
        a = self.init(make_mp4(seed=b"A"))[1]["uid"]
        b = self.init(make_mp4(seed=b"B"))[1]["uid"]
        self.app.state.video_sessions[a]["expires"] = 1
        self.assertEqual(self.status(b)[0], 200)  # request khác phiên → vẫn dọn phiên hết hạn
        self.assertNotIn(a, self.app.state.video_sessions)
        self.assertFalse(os.path.exists(os.path.join(self.d, "video-staging", a + ".part")))

    def test_security_poster_put_is_busy_while_session_lock_held(self):
        uid = self.session(make_mp4())
        with server_lock(self.app, uid):
            self.assertEqual(self.poster(uid, jpeg_bytes())[0:2], (409, dict(error="busy", offset=server_offset(self.app, uid))))
        self.assertEqual(self.poster(uid, jpeg_bytes())[0], 200)


def server_offset(app, uid):
    return app.state.video_sessions[uid]["offset"]


class DurabilityTests(RobustnessBase):
    def test_p2_7_directories_are_fsynced(self):
        calls = []
        real = server.fsync_path
        with mock.patch.object(server, "fsync_path", lambda p: (calls.append(os.path.basename(p)), real(p))[1]):
            data = make_mp4()
            uid = self.init(data)[1]["uid"]
            self.assertIn("video-staging", calls)  # sau khi đổi tên sidecar
            calls.clear()
            self.chunk(uid, 0, data)
            self.assertIn("video-staging", calls)
            calls.clear()
            self.poster(uid, jpeg_bytes())
            self.assertIn("video-staging", calls)
            calls.clear()
            self.assertEqual(self.finish(uid)[0], 200)
        self.assertTrue({"videos", "posters", "video-staging"} <= set(calls), calls)

    def test_p2_7_crash_between_poster_write_and_sidecar_save_keeps_old_poster(self):
        data = make_mp4()
        uid = self.session(data)
        p1, p2 = jpeg_bytes(color=(1, 2, 3)), jpeg_bytes(color=(200, 100, 50))
        self.poster(uid, p1)
        self.app.state.video_hooks["poster_written"] = crash
        with self.assertRaises(Crash):
            self.poster(uid, p2)
        self.restart()
        self.assertTrue(self.status(uid)[1]["poster"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "video-staging", uid + ".poster.new")))
        st, res = self.finish(uid)
        self.assertEqual((st, res["pv"]), (200, sha(p1)[:10]))
        self.assertEqual(open(os.path.join(self.d, "videos", "posters", f"{res['id']}-{res['pv']}.jpg"), "rb").read(), p1)

    def test_p2_7_crash_after_sidecar_save_before_rename_completes_new_poster(self):
        data = make_mp4()
        uid = self.session(data)
        p1, p2 = jpeg_bytes(color=(1, 2, 3)), jpeg_bytes(color=(200, 100, 50))
        self.poster(uid, p1)
        self.app.state.video_hooks["poster_saved"] = crash
        with self.assertRaises(Crash):
            self.poster(uid, p2)
        self.restart()
        self.assertFalse(os.path.exists(os.path.join(self.d, "video-staging", uid + ".poster.new")))
        st, res = self.finish(uid)
        self.assertEqual((st, res["pv"]), (200, sha(p2)[:10]))
        self.assertEqual(open(os.path.join(self.d, "videos", "posters", f"{res['id']}-{res['pv']}.jpg"), "rb").read(), p2)

    def test_p2_7_first_poster_crash_before_sidecar_leaves_no_poster(self):
        uid = self.session(make_mp4())
        self.app.state.video_hooks["poster_written"] = crash
        with self.assertRaises(Crash):
            self.poster(uid, jpeg_bytes())
        self.restart()
        self.assertFalse(self.status(uid)[1]["poster"])
        self.assertEqual(self.finish(uid), (409, dict(error="no_poster")))

    def test_p2_7_stale_psha_in_sidecar_is_recomputed_from_bytes(self):
        uid = self.session(make_mp4())
        pb = jpeg_bytes()
        self.poster(uid, pb)
        meta = os.path.join(self.d, "video-staging", uid + ".json")
        s = json.load(open(meta))
        s["psha"] = "0" * 64
        json.dump(s, open(meta, "w"))
        self.restart()
        self.assertEqual(json.load(open(meta))["psha"], sha(pb))
        st, res = self.finish(uid)
        self.assertEqual((st, res["pv"]), (200, sha(pb)[:10]))

    def test_p2_7_finish_uses_actual_poster_bytes_not_sidecar_hash(self):
        uid = self.session(make_mp4())
        pb = jpeg_bytes()
        self.poster(uid, pb)
        self.app.state.video_sessions[uid]["psha"] = "f" * 64
        st, res = self.finish(uid)
        self.assertEqual((st, res["pv"]), (200, sha(pb)[:10]))


class EventTests(VBase):
    def setUp(self):
        super().setUp()
        self.a = self.publish(make_mp4(seed=b"a"), ev=E17, title="a")[1]
        self.b = self.publish(make_mp4(seed=b"b"), ev=E4, title="b")[1]

    def ev(self, vid, body, headers=None, ctype="application/json", raw=None):
        return post(self.app, f"/api/admin/video/{vid}/event", body, headers=headers, ctype=ctype, raw=raw)

    def order(self):
        return [(r[0], r[7], r[8]) for r in self.vrows()]

    def test_auth_origin_and_format(self):
        u = f"/api/admin/video/{self.a['id']}/event"
        body = json.dumps({"ev": None}).encode()
        jh = {"Content-Type": "application/json"}
        self.assertEqual(call(self.app, "POST", u, headers=jh, body=body)[0], 401)
        self.assertEqual(call(self.app, "POST", u, headers=dict(jh, **{"X-Album-Token": "bad"}), body=body)[0], 401)
        self.assertEqual(self.ev(self.a["id"], {"ev": None}, headers=self.hdr(Origin="http://evil.example"))[0], 403)
        self.assertEqual(self.ev(self.a["id"], {"ev": None}, headers=self.hdr(**{"Sec-Fetch-Site": "cross-site"}))[0], 403)
        self.assertEqual(self.ev(self.a["id"], {"ev": None}, headers=self.hdr(Origin="http://localhost"))[0], 200)
        self.assertEqual(self.ev(self.a["id"], None, ctype="text/plain", raw=b'{"ev":null}')[0], 400)
        self.assertEqual(self.ev(self.a["id"], None, raw=b"[1]")[0], 400)
        self.assertEqual(self.ev(self.a["id"], {"ev": None, "pad": "x" * 5000})[0], 413)
        off = make_app(tempfile.mkdtemp(), admin_token="")
        self.assertEqual(post(off, u, {"ev": None}, headers={})[0], 403)

    def test_bad_id_404_and_bad_ev_400(self):
        for vid in ("xyz", "0" * 16, "0" * 15, "G" * 16, self.a["id"].upper(), "0" * 17):
            with self.subTest(vid=vid):
                self.assertEqual(self.ev(vid, {"ev": E4})[0], 404)
        for body in ({"ev": "2026-01-01"}, {"ev": 3}, {"ev": ["x"]}, {"ev": ""}, {}, {"event": E4}, {"ev": True}):
            with self.subTest(body=body):
                self.assertEqual(self.ev(self.a["id"], body)[0], 400)
        self.assertEqual(self.order(), [(self.b["id"], E4, 1), (self.a["id"], E17, 1)])

    def test_change_to_null_and_back_updates_gallery_and_etag(self):
        seen = {gallery(self.app)[1]["etag"]}
        st, _, out, _ = self.ev(self.a["id"], {"ev": None})
        self.assertEqual((st, json.loads(out)), (200, dict(id=self.a["id"], ev=None)))
        self.assertEqual(self.order(), [(self.b["id"], E4, 1), (self.a["id"], None, 1)])
        e1 = gallery(self.app)[1]["etag"]
        self.assertNotIn(e1, seen)
        seen.add(e1)
        st, _, out, _ = self.ev(self.a["id"], {"ev": E4})
        self.assertEqual((st, json.loads(out)), (200, dict(id=self.a["id"], ev=E4)))
        self.assertEqual(self.order(), [(self.a["id"], E4, 1), (self.b["id"], E4, 1)])  # cùng ngày + cùng sort: theo created
        e2 = gallery(self.app)[1]["etag"]
        self.assertNotIn(e2, seen)
        self.assertEqual(gallery(self.app, {"If-None-Match": e1})[0], 200)  # validator cũ → 200
        self.ev(self.a["id"], {"ev": E17})
        self.assertEqual(self.order(), [(self.b["id"], E4, 1), (self.a["id"], E17, 1)])

    def test_idempotent_and_sort_kept(self):
        self.ev(self.a["id"], {"ev": None})
        e1 = gallery(self.app)[1]["etag"]
        self.assertEqual(self.ev(self.a["id"], {"ev": None})[0], 200)
        self.assertEqual(gallery(self.app)[1]["etag"], e1)
        self.assertEqual(self.order()[1][2], 1)
        db = sqlite3.connect(store.db_path(self.d))
        self.assertEqual(db.execute("SELECT count(*) FROM videos").fetchone()[0], 2)
        db.close()

    def test_cdn_host_cannot_reach_it(self):
        st, _, _, _ = call(self.app, "POST", f"/api/admin/video/{self.a['id']}/event", host="cdn.example.com",
                           headers=dict(self.hdr(), **{"Content-Type": "application/json"}), body=b'{"ev":null}')
        self.assertEqual(st, 404)
        self.assertEqual(self.order()[1][1], E17)

    def test_no_other_mutation_routes(self):
        for m, u in (("DELETE", f"/api/admin/video/{self.a['id']}"), ("POST", f"/api/admin/video/{self.a['id']}/hide"),
                     ("POST", f"/api/admin/video/{self.a['id']}/delete")):
            self.assertIn(call(self.app, m, u, headers=self.hdr())[0], (404, 405))


class EventLoopTests(RobustnessBase):
    """Một event loop chung cho mọi request trong test: nếu handler async chờ vglobal đồng bộ thì cả loop (kể cả /healthz) đứng theo."""

    def hold_global(self, seconds=6):
        lk, ready, release = self.app.state.video_global, threading.Event(), threading.Event()

        def run():
            with lk:
                ready.set()
                release.wait(20)
        t = threading.Thread(target=run)
        t.start()
        ready.wait(5)
        timer = threading.Timer(seconds, release.set)  # chốt an toàn: nếu loop bị chặn thì khoá được thả sau `seconds` và test báo lỗi về thời gian
        timer.start()
        self.addCleanup(timer.cancel)
        self.addCleanup(t.join, 30)
        self.addCleanup(release.set)
        return release

    def test_p1_requests_complete_while_vglobal_is_held(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        uid2 = self.session(make_mp4(seed=b"other"))
        h = self.hdr()
        release = self.hold_global()
        t0 = time.time()

        async def go():
            return await asyncio.gather(
                acall(self.app, "GET", f"/api/admin/video/{uid}", headers=h),
                acall(self.app, "GET", "/healthz"),
                acall(self.app, "GET", "/api/gallery"),
                acall(self.app, "PUT", f"/api/admin/video/{uid}/chunk?off=0", headers=h, body=data[:100]),
                acall(self.app, "PUT", f"/api/admin/video/{uid2}/poster", headers=h, body=jpeg_bytes()),
                acall(self.app, "GET", f"/api/admin/video/{'0' * 32}", headers=h))
        res = asyncio.run(go())
        self.assertLess(time.time() - t0, 3, "event loop bị chặn bởi vglobal")
        self.assertEqual([r[0] for r in res], [200, 200, 200, 200, 200, 404])
        release.set()

    def test_p1_init_waits_in_thread_not_on_loop(self):
        data = make_mp4()
        h = dict(self.hdr(), **{"Content-Type": "application/json"})
        body = json.dumps(dict(name="a.mp4", size=len(data), sha256=sha(data), ev=None, title="t")).encode()
        release = self.hold_global()

        async def go():
            t0 = time.time()
            init = asyncio.ensure_future(acall(self.app, "POST", "/api/admin/video/init", headers=h, body=body))
            ev = asyncio.ensure_future(acall(self.app, "POST", f"/api/admin/video/{'0' * 16}/event", headers=h, body=b'{"ev":null}'))
            hz = await acall(self.app, "GET", "/healthz")
            quick = time.time() - t0
            release.set()  # init/event mới được vglobal (chúng chờ trong luồng, không trên loop)
            return quick, hz, await init, await ev
        quick, hz, init, ev = asyncio.run(go())
        self.assertLess(quick, 3, "event loop bị chặn")
        self.assertEqual((hz[0], init[0], ev[0]), (200, 200, 404))


class LedgerTests(RobustnessBase):
    FLOOR = 5120 * 1024 ** 2

    def disk(self, free):
        return mock.patch.object(server.shutil, "disk_usage", return_value=types.SimpleNamespace(free=free, total=free * 2, used=0))

    def test_p1_two_posters_same_snapshot_one_rejected(self):
        u1, u2 = self.session(make_mp4(seed=b"1")), self.session(make_mp4(seed=b"2"))
        pb = jpeg_bytes()
        entered, go, first = threading.Event(), threading.Event(), []

        def hook():
            if not first:
                first.append(1)
                entered.set()
                go.wait(20)
        self.app.state.video_hooks["poster_written"] = hook
        res = []
        t = threading.Thread(target=lambda: res.append(self.poster(u1, pb)))
        with self.disk(self.FLOOR + len(pb) + 5):
            t.start()
            self.assertTrue(entered.wait(10))
            self.assertEqual(self.poster(u2, pb)[0], 507)  # poster 1 đang giữ chỗ trong sổ
            go.set()
            t.join(30)
            self.assertEqual(res[0][0], 200)
            self.assertEqual(self.poster(u2, pb)[0], 200)  # đã trả chỗ

    def test_p1_ledger_primitives_are_atomic_and_released(self):
        reserve, release = self.app.state.disk_reserve, self.app.state.disk_release
        with self.disk(self.FLOOR + 100):
            self.assertTrue(reserve(60))
            self.assertFalse(reserve(60))   # cùng snapshot đĩa: lần hai bị từ chối
            self.assertTrue(reserve(40))
            self.assertFalse(reserve(1))
            release(60)
            self.assertTrue(reserve(60))
            release(60)
            release(40)
            self.assertTrue(reserve(100, hold=False))
            self.assertTrue(reserve(100, hold=False))  # hold=False không ghi sổ

    def test_p1_photo_in_flight_blocks_new_reservations_but_not_reserved_chunk(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        photo = jpeg_bytes(64, 48, (9, 9, 9))
        h = self.hdr()
        jh = dict(h, **{"Content-Type": "application/json"})
        d2 = make_mp4(seed=b"second")
        init2 = json.dumps(dict(name="b.mp4", size=len(d2), sha256=sha(d2), ev=None, title="")).encode()

        async def go():
            gate, entered = asyncio.Event(), asyncio.Event()
            with self.disk(self.FLOOR + len(data) + len(photo) + len(d2) - 1):  # vừa đủ cho ảnh + phần còn nợ của phiên 1, thiếu cho phiên 2
                ph = asyncio.ensure_future(acall(self.app, "POST", "/api/upload?name=q.jpg", headers=h, parts=[photo[:-1], photo[-1:]],
                                                 gate=gate, entered=entered))
                await entered.wait()  # ảnh đã đặt chỗ Content-Length và đang chờ phần còn lại
                blocked_init = await acall(self.app, "POST", "/api/admin/video/init", headers=jh, body=init2)
                chunk = await acall(self.app, "PUT", f"/api/admin/video/{uid}/chunk?off=0", headers=h, body=data)  # đã giữ chỗ từ init
                gate.set()
                done = await ph
                init_after = await acall(self.app, "POST", "/api/admin/video/init", headers=jh, body=init2)
            return blocked_init[0], chunk[0], done[0], init_after[0]
        self.assertEqual(asyncio.run(go()), (507, 200, 200, 200))

    def test_p1_photo_and_chunk_same_snapshot_one_rejected(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        photo = jpeg_bytes(64, 48, (7, 7, 7))
        pu = lambda: call(self.app, "POST", "/api/upload?name=z.jpg", headers=self.hdr(), body=photo)[0]  # noqa: E731
        with self.disk(self.FLOOR + len(data) + len(photo) - 1):
            self.assertEqual(pu(), 507)                      # ảnh không được lấn vào chỗ phiên video đã giữ
            self.assertEqual(self.chunk(uid, 0, data)[0], 200)
        uid2 = self.init(make_mp4(seed=b"z"))[1]["uid"]
        with self.disk(self.FLOOR + len(photo) + len(make_mp4(seed=b"z")) - 1):
            self.assertEqual(pu(), 507)                      # phiên 2 còn nợ nên ảnh thiếu chỗ
        self.chunk(uid2, 0, make_mp4(seed=b"z"))
        with self.disk(self.FLOOR + len(photo)):
            self.assertEqual(pu(), 200)


class ReconValidationTests(RobustnessBase):
    def assert_unresolved(self, uid, parts=("part", "poster")):
        self.restart()
        self.assertEqual(self.vrows(), [])
        self.assertEqual(json.load(open(self.sidecar(uid)))["state"], "failed")
        for ext in parts:
            self.assertTrue(os.path.exists(os.path.join(self.d, "video-staging", f"{uid}.{ext}")), ext)  # file staging còn nguyên
        self.assertEqual(os.listdir(os.path.join(self.d, "videos")), ["posters"])
        self.assertEqual(os.listdir(os.path.join(self.d, "videos", "posters")), [])
        st, out = self.finish(uid)
        self.assertEqual((st, out["error"]), (500, "publish_incomplete"))
        self.assertTrue(out["reason"])

    def test_p2_3_truncated_staged_mp4_is_not_published(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        part = os.path.join(self.d, "video-staging", uid + ".part")
        with open(part, "r+b") as fh:
            fh.truncate(len(data) - 1)
        self.assert_unresolved(uid)

    def test_p2_3_same_size_corrupt_staged_mp4_is_not_published(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        part = os.path.join(self.d, "video-staging", uid + ".part")
        b = bytearray(data)
        b[-3] ^= 0xFF
        open(part, "wb").write(bytes(b))
        self.assert_unresolved(uid)

    def test_p2_3_replaced_staged_poster_is_not_published(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        open(os.path.join(self.d, "video-staging", uid + ".poster"), "wb").write(jpeg_bytes(color=(250, 0, 0)))
        self.assert_unresolved(uid)

    def test_p2_3_matching_sha_but_not_a_valid_mp4_is_not_published(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        junk = b"\x00" * len(data)
        open(os.path.join(self.d, "video-staging", uid + ".part"), "wb").write(junk)
        self.edit(uid, sha256=sha(junk))  # sha khớp bản ghi nhưng mp4box không chấp nhận
        self.assert_unresolved(uid)

    def test_p2_3_invalid_poster_with_matching_sha_is_not_published(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        junk = b"not a jpeg"
        open(os.path.join(self.d, "video-staging", uid + ".poster"), "wb").write(junk)
        self.edit(uid, psha=sha(junk))
        self.assert_unresolved(uid)

    def test_p2_3_final_mp4_is_validated_too_and_never_deleted(self):
        uid, data, pb = self.crash_finish("moved")
        res = json.load(open(self.sidecar(uid)))["result"]
        fm = os.path.join(self.d, "videos", f"{res['id']}-{res['v']}.mp4")
        open(fm, "r+b").truncate(len(data) - 2)
        self.restart()
        self.assertEqual(self.vrows(), [])
        self.assertEqual(os.path.getsize(fm), len(data) - 2)
        self.assertTrue(os.path.exists(os.path.join(self.d, "videos", "posters", f"{res['id']}-{res['pv']}.jpg")))
        self.assertEqual(json.load(open(self.sidecar(uid)))["state"], "failed")

    def test_p2_3_valid_staged_files_still_publish(self):
        self.assert_published_after_restart(*self.crash_finish("publishing_saved"))


class SessionCapTests(RobustnessBase):
    def test_p2_4_unresolved_sessions_count_toward_cap(self):
        s1 = self.crash_finish("publishing_saved", data=make_mp4(seed=b"1"))
        s2 = self.crash_finish("publishing_saved", data=make_mp4(seed=b"2"))
        self.assertEqual(self.init(make_mp4(seed=b"3"))[0], 429)  # đang publishing cũng chiếm chỗ
        for uid, data, pb in (s1, s2):
            open(os.path.join(self.d, "video-staging", uid + ".part"), "r+b").truncate(len(data) - 1)
        self.restart()
        self.assertEqual({json.load(open(self.sidecar(u)))["state"] for u, _, _ in (s1, s2)}, {"failed"})
        st, out = self.init(make_mp4(seed=b"3"))
        self.assertEqual((st, out["error"]), (429, "too_many_sessions"))
        with mock.patch.object(server.time, "time", return_value=server.time.time() + 25 * 3600):  # failed hết hạn như phiên thường
            self.assertEqual(self.init(make_mp4(seed=b"3"))[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.d, "video-staging", s1[0] + ".part")))
        self.assertEqual(os.listdir(os.path.join(self.d, "videos")), ["posters"])

    def test_p2_4_reinit_same_sha_replaces_failed_session_staging_only(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        open(os.path.join(self.d, "video-staging", uid + ".part"), "r+b").truncate(len(data) - 1)
        self.restart()
        st, out = self.init(data)
        self.assertEqual(st, 200)
        self.assertNotEqual(out["uid"], uid)
        self.assertEqual(out["offset"], 0)
        self.assertEqual(self.status(uid)[0], 404)
        self.assertEqual(self.chunk(out["uid"], 0, data)[0], 200)
        self.assertEqual(self.poster(out["uid"], pb)[0], 200)
        self.assertEqual(self.finish(out["uid"])[0], 200)

    def test_p2_4_status_of_failed_session_is_ready_state(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        open(os.path.join(self.d, "video-staging", uid + ".part"), "r+b").truncate(len(data) - 1)
        self.restart()
        self.assertEqual(self.status(uid)[1]["state"], "ready")


class CraftedSidecarTests(RobustnessBase):
    def outside(self):
        return sorted(os.listdir(os.path.dirname(self.d)))

    def test_security_traversal_in_sha_is_unresolved_and_writes_nothing_outside(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        before = self.outside()
        self.edit(uid, sha256="../../../escape", psha="../../escape2",
                  result=dict(id="../../escape", v="../x", pv="../y", w=1, h=1, duration=1.0, title="t", ev=None))
        self.restart()
        self.assertEqual(json.load(open(self.sidecar(uid)))["state"], "failed")
        self.assertEqual(self.vrows(), [])
        self.assertEqual(self.outside(), before)
        self.assertEqual(self.finish(uid)[0], 500)

    def test_security_traversal_in_result_fields_is_ignored_ids_recomputed(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        before = self.outside()
        self.edit(uid, name="../../evil.mp4",
                  result=dict(id="../../evil", v="../x", pv="../y", w=9999, h=9999, duration=99999.0, title="x", ev=None))
        self.restart()
        st, res = self.finish(uid)
        self.assertEqual(st, 200, res)
        vid = hashlib.sha1(sha(data).encode()).hexdigest()[:16]
        self.assertEqual((res["id"], res["v"], res["pv"], res["w"], res["h"], res["duration"]), (vid, sha(data)[:10], sha(pb)[:10], 320, 240, 2.5))
        self.assertTrue(os.path.exists(os.path.join(self.d, "videos", f"{vid}-{sha(data)[:10]}.mp4")))
        db = sqlite3.connect(store.db_path(self.d))
        self.assertEqual(db.execute("SELECT name FROM videos").fetchall(), [("evil.mp4",)])
        db.close()
        self.assertEqual(self.outside(), before)

    def test_security_non_string_or_bad_hex_values_never_crash_startup(self):
        for field, value in (("sha256", 5), ("sha256", None), ("psha", ["x"]), ("size", "9"), ("offset", None), ("state", "weird"), ("uid", "../../x")):
            with self.subTest(field=field, value=value):
                d = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, d, True)
                self.d = d
                self.app = make_app(d)
                uid, data, pb = self.crash_finish("publishing_saved")
                self.edit(uid, **{field: value})
                app2 = make_app(d)  # không được nổ
                self.assertEqual(json.loads(gallery(app2)[2])["videos"], [])

    def test_security_bad_metadata_in_sidecar_is_unresolved(self):
        for kw in (dict(title="a\nb"), dict(title="x" * 500), dict(ev="2026-01-01"), dict(name="x.exe"), dict(size=0)):
            with self.subTest(kw=kw):
                d = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, d, True)
                self.d = d
                self.app = make_app(d)
                uid, data, pb = self.crash_finish("publishing_saved")
                self.edit(uid, **kw)
                self.restart()
                self.assertEqual(self.vrows(), [])
                self.assertEqual(os.listdir(os.path.join(d, "videos")), ["posters"])


class FinalRepairTests(RobustnessBase):
    FLOOR = 5120 * 1024 ** 2

    def disk(self, free):
        return mock.patch.object(server.shutil, "disk_usage", return_value=types.SimpleNamespace(free=free, total=free * 2, used=0))

    def photo(self, shade=1):
        return call(self.app, "POST", "/api/upload?name=p.jpg", headers=self.hdr(), body=jpeg_bytes(64, 48, (shade, 5, 6)))[0]

    def hold(self, lock, seconds=6):
        ready, release = threading.Event(), threading.Event()

        def run():
            with lock:
                ready.set()
                release.wait(20)
        t = threading.Thread(target=run)
        t.start()
        ready.wait(5)
        timer = threading.Timer(seconds, release.set)  # chốt an toàn: loop bị chặn thì khoá được thả sau `seconds` và test báo lỗi thời gian
        timer.start()
        self.addCleanup(timer.cancel)
        self.addCleanup(t.join, 30)
        self.addCleanup(release.set)
        return release

    def test_1_photo_upload_never_waits_on_ledger_lock_on_the_event_loop(self):
        h = self.hdr()
        release = self.hold(self.app.state.disk_lock)
        body = jpeg_bytes(64, 48, (3, 3, 3))

        async def go():
            t0 = time.time()
            ph = asyncio.ensure_future(acall(self.app, "POST", "/api/upload?name=w.jpg", headers=h, body=body))
            hz = await acall(self.app, "GET", "/healthz")
            quick = time.time() - t0
            gal = await acall(self.app, "GET", "/api/gallery")
            release.set()
            return quick, hz[0], gal[0], (await ph)[0]
        quick, hz, gal, ph = asyncio.run(go())
        self.assertLess(quick, 3, "event loop bị chặn bởi rlock")
        self.assertEqual((hz, gal, ph), (200, 200, 200))
        self.assertEqual(self.app.state.disk_inflight(), 0)

    def test_1_init_persistence_happens_outside_ledger_lock(self):
        lock = self.app.state.disk_lock
        seen = []
        real = os.fsync
        orig_save = None

        def spy(fd):
            seen.append(lock.locked())
            return real(fd)
        data = make_mp4()
        with mock.patch.object(server.os, "fsync", spy):
            self.assertEqual(self.init(data)[0], 200)
        self.assertTrue(seen)
        self.assertFalse(any(seen), "fsync khi đang giữ rlock")

    def test_1_init_persist_failure_rolls_back_reservation(self):
        data = make_mp4()
        with mock.patch.object(server.os, "fsync", side_effect=OSError("disk gone")):
            with self.assertRaises(Exception):
                self.init(data)
        self.assertEqual(self.app.state.video_sessions, {})
        self.assertEqual(os.listdir(os.path.join(self.d, "video-staging")), [])
        free = types.SimpleNamespace(free=self.FLOOR + len(data), total=1, used=0)
        with mock.patch.object(server.shutil, "disk_usage", return_value=free):
            self.assertEqual(self.init(data)[0], 200)  # chỗ đã đặt được trả lại

    def test_2_chunk_bytes_stay_reserved_until_offset_advance_under_ledger_lock(self):
        size = server.VIDEO_CHUNK_MAX
        data = bytes(size)
        uid = self.init(data)[1]["uid"] if False else None
        free = {"v": self.FLOOR + size}
        phase = {"admitting": False, "fired": False}
        at_hook, go, advanced = threading.Event(), threading.Event(), threading.Event()

        def du(path):
            if phase["admitting"] and not phase["fired"]:
                phase["fired"] = True  # chunk được cho đi tiếp đúng lúc ảnh đang đọc free (giữ rlock)
                go.set()
                advanced.wait(0.7)  # sửa đúng: offset chỉ tiến được SAU khi ảnh nhả rlock → không bao giờ set trong lúc chờ
            return types.SimpleNamespace(free=free["v"], total=free["v"] * 2, used=0)

        def hook_written():
            at_hook.set()
            go.wait(20)
        with mock.patch.object(server.shutil, "disk_usage", du):
            uid = self.init(data)[1]["uid"]
            self.app.state.video_hooks["chunk_written"] = hook_written
            self.app.state.video_hooks["chunk_advanced"] = advanced.set
            res = []
            t = threading.Thread(target=lambda: res.append(self.chunk(uid, 0, data)))
            t.start()
            self.assertTrue(at_hook.wait(20))
            phase["admitting"] = True
            self.assertEqual(self.photo(), 507)  # free còn floor + 16 MiB (đọc cũ) mà chunk 16 MiB chưa được coi là đã ghi
            phase["admitting"] = False
            t.join(30)
        self.assertEqual(res[0][0], 200)
        self.assertEqual(self.app.state.video_sessions[uid]["offset"], size)

    def test_2_chunk_offset_rollback_also_goes_through_ledger(self):
        data = make_mp4()
        uid = self.init(data)[1]["uid"]
        real = server.json.dump
        calls = {"n": 0}

        def failing_dump(obj, fh, *a, **k):
            if obj.get("offset") == len(data) and calls["n"] == 0:
                calls["n"] += 1
                raise OSError("sidecar write failed")
            return real(obj, fh, *a, **k)
        with mock.patch.object(server.json, "dump", failing_dump):
            st, out = self.chunk(uid, 0, data)
        self.assertEqual((st, out["offset"]), (500, 0))
        self.assertEqual(os.path.getsize(os.path.join(self.d, "video-staging", uid + ".part")), 0)
        self.assertEqual(self.app.state.video_sessions[uid]["offset"], 0)
        self.assertEqual(self.chunk(uid, 0, data)[0], 200)

    def test_3_photo_cleanup_releases_everything_even_if_temp_remove_fails(self):
        real = os.remove

        def bad_remove(path, *a, **k):
            if str(path).endswith(".part") and os.sep + "tmp" + os.sep in str(path):
                raise OSError("cannot remove")
            return real(path, *a, **k)
        with mock.patch.object(server.os, "remove", bad_remove):
            for i in range(server.MAX_UPLOAD_SLOTS + 2):  # nhiều hơn số slot: nếu slot rò thì các lần sau phải 503
                with self.assertRaises(Exception):  # ảnh hỏng → 400 nhưng file tạm còn đó và xoá nó lỗi OSError
                    call(self.app, "POST", "/api/upload?name=bad.jpg", headers=self.hdr(), body=b"not an image %d" % i)
        self.assertEqual(self.app.state.disk_inflight(), 0)
        for f in os.listdir(os.path.join(self.d, "tmp")):
            real(os.path.join(self.d, "tmp", f))
        self.assertEqual(self.photo(200), 200)  # slot, hạn mức và chỗ đĩa đều đã được trả
        with self.disk(self.FLOOR + len(jpeg_bytes(64, 48, (9, 5, 6)))):
            self.assertEqual(self.photo(9), 200)

    def test_4_publish_incomplete_response_has_only_a_short_code(self):
        for corrupt in ("part_truncate", "poster_swap", "missing"):
            with self.subTest(corrupt=corrupt):
                d = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, d, True)
                self.d = d
                self.app = make_app(d)
                uid, data, pb = self.crash_finish("publishing_saved")
                stage = os.path.join(d, "video-staging")
                if corrupt == "part_truncate":
                    open(os.path.join(stage, uid + ".part"), "r+b").truncate(len(data) - 1)
                elif corrupt == "poster_swap":
                    open(os.path.join(stage, uid + ".poster"), "wb").write(jpeg_bytes(color=(250, 0, 0)))
                else:
                    os.remove(os.path.join(stage, uid + ".poster"))
                self.restart()
                st, _, raw, _ = call(self.app, "POST", f"/api/admin/video/{uid}/finish", headers=self.hdr())
                out = json.loads(raw)
                self.assertEqual((st, out["error"]), (500, "publish_incomplete"))
                self.assertRegex(out["reason"], r"^[a-z0-9_]{1,40}$")
                self.assertNotIn(d, raw.decode())
                self.assertNotIn("/", raw.decode())
                self.assertNotIn(d, open(os.path.join(stage, uid + ".json")).read())

    def test_4_old_long_reason_in_sidecar_is_not_echoed(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        open(os.path.join(self.d, "video-staging", uid + ".part"), "r+b").truncate(len(data) - 1)
        self.restart()
        self.edit(uid, reason=f"{self.d}/video-staging/x.part: kích thước/sha256 không khớp")
        self.restart()
        st, _, raw, _ = call(self.app, "POST", f"/api/admin/video/{uid}/finish", headers=self.hdr())
        self.assertEqual(st, 500)
        self.assertNotIn(self.d, raw.decode())

    def test_4_oserror_text_stays_out_of_response(self):
        uid, data, pb = self.crash_finish("publishing_saved")
        with mock.patch.object(server, "sha_file", side_effect=OSError(f"[Errno 5] boom {self.d}/secret")):
            st, _, raw, _ = call(self.app, "POST", f"/api/admin/video/{uid}/finish", headers=self.hdr())
        self.assertEqual(st, 500)
        self.assertNotIn(self.d, raw.decode())
        self.assertNotIn("boom", raw.decode())


if __name__ == "__main__":
    unittest.main()
