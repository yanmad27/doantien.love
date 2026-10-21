# dev/realrun — chạy thử ảnh thật (4/10 + 17/10) trên backend thật

Chỉ dùng khi phát triển. Chỉ có script và văn bản: KHÔNG commit ảnh, mẫu WebP, thư mục dữ liệu, ảnh chụp màn hình hay JSON kiểm kê của ảnh thật.
Ảnh tải về/giải nén là dữ liệu không tin cậy: không chạy gì từ chúng; mọi script Python đọc ảnh chạy bằng `-I`, đường dẫn truyền qua tham số.
Dùng python của venv (`album/.venv/bin/python`, cần Pillow + Playwright: `pip install -r requirements-dev.txt`, `playwright install chromium`).

| File | Việc |
|---|---|
| `unpack.py` | giải nén an toàn từng zip vào thư mục MỚI `unz-N`, từ chối `..`/đường dẫn tuyệt đối/symlink, dồn ảnh vào một thư mục phẳng |
| `census.py` | kiểm kê thư mục ảnh so với `names-1710.txt`: có/thiếu/thừa, trùng sha256, ngày EXIF, máy ảnh → `.json` + `.md`; thoát 1 nếu không sạch |
| `names-1710.txt` | tên 1637 file của thư mục Drive 17/10 (mỗi dòng một tên; không có id/đường dẫn Drive) |
| `sample0410.py` | lấy N ảnh 4/10 công khai (GET đọc-thôi `/api/gallery`, `/i/<id>-<v>-2000.webp`) làm mẫu |
| `run_all.sh` | MỘT lệnh: máy chủ thật trên DATA_DIR mới → tải lên 4/10 + 17/10 qua /manage → Xử lý → ảnh chụp, tìm selfie theo phạm vi, chuyển ngày + hoàn tác, ẩn ảnh → khởi động lại → `--rebuild` trên bản sao |
| `e2e_run.py` | các bước `main` / `verify` / `dbcmp` mà `run_all.sh` gọi |
| `make_rehearsal.py` | chỉ để diễn tập khi chưa có ảnh 17/10 thật (KHÔNG phải bằng chứng) |

## Chạy
Mọi lệnh chạy từ **thư mục gốc repo**. Cần Python 3.9 (`python3 --version`; requirements ghim numpy 2.0.2 / onnxruntime 1.19.2) và ≥ ~40 GB trống ở `~/album-local`
(13 GB ảnh giải nén + 13 GB bản trong DATA_DIR + WebP + ngưỡng `--min-free-mb 5120`). Zip luôn chọn bằng `~/Downloads/10*.zip`.
```
P=album/.venv/bin/python
$P -I album/dev/realrun/unpack.py --zips ~/Downloads/10*.zip --work ~/album-local/incoming --flat ~/album-local/drive-1710
$P -I album/dev/realrun/census.py --dir ~/album-local/drive-1710 --names album/dev/realrun/names-1710.txt --out ~/album-local/census-1710 --expect-day 2026-10-17
$P -I album/dev/realrun/sample0410.py --url https://album.doantien.love --n 50 --out ~/album-local/sample-0410
album/dev/realrun/run_all.sh ~/album-local/sample-0410 ~/album-local/drive-1710 real [PORT]
```
`unpack.py` bị dừng giữa chừng: xoá `~/album-local/incoming/unz-*` và `~/album-local/drive-1710` mà lần chạy đó đã tạo rồi chạy lại; **không đụng tới các file zip trong `~/Downloads`**.

`run_all.sh DIR_0410 DIR_1710 TAG [PORT]`: mặc định cổng 8830; biến môi trường `ALBUM_E2E_ROOT` (mặc định `~/album-local/e2e`, nơi đặt `data-TAG/`, `out-TAG/`),
`ALBUM_E2E_TOKEN` (mặc định `e2e-token`), `PY`. Từ chối chạy nếu `data-TAG` đã có. Chỉ nói chuyện với localhost.
Kết quả trong `out-TAG/`: `shots/*.png`, `state.json` (số tải lên/lập chỉ mục/lỗi theo ngày, thời gian Xử lý), `log-main.txt`, `log-verify.txt`, `dbcmp.txt`, `rebuild.log`, `server.log`.
Ảnh chụp có thể chứa khuôn mặt khách: giữ ngoài repo.
