#!/usr/bin/env bash
# Chạy MỘT lệnh: máy chủ thật (mô hình thật) trên DATA_DIR mới → tải lên 4/10 + 17/10 qua /manage → Xử lý → ảnh chụp, tìm selfie theo phạm vi,
# chuyển ngày, ẩn ảnh → khởi động lại máy chủ → --rebuild trên BẢN SAO. Chỉ localhost.
#   dùng: run_all.sh DIR_0410 DIR_1710 TAG [PORT]
#   biến môi trường: ALBUM_E2E_ROOT (mặc định ~/album-local/e2e): nơi đặt data-TAG/ và out-TAG/; ALBUM_E2E_TOKEN (mặc định e2e-token);
#                    PY (mặc định album/.venv/bin/python). Từ chối chạy nếu data-TAG đã có.
set -u
D4=${1:?thư mục ảnh 4/10}; D17=${2:?thư mục ảnh 17/10}; TAG=${3:?tag}; PORT=${4:-8830}
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"; PY=${PY:-$REPO/.venv/bin/python}
E=${ALBUM_E2E_ROOT:-$HOME/album-local/e2e}; TOKEN=${ALBUM_E2E_TOKEN:-e2e-token}
DATA=$E/data-$TAG; OUT=$E/out-$TAG; URL=http://127.0.0.1:$PORT
[ -e "$DATA" ] && { echo "từ chối: $DATA đã tồn tại"; exit 2; }
mkdir -p "$OUT"; export PYTHONDONTWRITEBYTECODE=1
(cd "$REPO" && git rev-parse --abbrev-ref HEAD && git rev-parse --short HEAD) | tee "$OUT/repo-version.txt"
start() { # máy chủ mới; chờ sẵn sàng bằng MỘT lệnh curl có giới hạn
  (cd "$REPO" && ALBUM_ADMIN_TOKEN=$TOKEN "$PY" server.py --data-dir "$DATA" --port "$PORT" >>"$OUT/server.log" 2>&1 &)
  curl -s -o /dev/null --retry 60 --retry-connrefused --retry-delay 1 "$URL/healthz" || { echo "máy chủ không lên"; exit 3; }
}
stop() { pkill -f "server.py --data-dir $DATA --port $PORT\$"; }  # neo cuối dòng lệnh: không trúng DATA khác chung tiền tố (vd. data-real vs data-real2)
start
echo "== MAIN =="
"$PY" -I "$HERE/e2e_run.py" main --url "$URL" --token "$TOKEN" --data-dir "$DATA" --out "$OUT" --dir0410 "$D4" --dir1710 "$D17" || { echo "MAIN HỎNG"; stop; exit 1; }
stop; wait 2>/dev/null
echo "== KIỂM TRA KHỞI ĐỘNG LẠI =="; start
"$PY" -I "$HERE/e2e_run.py" verify --url "$URL" --token "$TOKEN" --data-dir "$DATA" --out "$OUT" || { echo "VERIFY HỎNG"; stop; exit 1; }
stop
echo "== --rebuild TRÊN BẢN SAO =="
COPY=$E/data-$TAG-rebuildcopy; cp -R "$DATA" "$COPY"
( cd "$REPO" && time "$PY" index_photos.py --photos "$COPY/originals" --data-dir "$COPY" --model-dir "$REPO/models" --rebuild ) > "$OUT/rebuild.log" 2>&1; RC=$?
tail -15 "$OUT/rebuild.log"
[ $RC -eq 0 ] || { echo "REBUILD HỎNG rc=$RC"; exit 1; }
ls "$COPY"/index.db.bak-* >/dev/null 2>&1 || { echo "REBUILD không chạy (không có index.db.bak-* trong bản sao)"; exit 1; }
"$PY" -I "$HERE/e2e_run.py" dbcmp --db-a "$DATA/index.db" --db-b "$COPY/index.db" | tee "$OUT/dbcmp.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "DBCMP HỎNG"; exit 1; }
echo "DATA_DIR gốc không bị rebuild đụng tới: $DATA ; bản sao: $COPY ; kết quả: $OUT"
