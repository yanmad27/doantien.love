#!/usr/bin/env python3
"""Quản lý danh sách khách + sinh trang thiệp riêng cho từng người.

Chỉ dùng thư viện chuẩn để chạy được trong Docker build stage.

    python3 tools/guests.py assign            # điền mã cho dòng chưa có mã
    python3 tools/guests.py links             # in name,code,url để dán vào danh sách gửi
    python3 tools/guests.py build <out_dir>   # sinh <out_dir>/<code>/index.html

Trang riêng được sinh từ index.html nên chỉ cần sửa template một chỗ.
"""
import csv
import html
import pathlib
import re
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "guests.csv"
TEMPLATE = ROOT / "index.html"
SITE = "https://doantien.love"
BASE = "/t"                     # doantien.love/t/<code>
ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"   # bỏ l/o/0/1 cho dễ đọc khi đọc miệng
CODE_LEN = 6


def read_rows():
    """Đọc CSV, giữ nguyên comment để ghi lại được."""
    raw = CSV_PATH.read_text(encoding="utf-8").splitlines()
    comments = [l for l in raw if l.startswith("#")]
    body = [l for l in raw if not l.startswith("#")]
    rows = list(csv.DictReader(body))
    return comments, rows


def write_rows(comments, rows):
    out = ["code,title,name"] + comments
    for r in rows:
        out.append(",".join(_csv_cell(r.get(k, "") or "") for k in ("code", "title", "name")))
    CSV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _csv_cell(v):
    return f'"{v}"' if any(c in v for c in ',"\n') else v


def guests():
    _, rows = read_rows()
    out = []
    for r in rows:
        name = (r.get("name") or "").strip()
        code = (r.get("code") or "").strip()
        if not name or not code:
            continue
        out.append({"code": code, "title": (r.get("title") or "").strip(), "name": name})
    return out


def full_name(g):
    return f'{g["title"]} {g["name"]}'.strip()


def cmd_assign():
    comments, rows = read_rows()
    used = {(r.get("code") or "").strip() for r in rows if (r.get("code") or "").strip()}
    added = 0
    for r in rows:
        if (r.get("code") or "").strip() or not (r.get("name") or "").strip():
            continue
        while True:
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            if code not in used:
                break
        used.add(code)
        r["code"] = code
        added += 1
    write_rows(comments, rows)
    print(f"đã sinh mã cho {added} khách, tổng {len(used)}")


def cmd_links():
    print("name,code,url")
    for g in guests():
        print(f'"{full_name(g)}",{g["code"]},{SITE}{BASE}/{g["code"]}/')


def render(template, g):
    """Chèn tên khách vào template. Fail nhanh nếu template đổi mà quên cập nhật ở đây."""
    who = html.escape(full_name(g))
    code = html.escape(g["code"])
    url = f"{SITE}{BASE}/{g['code']}/"
    doc = template

    def sub_str(old, new, label):
        nonlocal doc
        if old not in doc:
            raise SystemExit(f"[guests] không tìm thấy {label} trong index.html — template đã đổi?")
        doc = doc.replace(old, new, 1)

    def sub_re(pattern, new, label):
        """Dùng regex để không phụ thuộc từng ký tự của nội dung thẻ."""
        nonlocal doc
        doc, n = re.subn(pattern, lambda _m: new, doc, count=1)
        if n == 0:
            raise SystemExit(f"[guests] không thay được {label} trong index.html — template đã đổi?")

    sub_str("<!--GUEST_COVER-->", f'<p class="guest-cover">Thân mời <b>{who}</b></p>', "GUEST_COVER")
    sub_str("<!--GUEST_HERO-->", f'<p class="guest-hero">{who}</p>', "GUEST_HERO")

    # dataset cho script.js (điền sẵn RSVP + gắn mã vào payload)
    sub_str('<body class="is-closed">',
            f'<body class="is-closed" data-guest-name="{who}" data-guest-code="{code}">', "thẻ body")

    # trang nằm ở /t/<code>/ nên đường dẫn tương đối ./ phải thành tuyệt đối /
    doc = doc.replace('"./', '"/')

    sub_re(r"<title>.*?</title>",
           f"<title>Kính mời {who} — Khắc Doãn & Đông Tiên 04.10.2026</title>", "thẻ title")
    sub_re(r'<link rel="canonical" href="[^"]*">',
           f'<link rel="canonical" href="{url}">\n  <meta name="robots" content="noindex, nofollow">',
           "canonical")
    sub_re(r'<meta property="og:url" content="[^"]*">',
           f'<meta property="og:url" content="{url}">', "og:url")
    sub_re(r'<meta property="og:title" content="[^"]*">',
           f'<meta property="og:title" content="Kính mời {who}">', "og:title")
    sub_re(r'<meta property="og:description" content="[^"]*">',
           f'<meta property="og:description" content="Trân trọng mời {who} đến chung vui cùng gia đình '
           'chúng tôi, Chủ nhật 04.10.2026 tại Nhà hàng Long Phụng, Khánh Hòa.">', "og:description")
    return doc


def cmd_build(out_dir):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE.read_text(encoding="utf-8")
    people = guests()
    if not people:
        print("[guests] chưa có khách nào trong guests.csv — bỏ qua")
        return
    seen = set()
    for g in people:
        if g["code"] in seen:
            raise SystemExit(f"[guests] mã trùng: {g['code']}")
        seen.add(g["code"])
        if not re.fullmatch(r"[a-z0-9]{4,16}", g["code"]):
            raise SystemExit(f"[guests] mã không hợp lệ: {g['code']} (chỉ a-z0-9, 4-16 ký tự)")
        d = out / g["code"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(render(template, g), encoding="utf-8")
    print(f"[guests] đã sinh {len(people)} trang vào {out}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "assign":
        cmd_assign()
    elif cmd == "links":
        cmd_links()
    elif cmd == "build":
        cmd_build(sys.argv[2] if len(sys.argv) > 2 else "dist/t")
    else:
        raise SystemExit(__doc__)
