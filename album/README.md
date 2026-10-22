# album — tìm ảnh cưới theo khuôn mặt (album.doantien.love)

App Python độc lập (thư mục riêng, dependency riêng): chủ tải nhiều ảnh cưới lên qua trình duyệt,
bấm **Xử lý** để lập chỉ mục khuôn mặt (một job nền, tăng dần), khách tải một selfie và nhận lưới
ảnh có mặt mình, điểm cao trước, kèm khung đỏ quanh khuôn mặt khớp; bấm ảnh để xem bản lớn.
**Không có mật khẩu** (quyết định của chủ dự án) — xem "Chặn lạm dụng". Toàn bộ xử lý nằm trên máy
chủ của mình: ảnh, selfie, dữ liệu khuôn mặt không gửi đi đâu, giao diện không nạp CDN nào.
Triển khai Dokploy: xem [`DEPLOY.md`](DEPLOY.md).

> **Giấy phép model:** trọng số InsightFace `buffalo_l` chỉ cho dùng **phi thương mại**
> (nghiên cứu/cá nhân). Đừng dùng cho dịch vụ thương mại khi chưa xin phép tác giả.

## Chạy từ checkout sạch (giống container)

Cần Python 3.9–3.12. Mọi lệnh chạy trong `album/`.

```sh
cd album
python3 -m venv .venv                          # venv cục bộ, không cài global
.venv/bin/pip install -r requirements.txt
.venv/bin/python download_models.py            # ~290 MB tải về (kiểm SHA-256), giữ det_10g + w600k_r50 trong models/
DATA_DIR=./data PORT=8000 ALBUM_HOST=0.0.0.0 .venv/bin/python server.py
```

- `http://localhost:8000/manage` — trang chủ nhà: kéo thả/chọn nhiều ảnh (mỗi ảnh một request, tiến độ từng ảnh + tổng, trạng thái trùng/lỗi/quá lớn), dung lượng đĩa, nút **Xử lý** kèm tiến độ và số đã xong/lỗi/bị loại.
- `http://localhost:8000/` — trang khách: "Chụp ảnh selfie" (`capture="user"`) và "Chọn từ thư viện" trên điện thoại, một ô chọn/kéo thả trên máy tính; kết quả là lưới ảnh, bấm ảnh mở lightbox (vuốt/mũi tên/phím, **Lưu ảnh** tải ảnh gốc, nút Chia sẻ khi trình duyệt hỗ trợ Web Share file).
- Khách **không thấy điểm**. Hai mức cắt là hằng số ở đầu `static/guest.js`: `CONFIDENT_CUT = 0.40` ("Ảnh của bạn") và `MAYBE_CUT = 0.28` ("Có thể cũng là bạn", thu gọn). Chủ nhà xem điểm + thanh trượt ngưỡng ở `/?tune=1` (không có link tới đó từ trang khách).
- Dữ liệu trong `DATA_DIR`: `originals/` (ảnh gốc), `previews/` (2000 px JPEG) + `thumbs/` (640 px JPEG) (bản cũ, dự phòng), `i/` (WebP 240/480/2000 px đặt tên theo nội dung), `index.db`, `tmp/` (upload dở dang). Khởi động lại với cùng `DATA_DIR` là tìm được ngay, không phải Xử lý lại.

**Xử lý:** chạy trong thread nền của chính server (dùng lại model đã nạp), chỉ một job một lúc (bấm lần hai khi đang chạy → 409 "Đang xử lý rồi"), làm ảnh chưa lập chỉ mục (giai đoạn `index`: dò mặt) rồi sinh WebP + metadata cho ảnh còn thiếu (giai đoạn `derive`: **không dò mặt**, ảnh đã lập chỉ mục từ bản cũ cũng được bù ở đây; lỗi chỉ ghi log, không bao giờ chuyển ảnh sang `rejected/`). `GET /api/process` trả tiến độ (`phase`: `index`/`derive`/null, done/total của giai đoạn hiện tại, mặt, lỗi, `derive_failed`, thời gian). Ảnh lỗi chỉ báo lỗi, không dừng job. Tìm kiếm trong lúc xử lý vẫn chạy (chậm hơn) và thấy kết quả mới ngay khi mỗi ảnh xong.

Indexer dòng lệnh vẫn dùng được (cùng lõi `indexer.py`): `.venv/bin/python index_photos.py --photos <thư mục ảnh> --data-dir <DATA_DIR>`; ghi cả thumbnail và bản xem, nhưng dùng thư mục ảnh riêng (không phải `originals/`) thì không hiện nút tải ảnh gốc trên web khi `originals/` không chứa ảnh đó.

Đo RAM + dung lượng/ảnh qua đúng đường chạy thật (khởi động server, tải ảnh lên bằng HTTP, Xử lý, truy vấn; dùng data dir tạm):

```sh
.venv/bin/python measure.py --photos <thư mục/file ảnh, nên kèm 1 ảnh 20–45 MP> --queries <selfie...> [--env ALBUM_LOW_MEM=1]
```

## Trang công khai (gallery) + CDN

- `GET /api/gallery`: manifest gọn `{mv, cdn, dv, photos:[[id,w,h,t,v,c,name],…]}` (gzip, ETag/304, `max-age=60`; không có mặt/embedding). `t` = giờ chụp (EXIF DateTimeOriginal + SubSec, epoch giây; giờ trên ảnh coi như UTC, chỉ để sắp xếp), `v` = mã nội dung 8 hex (`sha256(gốc)[:6]` + `DERIV_VERSION` 2 hex), `c` = màu chủ đạo. Thứ tự: có `t` trước, rồi `t`, rồi tên. Dữ liệu ở bảng riêng `photo_meta` (không đụng bảng `photos`, nên quay về bản cũ vẫn chạy).
- `GET /i/{id}-{v}-{240|480|2000}.webp`: WebP (q78, sRGB theo ICC nhúng, đã xoay EXIF), `public, max-age=31536000, immutable`. Sai id/`v`/cỡ → 404 `no-store`. Đổi ảnh gốc hoặc `DERIV_VERSION` (`indexer.py`) → `v` đổi → URL mới.
- Ảnh gốc luôn cùng origin: `/originals/{id}` (attachment). `/thumbs`, `/previews` là bản JPEG cũ (`public, max-age=3600`) cho ảnh chưa có WebP.
- **Chặn theo Host:** chỉ host trong `ALBUM_APP_HOSTS` thấy cả app; host khác (tên miền CDN) chỉ GET/HEAD/OPTIONS `/i/`, `/thumbs/`, `/previews/`, `/static/`, `/healthz`, `/robots.txt`, còn lại 404 `no-store` (chạy trước khi đọc body).
- **Mã chủ nhà tuỳ chọn:** đặt `ALBUM_ADMIN_TOKEN` thì `/manage` cần cookie phiên `album_session` (không có → 401 + trang đăng nhập; `POST /api/admin-login` đặt cookie HttpOnly/SameSite=Strict, là HMAC của nhãn cố định khoá bằng mã nên không lộ mã), còn `POST /api/upload` và `POST /api/process` nhận cookie phiên hoặc header `X-Album-Token`. Mọi thông tin xác thực được đưa ra qua cùng một bộ giới hạn (10/phút/IP, 30/phút toàn cục → 429); request không đưa gì thì không tính. `GET /api/process`, `/api/stats` cũng chỉ chủ nhà (xem "Ẩn / hiện ảnh"). Chưa đặt = mở như trước.
- `ALBUM_CDN_ORIGIN` điền vào `cdn` của manifest (và `<link rel=preconnect>` thay cho `<!--__CDN_PRECONNECT__-->` trong HTML); để trống = cùng origin (công tắc tắt CDN).
- Nhóm chụp liên tiếp (`b`): `.venv/bin/python dev/burst_stats.py DATA_DIR` in số nhóm, histogram kích thước, nhóm lớn nhất. Đo độ trễ khi Xử lý: `dev/load_process.py`.
- Kiểm: `.venv/bin/python -m unittest discover -s dev -p 'test_*.py'` (test migration cần model + `/tmp/album-test/photos`, tự bỏ qua nếu thiếu).

## Ẩn / hiện ảnh (chủ nhà)

- Bảng `photo_hidden(id, hidden_at)` (không khoá ngoại) + `settings` (`search_includes_hidden`, vắng = BẬT). Xử lý, backfill, `--prune` và `--rebuild` không bao giờ xoá/sửa chúng (`--rebuild` đọc hai bảng trước khi xoá `index.db` rồi chép lại). Ảnh mới tải lên luôn hiện. **Đổi tên file = id mới = hiện.** Ngược lại, tải lại ảnh CÙNG tên (đường dẫn) với một ảnh đã ẩn thì id (băm của đường dẫn) trùng nên dòng ẩn được giữ và ảnh vẫn ẩn (an toàn theo hướng đóng). Đếm luôn JOIN `photos` (dòng mồ côi bị bỏ qua).
- API chỉ chủ nhà (cần cookie phiên/`X-Album-Token`; 401 nếu không; `require_admin` chạy trước khi đọc body; POST phải `Content-Type: application/json`, body ≤ 256 KB, `Origin`/`Sec-Fetch-Site` nếu có phải cùng origin, nếu không 403; mọi response `no-store`): `GET /api/admin/photos` (mọi ảnh + `hidden` + `b` gốc + `counts` + `settings`), `POST /api/admin/visibility {"ids":[1..5000 mã 16 hex],"hidden":bool}` (một giao dịch, idempotent, trả `changed/unknown/counts`, **dựng lại manifest công khai ngay trước khi trả lời, kể cả khi đang Xử lý**), `POST /api/admin/settings {"search_includes_hidden":bool}`. `GET /api/stats` và `GET /api/process` cũng chỉ chủ nhà. Khi `ALBUM_ADMIN_TOKEN` chưa đặt, `/api/admin/*` trả 403 (đóng): không thể ẩn/hiện hay đổi cài đặt nếu không có mã (stats/process GET mở như trước). Token ảnh ký thêm `hidden_at` hiện tại và (với ảnh ẩn) epoch cài đặt: ẩn ảnh, ẩn lại sau khi hiện, hoặc đổi `search_includes_hidden` làm token cấp trước đó hết hiệu lực; token của ảnh hiện không bị ảnh hưởng khi ẩn ảnh khác. `/thumbs`, `/previews` cache công khai `max-age=300`. `--rebuild` sao DB cũ sang `index.db.bak-<ts>` rồi đọc trạng thái ẩn từ bản sao; DB hỏng → dừng (dùng `--discard-owner-state` để bỏ).
- Công khai: `/api/gallery` chỉ có ảnh hiện (`Cache-Control: no-cache` + ETag; `b` = nhóm chụp liên tiếp tính lại trên các khung hiện, nhóm < 2 khung hiện → null; hiện lại → ETag y hệt ban đầu). `/originals`, `/thumbs`, `/previews` của ảnh ẩn → 404 y hệt id không tồn tại, trừ khi có `?t=` hợp lệ (token HMAC id + hạn 24 h, bí mật `DATA_DIR/.image_secret` 0600; có token → `Cache-Control: private, no-store`). `/i/{id}-{v}-{size}.webp` vẫn phục vụ ảnh ẩn (v là mã nội dung không đoán được; `v` kiểm theo meta của MỌI ảnh).
- Tìm kiếm: cài đặt TẮT → ảnh ẩn bị loại trước khi chọn tốt nhất/xếp hạng/cắt/đếm (`total_matches`, `truncated`). BẬT → ảnh ẩn vẫn trúng, không cờ (không tránh được việc suy ra ảnh đang ẩn); mọi kết quả (hiện và ẩn) mang `t`. `b` của ảnh hiện = đúng `b` công khai; ảnh ẩn: `b` = null, trừ khi ≥ 2 khung ẩn cùng nhóm gốc cùng trúng → id khung đầu trong số trúng. Phản hồi không còn `indexed_photos`/`indexed_faces`.

## Hai ngày (4/10 và 17/10) trong một album

- Danh sách ngày cố định trong code (`store.EVENTS`): `2026-10-04` (nhãn "4/10", **mặc định**) và `2026-10-17` ("17/10"). Máy chủ từ chối id lạ (400).
- Lưu ở bảng `photo_event(photo_id PRIMARY KEY, event)` (không khoá ngoại, không thêm cột vào `photos`, không đổi `DERIV_VERSION`). **Vắng dòng = 4/10**, nên mọi ảnh hiện có tự động thuộc 4/10; chỉ ảnh 17/10 mới có dòng (gán lại về 4/10 thì xoá dòng). Xử lý, backfill, `--prune` không đụng bảng này; `--rebuild` chép nó sang DB mới như `photo_hidden`. Bản code cũ bỏ qua bảng.
- Tải lên (`POST /api/upload?name=…&event=<id>`, mặc định 4/10): ngày được ghi cùng giao dịch với `originals`, khoá theo id cuối của ảnh (không đợi Xử lý). Ảnh **trùng SHA-256** KHÔNG đổi ngày của ảnh đã có (phản hồi `status: "duplicate"` kèm `event` hiện tại; muốn chuyển, dùng công cụ chuyển ngày). Tải lại ảnh bản gốc bị loại/mất (cùng id) thì ngày vừa chọn ghi đè.
- Chuyển ngày hàng loạt: `POST /api/admin/event {"ids": [≤5000 mã], "event": "<id>"}` (chủ nhà, cùng khuôn với `/api/admin/visibility`; id lạ bị bỏ qua, ảnh đang ẩn cũng chuyển được; dựng lại manifest trước khi trả lời). Trong /manage: chọn ảnh → "Chuyển sang ngày …" (có Hoàn tác); mỗi ô có huy hiệu ngày; thêm bộ lọc ngày.
- Manifest `/api/gallery`: mỗi hàng thêm **trường cuối** = id ngày (hàng admin cũng vậy, sau cờ ẩn); thêm `events: [{id, label, n}]` (n = số ảnh hiện, không tính ảnh ẩn). `guest.js` coi hàng thiếu trường = 4/10. Nhóm chụp liên tiếp (`b`) công khai không bao giờ gộp khung của hai ngày (nhóm gốc có thể chứa cả hai; mỗi ngày dồn riêng).
- Khách: tab "Ngày 4/10 / Ngày 17/10" (mặc định 4/10, chọn nằm trên URL `#ev=<id>` nên tải lại vẫn giữ). Các tab ngày **chỉ hiện khi ≥ 2 ngày có ảnh công khai** — production trông như cũ cho tới khi có ảnh 17/10. Tìm selfie của khách luôn là một `POST /api/search` không kèm phạm vi (kết quả cả hai ngày trong một danh sách). Máy chủ vẫn hỗ trợ `POST /api/search?scope=<id>|all` (mặc định `all`, sai → 400), áp cùng chỗ với lọc ảnh ẩn, TRƯỚC best-per-burst/xếp hạng/cắt/đếm; kết quả có thêm trường `ev`.
- Khách: tab "Video" cùng dải tab ngày (hiện khi ≥ 2 ngày có ảnh hoặc có video; `#ev=video`): thẻ poster, chạm mới tải và phát (`preload="none"`), mỗi lần một video, rời tab hoặc vào tìm kiếm thì dừng và nhả.
- Thử: `.venv/bin/python -m unittest dev/test_events.py`; giao diện: `dev/mock_gallery.py --events --admin-token test-token` rồi `dev/e2e.py --events …`.

## Cấu hình (biến môi trường hoặc tham số dòng lệnh)

| Env | Tham số | Mặc định |
|---|---|---|
| `DATA_DIR` (hoặc `ALBUM_DATA_DIR`) | `--data-dir` | `album/data` |
| `ALBUM_MODEL_DIR` | `--model-dir` | `album/models` (container: `/opt/models`) |
| `ALBUM_HOST` / `PORT` (hoặc `ALBUM_PORT`) | `--host` / `--port` | `127.0.0.1` / `8000` (container: `0.0.0.0`) |
| `ALBUM_MAX_UPLOAD_MB` | `--max-upload-mb` | `60` |
| `ALBUM_MIN_FREE_MB` | `--min-free-mb` | `5120` |
| `ALBUM_MAX_TOTAL_GB` | `--max-total-gb` | `20` (`0`/trống = không giới hạn tổng) |
| `ALBUM_INDEX_WINDOW` | — | `1` (tuần tự; `2` = giải mã trước một ảnh, nhanh hơn nhưng tốn RAM) |
| `ALBUM_THREADS` | `--threads` | `0` (theo số lõi; container đặt `2`) — luồng ORT của tìm kiếm |
| `ALBUM_PROCESS_THREADS` | `--process-threads` | lõi CPU khả dụng − 2 — luồng ORT của Xử lý (model riêng); `0` = dùng chung |
| `ALBUM_MAX_RESULTS` | — | `5000` |
| `ALBUM_LOW_MEM` | — | chưa đặt; `1` = tắt arena onnxruntime (RAM đỉnh thấp hơn; container đặt sẵn) |
| `ALBUM_APP_HOSTS` | — | `album.doantien.love,localhost,127.0.0.1` (host được dùng cả app) |
| `ALBUM_CDN_ORIGIN` | — | trống = cùng origin; vd. `https://img.rsrm.dev` |
| `ALBUM_ADMIN_TOKEN` | — | trống = không cần mã |
| `ALBUM_DERIVE_WORKERS` | — | `2` (số ảnh sinh WebP cùng lúc) |
| `ALBUM_DET_SIZE` | `index_photos.py --det-size` | `1600` |
| `ALBUM_PROVIDERS` | `--providers` | `CPUExecutionProvider` |

## Chặn lạm dụng (vì không có mật khẩu)

- Tải lên: chỉ `.jpg/.jpeg/.png/.webp` **và** Pillow đọc được header (định dạng thật JPEG/PNG/WEBP), tối đa **64 MP** (PNG khai báo hàng trăm MP ngốn ~18 B/điểm ảnh khi giải mã; kiểm lại ở indexer trước khi giải mã), tối đa `ALBUM_MAX_UPLOAD_MB`/ảnh (kiểm `Content-Length` rồi đếm khi stream; ghi thẳng ra đĩa, không buffer RAM). Tên file chỉ giữ phần cuối đã làm sạch (không path traversal); trùng nội dung (SHA-256) → bỏ qua; cùng tên khác nội dung → thêm hậu tố `-<sha8>`, không bao giờ ghi đè.
- Ổ đĩa chứa `DATA_DIR` còn trống < `ALBUM_MIN_FREE_MB` (mặc định 5120; kiểm lúc bắt đầu và mỗi 8 MB khi nhận) → 507 + thông báo tiếng Việt. Hạn mức tổng `ALBUM_MAX_TOTAL_GB` (mặc định 20; tính ảnh đã lưu + chỗ đặt trước cho upload đang chạy) → 507. Tối đa 4 upload cùng lúc, quá → 503 (trình duyệt tự thử lại). `/healthz` và `/api/stats` trả `disk_free`/`disk_total`.
- Ảnh làm process chết khi giải mã (OOM) được chuyển sang `DATA_DIR/rejected/` ở lần chạy sau (marker `decoding.json`); ảnh lỗi chỉ báo một lần (bảng `failed`), không thử lại mỗi lượt.
- Một job Xử lý một lúc; selfie ≤ 25 MB và ≤ **40 MP**, chỉ trong RAM; tối đa 2 truy vấn tính toán cùng lúc, cái thứ 3 → 503 "đang bận".
- Mọi response có `X-Robots-Tag: noindex, nofollow`; `/robots.txt` = `Disallow: /`. Không trả đường dẫn hệ thống: chỉ tên file đã lưu; lỗi job đã gỡ đường dẫn tuyệt đối.
- Sinh ảnh bom: `.venv/bin/python dev/make_poison.py /tmp/album-poison`. Kiểm bằng script: `.venv/bin/python dev/check_limits.py --photo <ảnh ≤ 2 MB> --selfie x [--poison <thư mục ảnh bom PNG>]`; với `--poison` kiểm thêm ảnh 150 MP, selfie 50 MP, 3 truy vấn đồng thời, burst upload (quota/slot), kill giữa lúc giải mã → `rejected/`.

## Lập chỉ mục

- CLI quét đệ quy `--photos` (jpg/png/webp/bmp/tif); web quét `DATA_DIR/originals/`, áp EXIF orientation, dò **mọi** khuôn mặt
  (ảnh nhóm cũng vậy), lưu embedding 512-d (float16) + bbox + điểm dò vào `data/index.db`
  , thumbnail 640 px vào `thumbs/` và bản xem 2000 px vào `previews/`; sau đó (CLI cũng vậy, tắt bằng `--no-derive`) WebP 240/480/2000 vào `i/` + `photo_meta`.
- **Chạy lại được, theo kiểu tăng dần:** ảnh không đổi (cùng đường dẫn + kích thước + mtime +
  det-size) bị bỏ qua. `--prune` xoá ảnh đã biến mất khỏi thư mục; `--rebuild` làm lại từ đầu.
  Một `--data-dir` ứng với đúng một thư mục ảnh.
- File hỏng/không đọc được, hay ảnh làm lỗi bước dò/nhúng: in `CẢNH BÁO` và bỏ qua, các ảnh còn lại vẫn chạy tiếp.
- CLI bỏ giới hạn số điểm ảnh của Pillow (ảnh của chủ nhà); server giữ giới hạn (upload ≤ 150 MP).
- `--min-face` (mặc định 40 px cạnh ngắn của bbox, trên ảnh gốc): bỏ mặt quá nhỏ vì embedding không đáng tin (đo cosine so với bản full-res: ≥66 px 0.93–0.99, ~44 px 0.70–0.95, ~29 px 0.05–0.78). Kích thước mỗi mặt suy ra được từ bbox lưu trong bảng `faces` (`min(x2-x1, y2-y1)`), nên đánh giá ảnh thật có thể thống kê sót theo cỡ mặt; mặt nhỏ hơn ngưỡng không được lưu, muốn đo thì chạy với `--min-face 0`.
- App tự nạp lại chỉ mục khi `index.db` đổi — không cần khởi động lại server.

### Độ phân giải dò (`--det-size`)

Ảnh cưới 20–45 MP; thu về 640 như `FaceAnalysis` mặc định sẽ làm mất mặt nhỏ ở ảnh nhóm.
`--det-size` là cạnh dài sau khi thu nhỏ (giữ tỉ lệ). Mặc định **1600**. Ngoài ra luôn có thêm
các lượt 640 và 320 rẻ tiền rồi gộp bằng NMS, vì SCRFD ngược lại bỏ sót mặt quá to (chân dung/selfie
cận cảnh, mặt chiếm ~80% khung: mất ở 640 và 1600 nhưng thấy ở 320). Cả đường lập chỉ mục lẫn đường truy vấn đều dùng cách này. Embedding luôn cắt từ ảnh gốc độ phân giải đầy đủ; mặt rất to (co >3 lần) được thu nhỏ theo diện tích trước khi warp để khỏi răng cưa. Ảnh xám 16-bit được co về 8-bit đúng thang.

Ảnh nội các Obama (3840×2560, 25 mặt thật; mặt nhỏ nhất ~32 px ở ảnh gốc), chỉ riêng bước dò, CPU M3:

| det-size | mặt tìm được | thời gian dò |
|---|---|---|
| 640 | 24 | 0.07 s |
| 1280 | 25 | 0.22 s |
| **1600** | 25 | 0.36 s |
| 2048 | 25 | 0.57 s |
| 3200 | 25 | 1.39 s |

Với ảnh thật 6000 px+ mặt sẽ nhỏ hơn sau khi thu; nếu đánh giá thấy sót mặt nhỏ, thử
`--det-size 2048` hay `2560` (chậm tỉ lệ thuận với diện tích).

## Chi tiết kỹ thuật

- Không dùng gói `insightface` (cần build C-extension): nạp thẳng `det_10g.onnx` (SCRFD) và
  `w600k_r50.onnx` (ArcFace) bằng onnxruntime, tự giải mã SCRFD và căn chỉnh 5 điểm (xem
  `faces.py`). Cùng model, cùng tiền xử lý với `FaceAnalysis`.
- CPU only. Đã thử CoreML EP trên onnxruntime 1.19.2: **lỗi** ở model dò (shape động), nên
  không dùng; `--providers` vẫn cho phép thử bản onnxruntime mới hơn.
- Ảnh tải lên cũng là body thô theo từng file (`POST /api/upload?name=…`, ghi stream ra `DATA_DIR/tmp` rồi đổi tên). Selfie: client gửi body thô tới `/api/search` (không multipart ⇒ không có file tạm do
  framework spool), server giải mã và xử lý trong RAM, không ghi gì ra đĩa.
- Selfie bị giới hạn 25 MB và 100 MP (kiểm kích thước khai báo trong header trước khi giải mã; vượt → HTTP 413). Không đọc được → 400, lỗi khác → 500. Trang `/docs`, `/redoc` đã tắt (mặc định nạp JS từ CDN).
- Kết quả tối đa 300 ảnh; nếu bị cắt, API trả `truncated`/`total_matches` và giao diện báo.
- `download_models.py` ghim SHA-256 của hai file `.onnx`: file thiếu/hỏng/dở dang bị tải lại, ghi qua file `.part` rồi mới đổi tên.
- Selfie nhiều mặt: chọn mặt có (diện tích × điểm dò) lớn nhất, bỏ mặt < 40 px; giao diện vẽ khung đỏ và hiện thông báo tiếng Việt khi có nhiều hơn một mặt. Mọi mặt đều < 40 px → 422 `face_too_small`.
- Điểm = cosine giữa hai embedding chuẩn hoá. Tham khảo: cùng người thường ≳ 0.35–0.4,
  khác người thường < 0.2; vùng 0.2–0.35 là vùng mơ hồ — cần đánh giá trên ảnh thật để chọn
  ngưỡng mặc định (thanh trượt bắt đầu ở 0.35, server chỉ trả về điểm ≥ 0.15, tối đa 300 ảnh).

## Kiểm thử giao diện (chỉ dev)

```sh
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/playwright install chromium
.venv/bin/python dev/e2e.py --photos a.jpg b.jpg big24mp.jpg --selfie s.jpg --out /tmp/shots --pid <PID server>
```
Lái trình duyệt thật (desktop cho /manage, điện thoại 390×844 cho trang khách): tải lô ảnh lên → Xử lý → tìm bằng selfie → mở lightbox → Lưu ảnh (kiểm file tải về), chụp từng bước, khẳng định không có request nào ra ngoài localhost (và đo RSS nếu có `--pid`). `--reduced-motion` chạy với `prefers-reduced-motion: reduce`.

## Không commit

`.venv/`, `data/`, `models/`, ảnh, selfie, chỉ mục — đã nằm trong `.gitignore`.

## Giao diện (static/)

Cùng bộ nhận diện với thiệp mời (giấy ngà, hoa màu nước, viền san hô mảnh; token màu copy từ `styles.css` của thiệp). Mọi thứ phục vụ cục bộ dưới `/static/` — **không** gọi Google Fonts/CDN nào lúc chạy.

- `index.html` + `guest.css`/`guest.js` (khách), `manage.html` + `manage.css`/`manage.js` (chủ nhà), `album.css` (dùng chung).
- Font self-host trong `fonts/` (woff2, tập con vietnamese + latin): Style Script (tên/tiêu đề), Cormorant Garamond (nội dung), Be Vietnam Pro (nhãn/nút). Khai báo ở `fonts.css`.
- Hoạ tiết trong `img/` (webp thu nhỏ từ `assets/` của thiệp: `bloom-corner`, `bloom-side`, `heart-logo`) + `favicon.svg`. Tổng `static/` ≈ 330 KB. `*.png` bị `.gitignore`: nếu cần icon PNG, `git add -f` có chủ ý.
- Tôn trọng `prefers-reduced-motion` (tắt mọi animation/transition); nút ≥ 44 px; focus rõ.
- Selfie lớn hơn 2400 px được thu về 2000 px ở trình duyệt trước khi gửi (chỉ để gửi nhanh và khỏi vướng giới hạn 40 MP); vẫn chỉ nằm trong RAM của máy chủ.
