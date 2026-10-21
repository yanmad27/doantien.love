"""Test hai sự kiện (4/10 và 17/10) trong một album: bảng photo_event, tải lên theo sự kiện, gán lại hàng loạt, manifest, burst không
vắt qua sự kiện, tìm kiếm theo phạm vi, --rebuild, DB cũ chưa có bảng.   .venv/bin/python -W ignore -m unittest discover -s dev -p 'test_*.py'"""
import io
import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import index_photos  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
from _asgi import call  # noqa: E402
from test_hidden import (PID, Base, build_originals, cookie_hdr, gal_rows, gallery, hide, jpeg, make_app, post, process,  # noqa: E402
                         query_bytes, search)

E4, E17 = "2026-10-04", "2026-10-17"


def reassign(app, names, event, ids=None):
    return post(app, "/api/admin/event", {"ids": ids or [PID[n] for n in names], "event": event})


def ev_of(app):
    return {r[0]: r[8] for r in gal_rows(app)}


def upload(app, d, name, event=None, shade=77, tag=1, body=None):
    if body is None:
        p = os.path.join(d, "up-" + name)
        jpeg(p, shade, tag)
        body = open(p, "rb").read()
    q = f"/api/upload?name={name}" + (f"&event={event}" if event else "")
    st, _, out, _ = call(app, "POST", q, headers=cookie_hdr(app), body=body)
    return st, json.loads(out)


def db_events(d):
    db = sqlite3.connect(store.db_path(d))
    try:
        return dict(db.execute("SELECT photo_id, event FROM photo_event").fetchall())
    finally:
        db.close()


class EventTests(Base):
    def test_default_all_in_first_event_no_rows(self):
        rows = gal_rows(self.app)
        self.assertTrue(all(len(r) == 9 and r[8] == E4 for r in rows))
        self.assertEqual(db_events(self.d), {})
        g = json.loads(gallery(self.app)[2])
        self.assertEqual(g["events"], [dict(id=E4, label="4/10", n=11), dict(id=E17, label="17/10", n=0)])

    def test_reassign_hidden_unknown_bad(self):
        hide(self.app, ["a1"])
        st, _, body, _ = reassign(self.app, ["a1", "s1"], E17, ids=[PID["a1"], PID["s1"], "f" * 16])
        self.assertEqual((st, json.loads(body)), (200, dict(changed=2, unknown=1, event=E17)))
        self.assertNotIn("f" * 16, db_events(self.d))
        self.assertEqual(db_events(self.d), {PID["a1"]: E17, PID["s1"]: E17})
        st, _, body, _ = reassign(self.app, ["a1"], E17)
        self.assertEqual(json.loads(body)["changed"], 0)  # idempotent
        # manifest công khai: a1 ẩn không lộ; đếm theo sự kiện không tính ảnh ẩn
        g = json.loads(gallery(self.app)[2])
        self.assertNotIn(PID["a1"], json.dumps(g["photos"]))
        self.assertEqual([e["n"] for e in g["events"]], [9, 1])
        self.assertEqual(ev_of(self.app)[PID["s1"]], E17)
        adm = json.loads(call(self.app, "GET", "/api/admin/photos", headers=cookie_hdr(self.app))[2])
        self.assertEqual(adm["by_event"], {E4: dict(all=9, shown=9, hidden=0), E17: dict(all=2, shown=1, hidden=1)})
        self.assertEqual({p[0]: p[9] for p in adm["photos"]}[PID["a1"]], E17)
        # về mặc định → xoá dòng
        reassign(self.app, ["a1", "s1"], E4)
        self.assertEqual(db_events(self.d), {})
        for bad in (dict(ids=[PID["a1"]], event="2026-10-05"), dict(ids=[PID["a1"]]), dict(ids=[], event=E17), dict(ids=["zz"], event=E17),
                    dict(ids=[PID["a1"]], event=E17, x=1), dict(ids=[PID["a1"]], event=["2026-10-17"])):
            self.assertEqual(post(self.app, "/api/admin/event", bad)[0], 400, bad)
        self.assertEqual(post(self.app, "/api/admin/event", dict(ids=[PID["a1"]], event=E17), headers={})[0], 401)

    def test_bursts_do_not_cross_events(self):
        b = {r[0]: r[7] for r in gal_rows(self.app)}
        self.assertEqual(b[PID["a2"]], PID["a1"])
        reassign(self.app, ["a3"], E17)  # a1,a2 còn nhóm 2 ở 4/10; a3 đứng một mình ở 17/10
        b = {r[0]: r[7] for r in gal_rows(self.app)}
        self.assertEqual((b[PID["a1"]], b[PID["a2"]], b[PID["a3"]]), (PID["a1"], PID["a1"], None))
        reassign(self.app, ["a2"], E17)  # a1 một mình; a2+a3 thành nhóm của 17/10, khung đầu = a2
        b = {r[0]: r[7] for r in gal_rows(self.app)}
        self.assertEqual((b[PID["a1"]], b[PID["a2"]], b[PID["a3"]]), (None, PID["a2"], PID["a2"]))

    def test_search_scope(self):
        reassign(self.app, ["a1", "b1", "s1"], E17)
        hide(self.app, ["s2"])
        names = lambda r: {x["id"] for x in r["results"]}
        every = names(search(self.app, self.q))
        self.assertEqual(len(every), 11)  # mặc định = all (cài đặt tìm cả ảnh ẩn bật)
        for scope, want in ((E17, {PID[n] for n in ("a1", "b1", "s1")}), ("all", every)):
            st, _, body, _ = call(self.app, "POST", f"/api/search?scope={scope}", body=self.q)
            r = json.loads(body)
            self.assertEqual((st, names(r)), (200, want), scope)
            self.assertEqual(r["total_matches"], len(want))
        r4 = json.loads(call(self.app, "POST", f"/api/search?scope={E4}", body=self.q)[2])
        self.assertEqual(names(r4), every - {PID[n] for n in ("a1", "b1", "s1")})
        self.assertTrue(all(x["ev"] == E4 for x in r4["results"]))
        for bad in ("2026-10-05", "ALL", "", "a;b"):
            self.assertEqual(call(self.app, "POST", "/api/search?scope=" + bad, body=self.q)[0], 400, bad)

    def test_search_scope_applied_before_cut(self):
        reassign(self.app, ["a1", "b1", "s1"], E17)
        with mock.patch.object(server, "MAX_RESULTS", 2):
            r = json.loads(call(self.app, "POST", f"/api/search?scope={E17}", body=self.q)[2])
            self.assertEqual((len(r["results"]), r["total_matches"], r["truncated"]), (2, 3, True))
            self.assertTrue(all(x["ev"] == E17 for x in r["results"]))
            r = json.loads(call(self.app, "POST", f"/api/search?scope={E4}", body=self.q)[2])
            self.assertEqual((len(r["results"]), r["total_matches"]), (2, 8))

    def test_search_burst_not_across_scope(self):
        reassign(self.app, ["a3"], E17)
        r = json.loads(call(self.app, "POST", f"/api/search?scope={E17}", body=self.q)[2])
        self.assertEqual([x["b"] for x in r["results"]], [None])


class UploadTests(Base):
    def test_upload_event_default_and_duplicate_and_reupload(self):
        st, out = upload(self.app, self.d, "new1.jpg", E17, shade=70)
        self.assertEqual((st, out["status"], out["event"]), (200, "ok", E17))
        pid = store.photo_id("new1.jpg")
        self.assertEqual(db_events(self.d), {pid: E17})  # ghi cùng giao dịch với originals, chưa cần Xử lý
        st, out = upload(self.app, self.d, "new2.jpg", None, shade=71)
        self.assertEqual((out["status"], out["event"]), ("ok", E4))
        self.assertNotIn(store.photo_id("new2.jpg"), db_events(self.d))  # mặc định: không ghi dòng
        self.assertEqual(upload(self.app, self.d, "new3.jpg", "2026-10-05", shade=72)[0], 400)
        self.assertFalse(os.path.exists(os.path.join(self.d, "originals", "new3.jpg")))
        process(self.app)
        self.assertEqual(ev_of(self.app)[pid], E17)
        self.assertEqual(ev_of(self.app)[store.photo_id("new2.jpg")], E4)
        # trùng SHA-256: giữ sự kiện đang có, báo ra
        body = open(os.path.join(self.d, "up-new1.jpg"), "rb").read()
        st, out = upload(self.app, self.d, "again.jpg", E4, body=body)
        self.assertEqual((out["status"], out["name"], out["event"]), ("duplicate", "new1.jpg", E17))
        self.assertEqual(db_events(self.d), {pid: E17})
        st, out = upload(self.app, self.d, "again2.jpg", E17, body=open(os.path.join(self.d, "up-new2.jpg"), "rb").read())
        self.assertEqual((out["status"], out["event"]), ("duplicate", E4))
        self.assertNotIn(store.photo_id("new2.jpg"), db_events(self.d))
        # bản gốc mất → tải lại cùng tên: sự kiện được chọn ghi đè
        os.remove(os.path.join(self.d, "originals", "new1.jpg"))
        st, out = upload(self.app, self.d, "new1.jpg", E4, body=body)
        self.assertEqual((out["status"], out["name"], out["event"]), ("ok", "new1.jpg", E4))
        self.assertNotIn(pid, db_events(self.d))
        os.remove(os.path.join(self.d, "originals", "new1.jpg"))
        st, out = upload(self.app, self.d, "new1.jpg", E17, body=body)
        self.assertEqual((out["status"], out["event"]), ("ok", E17))
        self.assertEqual(db_events(self.d)[pid], E17)


class PersistenceTests(Base):
    def test_keep_and_restore_owner_state_roundtrip(self):
        reassign(self.app, ["a1", "s1"], E17)
        hide(self.app, ["s1"])
        want = db_events(self.d)
        self.assertEqual(len(want), 2)
        old = store.keep_owner_state(store.db_path(self.d))
        self.assertEqual(dict(old[2]), want)
        for sfx in ("", "-wal", "-shm"):
            if os.path.exists(store.db_path(self.d) + sfx):
                os.remove(store.db_path(self.d) + sfx)
        db = store.connect(self.d)
        store.restore_owner_state(db, *old)
        db.close()
        self.assertEqual(db_events(self.d), want)

    def test_rebuild_cli_copies_photo_event(self):
        reassign(self.app, ["a1"], E17)
        want = db_events(self.d)
        from test_hidden import TagEngine
        with mock.patch.object(sys, "argv", ["index_photos.py", "--photos", self.orig, "--data-dir", self.d, "--rebuild"]), \
                mock.patch.object(index_photos, "FaceEngine", TagEngine):
            index_photos.main()
        self.assertEqual(db_events(self.d), want)
        self.assertEqual(ev_of(make_app(self.d))[PID["a1"]], E17)

    def test_old_db_without_table_opens(self):
        db = sqlite3.connect(store.db_path(self.d))
        db.execute("DROP TABLE photo_event")
        db.commit()
        db.close()
        self.assertEqual(store.keep_owner_state(store.db_path(self.d))[2], [])
        app = make_app(self.d)  # connect() tạo lại bảng (CREATE TABLE IF NOT EXISTS)
        self.assertTrue(all(r[8] == E4 for r in gal_rows(app)))
        self.assertEqual(len(gal_rows(app)), 11)
        self.assertEqual(reassign(app, ["a1"], E17)[0], 200)

    def test_old_code_ignores_table(self):
        reassign(self.app, ["a1"], E17)
        db = sqlite3.connect(store.db_path(self.d))
        # truy vấn của bản cũ (không nhắc photo_event) vẫn chạy bình thường khi bảng tồn tại
        self.assertEqual(db.execute("SELECT count(*) FROM photos p LEFT JOIN photo_hidden h ON h.id=p.id").fetchone()[0], 11)
        db.close()


if __name__ == "__main__":
    unittest.main()
