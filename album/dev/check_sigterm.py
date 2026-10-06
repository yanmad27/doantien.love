"""Deploy (SIGTERM/SIGKILL) giữa lúc Xử lý không được loại oan ảnh; ảnh đã bị loại tải lại cùng nội dung phải được nhận.
  python dev/check_sigterm.py ảnh_có_mặt.jpg"""
import io, json, os, signal, subprocess, sys, tempfile, time, urllib.request, urllib.error
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT, URL, D = 8794, "http://127.0.0.1:8794", tempfile.mkdtemp()
MK = os.path.join(D, "decoding.json")
base = Image.open(sys.argv[1]).convert("RGB")
fails = []


def photo(i):
    im = base.copy(); im.paste((i * 37 % 256, i * 91 % 256, i * 53 % 256), (0, 0, 200, 200)); b = io.BytesIO(); im.save(b, "JPEG", quality=95); return b.getvalue()


def start():
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=dict(os.environ, DATA_DIR=D, PORT=str(PORT)),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try: urllib.request.urlopen(URL + "/healthz", timeout=1).read(); return p
        except Exception: time.sleep(0.5)


def call(path, data=None, method=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, method=method), timeout=60) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


def check(n, c, g):
    print(("OK   " if c else "FAIL ") + f"{n}: {g}")
    if not c: fails.append(n)


def wait_done():
    for _ in range(600):
        s = call("/api/process")[1]
        if s["state"] != "running": return s
        time.sleep(0.5)


def kill_when(srv, pred, sig):
    call("/api/process", b"", "POST")
    for _ in range(20000):
        try:
            if pred(json.load(open(MK))): break
        except (OSError, ValueError): pass
        time.sleep(0.002)
    else: return False
    srv.send_signal(sig); srv.wait(); return True


srv = start()
for i in range(8): call(f"/api/upload?name=P{i}.JPG", photo(i), "POST")
check("giết bằng SIGTERM giữa lúc Xử lý", kill_when(srv, lambda m: bool(m), signal.SIGTERM), "marker có file đang xử lý: " + str(json.load(open(MK))))
srv = start()
rej = os.path.join(D, "rejected")
check("1 lần chết không làm loại ảnh", not os.path.isdir(rej) or not os.listdir(rej), "originals=%d" % len(os.listdir(os.path.join(D, "originals"))))
call("/api/process", b"", "POST"); s = wait_done(); st = call("/api/stats")[1]
check("lượt Xử lý kế tiếp xong hết", s["state"] == "done" and s["rejected"] == [] and st["photos"] == 8 and st["pending"] == 0, (s["state"], s["rejected"], st["photos"]))
# ảnh X đã nằm trong marker của 1 lần chết trước, rồi chết lần nữa đúng lúc xử lý X → bị loại
body = photo(99); check("upload X", call("/api/upload?name=X.JPG", body, "POST")[0] == 200, "")
srv.kill(); srv.wait(); json.dump({"X.jpg": 1}, open(MK, "w"))
srv = start()
check("X được kiểm lại (chưa bị loại) khi khởi động", os.path.exists(os.path.join(D, "originals", "X.jpg")), "")
ok = kill_when(srv, lambda m: m.get("X.jpg") == 2, signal.SIGKILL)
check("SIGKILL lần 2 lúc đang xử lý X", ok, "")
srv = start()
check("X bị loại sau 2 lần chết", os.path.exists(os.path.join(rej, "X.jpg")) and not os.path.exists(os.path.join(D, "originals", "X.jpg")), "")
r = call("/api/upload?name=X.JPG", body, "POST"); check("tải lại cùng nội dung được nhận", r[0] == 200 and r[1]["status"] == "ok", r)
call("/api/process", b"", "POST"); s = wait_done(); st = call("/api/stats")[1]
check("X được xử lý lại", s["state"] == "done" and s["failed"] == 0 and st["photos"] == 9 and st["pending"] == 0, (s["state"], st["photos"], st["pending"]))
srv.terminate(); srv.wait()
sys.exit(1 if fails else 0)
