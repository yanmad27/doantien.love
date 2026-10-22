"""API /api/search phía máy chủ: ?scope= (máy chủ vẫn hỗ trợ) phải bằng việc lọc phản hồi "tất cả" theo `ev` (cùng một ảnh chụp dữ liệu), và
/api/search nén gzip theo Accept-Encoding. Khách không còn gửi ?scope= (mỗi lần tìm là một request không phạm vi).   .venv/bin/python -I -m unittest discover -s dev -p 'test_*.py'"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import numpy as np  # noqa: E402
import server  # noqa: E402
from _asgi import call  # noqa: E402
from test_events import E4, E17, reassign  # noqa: E402
from test_hidden import PID, TOKEN, TagEngine, build_originals, gallery, hide, jpeg, process  # noqa: E402

CONFIDENT, MAYBE = 0.40, 0.28
QUERY_TAG = 12  # không ảnh nào mang thẻ này
WEIGHT = {1: 0.90, 2: 0.45, 3: 0.30, 4: 0.20, 5: 0.10}  # 5: dưới SCORE_FLOOR
TAGS = dict(a1=1, a2=1, a3=2, b1=2, b2=3, c1=3, c2=4, c3=5, s1=1, s2=4, n1=2)  # trùng thẻ = trùng điểm


class MixEngine(TagEngine):
    def embed(self, rgb, faces):
        super().embed(rgb, faces)
        if int(round((int(rgb[16, 16, 0]) - 10) / 20)) == QUERY_TAG:
            for f in faces:
                f.emb = np.zeros(512, np.float32)
                for k, w in WEIGHT.items():
                    f.emb[k] = w


def filter_scope(r, sc):
    """Lọc phản hồi "tất cả" theo ngày — kết quả máy chủ ?scope= phải khớp."""
    res = [x for x in r["results"] if x["ev"] == sc]
    return dict(r, results=res, total_matches=len(res), truncated=False)


def complete(r, events=(E4, E17)):
    return r.get("truncated") is False and isinstance(r.get("results"), list) and all(x.get("ev") in events for x in r["results"])


def norm(r):
    """Bỏ phần thay đổi theo lượt gọi: thời gian, token ảnh hết hạn."""
    return dict({k: v for k, v in r.items() if k != "timing_ms"}, results=[{k: v for k, v in x.items() if k != "t"} for x in r["results"]])


def view(r):
    """Cái khách thấy: id theo thứ tự, điểm làm tròn, khung, chuỗi, chia nhóm chắc chắn/có thể, số đếm."""
    seen, hits = set(), []
    for x in sorted(r["results"], key=lambda x: -x["score"]):
        if x["id"] not in seen:
            seen.add(x["id"]); hits.append(x)
    tiles, by_b = [], set()
    for x in hits:
        if x["b"]:
            if x["b"] in by_b:
                continue
            by_b.add(x["b"])
        tiles.append(x)
    main = [x["id"] for x in tiles if x["score"] >= CONFIDENT]
    maybe = [x["id"] for x in tiles if MAYBE <= x["score"] < CONFIDENT]
    return dict(ids=[x["id"] for x in r["results"]], scores=[round(x["score"], 4) for x in r["results"]], bbox=[x["bbox"] for x in r["results"]],
                b=[x["b"] for x in r["results"]], main=main, maybe=maybe, total=r["total_matches"], trunc=r["truncated"], n=len(r["results"]))


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        build_originals(self.d, tags=TAGS)
        with mock.patch.object(server, "FaceEngine", MixEngine):
            self.app = server.create_app(self.d, "unused", ["CPUExecutionProvider"], admin_token=TOKEN)
        process(self.app)
        p = os.path.join(self.d, "q.jpg")
        jpeg(p, 128, QUERY_TAG)
        self.q = open(p, "rb").read()

    def go(self, scope=None, extra="", headers=None):
        q = "&".join(x for x in ((f"scope={scope}" if scope else ""), extra) if x)
        return call(self.app, "POST", "/api/search" + ("?" + q if q else ""), headers=headers, body=self.q)

    def js(self, scope=None, extra=""):
        st, _, body, _ = self.go(scope, extra)
        self.assertEqual(st, 200)
        return json.loads(body)


class EquivalenceTests(Base):
    def check_all_scopes(self, tune=False):
        extra = "tune=1" if tune else ""
        full = self.js(extra=extra)
        self.assertTrue(complete(full))
        for sc in (E4, E17):
            want = self.js(sc, extra)
            got = filter_scope(full, sc)
            self.assertEqual(norm(got) | {"query": 0}, norm(want) | {"query": 0}, sc)  # toàn bộ phản hồi, trừ query (giống nhau, so riêng)
            self.assertEqual(got["query"], want["query"])
            self.assertEqual(view(got), view(want), sc)
        return full

    def test_basic_with_ties_and_hidden(self):
        reassign(self.app, ["a1", "b1", "s1", "c2"], E17)
        hide(self.app, ["s2", "a2"])
        full = self.check_all_scopes()
        sc = [x["score"] for x in full["results"]]
        self.assertEqual(sc, sorted(sc, reverse=True))
        self.assertGreater(len(sc) - len(set(sc)), 0, "cần có điểm trùng")
        self.assertTrue(any(s >= CONFIDENT for s in sc) and any(MAYBE <= s < CONFIDENT for s in sc) and any(s < MAYBE for s in sc))
        self.assertNotIn(0.10, sc)

    def test_hidden_results_absent_from_manifest_carry_ev(self):
        reassign(self.app, ["a1", "a2"], E17)
        hide(self.app, ["a1", "a2", "s1"])
        shown = {p[0] for p in json.loads(gallery(self.app)[2])["photos"]}
        full = self.check_all_scopes()
        hid = [x for x in full["results"] if x["id"] in (PID["a1"], PID["a2"], PID["s1"])]
        self.assertEqual(len(hid), 3)
        self.assertFalse(any(x["id"] in shown for x in hid))
        self.assertEqual({x["id"]: x["ev"] for x in hid}, {PID["a1"]: E17, PID["a2"]: E17, PID["s1"]: E4})
        self.assertTrue(any(x["b"] for x in hid), "chuỗi ảnh ẩn trúng ≥ 2 khung phải có b")

    def test_burst_ids_stable_across_scope(self):
        reassign(self.app, ["a3"], E17)
        hide(self.app, ["c1", "c2"])
        self.check_all_scopes()

    def test_empty_scope(self):
        full = self.check_all_scopes()
        self.assertEqual(filter_scope(full, E17)["results"], [])
        self.assertEqual(self.js(E17)["results"], [])
        reassign(self.app, list(PID), E17)
        full = self.check_all_scopes()
        self.assertEqual(filter_scope(full, E4)["total_matches"], 0)

    def test_tune_query_ignored_by_server(self):
        reassign(self.app, ["a1", "b1"], E17)
        a, b = self.js(E17), self.js(E17, "tune=1")
        self.assertEqual(norm(a), norm(b))
        self.check_all_scopes(tune=True)

    def test_truncated_all_differs_from_scoped_which_is_authoritative(self):
        reassign(self.app, ["a1", "b1", "s1", "c2", "s2"], E17)
        with mock.patch.object(server, "MAX_RESULTS", 3):
            full = self.js()
            self.assertTrue(full["truncated"] and not complete(full))
            self.assertGreater(full["total_matches"], len(full["results"]))
            for sc in (E4, E17):
                r = self.js(sc)
                self.assertEqual(len(r["results"]), min(3, r["total_matches"]))
                self.assertTrue(all(x["ev"] == sc for x in r["results"]))
                self.assertEqual(r["truncated"], r["total_matches"] > 3)
                if r["truncated"]:  # lọc phản hồi bị cắt sẽ cho số đếm/thứ tự sai → máy chủ mới là nguồn đúng
                    self.assertNotEqual(view(filter_scope(full, sc)), view(r))
            self.assertEqual(norm(self.js())["results"], norm(full)["results"])  # quay lại "tất cả": cùng phản hồi cũ


class GzipTests(Base):
    def setUp(self):
        super().setUp()
        self.plain = self.go()[2]

    def test_accept_encoding_parsing(self):
        T, F = True, False
        for h, want in ((None, (F, T)), ("", (F, T)), ("gzip", (T, T)), ("GZIP", (T, T)), (" Gzip ; Q=0.5 ", (T, T)), ("br, gzip;q=0.5", (T, T)), ("gzip;q=0", (F, T)),
                        ("gzip; q=0.0", (F, T)), ("identity", (F, T)), ("br", (F, T)), ("*", (T, T)), ("*;q=0", (F, F)), ("gzip;q=0, *", (F, T)),
                        ("x-gzip", (T, T)), ("gzip;q=abc", (F, T)), ("deflate, gzip;q=1.0", (T, T)), ("gzip, identity;q=0", (T, F)),
                        ("identity;q=0", (F, F)), ("gzip;q=0, identity;q=0", (F, F)), ("*;q=0, gzip", (T, F)), ("*;q=0, identity", (F, T)),
                        ("  GZIP , IDENTITY ; q=0 ", (T, F)), ("gzip,identity;q=0.0", (T, F))):
            self.assertEqual(server.accepted_codings(h), want, h)

    def test_encoding_choice_small_and_neither_acceptable(self):
        small, big = 10, server.SEARCH_GZIP_MIN
        pick = server.search_encoding
        self.assertEqual(pick("gzip", small), "identity")  # nhỏ + identity ổn → không nén
        self.assertEqual(pick("gzip", big), "gzip")
        self.assertEqual(pick("gzip, identity;q=0", small), "gzip")  # identity bị loại → nén dù nhỏ (kể cả kết quả rỗng)
        self.assertEqual(pick("*;q=0, gzip", small), "gzip")
        self.assertEqual(pick("*;q=0", big), "identity")  # không cái nào chấp nhận được → identity, không 406
        self.assertEqual(pick("identity;q=0", big), "identity")
        self.assertEqual(pick("gzip;q=0, identity;q=0", small), "identity")
        self.assertEqual(pick(" GZIP ,IDENTITY;Q=0", small), "gzip")

    def test_wire_small_body_identity_excluded(self):
        reassign(self.app, list(PID), E4)
        for ae, gz in (("gzip, identity;q=0", True), ("*;q=0, gzip", True), ("*;q=0", False), ("identity;q=0", False), ("gzip;q=0, identity;q=0", False)):
            st, h, body, _ = call(self.app, "POST", f"/api/search?scope={E17}", headers={"Accept-Encoding": ae}, body=self.q)  # 17/10 rỗng → thân nhỏ
            self.assertEqual(st, 200, ae)
            self.assertEqual(h.get("content-encoding") == "gzip", gz, ae)
            self.assertEqual(h["vary"], "Accept-Encoding")
            raw = gzip.decompress(body) if gz else body
            self.assertLess(len(raw), server.SEARCH_GZIP_MIN)
            self.assertEqual(json.loads(raw)["results"], [], ae)

    def test_wire(self):
        for ae, gz in ((None, False), ("gzip", True), ("gzip, deflate, br", True), ("gzip;q=0", False), ("identity", False)):
            st, h, body, _ = self.go(headers={"Accept-Encoding": ae} if ae is not None else None)
            self.assertEqual(st, 200)
            self.assertEqual(h["cache-control"], "no-store", ae)
            self.assertEqual(h["vary"], "Accept-Encoding", ae)
            self.assertEqual(h["content-type"], "application/json", ae)
            self.assertEqual(h["content-length"], str(len(body)))
            if gz:
                self.assertEqual(h["content-encoding"], "gzip", ae)
                self.assertLess(len(body), len(self.plain))
                body = gzip.decompress(body)
            else:
                self.assertNotIn("content-encoding", h, ae)
            self.assertEqual(norm(json.loads(body)), norm(json.loads(self.plain)))

    def test_plain_body_is_unchanged_json(self):
        r = json.loads(self.plain)
        self.assertEqual(set(r), {"query", "results", "total_matches", "truncated", "timing_ms"})

    def test_errors_not_compressed_and_scope_400(self):
        st, h, body, _ = call(self.app, "POST", "/api/search?scope=bad", headers={"Accept-Encoding": "gzip"}, body=self.q)
        self.assertEqual(st, 400)
        self.assertNotIn("content-encoding", h)

    def test_small_body_stays_identity(self):
        with mock.patch.object(server, "SEARCH_GZIP_MIN", 10**9):
            st, h, body, _ = self.go(headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", h)
        self.assertEqual(h["vary"], "Accept-Encoding")


if __name__ == "__main__":
    unittest.main()
