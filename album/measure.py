"""Đo 4 chỉ số trên một thư mục ảnh: thời gian lập chỉ mục/ảnh, độ trễ truy vấn selfie,
RAM (RSS) của app đang chạy, dung lượng chỉ mục/ảnh.

  python measure.py --photos <thư mục ảnh> --queries <thư mục hoặc file selfie>

Chạy trong data dir tạm (không đụng data thật), xoá khi xong. Server chạy với TMPDIR rỗng
riêng để kiểm tra selfie không bị ghi ra đĩa.
"""
import argparse
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EXTS = (".jpg", ".jpeg", ".png", ".webp")


def rss_mb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout
    return int(out.strip()) / 1024


def dir_bytes(d):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, round(p / 100 * (len(v) - 1)))]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--photos", required=True)
    ap.add_argument("--queries", help="thư mục/file selfie; mặc định dùng vài ảnh trong --photos")
    ap.add_argument("--model-dir", default=os.environ.get("ALBUM_MODEL_DIR", os.path.join(HERE, "models")))
    ap.add_argument("--det-size", type=int, default=1600)
    ap.add_argument("--providers", default="CPUExecutionProvider")
    ap.add_argument("--repeats", type=int, default=5, help="số lần lặp mỗi selfie")
    ap.add_argument("--port", type=int, default=8791)
    a = ap.parse_args()

    q = a.queries or a.photos
    qfiles = [q] if os.path.isfile(q) else sorted(
        os.path.join(r, f) for r, _, fs in os.walk(q) for f in fs if f.lower().endswith(EXTS))[:10]
    if not qfiles:
        sys.exit("Không có ảnh selfie để truy vấn")
    py = sys.executable
    work = tempfile.mkdtemp(prefix="album-measure-")
    data, tmp = os.path.join(work, "data"), os.path.join(work, "tmp")
    os.makedirs(tmp)
    try:
        # 1) lập chỉ mục
        subprocess.run([py, os.path.join(HERE, "index_photos.py"), "--photos", a.photos, "--data-dir", data,
                        "--model-dir", a.model_dir, "--det-size", str(a.det_size),
                        "--providers", a.providers], check=True, stdout=subprocess.DEVNULL)
        idx_peak_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2**20  # macOS: byte
        s = json.load(open(os.path.join(data, "last_index_run.json")))
        n, nf = s["photos_total"], s["faces_total"]

        # 2) dung lượng chỉ mục
        db_b = os.path.getsize(os.path.join(data, "index.db"))
        th_b = dir_bytes(os.path.join(data, "thumbs"))
        emb_b = nf * 512 * 2  # float16

        # 3) server + truy vấn
        env = dict(os.environ, TMPDIR=tmp)
        srv = subprocess.Popen([py, os.path.join(HERE, "server.py"), "--data-dir", data, "--model-dir", a.model_dir,
                                "--port", str(a.port), "--providers", a.providers], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            url = f"http://127.0.0.1:{a.port}"
            for _ in range(120):  # chờ app nạp model
                try:
                    urllib.request.urlopen(url + "/api/stats", timeout=1).read()
                    break
                except (urllib.error.URLError, ConnectionError):
                    if srv.poll() is not None:
                        sys.exit("Server thoát sớm")
                    time.sleep(0.5)
            rss_idle = rss_mb(srv.pid)

            def query(path):
                body = open(path, "rb").read()
                req = urllib.request.Request(url + "/api/search", data=body, method="POST",
                                             headers={"Content-Type": "application/octet-stream"})
                t = time.perf_counter()
                try:
                    d = json.load(urllib.request.urlopen(req, timeout=60))
                except urllib.error.HTTPError as e:
                    if e.code == 422:
                        return None
                    raise
                return (time.perf_counter() - t) * 1e3, d["timing_ms"]["total"], d["timing_ms"]

            query(qfiles[0])  # khởi động nguội, không tính
            e2e, srv_ms, parts, skipped = [], [], [], 0
            for _ in range(a.repeats):
                for f in qfiles:
                    r = query(f)
                    if r is None:
                        skipped += 1
                        continue
                    e2e.append(r[0]); srv_ms.append(r[1]); parts.append(r[2])
            rss_after = rss_mb(srv.pid)
        finally:
            srv.terminate()
            srv.wait(timeout=10)
        leaked = os.listdir(tmp)

        part = {k: statistics.mean(p[k] for p in parts) for k in parts[0]}
        print("\n=== KẾT QUẢ ĐO ===")
        print(f"Ảnh: {n}, khuôn mặt: {nf} ({nf / n:.1f}/ảnh), det-size {a.det_size}, providers {a.providers}")
        print(f"1) Lập chỉ mục: {s['wall_s_per_photo']:.3f} s/ảnh (tổng {s['wall_s']:.1f}s) — "
              f"giải mã {s['decode_s_per_photo']:.3f} + thumb {s['thumb_s_per_photo']:.3f} (luồng phụ, chạy song song), "
              f"dò {s['detect_s_per_photo']:.3f}, nhúng {s['embed_s_per_photo']:.3f} s/ảnh; "
              f"RSS đỉnh khi lập chỉ mục ~{idx_peak_mb:.0f} MB")
        print(f"2) Truy vấn selfie ({len(e2e)} lần, {len(qfiles)} ảnh x {a.repeats}, bỏ {skipped} ảnh không có mặt, {n} ảnh trong chỉ mục):")
        print(f"   trong máy chủ: p50 {pct(srv_ms, 50):.0f} ms, p95 {pct(srv_ms, 95):.0f} ms "
              f"(giải mã {part['decode']:.0f} + dò {part['detect']:.0f} + nhúng {part['embed']:.0f} + so sánh {part['compare']:.1f} ms)")
        print(f"   đầu-cuối qua HTTP: p50 {pct(e2e, 50):.0f} ms, p95 {pct(e2e, 95):.0f} ms")
        print(f"3) RSS app: nhàn rỗi {rss_idle:.0f} MB, sau {len(e2e) + 1} truy vấn {rss_after:.0f} MB")
        print(f"4) Chỉ mục: {(db_b + th_b) / n / 1024:.1f} KB/ảnh tổng = "
              f"embedding+metadata {db_b / n / 1024:.1f} KB/ảnh (index.db {db_b / 1024:.0f} KB; riêng embedding thô {emb_b / n / 1024:.1f} KB/ảnh) "
              f"+ thumbnail {th_b / n / 1024:.1f} KB/ảnh ({th_b / 1024:.0f} KB)")
        print(f"Selfie ghi ra TMPDIR của server: {'KHÔNG (rỗng)' if not leaked else 'CÓ: ' + str(leaked)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
