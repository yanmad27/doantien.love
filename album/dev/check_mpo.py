"""MPO .JPG (JPEG máy ảnh) phải tải lên + xử lý được. Chạy: python dev/check_mpo.py ảnh_có_mặt.jpg [base_url]
Không có base_url thì tự chạy server cục bộ."""
import io, json, os, subprocess, sys, tempfile, time, urllib.request
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1]
base = sys.argv[2] if len(sys.argv) > 2 else None
im = Image.open(src).convert("RGB"); im.thumbnail((1600, 1600))
buf = io.BytesIO(); im.save(buf, format="MPO", save_all=True, append_images=[im.resize((160, 120))])
body = buf.getvalue(); assert Image.open(io.BytesIO(body)).format == "MPO"
srv = None
if not base:
    base = "http://127.0.0.1:8793"
    env = dict(os.environ, DATA_DIR=tempfile.mkdtemp(), PORT="8793")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try: urllib.request.urlopen(base + "/healthz", timeout=1).read(); break
        except Exception: time.sleep(0.5)

def call(path, data=None, method=None):
    r = urllib.request.Request(base + path, data=data, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
try:
    s, b = call("/api/upload?name=TEST0001.JPG", body, "POST"); print("upload", s, b)
    ok = s == 200
    if ok:
        print("process", call("/api/process", b"", "POST"))
        for _ in range(120):
            s, b = call("/api/process"); d = json.loads(b)
            if d.get("state") != "running": break
            time.sleep(1)
        print("status", b)
        st = json.loads(call("/api/stats")[1]); print("stats", st)
finally:
    if srv: srv.terminate()
sys.exit(0 if ok else 1)
