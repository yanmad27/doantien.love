"""Thống kê nhóm chụp liên tiếp (photo_meta.burst) của một data dir, để kiểm tay trên bộ ảnh thật sau khi deploy.

  .venv/bin/python dev/burst_stats.py DATA_DIR [--recompute] [--show N]

--recompute: tính lại nhóm trước khi thống kê (ghi vào DB). Mặc định chỉ đọc. In ra cục bộ, không gửi đi đâu."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indexer  # noqa: E402
import store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--recompute", action="store_true")
    ap.add_argument("--show", type=int, default=5, help="in tên file của N nhóm lớn nhất")
    a = ap.parse_args()
    db = store.connect(a.data_dir) if a.recompute else store.connect_ro(a.data_dir)
    if a.recompute:
        indexer.compute_bursts(db)
    st = indexer.burst_stats(db)
    print(f"ảnh: {st['photos']}  nhóm: {st['groups']}  khung trong nhóm: {st['frames_in_groups']}  ô (tile) sau khi gộp: {st['tiles']}")
    print("histogram kích thước nhóm {số khung: số nhóm}:", json.dumps(st["histogram"]))
    print(f"nhóm lớn nhất: {st['largest']} khung")
    rows = db.execute("SELECT m.burst, group_concat(p.path, ' ') FROM photo_meta m JOIN photos p ON p.id=m.id "
                      "WHERE m.burst IS NOT NULL GROUP BY m.burst ORDER BY count(*) DESC LIMIT ?", (a.show,)).fetchall()
    for b, names in rows:
        print(f"  {b}: {names}")


if __name__ == "__main__":
    main()
